import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "calico_stage2.py"
_SPEC = importlib.util.spec_from_file_location("calico_stage2", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
stage2 = importlib.util.module_from_spec(_SPEC)
sys.modules["calico_stage2"] = stage2
_SPEC.loader.exec_module(stage2)

ADDED_RULE = stage2.ADDED_RULE
CALICO_ACTIVE = stage2.CALICO_ACTIVE
CALICO_HELD = stage2.CALICO_HELD
CALICO_RECREATED = stage2.CALICO_RECREATED
KINDNET_ACTIVE = stage2.KINDNET_ACTIVE
KINDNET_HELD = stage2.KINDNET_HELD
MARKER_NAME = stage2.MARKER_NAME
ORIGINAL_RULES = stage2.ORIGINAL_RULES
SNAPSHOT_NAME = stage2.SNAPSHOT_NAME
Abort = stage2.Abort
FixtureCluster = stage2.FixtureCluster
Procedure = stage2.Procedure
assert_safe_argv = stage2.assert_safe_argv
default_state = stage2.default_state
main = stage2.main
sole_added_permission = stage2.sole_added_permission


def procedure(state, tmp_path, execute=True, write_baseline=True):
    clock = {"now": 0.0}

    def sleep(seconds):
        clock["now"] += seconds

    def now():
        return clock["now"]

    cluster = FixtureCluster(state)
    proc = Procedure(cluster, execute=execute, state_dir=tmp_path, sleep=sleep, now=now)
    if write_baseline:
        identity = state.get("identity") or {}
        stage2.atomic_write_private(
            tmp_path / stage2.BASELINE_NAME,
            json.dumps(
                {
                    "kube_system_uid": identity.get("kube_system_uid", ""),
                    "reclaim_policy": state.get("reclaim_policy") or "",
                    "server": identity.get("server", ""),
                },
                sort_keys=True,
            ),
        )
    return proc, cluster


def ready_for_cutover(state):
    state["ifaces"] = {"veth": 0, "cali": 0}
    state["results"] = []
    state["clear_ifaces_on_scale"] = True
    return state


def test_dry_run_does_not_mutate(tmp_path, capsys):
    proc, cluster = procedure(default_state(), tmp_path, execute=False)
    before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    proc.forward()
    after = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    assert cluster.calls == []
    assert after == before
    assert main(["--fixture", "--state-dir", str(tmp_path)]) == 0
    assert not list(tmp_path.glob("replicas-*.json"))
    assert "DRY-RUN stopped before cutover" in capsys.readouterr().err


def test_wrong_context_aborts_before_scale(tmp_path):
    state = default_state()
    state["context"] = "kind-urumi-k8s-test"
    proc, cluster = procedure(state, tmp_path)
    with pytest.raises(Abort) as caught:
        proc.forward()
    assert caught.value.decision == "preflight"
    assert cluster.calls == []


def test_replica_mismatch_stops_before_any_scale(tmp_path):
    state = ready_for_cutover(default_state())
    state["replicas"][("deploy", "default", "urumi-dashboard")] = 3
    proc, cluster = procedure(state, tmp_path)
    with pytest.raises(Abort) as caught:
        proc.forward()
    assert caught.value.decision == "scale-down"
    assert cluster.calls == []


def test_failed_scale_does_not_continue_or_restore(tmp_path):
    state = ready_for_cutover(default_state())
    state["fail_scale_namespace"] = "ingress-nginx"
    proc, cluster = procedure(state, tmp_path)
    with pytest.raises(Abort):
        proc.forward()
    scaled = [call for call in cluster.calls if call[0] == "scale"]
    assert [call[2] for call in scaled] == ["default", "ingress-nginx"]
    assert all(call[4] == 0 for call in scaled)
    assert "kube-system" not in [call[2] for call in scaled]
    assert "local-path-storage" not in [call[2] for call in scaled]
    assert state["replicas"][("deploy", "ingress-nginx", "ingress-nginx-controller")] == 1
    assert state["replicas"][("deploy", "kube-system", "coredns")] == 2
    assert state["replicas"][("deploy", "local-path-storage", "local-path-provisioner")] == 1


def test_changed_volume_stops_the_next_group(tmp_path):
    state = ready_for_cutover(default_state())
    state["break_volumes_after_scale"] = True
    proc, cluster = procedure(state, tmp_path)
    with pytest.raises(Abort) as caught:
        proc.forward()
    assert caught.value.decision == "scale-down"
    assert [call[2] for call in cluster.calls if call[0] == "scale"] == ["default"]


def test_skipped_mv_is_rejected_even_when_move_returns(tmp_path):
    state = ready_for_cutover(default_state())
    state["race_dest"] = CALICO_ACTIVE
    proc, cluster = procedure(state, tmp_path)
    with pytest.raises(Abort) as caught:
        proc.forward()
    assert caught.value.decision == "cni-calico-move"
    assert CALICO_HELD in state["files"]
    assert ("mv", CALICO_HELD, CALICO_ACTIVE) in cluster.calls
    assert not any(call[0] == "set-env" for call in cluster.calls)
    assert ("mv", KINDNET_ACTIVE, KINDNET_HELD) not in cluster.calls


def test_selection_timeout_stops_before_later_moves(tmp_path):
    state = ready_for_cutover(default_state())
    state["selection_stuck"] = True
    proc, cluster = procedure(state, tmp_path)
    with pytest.raises(Abort) as caught:
        proc.forward()
    assert "timeout waiting for crictl k8s-pod-network" in str(caught.value)
    assert caught.value.decision == "cni-calico-move"
    assert ("mv", KINDNET_ACTIVE, KINDNET_HELD) not in cluster.calls
    assert not any(call[0] == "set-env" for call in cluster.calls)


def test_crash_before_marker_still_removes_exact_rule(tmp_path):
    state = ready_for_cutover(default_state())
    state["rules"] = [*ORIGINAL_RULES, ADDED_RULE]
    (tmp_path / SNAPSHOT_NAME).write_text(json.dumps(list(ORIGINAL_RULES)))
    proc, cluster = procedure(state, tmp_path)
    proc.rollback_rbac("rbac-rollback")
    assert any(call[0] == "patch" for call in cluster.calls)
    assert state["rules"] == list(ORIGINAL_RULES)


def test_missing_snapshot_refuses_rbac_patch(tmp_path):
    state = ready_for_cutover(default_state())
    state["rules"] = [*ORIGINAL_RULES, ADDED_RULE]
    (tmp_path / MARKER_NAME).write_text("added\n")
    proc, cluster = procedure(state, tmp_path)
    with pytest.raises(Abort) as caught:
        proc.rollback_rbac("rbac-rollback")
    assert "manual intervention" in str(caught.value)
    assert not any(call[0] == "patch" for call in cluster.calls)
    assert sole_added_permission(state["rules"], list(ORIGINAL_RULES))


def test_copied_snapshot_is_not_applied(tmp_path):
    state = ready_for_cutover(default_state())
    foreign = [*ORIGINAL_RULES, {"apiGroups": ["other"], "resources": ["widgets"], "verbs": ["get"]}]
    (tmp_path / SNAPSHOT_NAME).write_text(json.dumps(foreign))
    proc, cluster = procedure(state, tmp_path)
    with pytest.raises(Abort) as caught:
        proc.rollback_rbac("rbac-rollback")
    assert "left unchanged" in str(caught.value)
    assert not any(call[0] == "patch" for call in cluster.calls)
    item = next(entry for entry in proc.build_audit_report()["items"] if entry["name"] == "rbac-snapshot")
    assert item["status"] == "inconsistent"


def test_missing_state_dir_stays_unknown_and_is_not_created(tmp_path):
    missing = tmp_path / "absent"
    proc, cluster = procedure(default_state(), missing, execute=False, write_baseline=False)
    with pytest.raises(Abort) as caught:
        proc.audit_recovery()
    assert caught.value.decision == "audit-recovery"
    assert not missing.exists()
    assert not any(call[0] in {"scale", "patch", "set-env", "mv", "rollout"} for call in cluster.calls)


def test_unexpected_rbac_diff_leaves_role_unchanged(tmp_path):
    state = ready_for_cutover(default_state())
    unrelated = {"apiGroups": [""], "resources": ["services"], "verbs": ["get"]}
    state["rules"] = [*ORIGINAL_RULES, ADDED_RULE, unrelated]
    (tmp_path / SNAPSHOT_NAME).write_text(json.dumps(list(ORIGINAL_RULES)))
    (tmp_path / MARKER_NAME).write_text("added\n")
    proc, cluster = procedure(state, tmp_path)
    with pytest.raises(Abort) as caught:
        proc.rollback_rbac("rbac-rollback")
    assert "left unchanged" in str(caught.value)
    assert not any(call[0] == "patch" for call in cluster.calls)
    assert unrelated in state["rules"]


def test_exact_added_rule_is_removed_only_with_marker_and_snapshot(tmp_path):
    state = ready_for_cutover(default_state())
    state["rules"] = [*ORIGINAL_RULES, ADDED_RULE]
    (tmp_path / SNAPSHOT_NAME).write_text(json.dumps(list(ORIGINAL_RULES)))
    (tmp_path / MARKER_NAME).write_text("added\n")
    proc, cluster = procedure(state, tmp_path)
    proc.rollback_rbac("rbac-rollback")
    assert any(call[0] == "patch" for call in cluster.calls)
    assert state["rules"] == list(ORIGINAL_RULES)
    assert not (tmp_path / MARKER_NAME).exists()


def test_recreated_file_is_never_overwritten(tmp_path):
    state = ready_for_cutover(default_state())
    state["files"] = {CALICO_ACTIVE, CALICO_RECREATED, KINDNET_HELD}
    proc, cluster = procedure(state, tmp_path)
    with pytest.raises(Abort) as caught:
        proc.rollback_during_rollout()
    assert caught.value.decision == "containment-disabled"
    assert not any(call[0] in {"mv", "set-env", "patch", "scale"} for call in cluster.calls)
    assert CALICO_ACTIVE in state["files"]


def test_forward_execute_preserves_route_sync_and_stops_before_restore(tmp_path):
    state = ready_for_cutover(default_state())
    proc, cluster = procedure(state, tmp_path)
    proc.forward()
    assert state["files"] == {
        CALICO_ACTIVE,
        KINDNET_HELD,
        "10-calico.conflist.disabled",
        "calico-kubeconfig",
    }
    env = {item["name"]: item for item in state["felix"]}
    assert env["FELIX_ROUTESYNCDISABLED"]["value"] == "true"
    assert "FELIX_INTERFACEPREFIX" not in env
    names = [call[0] for call in cluster.calls]
    assert names.index("rollout-restart") < names.index("set-env")
    rollouts = [index for index, call in enumerate(cluster.calls) if call[0] == "rollout"]
    set_env_at = names.index("set-env")
    assert rollouts[0] < set_env_at < rollouts[1]
    assert "configMapKeyRef" in json.dumps(env["FELIX_TYPHAK8SSERVICENAME"])
    assert (tmp_path / MARKER_NAME).exists()
    assert all(call[4] == 0 for call in cluster.calls if call[0] == "scale")
    assert "canary and replica restore are not part of this script" in proc.log[-1]


def test_script_refuses_secret_and_volume_deletion():
    with pytest.raises(Abort):
        assert_safe_argv(["docker", "exec", "node", "cat", "/etc/cni/net.d/calico-kubeconfig"])
    with pytest.raises(Abort):
        assert_safe_argv(["kubectl", "delete", "pvc", "data-urumi-gateway-db-0"])
    with pytest.raises(Abort):
        assert_safe_argv(["kind", "delete", "cluster"])


def test_gateway_database_pod_is_not_a_gateway_deployment_pod():
    gateway, replica_name, parents = stage2._deployment_pod("urumi-gateway")
    database = stage2._statefulset_pod("urumi-gateway-db")

    def replicaset_owners(name):
        if name == replica_name:
            return parents
        return None

    owned = stage2.select_owned_pods([gateway, database], "deploy", "urumi-gateway", replicaset_owners)
    assert owned == ["urumi-gateway-pod"]
    assert "urumi-gateway-db-0" not in owned
    assert stage2.select_owned_pods([database], "sts", "urumi-gateway-db", replicaset_owners) == ["urumi-gateway-db-0"]


def test_missing_ownership_fails_closed():
    pod = {"metadata": {"name": "urumi-gateway-abc", "ownerReferences": []}}
    with pytest.raises(Abort) as caught:
        stage2.select_owned_pods([pod], "deploy", "urumi-gateway", lambda _name: None)
    assert "ownership cannot be established" in str(caught.value)


def test_unexpected_owned_pod_is_included_and_exact_statefulset_matches():
    extra, replica_name, parents = stage2._deployment_pod("urumi-gateway")
    extra["metadata"]["name"] = "urumi-gateway-extra"
    exact = stage2._statefulset_pod("urumi-store-db")

    def replicaset_owners(name):
        return parents if name == replica_name else None

    assert "urumi-gateway-extra" in stage2.select_owned_pods([extra], "deploy", "urumi-gateway", replicaset_owners)
    assert stage2.select_owned_pods([exact], "sts", "urumi-store-db", replicaset_owners) == ["urumi-store-db-0"]
    assert stage2.select_owned_pods([exact], "deploy", "urumi-store-db", replicaset_owners) == []


def test_replica_records_do_not_overwrite_each_other(tmp_path):
    proc, _cluster = procedure(default_state(), tmp_path, execute=True)
    proc.scale_down()
    coredns_path = stage2.replica_record_path(tmp_path, "kube-system", "deploy", "coredns")
    controllers_path = stage2.replica_record_path(tmp_path, "kube-system", "deploy", "calico-kube-controllers")
    coredns = json.loads(coredns_path.read_text())
    controllers = json.loads(controllers_path.read_text())
    assert coredns == {"kind": "deploy", "name": "coredns", "namespace": "kube-system", "replicas": 2}
    assert controllers["name"] == "calico-kube-controllers" and controllers["replicas"] == 1
    assert not list(tmp_path.glob("*.tmp"))


def test_scale_down_does_not_wait_for_gateway_database(tmp_path):
    state = ready_for_cutover(default_state())
    proc, cluster = procedure(state, tmp_path, execute=True)
    proc.scale_down()
    assert ("scale", "sts", "default", ("urumi-gateway-db", "urumi-store-db"), 0) in cluster.calls
    assert state["replicas"][("deploy", "default", "urumi-gateway")] == 0
    assert state["replicas"][("sts", "default", "urumi-gateway-db")] == 0


def _containment_state():
    state = ready_for_cutover(default_state())
    state["files"] = {CALICO_ACTIVE, KINDNET_HELD}
    state["results"] = []
    return state


def test_containment_stops_before_daemonset_change_when_install_state_is_ambiguous(tmp_path):
    state = _containment_state()
    state["calico_pods"] = [
        {"name": "calico-node-a", "image": stage2.INSTALL_CNI_IMAGE, "terminated": True, "exit_code": 0},
        {"name": "calico-node-b", "image": stage2.INSTALL_CNI_IMAGE, "terminated": True, "exit_code": 0},
    ]
    proc, cluster = procedure(state, tmp_path)
    with pytest.raises(Abort) as caught:
        proc.rollback_during_rollout()
    assert caught.value.decision == "containment-disabled"
    assert not any(call[0] == "set-env" for call in cluster.calls)


def test_containment_stops_before_file_move_if_rollout_identity_is_ambiguous(tmp_path):
    state = _containment_state()
    state["duplicate_after_set_env"] = True
    (tmp_path / SNAPSHOT_NAME).write_text(json.dumps(list(ORIGINAL_RULES)))
    proc, cluster = procedure(state, tmp_path)
    with pytest.raises(Abort) as caught:
        proc.rollback_during_rollout()
    assert caught.value.decision == "containment-disabled"
    assert not any(call[0] in {"set-env", "mv"} for call in cluster.calls)


def _after_prefix(cluster):
    index = next(position for position, call in enumerate(cluster.calls) if call[0] == "set-env")
    return cluster.calls[index + 1 :]


def test_successful_empty_interface_and_result_listings():
    listing = "1: lo: <LOOPBACK,UP> mtu 65536 qdisc noqueue state UNKNOWN\n2: eth0: <BROADCAST> mtu 1500\n"
    assert stage2.parse_ip_link(listing) == {"veth": 0, "cali": 0}
    assert stage2.parse_result_names("") == []
    assert stage2.parse_result_names("loopback-abc-eth0\n") == []


def test_interface_and_result_failures_are_distinct():
    with pytest.raises(Abort) as command_failed:
        stage2.completed_stdout(stage2.Completed(1, "", "ip: command failed"), "interface-gate")
    assert "command failed (1)" in str(command_failed.value)
    with pytest.raises(Abort) as remote_failed:
        stage2.completed_stdout(stage2.Completed(125, "", "docker exec failed"), "interface-gate")
    assert "remote execution failed (125)" in str(remote_failed.value)
    with pytest.raises(Abort) as malformed_ip:
        stage2.parse_ip_link("not-an-interface\n")
    assert "malformed ip link output" in str(malformed_ip.value)
    with pytest.raises(Abort) as empty_ip:
        stage2.parse_ip_link("")
    assert "no output" in str(empty_ip.value)
    with pytest.raises(Abort) as result_command:
        stage2.completed_stdout(stage2.Completed(3, "", "unavailable"), "interface-gate")
    assert "command failed (3)" in str(result_command.value)
    with pytest.raises(Abort) as malformed_results:
        stage2.parse_result_names("kindnet file\n")
    assert "malformed CNI result listing" in str(malformed_results.value)


def test_missing_interface_evidence_does_not_pass(tmp_path):
    state = ready_for_cutover(default_state())
    del state["ifaces"]
    proc, _cluster = procedure(state, tmp_path)
    with pytest.raises(Abort) as caught:
        proc.assert_no_workload_ifaces("interface-gate")
    assert "interface evidence missing" in str(caught.value)


def _cutover_failure(tmp_path, **flags):
    state = ready_for_cutover(default_state())
    state.update(flags)
    proc, cluster = procedure(state, tmp_path)
    with pytest.raises(Abort) as caught:
        proc.forward()
    later = _after_prefix(cluster)
    assert not any(call[0] in {"mv", "set-env", "scale"} for call in later)
    assert all(count == 0 for count in state["replicas"].values())
    progress = json.loads((tmp_path / stage2.PROGRESS_NAME).read_text())
    assert progress["last_completed_mutation"] == "FELIX_INTERFACEPREFIX-removed"
    assert progress["observed"]["interface_prefix"] == "absent"
    assert progress["observed"]["route_sync_true"] is True
    assert "calico-kubeconfig" not in (tmp_path / stage2.PROGRESS_NAME).read_text()
    return caught, progress


def test_rollout_timeout_records_prefix_removal_and_stops(tmp_path):
    caught, progress = _cutover_failure(tmp_path, rollout_fail=True)
    assert "rollout failed" in str(caught.value)
    assert "Replicas were not restored" in str(caught.value)
    assert "rollout failed" in progress["detail"]


def test_multiple_calico_pods_after_prefix_removal_do_not_move_files(tmp_path):
    caught, _progress = _cutover_failure(tmp_path, duplicate_after_set_env=True)
    assert "ambiguous" in str(caught.value)


def test_installer_not_terminated_after_prefix_removal_stops(tmp_path):
    caught, _progress = _cutover_failure(tmp_path, install_stuck_after_set_env=True)
    assert "termination cannot be proven" in str(caught.value)


def test_unexpected_cni_files_after_prefix_removal_are_not_moved(tmp_path):
    caught, _progress = _cutover_failure(tmp_path, unexpected_files_after_set_env=True)
    assert "unexpected CNI file state" in str(caught.value)


def test_failed_runtime_selection_after_prefix_removal_stops(tmp_path):
    caught, progress = _cutover_failure(tmp_path, selection_stuck_after_prefix=True)
    assert "runtime selection failed" in str(caught.value)
    assert progress["observed"]["runtime"]["networks"] == ["cni-loopback", "kindnet"]


def test_crash_after_scale_leaves_intent_and_blocks_the_next_execute(tmp_path):
    state = ready_for_cutover(default_state())
    state["crash_after"] = "scale-default-deploy"
    proc, cluster = procedure(state, tmp_path)
    with pytest.raises(Abort) as caught:
        proc.forward()
    assert "before outcome recording" in str(caught.value)
    assert ("scale", "deploy", "default", stage2.SCALE_GROUPS[0][2], 0) in cluster.calls
    assert not any(call[0] == "scale" and call[2] != "default" for call in cluster.calls)
    intent = json.loads(next((tmp_path / "intents").glob("*.json")).read_text())
    assert intent["outcome"] is None
    assert state["replicas"][("deploy", "default", "urumi-gateway")] == 0
    assert state["replicas"][("sts", "default", "urumi-gateway-db")] == 1
    state["crash_after"] = None
    proc2, cluster2 = procedure(state, tmp_path)
    with pytest.raises(Abort) as blocked:
        proc2.forward()
    assert "audit-recovery" in str(blocked.value)
    assert cluster2.calls == []


def test_stale_progress_is_not_authoritative(tmp_path):
    state = default_state()
    (tmp_path / stage2.PROGRESS_NAME).write_text(
        json.dumps({"observed": {"interface_prefix": "password=hunter2 token=abc"}})
    )
    proc, cluster = procedure(state, tmp_path, execute=False)
    with pytest.raises(Abort):
        proc.audit_recovery()
    item = next(entry for entry in proc.build_audit_report()["items"] if entry["name"] == "progress.json")
    assert item["status"] == "inconsistent"
    assert item["observed"]["live_prefix"] == "veth"
    rendered = json.dumps(stage2.redact_obj(proc.build_audit_report()))
    assert "hunter2" not in rendered
    assert "token=abc" not in rendered
    assert not any(call[0] in {"scale", "patch", "set-env", "mv"} for call in cluster.calls)


def test_open_intent_is_interrupted(tmp_path):
    state = default_state()
    proc, cluster = procedure(state, tmp_path, execute=False)
    proc.begin_mutation("set-env", "daemonset/calico-node", {"interface_prefix": "absent"})
    with pytest.raises(Abort):
        proc.audit_recovery()
    item = next(entry for entry in proc.build_audit_report()["items"] if entry["name"] == "intents")
    assert item["status"] == "inconsistent"
    assert "do not execute" in item["next_step"].lower()
    assert not any(call[0] in {"scale", "patch", "set-env", "mv", "rollout"} for call in cluster.calls)


def test_observation_failures_are_unknown_not_healthy(tmp_path):
    state = default_state()
    state["context_failure"] = True
    state["replica_failure"] = True
    state["rules_failure"] = True
    state["felix_failure"] = True
    state["cni_failure"] = True
    state["iface_failure"] = "remote execution failed (125)"
    state["result_failure"] = "command failed (3)"
    state["rollout_read_failure"] = True
    proc, _cluster = procedure(state, tmp_path, execute=False)
    report = proc.build_audit_report()
    by_name = {item["name"]: item for item in report["items"]}
    assert by_name["context"]["status"] == "unknown"
    assert by_name["replicas/default/deploy/urumi-gateway"]["status"] == "unknown"
    assert by_name["replicas/default/deploy/urumi-gateway"]["observed"] is None
    assert by_name["clusterrole"]["status"] == "unknown"
    assert by_name["daemonset-env"]["status"] == "unknown"
    assert by_name["cni-files"]["status"] == "unknown"
    assert by_name["interfaces"]["status"] == "unknown"
    assert by_name["daemonset-rollout"]["status"] == "unknown"


def test_malformed_local_and_live_evidence_is_unknown(tmp_path):
    state = default_state()
    state["rules"] = "not-a-list"
    state["felix"] = {"broken": True}
    state["files"] = ["not-a-set"]
    path = stage2.replica_record_path(tmp_path, "default", "deploy", "urumi-gateway")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{")
    (tmp_path / stage2.SNAPSHOT_NAME).write_text("{")
    proc, _cluster = procedure(state, tmp_path, execute=False)
    report = proc.build_audit_report()
    by_name = {item["name"]: item for item in report["items"]}
    assert by_name["saved-replicas/default/deploy/urumi-gateway"]["status"] == "unknown"
    assert by_name["clusterrole"]["status"] == "unknown"
    assert by_name["daemonset-env"]["status"] == "unknown"
    assert by_name["cni-files"]["status"] == "unknown"
    assert by_name["rbac-snapshot"]["status"] == "unknown"


def test_partial_scale_is_reported_and_not_restored(tmp_path):
    state = default_state()
    state["replicas"][("deploy", "default", "urumi-gateway")] = 0
    state["pods"]["default"] = [
        pod
        for pod in state["pods"]["default"]
        if pod["metadata"]["name"] != "urumi-gateway-pod"
    ]
    saved = stage2.replica_record_path(tmp_path, "default", "deploy", "urumi-gateway")
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_text(json.dumps({"kind": "deploy", "name": "urumi-gateway", "namespace": "default", "replicas": 1}))
    proc, cluster = procedure(state, tmp_path, execute=False)
    report = proc.build_audit_report()
    item = next(entry for entry in report["items"] if entry["name"] == "replicas/default/deploy/urumi-gateway")
    saved_item = next(entry for entry in report["items"] if entry["name"] == "saved-replicas/default/deploy/urumi-gateway")
    assert item["status"] == "inconsistent"
    assert item["observed"] == 0
    assert saved_item["status"] == "inconsistent"
    assert "Do not restore replicas automatically" in item["next_step"]
    assert not any(call[0] == "scale" for call in cluster.calls)


def test_missing_local_files_stay_unknown_and_exit_2(tmp_path, capsys):
    proc, cluster = procedure(default_state(), tmp_path, execute=False)
    with pytest.raises(Abort) as caught:
        proc.audit_recovery()
    assert caught.value.decision == "audit-recovery"
    report = json.loads(capsys.readouterr().out)
    by_name = {item["name"]: item for item in report["items"]}
    assert by_name["rbac-snapshot"]["status"] == "unknown"
    assert by_name["rbac-snapshot"]["observed"] == "missing"
    assert by_name["progress.json"]["status"] == "unknown"
    assert by_name["progress.json"]["observed"] == "missing"
    assert by_name["clusterrole"]["status"] == "consistent"
    assert by_name["daemonset-env"]["status"] == "consistent"
    assert main(["--fixture", "--audit-recovery", "--state-dir", str(tmp_path / "other")]) == 2
    assert not any(call[0] in {"scale", "patch", "set-env", "mv", "rollout"} for call in cluster.calls)


def test_audit_cannot_reach_mutation_methods(tmp_path):
    class Guarded(FixtureCluster):
        def scale_to_zero(self, *args, **kwargs):
            raise AssertionError("scale")

        def patch_role(self, *args, **kwargs):
            raise AssertionError("patch")

        def set_env(self, *args, **kwargs):
            raise AssertionError("set-env")

        def move(self, *args, **kwargs):
            raise AssertionError("mv")

        def rollout_status(self, *args, **kwargs):
            raise AssertionError("rollout")

    cluster = Guarded(default_state())
    proc = Procedure(cluster, execute=True, state_dir=tmp_path)
    with pytest.raises(Abort) as caught:
        proc.audit_recovery()
    assert caught.value.decision == "audit-recovery"
    import inspect

    forbidden = (
        "self.cluster.scale_to_zero",
        "self.cluster.patch_role",
        "self.cluster.set_env",
        "self.cluster.move(",
        "self.cluster.rollout_status",
        "self.perform(",
        "self.forward(",
        "self.rollback_",
    )
    for name in ("build_audit_report", "audit_recovery"):
        source = inspect.getsource(getattr(Procedure, name))
        for forbidden_name in forbidden:
            assert forbidden_name not in source


def test_audit_recovery_calls_no_mutation(tmp_path, capsys):
    proc, cluster = procedure(default_state(), tmp_path, execute=False)
    with pytest.raises(Abort) as caught:
        proc.audit_recovery()
    assert caught.value.decision == "audit-recovery"
    assert not any(call[0] in {"scale", "patch", "set-env", "mv", "rollout"} for call in cluster.calls)
    printed = capsys.readouterr().out
    assert "calico-kubeconfig" not in printed
    assert main(["--fixture", "--audit-recovery", "--execute", "--state-dir", str(tmp_path)]) == 2


def test_redact_removes_kubeconfig_and_secret_values():
    dirty = {
        "body": "-----BEGIN CERTIFICATE-----\nabc",
        "note": "see calico-kubeconfig",
        "password": "hunter2",
        "prefix": "veth",
    }
    rendered = json.dumps(stage2.redact_obj(dirty))
    assert "hunter2" not in rendered
    assert "BEGIN CERTIFICATE" not in rendered
    assert "calico-kubeconfig" not in rendered
    assert "veth" in rendered


def test_source_has_no_scale_up_or_cni_conf_name_override():
    text = _PATH.read_text()
    assert "--replicas=0" in text
    assert "--replicas=1" not in text
    assert "CNI_CONF_NAME" not in text
    assert "|| true" not in text
    assert "kind-urumi-k8s-dev" in text
    assert "canary and replica restore are not part of this script" in text
    runbook = (_PATH.parent.parent / "docs" / "calico-stage2-runbook.md").read_text()
    assert "The plan below is not authorized and is not implemented in the script." in runbook
    assert "These commands are for a later approval. They are not approved now." in runbook
    assert "python3 scripts/calico_stage2.py --execute --state-dir /tmp/urumi-calico-stage2" in runbook
    assert "kindnet containment" in runbook
    assert "Do not create `progress.json`" in runbook
    assert "containment execution is disabled" in runbook
    assert "operator-baseline.json" in runbook
    assert "Do not run either containment command" in runbook
    assert "The two renames are not one transaction." in runbook


def _clear_crash(state):
    state["crash_before"] = None
    state["crash_after"] = None


def _interrupt(tmp_path, key, token):
    state = ready_for_cutover(default_state())
    state[key] = token
    proc, cluster = procedure(state, tmp_path)
    with pytest.raises(Abort) as caught:
        proc.forward()
    assert caught.value.decision == "interrupted"
    payloads = [json.loads(path.read_text()) for path in (tmp_path / "intents").glob("*.json")]
    open_items = [item for item in payloads if item.get("outcome") != "verified"]
    assert len(open_items) == 1
    assert open_items[0]["outcome"] is None
    assert not (tmp_path / stage2.PROGRESS_NAME).exists()
    replicas = dict(state["replicas"])
    _clear_crash(state)
    proc2, cluster2 = procedure(state, tmp_path)
    with pytest.raises(Abort) as blocked:
        proc2.forward()
    assert "intent present" in str(blocked.value)
    assert cluster2.calls == []
    assert state["replicas"] == replicas
    assert list((tmp_path / "intents").glob("*.json"))
    report = proc2.build_audit_report()
    classified = [item for item in report["items"] if item["name"].startswith("intent-classification/")]
    assert len(classified) == 1
    assert classified[0]["status"] in {"change_absent", "change_present"}
    assert "Forward execution stays blocked" in classified[0]["next_step"]
    assert "success" not in classified[0]["next_step"].lower()
    assert "progress.json" not in classified[0]["next_step"]
    return caught, cluster, classified[0]


@pytest.mark.parametrize(
    ("key", "token", "forbidden"),
    [
        ("crash_before", "rbac-add", ("patch", "mv", "set-env")),
        ("crash_after", "rbac-add", ("mv", "set-env")),
        ("crash_before", f"cni-move:{CALICO_HELD}->{CALICO_ACTIVE}", ("mv", "set-env")),
        ("crash_after", f"cni-move:{CALICO_HELD}->{CALICO_ACTIVE}", ("set-env",)),
        ("crash_before", f"cni-move:{KINDNET_ACTIVE}->{KINDNET_HELD}", ("set-env",)),
        ("crash_after", f"cni-move:{KINDNET_ACTIVE}->{KINDNET_HELD}", ("set-env",)),
        ("crash_before", "set-env", ("set-env",)),
        ("crash_after", "set-env", ()),
    ],
)
def test_interruption_leaves_intent_open_and_blocks_forward(tmp_path, key, token, forbidden):
    _caught, cluster, item = _interrupt(tmp_path, key, token)
    assert not any(call[0] in forbidden for call in cluster.calls)
    if key == "crash_before":
        assert item["status"] == "change_absent"
        assert "prior observation" in item["next_step"]
    else:
        assert item["status"] == "change_present"
        assert "not established" in item["next_step"]
    if token == "rbac-add" and key == "crash_after":
        assert any(call[0] == "patch" for call in cluster.calls)
    if token.startswith("cni-move") and key == "crash_after" and CALICO_HELD in token:
        assert ("mv", CALICO_HELD, CALICO_ACTIVE) in cluster.calls
        assert ("mv", KINDNET_ACTIVE, KINDNET_HELD) not in cluster.calls
    if token.startswith("cni-move") and KINDNET_ACTIVE in token and key == "crash_before":
        assert ("mv", CALICO_HELD, CALICO_ACTIVE) in cluster.calls
        assert ("mv", KINDNET_ACTIVE, KINDNET_HELD) not in cluster.calls
    if token == "set-env" and key == "crash_after":
        assert ("set-env", "FELIX_INTERFACEPREFIX-") in cluster.calls
        set_at = next(index for index, call in enumerate(cluster.calls) if call[0] == "set-env")
        assert any(call[0] == "rollout" for call in cluster.calls[:set_at])
        assert not any(call[0] == "rollout" for call in cluster.calls[set_at + 1 :])


def _write_open_intent(proc, mutation, target, prior, intended):
    proc.state_dir.mkdir(parents=True, exist_ok=True)
    stage2.atomic_write_text(
        proc.intent_path(mutation, target),
        json.dumps(
            {
                "intended": intended,
                "mutation": mutation,
                "outcome": None,
                "prior": prior,
                "target": target,
                "timestamp": "2026-10-16T00:00:00+00:00",
            },
            sort_keys=True,
        ),
    )


def _env_prior(prefix="veth"):
    return {"interface_prefix": prefix, "route_sync_true": True}


def test_open_intent_matching_prior_blocks_without_claiming_non_execution(tmp_path):
    state = default_state()
    proc, cluster = procedure(state, tmp_path)
    _write_open_intent(
        proc,
        "set-env",
        "daemonset/calico-node",
        _env_prior("veth"),
        {"interface_prefix": "absent", "route_sync": True},
    )
    item = next(entry for entry in proc.build_audit_report()["items"] if entry["name"].startswith("intent-classification/"))
    assert item["status"] == "change_absent"
    assert item["observed"]["live"]["interface_prefix"] == "veth"
    assert "prior observation" in item["next_step"]
    assert "success" not in item["next_step"].lower()
    with pytest.raises(Abort) as blocked:
        proc.forward()
    assert "intent present" in str(blocked.value)
    assert not any(call[0] in {"scale", "patch", "set-env", "mv", "rollout"} for call in cluster.calls)
    assert not (tmp_path / stage2.PROGRESS_NAME).exists()


def test_open_intent_matching_intended_blocks_without_claiming_success(tmp_path):
    state = default_state()
    state["felix"] = [item for item in state["felix"] if item["name"] != "FELIX_INTERFACEPREFIX"]
    proc, cluster = procedure(state, tmp_path)
    _write_open_intent(
        proc,
        "set-env",
        "daemonset/calico-node",
        _env_prior("veth"),
        {"interface_prefix": "absent", "route_sync": True},
    )
    item = next(entry for entry in proc.build_audit_report()["items"] if entry["name"].startswith("intent-classification/"))
    assert item["status"] == "change_present"
    assert item["observed"]["live"]["interface_prefix"] == "absent"
    assert "not established" in item["next_step"]
    assert "success" not in item["next_step"].lower()
    with pytest.raises(Abort) as blocked:
        proc.forward()
    assert "intent present" in str(blocked.value)
    assert not any(call[0] in {"scale", "patch", "set-env", "mv", "rollout"} for call in cluster.calls)


def test_open_intent_mixed_live_state_is_ambiguous(tmp_path):
    state = default_state()
    for item in state["felix"]:
        if item["name"] == "FELIX_INTERFACEPREFIX":
            item["value"] = "cali"
    proc, _cluster = procedure(state, tmp_path, execute=False)
    _write_open_intent(
        proc,
        "set-env",
        "daemonset/calico-node",
        _env_prior("veth"),
        {"interface_prefix": "absent", "route_sync": True},
    )
    item = next(entry for entry in proc.build_audit_report()["items"] if entry["name"].startswith("intent-classification/"))
    assert item["status"] == "mixed_or_ambiguous"
    assert item["observed"]["live"]["interface_prefix"] == "present-unprinted"
    assert "conflict" in item["next_step"]


def test_open_intent_is_unreadable_when_live_evidence_is_missing(tmp_path):
    state = default_state()
    state["felix_failure"] = True
    proc, _cluster = procedure(state, tmp_path, execute=False)
    _write_open_intent(
        proc,
        "set-env",
        "daemonset/calico-node",
        _env_prior("veth"),
        {"interface_prefix": "absent", "route_sync": True},
    )
    item = next(entry for entry in proc.build_audit_report()["items"] if entry["name"].startswith("intent-classification/"))
    assert item["status"] == "unreadable"
    assert item["observed"]["live"] is None
    assert item["status"] != "change_absent"
    assert "not classified" in item["next_step"]
    incomplete = procedure(default_state(), tmp_path / "missing", execute=False)[0]
    _write_open_intent(incomplete, "set-env", "daemonset/calico-node", {}, {"interface_prefix": "absent"})
    missing = next(
        entry for entry in incomplete.build_audit_report()["items"] if entry["name"].startswith("intent-classification/")
    )
    assert missing["status"] == "unreadable"


def test_prefix_gate_rejects_installer_file_and_runtime_mismatches(tmp_path):
    cases = []
    wrong_image = ready_for_cutover(default_state())
    wrong_image["calico_pods"][0]["image"] = "docker.io/calico/cni:v0.0.0"
    cases.append(wrong_image)
    missing_name = ready_for_cutover(default_state())
    missing_name["calico_pods"][0]["name"] = ""
    cases.append(missing_name)
    running = ready_for_cutover(default_state())
    running["calico_pods"][0]["terminated"] = False
    running["calico_pods"][0]["exit_code"] = None
    cases.append(running)
    failed_exit = ready_for_cutover(default_state())
    failed_exit["calico_pods"][0]["exit_code"] = 1
    cases.append(failed_exit)
    duplicate = ready_for_cutover(default_state())
    duplicate["calico_pods"] = [
        duplicate["calico_pods"][0],
        {"name": "calico-node-other", "image": stage2.INSTALL_CNI_IMAGE, "terminated": True, "exit_code": 0},
    ]
    cases.append(duplicate)
    extra_file = ready_for_cutover(default_state())
    extra_file["files"] = set(extra_file["files"]) | {CALICO_RECREATED}
    cases.append(extra_file)
    runtime = ready_for_cutover(default_state())
    runtime["selection_fails_at_prefix_gate"] = True
    cases.append(runtime)
    for state in cases:
        proc, cluster = procedure(state, tmp_path / state["calico_pods"][0].get("name", "pod"))
        with pytest.raises(Abort) as caught:
            proc.forward()
        assert "before" in str(caught.value) or "crictl selection" in str(caught.value)
        assert not any(call[0] == "set-env" for call in cluster.calls)
        assert all(count == 0 for count in state["replicas"].values())


def test_containment_collision_and_missing_files_abort_before_rename(tmp_path):
    collision = _containment_state()
    collision["files"] = {CALICO_ACTIVE, KINDNET_HELD, KINDNET_ACTIVE}
    missing = _containment_state()
    missing["files"] = {KINDNET_HELD}
    unexpected = _containment_state()
    unexpected["files"] = {CALICO_ACTIVE, CALICO_RECREATED, KINDNET_HELD}
    absent_kindnet = _containment_state()
    absent_kindnet["files"] = {CALICO_ACTIVE}
    for state in (collision, missing, unexpected, absent_kindnet):
        proc, cluster = procedure(state, tmp_path)
        before = set(state["files"])
        with pytest.raises(Abort):
            proc.rollback_during_rollout()
        assert state["files"] == before
        assert not any(call[0] in {"mv", "set-env", "scale", "patch"} for call in cluster.calls)


def test_second_containment_move_failure_is_not_reversed_or_restored(tmp_path):
    state = _containment_state()
    replicas = dict(state["replicas"])
    files = set(state["files"])
    proc, cluster = procedure(state, tmp_path)
    with pytest.raises(Abort) as caught:
        proc.rollback_during_rollout()
    assert caught.value.decision == "containment-disabled"
    assert state["files"] == files
    assert state["replicas"] == replicas
    assert cluster.calls == []


def test_mixed_scale_intent_blocks_without_another_scale(tmp_path):
    state = default_state()
    state["replicas"][("deploy", "default", "urumi-gateway")] = 0
    state["pods"]["default"] = [
        pod for pod in state["pods"]["default"] if pod["metadata"]["name"] != "urumi-gateway-pod"
    ]
    proc, cluster = procedure(state, tmp_path)
    prior = proc.public_observation()
    prior["replicas"]["deploy/default/urumi-gateway"] = 1
    _write_open_intent(
        proc,
        "scale-default-deploy",
        "urumi-dashboard,urumi-gateway",
        prior,
        {"replicas": 0},
    )
    item = next(entry for entry in proc.build_audit_report()["items"] if entry["name"].startswith("intent-classification/"))
    assert item["status"] == "mixed_or_ambiguous"
    assert item["observed"]["live"]["urumi-gateway"] == 0
    assert item["observed"]["live"]["urumi-dashboard"] == 1
    with pytest.raises(Abort) as blocked:
        proc.forward()
    assert "intent present" in str(blocked.value)
    assert not any(call[0] in {"scale", "patch", "set-env", "mv"} for call in cluster.calls)
    assert state["replicas"][("deploy", "default", "urumi-dashboard")] == 1


def test_change_present_does_not_read_installer_or_runtime(tmp_path):
    state = default_state()
    state["felix"] = [item for item in state["felix"] if item["name"] != "FELIX_INTERFACEPREFIX"]
    state["calico_pods"] = [{"name": "calico-node-stale", "image": "docker.io/calico/cni:v0.0.0", "terminated": True, "exit_code": 0}]
    state["networks"] = ("OK", [("kindnet", ["ptp"])])
    proc, _cluster = procedure(state, tmp_path, execute=False)
    _write_open_intent(
        proc,
        "set-env",
        "daemonset/calico-node",
        _env_prior("veth"),
        {"interface_prefix": "absent", "route_sync": True},
    )
    item = next(entry for entry in proc.build_audit_report()["items"] if entry["name"].startswith("intent-classification/"))
    assert item["status"] == "change_present"
    assert "install" not in item["observed"]["live"]
    assert "not established" in item["next_step"]


def test_runtime_disagreement_after_containment_renames_is_not_reversed(tmp_path):
    state = _containment_state()
    files = set(state["files"])
    proc, cluster = procedure(state, tmp_path)
    with pytest.raises(Abort) as caught:
        proc.rollback_during_rollout()
    assert caught.value.decision == "containment-disabled"
    assert state["files"] == files
    assert cluster.calls == []


def test_containment_collision_after_env_change_does_not_rename(tmp_path):
    state = _containment_state()
    files = set(state["files"])
    proc, cluster = procedure(state, tmp_path)
    with pytest.raises(Abort) as caught:
        proc.rollback_during_rollout()
    assert caught.value.decision == "containment-disabled"
    assert state["files"] == files
    assert cluster.calls == []
    assert main(["--fixture", "--rollback", "containment-during-rollout", "--execute", "--state-dir", str(tmp_path / "cli")]) == 2


def test_cluster_identity_must_match_before_scale(tmp_path):
    state = ready_for_cutover(default_state())
    proc, cluster = procedure(state, tmp_path)
    proc.forward()
    assert any(call[0] == "scale" for call in cluster.calls)

    mismatch = ready_for_cutover(default_state())
    mismatch["identity"] = {"kube_system_uid": "other", "server": "https://fixture.invalid:6443"}
    proc, cluster = procedure(mismatch, tmp_path / "uid")
    mismatch["identity"]["kube_system_uid"] = "changed-after-file"
    with pytest.raises(Abort) as uid_mismatch:
        proc.forward()
    assert "cluster identity" in str(uid_mismatch.value)
    assert cluster.calls == []

    endpoint = ready_for_cutover(default_state())
    proc, cluster = procedure(endpoint, tmp_path / "endpoint")
    endpoint["identity"]["server"] = "https://other.invalid:6443"
    with pytest.raises(Abort) as endpoint_mismatch:
        proc.forward()
    assert cluster.calls == []
    assert endpoint_mismatch.value.decision == "preflight"

    unreadable = ready_for_cutover(default_state())
    unreadable["identity_failure"] = True
    proc, cluster = procedure(unreadable, tmp_path / "unread")
    with pytest.raises(Abort):
        proc.forward()
    assert cluster.calls == []

    missing = ready_for_cutover(default_state())
    proc, cluster = procedure(missing, tmp_path / "nobaseline", write_baseline=False)
    with pytest.raises(Abort) as missing_baseline:
        proc.forward()
    assert "operator baseline is missing" in str(missing_baseline.value)
    assert cluster.calls == []


def test_reclaim_policy_and_binding_block_before_scale(tmp_path):
    unexpected = ready_for_cutover(default_state())
    proc, cluster = procedure(unexpected, tmp_path / "policy")
    unexpected["reclaim_policy"] = "Retain"
    with pytest.raises(Abort) as policy:
        proc.forward()
    assert "binding changed" in str(policy.value)
    assert cluster.calls == []

    missing = ready_for_cutover(default_state())
    proc, cluster = procedure(missing, tmp_path / "missing-policy")
    missing["reclaim_policy"] = ""
    with pytest.raises(Abort):
        proc.forward()
    assert cluster.calls == []

    changed = ready_for_cutover(default_state())
    changed["volumes_ok"] = False
    proc, cluster = procedure(changed, tmp_path / "binding")
    with pytest.raises(Abort):
        proc.forward()
    assert cluster.calls == []


def test_snapshot_is_private_atomic_and_blocks_rbac_on_failure(tmp_path):
    state = ready_for_cutover(default_state())
    proc, cluster = procedure(state, tmp_path)
    proc.add_rbac()
    path = tmp_path / SNAPSHOT_NAME
    assert (path.stat().st_mode & 0o777) == 0o600
    assert not list(tmp_path.glob("*.tmp"))
    assert any(call[0] == "patch" for call in cluster.calls)
    saved = path.read_text()

    again = ready_for_cutover(default_state())
    again["rules"] = json.loads(saved)
    proc2, cluster2 = procedure(again, tmp_path / "same")
    same = tmp_path / "same" / SNAPSHOT_NAME
    same.write_text(saved)
    os.chmod(same, 0o600)
    proc2.add_rbac()
    assert same.read_text() == saved
    assert any(call[0] == "patch" for call in cluster2.calls)

    foreign = ready_for_cutover(default_state())
    proc3, cluster3 = procedure(foreign, tmp_path / "foreign")
    other = tmp_path / "foreign" / SNAPSHOT_NAME
    other.write_text("{}\n")
    os.chmod(other, 0o600)
    with pytest.raises(Abort) as refused:
        proc3.add_rbac()
    assert "not replaced" in str(refused.value)
    assert other.read_text() == "{}\n"
    assert not any(call[0] == "patch" for call in cluster3.calls)

    loose = ready_for_cutover(default_state())
    proc4, cluster4 = procedure(loose, tmp_path / "loose")
    loose_path = tmp_path / "loose" / SNAPSHOT_NAME
    loose_path.write_text(saved)
    os.chmod(loose_path, 0o644)
    with pytest.raises(Abort):
        proc4.add_rbac()
    assert (loose_path.stat().st_mode & 0o777) == 0o644
    assert not any(call[0] == "patch" for call in cluster4.calls)

    blocked = ready_for_cutover(default_state())
    proc5, cluster5 = procedure(blocked, tmp_path / "blocked")
    os.chmod(tmp_path / "blocked", 0o500)
    try:
        with pytest.raises(Abort) as failed_write:
            proc5.add_rbac()
    finally:
        os.chmod(tmp_path / "blocked", 0o700)
    assert "not saved" in str(failed_write.value)
    assert not any(call[0] == "patch" for call in cluster5.calls)


def test_install_watermark_rejects_stale_identity_before_or_after_prefix_removal(tmp_path):
    def run(flag, **pod):
        state = ready_for_cutover(default_state())
        state["calico_pods"][0].update(pod)
        if flag:
            state[flag] = True
        directory = tmp_path / (flag or pod.get("finished_at", "ok")).replace(":", "").replace("+", "")
        proc, cluster = procedure(state, directory)
        with pytest.raises(Abort):
            proc.forward()
        return cluster

    missing_time = run(None, finished_at="")
    assert not any(call[0] == "set-env" for call in missing_time.calls)
    bad_time = run(None, finished_at="yesterday")
    assert not any(call[0] == "set-env" for call in bad_time.calls)
    missing_uid = run(None, uid="")
    assert not any(call[0] == "set-env" for call in missing_uid.calls)
    missing_container = run(None, container_id="")
    assert not any(call[0] == "set-env" for call in missing_container.calls)
    running = run(None, terminated=False, exit_code=None)
    assert not any(call[0] == "set-env" for call in running.calls)
    failed = run(None, exit_code=1)
    assert not any(call[0] == "set-env" for call in failed.calls)

    stale = run("keep_install_identity")
    assert any(call[0] == "rollout-restart" for call in stale.calls)
    assert not any(call[0] == "set-env" for call in stale.calls)
    assert any(item["name"] == "FELIX_INTERFACEPREFIX" for item in stale.state["felix"])

    current = ready_for_cutover(default_state())
    proc, cluster = procedure(current, tmp_path / "current")
    proc.forward()
    pod = current["calico_pods"][0]
    assert pod["uid"].endswith("-after")
    assert pod["container_id"].endswith("-after")
    assert pod["finished_at"] == "2026-10-16T12:00:00+00:00"
    assert pod["exit_code"] == 0
    assert any(call[0] == "set-env" for call in cluster.calls)


def test_current_context_read_is_the_only_unpinned_kubectl_command(monkeypatch):
    assert stage2.CURRENT_CONTEXT_ARGV == ("kubectl", "config", "current-context")
    stage2.assert_kubectl_pinned(list(stage2.CURRENT_CONTEXT_ARGV))
    stage2.assert_kubectl_pinned(["kubectl", "--context", stage2.CONTEXT, "get", "ns"])
    stage2.assert_kubectl_pinned(["kubectl", "get", "--context", stage2.CONTEXT, "pods"])
    stage2.assert_kubectl_pinned(["docker", "inspect", "--format", "{{.Name}}", "node"])
    flagged = ["kubectl", "--context", stage2.CONTEXT, "config", "current-context"]
    assert tuple(flagged) != stage2.CURRENT_CONTEXT_ARGV
    stage2.assert_kubectl_pinned(flagged)
    for argv in (
        ["kubectl", "config", "current-context", "extra"],
        ["kubectl", "get", "pods"],
        ["kubectl", "--context", "kind-urumi-k8s-test", "get", "ns"],
        ["kubectl", "--context", "kind-urumi-k8s-test", "get", "ns", stage2.CONTEXT],
        ["kubectl", "--context"],
        ["kubectl", "get", "--context"],
        ["kubectl", "--context", stage2.CONTEXT, "--context", stage2.CONTEXT, "get", "ns"],
        ["kubectl", "config", "current-context", "--context", "kind-urumi-k8s-test"],
        ["kubectl", "--context=kind-urumi-k8s-dev", "get", "ns"],
    ):
        with pytest.raises(Abort):
            stage2.assert_kubectl_pinned(argv)

    calls = []
    responses = iter(
        (
            (0, "kind-urumi-k8s-dev\n"),
            (0, "kind-urumi-k8s-test\n"),
            (0, ""),
            (0, "kind-urumi-k8s-dev\nextra\n"),
            (1, ""),
        )
    )

    def fake_run(argv, check, text, capture_output):
        calls.append(list(argv))
        code, stdout = next(responses)

        class Proc:
            returncode = code
            stderr = "failed" if code else ""

        Proc.stdout = stdout
        return Proc()

    monkeypatch.setattr(stage2.subprocess, "run", fake_run)
    cluster = stage2.LiveCluster()
    assert cluster.context() == stage2.CONTEXT
    with pytest.raises(Abort):
        cluster.context()
    with pytest.raises(Abort):
        cluster.context()
    with pytest.raises(Abort):
        cluster.context()
    with pytest.raises(Abort):
        cluster.context()
    assert calls == [list(stage2.CURRENT_CONTEXT_ARGV)] * 5
    with pytest.raises(Abort):
        cluster._run(["kubectl", "get", "pods"])
    assert calls == [list(stage2.CURRENT_CONTEXT_ARGV)] * 5


def test_different_or_missing_context_aborts_before_mutation(tmp_path):
    matching = ready_for_cutover(default_state())
    proc, cluster = procedure(matching, tmp_path / "matching")
    proc.forward()
    assert any(call[0] == "scale" for call in cluster.calls)

    for name in ("kind-urumi-k8s-test", ""):
        state = ready_for_cutover(default_state())
        state["context"] = name
        proc, cluster = procedure(state, tmp_path / (name or "empty"))
        with pytest.raises(Abort) as caught:
            proc.forward()
        assert caught.value.decision == "preflight"
        assert cluster.calls == []
        assert any(item["name"] == "FELIX_INTERFACEPREFIX" for item in state["felix"])

    failed = ready_for_cutover(default_state())
    failed["context_failure"] = True
    proc, cluster = procedure(failed, tmp_path / "failed")
    with pytest.raises(Abort):
        proc.forward()
    assert cluster.calls == []


def test_installer_proof_failures_stop_before_prefix_change(tmp_path):
    modes = ("keep", "missing", "same_uid", "same_container", "stale_time", "wrong_image", "nonzero", "missing_exit")
    for mode in modes:
        state = ready_for_cutover(default_state())
        if mode == "keep":
            state["keep_install_identity"] = True
        else:
            state["restart_install"] = mode
        proc, cluster = procedure(state, tmp_path / mode)
        with pytest.raises(Abort):
            proc.forward()
        assert any(call[0] == "rollout-restart" for call in cluster.calls)
        assert not any(call[0] == "set-env" for call in cluster.calls)
        assert any(item["name"] == "FELIX_INTERFACEPREFIX" and item["value"] == "veth" for item in state["felix"])
        assert not (tmp_path / mode / stage2.PROGRESS_NAME).exists()

    route = ready_for_cutover(default_state())
    route["route_sync_false_after_rollout"] = True
    proc, cluster = procedure(route, tmp_path / "route-sync")
    with pytest.raises(Abort) as caught:
        proc.forward()
    assert "FELIX_ROUTESYNCDISABLED" in str(caught.value)
    assert not any(call[0] == "set-env" for call in cluster.calls)
    assert any(item["name"] == "FELIX_INTERFACEPREFIX" for item in route["felix"])
    assert not (tmp_path / "route-sync" / stage2.PROGRESS_NAME).exists()


def test_context_value_must_be_the_flag_argument():
    stage2.assert_kubectl_pinned(["kubectl", "get", "--context", stage2.CONTEXT, "pods"])
    with pytest.raises(Abort):
        stage2.assert_kubectl_pinned(
            ["kubectl", "--context", "kind-urumi-k8s-test", "get", "pods", stage2.CONTEXT]
        )


def test_non_veth_sandbox_and_unexpected_pod_block_cni_move(tmp_path):
    sandbox = ready_for_cutover(default_state())
    sandbox["sandboxes"] = [{"namespace": "default", "name": "leftover", "interface": "wg0"}]
    proc, cluster = procedure(sandbox, tmp_path / "sandbox")
    with pytest.raises(Abort) as caught:
        proc.forward()
    assert "wg0" in str(caught.value)
    assert "sandbox" in str(caught.value)
    assert not any(call[0] in {"mv", "set-env", "rollout-restart"} for call in cluster.calls)

    stranger = ready_for_cutover(default_state())
    stranger["pods"].setdefault("kube-system", []).append(
        {
            "metadata": {
                "name": "unexpected-net",
                "namespace": "kube-system",
                "ownerReferences": [
                    {"apiVersion": stage2.APPS_V1, "controller": True, "kind": "DaemonSet", "name": "unexpected-net"}
                ],
            },
            "spec": {"hostNetwork": False},
        }
    )
    proc, cluster = procedure(stranger, tmp_path / "pod")
    with pytest.raises(Abort) as caught:
        proc.forward()
    assert "unexpected-net" in str(caught.value)
    assert not any(call[0] == "mv" for call in cluster.calls)

    survivor = ready_for_cutover(default_state())
    survivor["pods"].setdefault("kube-system", []).append(
        {
            "metadata": {
                "name": "calico-node-5mll4",
                "namespace": "kube-system",
                "ownerReferences": [
                    {"apiVersion": stage2.APPS_V1, "controller": True, "kind": "DaemonSet", "name": "calico-node"}
                ],
            },
            "spec": {"hostNetwork": True},
        }
    )
    survivor["sandboxes"] = [{"interface": "", "name": "calico-node-5mll4", "namespace": "kube-system"}]
    proc, cluster = procedure(survivor, tmp_path / "survivor")
    proc.forward()
    assert any(call[0] == "set-env" for call in cluster.calls)


def test_rewritten_conflist_blocks_prefix_removal(tmp_path):
    state = ready_for_cutover(default_state())
    state["rewrite_cni_digest"] = True
    proc, cluster = procedure(state, tmp_path)
    with pytest.raises(Abort) as caught:
        proc.forward()
    assert "conflist bytes" in str(caught.value)
    assert not any(call[0] == "set-env" for call in cluster.calls)
    assert any(item["name"] == "FELIX_INTERFACEPREFIX" for item in state["felix"])


def test_missing_terminated_image_id_blocks_prefix_removal(tmp_path):
    state = ready_for_cutover(default_state())
    state["calico_pods"][0].pop("image_id")
    proc, cluster = procedure(state, tmp_path)
    with pytest.raises(Abort) as caught:
        proc.forward()
    assert "imageID" in str(caught.value)
    assert not any(call[0] in {"set-env", "rollout-restart"} for call in cluster.calls)


def test_default_dry_run_and_execute_create_no_default_state(tmp_path, monkeypatch):
    made = []
    original = Path.mkdir

    def track(self, *args, **kwargs):
        made.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", track)
    assert main(["--fixture"]) == 2
    assert main(["--fixture", "--execute"]) == 2
    assert main(["--fixture", "--audit-recovery"]) == 2
    assert all("urumi-calico-stage2" not in str(path) for path in made)
    proc = Procedure(FixtureCluster(ready_for_cutover(default_state())), execute=True, state_dir=None)
    with pytest.raises(Abort) as caught:
        proc.forward()
    assert "explicit --state-dir" in str(caught.value)
    assert proc.cluster.calls == []
    help_text = " ".join(stage2.build_parser().format_help().split())
    assert "still requires --execute" not in help_text
    assert "disabled" in help_text
    assert "Containment is not recovery" in help_text


def test_unresolved_node_stops_before_cni_move(tmp_path):
    early = ready_for_cutover(default_state())
    early["node_failure"] = True
    proc, cluster = procedure(early, tmp_path / "early")
    with pytest.raises(Abort):
        proc.forward()
    assert cluster.calls == []

    ambiguous = ready_for_cutover(default_state())
    ambiguous["api_nodes"] = [
        {"container_name": "/a", "name": "a", "ready": True, "uid": "1"},
        {"container_name": "/b", "name": "b", "ready": True, "uid": "2"},
    ]
    proc, cluster = procedure(ambiguous, tmp_path / "two")
    with pytest.raises(Abort) as caught:
        proc.forward()
    assert "exactly one node" in str(caught.value)
    assert cluster.calls == []

    mismatch = ready_for_cutover(default_state())
    mismatch["api_nodes"] = [{"container_name": "/other", "name": "urumi-k8s-dev-control-plane", "ready": True, "uid": "1"}]
    proc, cluster = procedure(mismatch, tmp_path / "name")
    with pytest.raises(Abort):
        proc.forward()
    assert cluster.calls == []

    later = ready_for_cutover(default_state())
    later["break_node_after_scale"] = True
    proc, cluster = procedure(later, tmp_path / "later")
    with pytest.raises(Abort) as caught:
        proc.forward()
    assert "does not match" in str(caught.value)
    assert any(call[0] == "scale" for call in cluster.calls)
    assert not any(call[0] in {"mv", "set-env", "rollout-restart"} for call in cluster.calls)
