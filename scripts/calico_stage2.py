#!/usr/bin/env python3
"""Fail-fast Stage 2 procedure for the Calico CNI cutover.

Dry-run is the default. Live mutations run only with --execute, and only
through a real cluster object. Tests pass a fixture and never talk to the
node. This procedure does not restore replicas and does not delete volumes.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

CONTEXT = "kind-urumi-k8s-dev"
# Historical node name. Live docker commands do not exec this string. They exec
# the single Ready node returned by the pinned context after docker inspect
# confirms that container's name.
NODE = "urumi-k8s-dev-control-plane"
# This exact argv is the only kubectl command allowed without --context.
# `kubectl config current-context` prints the stored current context. Adding
# --context makes that output ambiguous, so the flag is not used here.
CURRENT_CONTEXT_ARGV = ("kubectl", "config", "current-context")
CNI_DIR = "/etc/cni/net.d"
CALICO_ACTIVE = "10-calico.conflist"
CALICO_HELD = "10-calico.conflist.held"
CALICO_RECREATED = "10-calico.conflist.recreated"
KINDNET_ACTIVE = "10-kindnet.conflist"
KINDNET_HELD = "10-kindnet.conflist.held-migration"
MARKER_NAME = "rbac-added-by-this-run"
SNAPSHOT_NAME = "calico-cni-plugin-original.json"
PROGRESS_NAME = "progress.json"
INTENT_DIRNAME = "intents"
BASELINE_NAME = "operator-baseline.json"

VOLUMES = (
    (
        "default",
        "data-urumi-gateway-db-0",
        "pvc-f77b4777-44e3-4c02-9309-a1cf3c1332cf",
        "/var/local-path-provisioner/pvc-f77b4777-44e3-4c02-9309-a1cf3c1332cf_default_data-urumi-gateway-db-0",
    ),
    (
        "default",
        "data-urumi-store-db-0",
        "pvc-4283d20f-494e-4a15-81d4-6f514adf028f",
        "/var/local-path-provisioner/pvc-4283d20f-494e-4a15-81d4-6f514adf028f_default_data-urumi-store-db-0",
    ),
    (
        "default",
        "urumi-wordpress-data",
        "pvc-1e0101de-5c7e-4f29-b19c-7a0c25eddbf3",
        "/var/local-path-provisioner/pvc-1e0101de-5c7e-4f29-b19c-7a0c25eddbf3_default_urumi-wordpress-data",
    ),
)

ORIGINAL_RULES = (
    {"apiGroups": [""], "resources": ["pods", "nodes", "namespaces"], "verbs": ["get"]},
    {"apiGroups": [""], "resources": ["pods/status"], "verbs": ["patch"]},
)
ADDED_RULE = {
    "apiGroups": ["crd.projectcalico.org"],
    "resources": ["clusterinformations"],
    "verbs": ["get"],
}

# kind, namespace, names, expected spec.replicas
SCALE_GROUPS = (
    (
        "deploy",
        "default",
        (
            "urumi-dashboard",
            "urumi-gateway",
            "urumi-support-mcp",
            "urumi-utility-mcp",
            "urumi-woocommerce-mcp",
            "urumi-wordpress",
        ),
        1,
    ),
    ("deploy", "ingress-nginx", ("ingress-nginx-controller",), 1),
    ("deploy", "kube-system", ("coredns",), 2),
    ("deploy", "kube-system", ("calico-kube-controllers",), 1),
    ("deploy", "local-path-storage", ("local-path-provisioner",), 1),
    ("sts", "default", ("urumi-gateway-db", "urumi-store-db"), 1),
)

PROTECTED_CONTROLLERS = (
    ("deploy", "kube-system", "calico-typha"),
    ("ds", "kube-system", "calico-node"),
    ("ds", "kube-system", "kindnet"),
    ("ds", "kube-system", "kube-proxy"),
)

SCALE_TIMEOUT = 300
SELECT_TIMEOUT = 60
ROLLOUT_TIMEOUT = 180
APPS_V1 = "apps/v1"
INSTALL_CNI_IMAGE = "docker.io/calico/cni:v3.30.2"
INSTALL_IMAGE_ID = re.compile(r"^docker.io/calico/cni@sha256:[0-9a-f]{64}$")
SURVIVOR_DAEMONSETS = frozenset({"calico-node", "kindnet", "kube-proxy"})
REMOTE_FAILURE_CODES = frozenset({125, 126, 127, 255})
IFACE_LINE = re.compile(r"^\d+: ([^:\s@]+)(?:@[^:\s]+)?: ")
RESULT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@+-]*$")
IFACE_SCRIPT = "ip -o link"
RESULT_SCRIPT = (
    "set -eu; "
    'dir=/var/lib/cni/results; '
    'if [ ! -d "$dir" ]; then echo unavailable >&2; exit 3; fi; '
    'find "$dir" -maxdepth 1 -type f -printf \'%f\\n\''
)


def completed_stdout(result: Completed, decision: str) -> str:
    if result.returncode == 0:
        return result.stdout
    if result.returncode in REMOTE_FAILURE_CODES:
        raise Abort(f"remote execution failed ({result.returncode})", decision)
    raise Abort(f"command failed ({result.returncode})", decision)


def parse_ip_link(stdout: str) -> dict[str, int]:
    """Count veth and cali interfaces. Other names are not classified.

    A listing that contains neither prefix is not proof that every host
    interface is a node device. Correlating an arbitrary name with a sandbox
    needs evidence this function does not have, and unknown names are not
    accepted through an allowlist.
    """
    counts = {"veth": 0, "cali": 0}
    saw_line = False
    for line in stdout.splitlines():
        if line.strip() == "":
            continue
        saw_line = True
        match = IFACE_LINE.match(line)
        if not match:
            raise Abort("malformed ip link output", "interface-gate")
        name = match.group(1)
        if name.startswith("veth"):
            counts["veth"] += 1
        elif name.startswith("cali"):
            counts["cali"] += 1
    if not saw_line:
        raise Abort("ip link returned no output", "interface-gate")
    return counts


def redact_obj(value: Any) -> Any:
    """Drop secret-bearing strings. A marker replaces the whole value."""
    if isinstance(value, str):
        lowered = value.lower()
        markers = (
            "calico-kubeconfig",
            "begin certificate",
            "begin private",
            "password",
            "token=",
            "authorization:",
        )
        if any(marker in lowered for marker in markers):
            return "[redacted]"
        return value
    if isinstance(value, list):
        return [redact_obj(item) for item in value]
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if str(key).lower() in {"password", "token", "secret", "authorization"}:
                cleaned[str(key)] = "[redacted]"
            else:
                cleaned[str(key)] = redact_obj(item)
        return cleaned
    return value


def mutation_slug(mutation: str, target: str) -> str:
    raw = f"{mutation}--{target}"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", raw)[:140]


def parse_result_names(stdout: str) -> list[str]:
    """Return workload CNI result names. An empty successful listing is valid."""
    found = []
    for line in stdout.splitlines():
        if line == "":
            continue
        if RESULT_NAME.fullmatch(line) is None:
            raise Abort("malformed CNI result listing", "interface-gate")
        if line.startswith("loopback-"):
            continue
        if line.startswith(("kindnet-", "k8s-pod-network-")):
            found.append(line)
            continue
        raise Abort(f"unrecognized CNI result file {line}", "interface-gate")
    return found


class Abort(Exception):
    def __init__(self, message: str, decision: str) -> None:
        super().__init__(message)
        self.decision = decision


def canonical_rule(rule: dict[str, Any]) -> str:
    kept = {
        "apiGroups": rule.get("apiGroups"),
        "resources": rule.get("resources"),
        "verbs": rule.get("verbs"),
        "resourceNames": rule.get("resourceNames"),
        "nonResourceURLs": rule.get("nonResourceURLs"),
    }
    return json.dumps(kept, sort_keys=True)


def rules_diff(live: Sequence[dict[str, Any]], original: Sequence[dict[str, Any]]) -> tuple[list[str], list[str]]:
    pool = [canonical_rule(rule) for rule in original]
    extra: list[str] = []
    for rule in live:
        key = canonical_rule(rule)
        if key in pool:
            pool.remove(key)
        else:
            extra.append(key)
    return extra, pool


def sole_added_permission(live: Sequence[dict[str, Any]], original: Sequence[dict[str, Any]]) -> bool:
    extra, missing = rules_diff(live, original)
    return extra == [canonical_rule(ADDED_RULE)] and missing == []


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text)
    os.replace(temporary, path)


def atomic_write_private(path: Path, text: str) -> None:
    """Replace path atomically and leave it readable only by the owner."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def replica_record_path(state_dir: Path, namespace: str, kind: str, name: str) -> Path:
    return state_dir / f"replicas-{namespace}-{kind}-{name}.json"


def owner_matches(owner: dict[str, Any], kind: str, name: str | None = None) -> bool:
    if owner.get("controller") is not True:
        return False
    if owner.get("kind") != kind or owner.get("apiVersion") != APPS_V1:
        return False
    return name is None or owner.get("name") == name


def name_resembles_controller(pod_name: str, controller_name: str) -> bool:
    return pod_name == controller_name or pod_name.startswith(controller_name + "-")


def select_owned_pods(
    pod_items: Sequence[dict[str, Any]],
    kind: str,
    controller_name: str,
    replicaset_owners: Callable[[str], list[dict[str, Any]] | None],
) -> list[str]:
    """Return pods proven to belong to the controller. Fail closed otherwise."""
    if kind not in {"deploy", "sts"}:
        raise Abort(f"unsupported controller kind {kind}", "scale-down")
    matched: list[str] = []
    for item in pod_items:
        metadata = item.get("metadata")
        if not isinstance(metadata, dict) or not metadata.get("name"):
            raise Abort("pod metadata is missing", "scale-down")
        pod_name = str(metadata["name"])
        owners = metadata.get("ownerReferences", [])
        if not isinstance(owners, list):
            raise Abort(f"ownership metadata is unreadable for {pod_name}", "scale-down")
        resembles = name_resembles_controller(pod_name, controller_name)
        if kind == "sts":
            if any(owner_matches(owner, "StatefulSet", controller_name) for owner in owners):
                matched.append(pod_name)
                continue
            owned_by_other = any(
                owner_matches(owner, other_kind)
                for owner in owners
                for other_kind in ("StatefulSet", "ReplicaSet", "DaemonSet", "Job")
            )
            if resembles and not owned_by_other:
                raise Abort(f"ownership cannot be established for {pod_name}", "scale-down")
            continue
        replica_sets = [owner for owner in owners if owner_matches(owner, "ReplicaSet")]
        if len(replica_sets) > 1:
            raise Abort(f"ambiguous ReplicaSet ownership for {pod_name}", "scale-down")
        if len(replica_sets) == 1:
            replica_name = replica_sets[0].get("name")
            if not isinstance(replica_name, str) or not replica_name:
                raise Abort(f"ReplicaSet owner name is missing for {pod_name}", "scale-down")
            parents = replicaset_owners(replica_name)
            if parents is None:
                raise Abort(f"ReplicaSet {replica_name} ownership cannot be established", "scale-down")
            if any(owner_matches(owner, "Deployment", controller_name) for owner in parents):
                matched.append(pod_name)
                continue
            owned_by_other_deployment = any(owner_matches(owner, "Deployment") for owner in parents)
            if resembles and not owned_by_other_deployment:
                raise Abort(f"ownership cannot be established for {pod_name}", "scale-down")
            continue
        owned_by_other = any(
            owner_matches(owner, other_kind) for owner in owners for other_kind in ("StatefulSet", "DaemonSet", "Job")
        )
        if resembles and not owned_by_other:
            raise Abort(f"ownership cannot be established for {pod_name}", "scale-down")
    return matched


def assert_kubectl_pinned(argv: Sequence[str]) -> None:
    """Reject kubectl commands that are not pinned to the one context.

    Only the exact argv CURRENT_CONTEXT_ARGV may omit --context. Every other
    kubectl command needs --context exactly once, and the next argument must
    be kind-urumi-k8s-dev. The name appearing anywhere else does not count.
    """
    if not argv or argv[0] != "kubectl":
        return
    if tuple(argv) == CURRENT_CONTEXT_ARGV:
        return
    indexes = [index for index, part in enumerate(argv) if part == "--context"]
    if len(indexes) != 1:
        raise Abort("kubectl command is missing the pinned context", "preflight")
    value_at = indexes[0] + 1
    if value_at >= len(argv) or argv[value_at] != CONTEXT:
        raise Abort("kubectl command is missing the pinned context", "preflight")


def assert_safe_argv(argv: Sequence[str]) -> None:
    text = " ".join(argv)
    lowered = [part.lower() for part in argv]
    if "calico-kubeconfig" in text:
        raise Abort("refusing to read calico-kubeconfig", "preflight")
    if "secret" in lowered or any(part.startswith("secret/") for part in lowered):
        raise Abort("refusing to read Secrets", "preflight")
    if "delete" in lowered and ("pvc" in lowered or "pv" in lowered or "persistentvolume" in lowered):
        raise Abort("refusing to delete persistent volumes", "preflight")
    if argv[:2] == ["kind", "delete"] or "kind delete cluster" in text:
        raise Abort("refusing to delete the cluster", "preflight")


class Completed:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class LiveCluster:
    """Real kubectl and docker commands. Unused by the tests."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self._access_guard: Callable[[str], None] | None = None
        self._resolved_node: str | None = None

    def bind_guard(self, guard: Callable[[str], None]) -> None:
        self._access_guard = guard

    def _run(self, argv: Sequence[str], check: bool = True) -> Completed:
        assert_safe_argv(argv)
        assert_kubectl_pinned(argv)
        self.calls.append(tuple(argv))
        proc = subprocess.run(list(argv), check=False, text=True, capture_output=True)
        if check and proc.returncode != 0:
            raise Abort(
                f"command failed ({proc.returncode}): {' '.join(argv)}\n{proc.stderr.strip()}",
                "command",
            )
        return Completed(proc.returncode, proc.stdout, proc.stderr)

    def _invoke_guard(self, decision: str) -> None:
        if self._access_guard is None:
            raise Abort("cluster access has no identity guard", decision)
        self._access_guard(decision)

    def _docker(self, args: Sequence[str], check: bool = True) -> Completed:
        self._invoke_guard("node-access")
        name = self._resolved_node
        if not isinstance(name, str) or not name or "/" in name:
            raise Abort("node container was not resolved", "node-access")
        return self._run(["docker", "exec", name, *args], check=check)

    def context(self) -> str:
        completed = self._run(list(CURRENT_CONTEXT_ARGV))
        text = completed.stdout.removesuffix("\n")
        if text != CONTEXT:
            raise Abort("stored current context is not kind-urumi-k8s-dev", "preflight")
        return text

    def resolve_node(self) -> str:
        """Return the only Ready node in the pinned context.

        The docker target is that API name, and only after `docker inspect`
        prints the same name. The historical NODE constant is not consulted.
        Inspect output is limited to the container name so environment values
        are not read.
        """
        self._resolved_node = None
        payload = json.loads(
            self._run(["kubectl", "--context", CONTEXT, "get", "nodes", "-o", "json"]).stdout
        )
        items = payload.get("items")
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise Abort("pinned context does not have exactly one node", "preflight")
        item = items[0]
        meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        name = meta.get("name")
        uid = meta.get("uid")
        conditions = item.get("status", {}).get("conditions") if isinstance(item.get("status"), dict) else None
        ready = isinstance(conditions, list) and any(
            isinstance(condition, dict) and condition.get("type") == "Ready" and condition.get("status") == "True"
            for condition in conditions
        )
        if not isinstance(name, str) or not name or "/" in name or not isinstance(uid, str) or not uid or not ready:
            raise Abort("API node identity is unreadable", "preflight")
        shown = self._run(["docker", "inspect", "--format", "{{.Name}}", name]).stdout.strip()
        if shown != f"/{name}":
            raise Abort("docker container name does not match the API node", "preflight")
        self._resolved_node = name
        return name

    def node_ready(self) -> bool:
        name = self.resolve_node()
        out = self._run(
            [
                "kubectl",
                "--context",
                CONTEXT,
                "get",
                "node",
                name,
                "-o",
                "jsonpath={.status.conditions[?(@.type==\"Ready\")].status}",
            ]
        ).stdout.strip()
        return out == "True"

    def readyz(self) -> bool:
        out = self._run(["kubectl", "--context", CONTEXT, "get", "--raw=/readyz"]).stdout.strip()
        return out == "ok"

    def cluster_identity(self) -> dict[str, str]:
        config = json.loads(
            self._run(["kubectl", "config", "view", "--minify", "--context", CONTEXT, "-o", "json"]).stdout
        )
        clusters = config.get("clusters")
        if not isinstance(clusters, list) or len(clusters) != 1 or not isinstance(clusters[0], dict):
            raise Abort("kubeconfig cluster entry is missing", "preflight")
        server = clusters[0].get("cluster", {}).get("server")
        uid = self._run(
            ["kubectl", "--context", CONTEXT, "get", "ns", "kube-system", "-o", "jsonpath={.metadata.uid}"]
        ).stdout.strip()
        if not isinstance(server, str) or not server.startswith("https://") or not uid:
            raise Abort("cluster server or kube-system uid is missing", "preflight")
        return {"kube_system_uid": uid, "server": server}

    def volumes(self) -> list[tuple[str, str, str, str, bool]]:
        found: list[tuple[str, str, str, str, bool]] = []
        for namespace, pvc, pv, path in VOLUMES:
            claim = json.loads(
                self._run(
                    ["kubectl", "--context", CONTEXT, "get", "pvc", pvc, "-n", namespace, "-o", "json"]
                ).stdout
            )
            volume = json.loads(
                self._run(["kubectl", "--context", CONTEXT, "get", "pv", claim["spec"]["volumeName"], "-o", "json"]).stdout
            )
            host = volume["spec"]["hostPath"]["path"]
            policy = volume.get("spec", {}).get("persistentVolumeReclaimPolicy")
            exists = self.remote_exists(host, directory=True)
            policy_text = policy if isinstance(policy, str) else ""
            bound = exists and claim["status"]["phase"] == "Bound" and bool(policy_text)
            found.append((claim["metadata"]["name"], claim["spec"].get("volumeName", ""), host, policy_text, bound))
            if pv != claim["spec"].get("volumeName") or path != host or not policy_text:
                found[-1] = (claim["metadata"]["name"], claim["spec"].get("volumeName", ""), host, policy_text, False)
        return found

    def replicas(self, kind: str, namespace: str, name: str) -> int:
        resource = "deploy" if kind == "deploy" else "sts"
        out = self._run(
            [
                "kubectl",
                "--context",
                CONTEXT,
                "get",
                resource,
                name,
                "-n",
                namespace,
                "-o",
                "jsonpath={.spec.replicas}",
            ]
        ).stdout.strip()
        if not out.isdigit():
            raise Abort(f"replica count missing for {kind}/{namespace}/{name}", "scale-down")
        return int(out)

    def scale_to_zero(self, kind: str, namespace: str, names: Sequence[str]) -> None:
        resource = "deploy" if kind == "deploy" else "sts"
        targets = [f"{resource}/{name}" for name in names]
        self._run(
            ["kubectl", "--context", CONTEXT, "scale", *targets, "-n", namespace, "--replicas=0"]
        )

    def controller_pods(self, namespace: str, kind: str, name: str) -> list[str]:
        payload = json.loads(
            self._run(["kubectl", "--context", CONTEXT, "get", "pods", "-n", namespace, "-o", "json"]).stdout
        )
        if not isinstance(payload.get("items"), list):
            raise Abort(f"pod list is missing items in {namespace}", "scale-down")

        def replicaset_owners(replica_name: str) -> list[dict[str, Any]] | None:
            try:
                replica = json.loads(
                    self._run(
                        [
                            "kubectl",
                            "--context",
                            CONTEXT,
                            "get",
                            "rs",
                            replica_name,
                            "-n",
                            namespace,
                            "-o",
                            "json",
                        ]
                    ).stdout
                )
            except Abort:
                return None
            owners = replica.get("metadata", {}).get("ownerReferences", [])
            if not isinstance(owners, list):
                return None
            return owners

        return select_owned_pods(payload["items"], kind, name, replicaset_owners)

    def remote_exists(self, path: str, directory: bool) -> bool:
        flag = "-d" if directory else "-e"
        quoted = shlex.quote(path)
        script = f"set -eu; if [ {flag} {quoted} ]; then echo present; else echo absent; fi"
        out = self._docker(["sh", "-c", script]).stdout.strip()
        if out == "present":
            return True
        if out == "absent":
            return False
        raise Abort("malformed remote existence output", "preflight")

    def workload_ifaces(self) -> dict[str, int]:
        result = self._docker(["sh", "-c", IFACE_SCRIPT], check=False)
        return parse_ip_link(completed_stdout(result, "interface-gate"))

    def bad_result_files(self) -> list[str]:
        result = self._docker(["sh", "-c", RESULT_SCRIPT], check=False)
        return parse_result_names(completed_stdout(result, "interface-gate"))

    def cni_exists(self, name: str) -> bool:
        if "/" in name or name in {"", ".", ".."}:
            raise Abort("refusing unexpected CNI filename", "cni-move")
        return self.remote_exists(f"{CNI_DIR}/{name}", directory=False)

    def cni_digest(self, name: str) -> str:
        if "kubeconfig" in name or name in {"", ".", ".."} or "/" in name:
            raise Abort("refusing to read calico-kubeconfig", "audit-recovery")
        if not self.cni_exists(name):
            raise Abort(f"CNI file absent: {name}", "audit-recovery")
        out = self._docker(["sha256sum", f"{CNI_DIR}/{name}"]).stdout.strip()
        digest = out.split()[0] if out.split() else ""
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise Abort("malformed CNI digest", "audit-recovery")
        return digest

    def move(self, src: str, dst: str) -> None:
        self._docker(["mv", "-n", f"{CNI_DIR}/{src}", f"{CNI_DIR}/{dst}"])

    def networks(self) -> tuple[str, list[tuple[str, list[str]]]]:
        payload = json.loads(self._docker(["crictl", "info"]).stdout)
        if "lastCNILoadStatus" not in payload or not isinstance(payload.get("cniconfig"), dict):
            raise Abort("crictl info is missing selection fields", "selection")
        networks = payload["cniconfig"].get("Networks")
        if not isinstance(networks, list):
            raise Abort("crictl info network list is missing", "selection")
        found = []
        for network in networks:
            if not isinstance(network, dict) or not isinstance(network.get("Config"), dict):
                raise Abort("malformed crictl network entry", "selection")
            config = network["Config"]
            if not isinstance(config.get("Name"), str) or not isinstance(config.get("Plugins"), list):
                raise Abort("malformed crictl network entry", "selection")
            plugins = []
            for plugin in config["Plugins"]:
                if not isinstance(plugin, dict):
                    raise Abort("malformed crictl plugin entry", "selection")
                body = plugin.get("Network") or plugin
                if not isinstance(body, dict) or not isinstance(body.get("type"), str):
                    raise Abort("malformed crictl plugin entry", "selection")
                plugins.append(body["type"])
            found.append((config["Name"], plugins))
        return str(payload["lastCNILoadStatus"]), found

    def clusterrole(self) -> list[dict[str, Any]]:
        payload = json.loads(
            self._run(
                ["kubectl", "--context", CONTEXT, "get", "clusterrole", "calico-cni-plugin", "-o", "json"]
            ).stdout
        )
        rules = payload.get("rules")
        if not isinstance(rules, list):
            raise Abort("ClusterRole rules are missing", "preflight")
        return list(rules)

    def patch_role(self, patch: str) -> None:
        self._run(
            [
                "kubectl",
                "--context",
                CONTEXT,
                "patch",
                "clusterrole",
                "calico-cni-plugin",
                "--type=json",
                "-p",
                patch,
            ]
        )

    def felix_env(self) -> list[dict[str, Any]]:
        payload = json.loads(
            self._run(
                ["kubectl", "--context", CONTEXT, "get", "ds", "calico-node", "-n", "kube-system", "-o", "json"]
            ).stdout
        )
        containers = payload["spec"]["template"]["spec"].get("containers")
        if not isinstance(containers, list):
            raise Abort("DaemonSet env is missing", "felix-rollout")
        named = [container for container in containers if isinstance(container, dict) and container.get("name") == "calico-node"]
        if len(named) != 1:
            raise Abort("calico-node container is ambiguous", "felix-rollout")
        env = named[0].get("env")
        if not isinstance(env, list):
            raise Abort("DaemonSet env is missing", "felix-rollout")
        return list(env)

    def set_env(self, assignment: str) -> None:
        self._run(
            [
                "kubectl",
                "--context",
                CONTEXT,
                "set",
                "env",
                "daemonset/calico-node",
                "-n",
                "kube-system",
                "-c",
                "calico-node",
                assignment,
            ]
        )

    def rollout_observed(self) -> str:
        self._run(
            [
                "kubectl",
                "--context",
                CONTEXT,
                "rollout",
                "status",
                "daemonset/calico-node",
                "-n",
                "kube-system",
                "--timeout=15s",
            ]
        )
        return "complete"

    def rollout_restart(self) -> None:
        """Start a new pod without changing Felix env.

        install-cni runs only when a pod starts. This annotation change is
        what creates the installer attempt that must succeed before the
        interface prefix may be removed.
        """
        self._run(
            [
                "kubectl",
                "--context",
                CONTEXT,
                "rollout",
                "restart",
                "daemonset/calico-node",
                "-n",
                "kube-system",
            ]
        )

    def rollout_status(self) -> None:
        self._run(
            [
                "kubectl",
                "--context",
                CONTEXT,
                "rollout",
                "status",
                "daemonset/calico-node",
                "-n",
                "kube-system",
                f"--timeout={ROLLOUT_TIMEOUT}s",
            ]
        )

    def calico_install_state(self) -> list[dict[str, Any]]:
        payload = json.loads(
            self._run(
                [
                    "kubectl",
                    "--context",
                    CONTEXT,
                    "get",
                    "pods",
                    "-n",
                    "kube-system",
                    "-l",
                    "k8s-app=calico-node",
                    "-o",
                    "json",
                ]
            ).stdout
        )
        items = payload.get("items")
        if not isinstance(items, list):
            raise Abort("calico-node pod list is missing items", "felix-rollout")
        found = []
        for item in items:
            statuses = {
                status.get("name"): status for status in item.get("status", {}).get("initContainerStatuses") or []
            }
            status = statuses.get("install-cni") or {}
            terminated = (status.get("state") or {}).get("terminated") or {}
            found.append(
                {
                    "container_id": status.get("containerID") or "",
                    "exit_code": terminated.get("exitCode"),
                    "finished_at": terminated.get("finishedAt") or "",
                    "image": status.get("image") or "",
                    "image_id": status.get("imageID") or "",
                    "name": item.get("metadata", {}).get("name"),
                    "terminated": bool(terminated),
                    "uid": item.get("metadata", {}).get("uid") or "",
                }
            )
        return found

    def cluster_pods(self) -> list[dict[str, Any]]:
        payload = json.loads(
            self._run(["kubectl", "--context", CONTEXT, "get", "pods", "-A", "-o", "json"]).stdout
        )
        items = payload.get("items")
        if not isinstance(items, list):
            raise Abort("pod list is unreadable", "interface-gate")
        found = []
        for item in items:
            if not isinstance(item, dict):
                raise Abort("pod list is unreadable", "interface-gate")
            meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            spec = item.get("spec") if isinstance(item.get("spec"), dict) else {}
            owners = meta.get("ownerReferences") or []
            if not isinstance(owners, list):
                raise Abort("pod owner references are unreadable", "interface-gate")
            found.append(
                {
                    "host_network": spec.get("hostNetwork") is True,
                    "name": meta.get("name") if isinstance(meta.get("name"), str) else "",
                    "namespace": meta.get("namespace") if isinstance(meta.get("namespace"), str) else "",
                    "owners": owners,
                }
            )
        return found

    def replicaset_parents(self, namespace: str, name: str) -> list[dict[str, Any]] | None:
        try:
            replica = json.loads(
                self._run(
                    ["kubectl", "--context", CONTEXT, "get", "rs", name, "-n", namespace, "-o", "json"]
                ).stdout
            )
        except Abort:
            return None
        owners = replica.get("metadata", {}).get("ownerReferences", [])
        if not isinstance(owners, list):
            return None
        return owners

    def sandboxes(self) -> list[dict[str, str]]:
        payload = json.loads(self._docker(["crictl", "pods", "-o", "json"]).stdout)
        items = payload.get("items") if isinstance(payload, dict) else None
        if items is None:
            items = []
        if not isinstance(items, list):
            raise Abort("sandbox list is unreadable", "interface-gate")
        found = []
        for item in items:
            if not isinstance(item, dict):
                raise Abort("sandbox identity is unreadable", "interface-gate")
            meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            labels = item.get("labels") if isinstance(item.get("labels"), dict) else {}
            name = meta.get("name") or labels.get("io.kubernetes.pod.name")
            namespace = meta.get("namespace") or labels.get("io.kubernetes.pod.namespace")
            interface = item.get("interface") if isinstance(item.get("interface"), str) else ""
            if not isinstance(name, str) or not name or not isinstance(namespace, str) or not namespace:
                raise Abort("sandbox identity is unreadable", "interface-gate")
            found.append({"interface": interface, "name": name, "namespace": namespace})
        return found

    def cni_public_config(self, name: str) -> dict[str, Any]:
        if "kubeconfig" in name or "/" in name or name in {"", ".", ".."}:
            raise Abort("refusing to read calico-kubeconfig", "cni-move")
        raw = self._docker(["cat", f"{CNI_DIR}/{name}"]).stdout
        if "BEGIN CERTIFICATE" in raw or "BEGIN PRIVATE" in raw or "BEGIN RSA" in raw:
            raise Abort("CNI file contains certificate material", "cni-move")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise Abort("CNI conflist is not JSON", "cni-move") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("plugins"), list):
            raise Abort("CNI conflist plugins are missing", "cni-move")
        types = []
        for plugin in payload["plugins"]:
            if not isinstance(plugin, dict) or not isinstance(plugin.get("type"), str):
                raise Abort("CNI plugin type is missing", "cni-move")
            types.append(plugin["type"])
        network = payload.get("name")
        if not isinstance(network, str) or not network:
            raise Abort("CNI network name is missing", "cni-move")
        return {"digest": hashlib.sha256(raw.encode()).hexdigest(), "name": network, "plugins": types}


class FixtureCluster:
    """In-memory cluster. mv -n leaves the source in place when the destination exists."""

    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state
        self.calls: list[tuple[Any, ...]] = []
        self.fail_scale_namespace: str | None = state.get("fail_scale_namespace")
        self._access_guard: Callable[[str], None] | None = None
        self._resolved_node: str | None = None

    def bind_guard(self, guard: Callable[[str], None]) -> None:
        self._access_guard = guard

    def resolve_node(self) -> str:
        self._resolved_node = None
        if self.state.get("node_failure"):
            raise Abort("node identity unreadable", "preflight")
        if self.state.get("break_node_after_scale") and any(call[0] == "scale" for call in self.calls):
            raise Abort("docker container name does not match the API node", "preflight")
        nodes = self.state.get("api_nodes")
        if nodes is None:
            nodes = [{"container_name": f"/{NODE}", "name": NODE, "ready": True, "uid": "fixture-node-uid"}]
        if not isinstance(nodes, list) or len(nodes) != 1 or not isinstance(nodes[0], dict):
            raise Abort("pinned context does not have exactly one node", "preflight")
        node = nodes[0]
        name = node.get("name")
        if (
            not isinstance(name, str)
            or not name
            or not node.get("uid")
            or node.get("ready") is not True
            or node.get("container_name") != f"/{name}"
        ):
            raise Abort("API node identity is unreadable", "preflight")
        self._resolved_node = name
        return name

    def context(self) -> str:
        if self.state.get("context_failure"):
            raise Abort("context API failed", "command")
        return str(self.state.get("context", CONTEXT))

    def node_ready(self) -> bool:
        return bool(self.state.get("node_ready", True))

    def readyz(self) -> bool:
        return bool(self.state.get("readyz", True))

    def cluster_identity(self) -> dict[str, str]:
        if self.state.get("identity_failure"):
            raise Abort("cluster identity unreadable", "preflight")
        identity = self.state.get("identity")
        if not isinstance(identity, dict):
            raise Abort("cluster identity missing", "preflight")
        return {
            "kube_system_uid": str(identity.get("kube_system_uid") or ""),
            "server": str(identity.get("server") or ""),
        }

    def volumes(self) -> list[tuple[str, str, str, str, bool]]:
        policy = self.state.get("reclaim_policy")
        policy_text = policy if isinstance(policy, str) else ""
        if not self.state.get("volumes_ok", True):
            return [(VOLUMES[0][1], "changed", VOLUMES[0][3], policy_text, False)]
        return [(pvc, pv, path, policy_text, bool(policy_text)) for _, pvc, pv, path in VOLUMES]

    def replicas(self, kind: str, namespace: str, name: str) -> int:
        if self.state.get("replica_failure"):
            raise Abort("replica API failed", "command")
        value = self.state["replicas"][(kind, namespace, name)]
        if isinstance(value, str):
            raise Abort("malformed replica count", "scale-down")
        return int(value)

    def scale_to_zero(self, kind: str, namespace: str, names: Sequence[str]) -> None:
        self.calls.append(("scale", kind, namespace, tuple(names), 0))
        if namespace == self.fail_scale_namespace:
            raise Abort(f"scale failed in {namespace}", "scale-down")
        for name in names:
            self.state["replicas"][(kind, namespace, name)] = 0
        if self.state.get("break_volumes_after_scale"):
            self.state["volumes_ok"] = False
        remaining = []
        for item in self.state["pods"].get(namespace, []):
            owned = False
            for name in names:
                if select_owned_pods([item], kind, name, self._replicaset_owners(namespace)):
                    owned = True
            if not owned:
                remaining.append(item)
        self.state["pods"][namespace] = remaining
        if self.state.get("clear_ifaces_on_scale"):
            self.state["ifaces"] = {"veth": 0, "cali": 0}
            self.state["results"] = []

    def _replicaset_owners(self, namespace: str) -> Callable[[str], list[dict[str, Any]] | None]:
        def lookup(replica_name: str) -> list[dict[str, Any]] | None:
            return self.state.get("replica_sets", {}).get(namespace, {}).get(replica_name)

        return lookup

    def controller_pods(self, namespace: str, kind: str, name: str) -> list[str]:
        return select_owned_pods(
            self.state["pods"].get(namespace, []),
            kind,
            name,
            self._replicaset_owners(namespace),
        )

    def cluster_pods(self) -> list[dict[str, Any]]:
        if self.state.get("pod_list_failure"):
            raise Abort("pod list unreadable", "interface-gate")
        found = []
        pods = self.state.get("pods")
        if not isinstance(pods, dict):
            raise Abort("pod list unreadable", "interface-gate")
        for namespace, items in pods.items():
            if not isinstance(items, list):
                raise Abort("pod list unreadable", "interface-gate")
            for item in items:
                if not isinstance(item, dict):
                    raise Abort("pod list unreadable", "interface-gate")
                meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                spec = item.get("spec") if isinstance(item.get("spec"), dict) else {}
                owners = meta.get("ownerReferences") or []
                if not isinstance(owners, list):
                    raise Abort("pod owner references are unreadable", "interface-gate")
                found.append(
                    {
                        "host_network": spec.get("hostNetwork") is True,
                        "name": meta.get("name") if isinstance(meta.get("name"), str) else "",
                        "namespace": meta.get("namespace") if isinstance(meta.get("namespace"), str) else str(namespace),
                        "owners": owners,
                    }
                )
        return found

    def replicaset_parents(self, namespace: str, name: str) -> list[dict[str, Any]] | None:
        return self._replicaset_owners(namespace)(name)

    def sandboxes(self) -> list[dict[str, str]]:
        if self.state.get("sandbox_failure"):
            raise Abort(str(self.state["sandbox_failure"]), "interface-gate")
        items = self.state.get("sandboxes")
        if not isinstance(items, list):
            raise Abort("sandbox evidence missing", "interface-gate")
        found = []
        for item in items:
            if not isinstance(item, dict):
                raise Abort("sandbox identity is unreadable", "interface-gate")
            name = item.get("name")
            namespace = item.get("namespace")
            interface = item.get("interface") or ""
            if not isinstance(name, str) or not name or not isinstance(namespace, str) or not namespace:
                raise Abort("sandbox identity is unreadable", "interface-gate")
            if not isinstance(interface, str):
                raise Abort("sandbox interface evidence is unreadable", "interface-gate")
            found.append({"interface": interface, "name": name, "namespace": namespace})
        return found

    def cni_public_config(self, name: str) -> dict[str, Any]:
        if "kubeconfig" in name:
            raise Abort("refusing to read calico-kubeconfig", "cni-move")
        if self.state.get("cni_config_failure"):
            raise Abort("CNI config unreadable", "cni-move")
        if name not in self.state.get("files", set()):
            raise Abort(f"CNI file absent: {name}", "cni-move")
        config = self.state.get("cni_config", {}).get(name)
        if config is None and name == CALICO_ACTIVE:
            config = {
                "digest": "ab" * 32,
                "name": "k8s-pod-network",
                "plugins": ["calico", "portmap"],
            }
        if not isinstance(config, dict):
            raise Abort("CNI config unreadable", "cni-move")
        return {
            "digest": str(config.get("digest") or ""),
            "name": config.get("name"),
            "plugins": list(config.get("plugins") or []),
        }

    def workload_ifaces(self) -> dict[str, int]:
        if self.state.get("iface_failure"):
            raise Abort(str(self.state["iface_failure"]), "interface-gate")
        counts = self.state.get("ifaces")
        if not isinstance(counts, dict) or set(counts) != {"veth", "cali"}:
            raise Abort("interface evidence missing", "interface-gate")
        if any(not isinstance(value, int) or value < 0 for value in counts.values()):
            raise Abort("malformed interface evidence", "interface-gate")
        return dict(counts)

    def bad_result_files(self) -> list[str]:
        if self.state.get("result_failure"):
            raise Abort(str(self.state["result_failure"]), "interface-gate")
        results = self.state.get("results")
        if not isinstance(results, list) or any(not isinstance(name, str) for name in results):
            raise Abort("CNI result evidence missing", "interface-gate")
        return list(results)

    def cni_exists(self, name: str) -> bool:
        if self.state.get("cni_failure"):
            raise Abort("CNI observation failed", "command")
        files = self.state.get("files")
        if not isinstance(files, set):
            raise Abort("malformed CNI file evidence", "cni-move")
        return name in files

    def cni_digest(self, name: str) -> str:
        if "kubeconfig" in name:
            raise Abort("refusing to read calico-kubeconfig", "audit-recovery")
        if self.state.get("digest_failure"):
            raise Abort("CNI digest failed", "audit-recovery")
        if name not in self.state["files"]:
            raise Abort(f"CNI file absent: {name}", "audit-recovery")
        body = str(self.state.get("cni_bodies", {}).get(name, ""))
        if "calico-kubeconfig" in body or "BEGIN CERTIFICATE" in body:
            raise Abort("refusing to expose CNI secret material", "audit-recovery")
        return str(self.state.get("digests", {}).get(name, "0" * 64))

    def move(self, src: str, dst: str) -> None:
        self.calls.append(("mv", src, dst))
        if self.state.get("fail_move") == dst:
            raise Abort(f"mv failed for {dst}", "cni-move")
        if self.state.get("race_dest") == dst:
            self.state["files"].add(dst)
            return
        if dst in self.state["files"]:
            return
        if src not in self.state["files"]:
            raise Abort(f"mv source missing: {src}", "cni-move")
        self.state["files"].remove(src)
        self.state["files"].add(dst)
        if dst == CALICO_ACTIVE:
            self.state["networks"] = ("OK", [("cni-loopback", ["loopback"]), ("k8s-pod-network", ["calico", "portmap"])])
        if dst == KINDNET_HELD and CALICO_ACTIVE in self.state["files"]:
            self.state["networks"] = ("OK", [("cni-loopback", ["loopback"]), ("k8s-pod-network", ["calico", "portmap"])])
        if src == CALICO_ACTIVE and dst in {CALICO_HELD, CALICO_RECREATED} and KINDNET_ACTIVE in self.state["files"]:
            self.state["networks"] = ("OK", [("cni-loopback", ["loopback"]), ("kindnet", ["ptp", "portmap"])])
        if dst == KINDNET_ACTIVE and CALICO_ACTIVE not in self.state["files"]:
            self.state["networks"] = ("OK", [("cni-loopback", ["loopback"]), ("kindnet", ["ptp", "portmap"])])

    def networks(self) -> tuple[str, list[tuple[str, list[str]]]]:
        if self.state.get("selection_stuck"):
            return "OK", [("cni-loopback", ["loopback"]), ("kindnet", ["ptp", "portmap"])]
        files = self.state.get("files")
        post_move = (
            isinstance(files, set)
            and CALICO_ACTIVE in files
            and KINDNET_HELD in files
            and KINDNET_ACTIVE not in files
        )
        if self.state.get("selection_fails_at_prefix_gate") and post_move:
            seen = int(self.state.get("prefix_gate_reads", 0)) + 1
            self.state["prefix_gate_reads"] = seen
            if seen >= 4:
                return "OK", [("cni-loopback", ["loopback"]), ("kindnet", ["ptp", "portmap"])]
        status, nets = self.state["networks"]
        return status, list(nets)

    def clusterrole(self) -> list[dict[str, Any]]:
        if self.state.get("rules_failure"):
            raise Abort("ClusterRole unreadable", "command")
        rules = self.state.get("rules")
        if not isinstance(rules, list):
            raise Abort("malformed ClusterRole rules", "preflight")
        return list(rules)

    def patch_role(self, patch: str) -> None:
        self.calls.append(("patch", patch))
        ops = json.loads(patch)
        for op in ops:
            if op["op"] == "add":
                self.state["rules"] = [*self.state["rules"], op["value"]]
            elif op["op"] == "remove":
                index = int(op["path"].rsplit("/", 1)[-1])
                rules = list(self.state["rules"])
                del rules[index]
                self.state["rules"] = rules

    def felix_env(self) -> list[dict[str, Any]]:
        if self.state.get("felix_failure"):
            raise Abort("DaemonSet env unreadable", "command")
        env = self.state.get("felix")
        if not isinstance(env, list):
            raise Abort("malformed DaemonSet env", "felix-rollout")
        return list(env)

    def set_env(self, assignment: str) -> None:
        self.calls.append(("set-env", assignment))
        env = [item for item in self.state["felix"] if item["name"] != "FELIX_INTERFACEPREFIX"]
        if assignment == "FELIX_INTERFACEPREFIX=veth":
            env.append({"name": "FELIX_INTERFACEPREFIX", "value": "veth"})
        elif assignment != "FELIX_INTERFACEPREFIX-":
            raise Abort(f"unexpected env assignment {assignment}", "felix-rollout")
        self.state["felix"] = env
        if not self.state.get("keep_install_identity"):
            for pod in self.state["calico_pods"]:
                pod["uid"] = str(pod.get("uid") or "pod") + "-after"
                pod["container_id"] = str(pod.get("container_id") or "containerd://old") + "-after"
                pod["finished_at"] = "2026-10-16T12:00:00+00:00"
        if self.state.get("collide_after_set_env"):
            self.state["files"].add(KINDNET_ACTIVE)
        if self.state.get("duplicate_after_set_env"):
            self.state["calico_pods"] = [*self.state["calico_pods"], dict(self.state["calico_pods"][0])]
        elif self.state.get("install_stuck_after_set_env"):
            for pod in self.state["calico_pods"]:
                pod["terminated"] = False
                pod["exit_code"] = None
        else:
            for pod in self.state["calico_pods"]:
                pod["terminated"] = True
                pod["exit_code"] = 0
        if self.state.get("unexpected_files_after_set_env"):
            self.state["files"].add(CALICO_RECREATED)
        if self.state.get("selection_stuck_after_prefix"):
            self.state["selection_stuck"] = True

    def _refresh_install(self, mode: str) -> None:
        pods = self.state.get("calico_pods")
        if not isinstance(pods, list):
            raise Abort("install-cni evidence is missing", "installer-restart")
        if mode == "keep":
            return
        if mode == "missing":
            self.state["calico_pods"] = []
            return
        for pod in pods:
            if mode == "same_uid":
                pod["container_id"] = str(pod.get("container_id") or "") + "-restart"
                pod["finished_at"] = "2026-10-16T10:00:00+00:00"
            elif mode == "same_container":
                pod["uid"] = str(pod.get("uid") or "") + "-restart"
                pod["finished_at"] = "2026-10-16T10:00:00+00:00"
            elif mode == "stale_time":
                pod["uid"] = str(pod.get("uid") or "") + "-restart"
                pod["container_id"] = str(pod.get("container_id") or "") + "-restart"
            elif mode == "wrong_image":
                pod["uid"] = str(pod.get("uid") or "") + "-restart"
                pod["container_id"] = str(pod.get("container_id") or "") + "-restart"
                pod["finished_at"] = "2026-10-16T10:00:00+00:00"
                pod["image"] = "docker.io/calico/cni:v0.0.0"
            elif mode == "nonzero":
                pod["uid"] = str(pod.get("uid") or "") + "-restart"
                pod["container_id"] = str(pod.get("container_id") or "") + "-restart"
                pod["finished_at"] = "2026-10-16T10:00:00+00:00"
                pod["exit_code"] = 1
                pod["terminated"] = True
            elif mode == "missing_exit":
                pod["uid"] = str(pod.get("uid") or "") + "-restart"
                pod["container_id"] = str(pod.get("container_id") or "") + "-restart"
                pod["finished_at"] = "2026-10-16T10:00:00+00:00"
                pod["exit_code"] = None
                pod["terminated"] = True
            elif mode == "new":
                pod["uid"] = str(pod.get("uid") or "") + "-restart"
                pod["container_id"] = str(pod.get("container_id") or "") + "-restart"
                pod["finished_at"] = "2026-10-16T10:00:00+00:00"
                pod["image"] = INSTALL_CNI_IMAGE
                pod["terminated"] = True
                pod["exit_code"] = 0
            else:
                raise Abort("unknown installer restart fixture", "installer-restart")

    def rollout_restart(self) -> None:
        self.calls.append(("rollout-restart",))
        if self.state.get("restart_fail"):
            raise Abort("rollout restart failed", "installer-restart")
        mode = "keep" if self.state.get("keep_install_identity") else str(self.state.get("restart_install") or "new")
        self._refresh_install(mode)
        if self.state.get("rewrite_cni_digest"):
            config = self.state.setdefault("cni_config", {}).get(CALICO_ACTIVE)
            if not isinstance(config, dict):
                config = {"digest": "ab" * 32, "name": "k8s-pod-network", "plugins": ["calico", "portmap"]}
            config = dict(config)
            config["digest"] = "cd" * 32
            self.state.setdefault("cni_config", {})[CALICO_ACTIVE] = config

    def rollout_observed(self) -> str:
        self.calls.append(("rollout-status-read",))
        if self.state.get("rollout_read_failure"):
            raise Abort("rollout status unreadable", "command")
        return "complete"

    def rollout_status(self) -> None:
        self.calls.append(("rollout",))
        prefix_absent = not any(item.get("name") == "FELIX_INTERFACEPREFIX" for item in self.state.get("felix", []))
        if self.state.get("rollout_fail") and prefix_absent:
            raise Abort("rollout timeout", "felix-rollout")
        if self.state.get("rollout_fail_before_prefix") and not prefix_absent:
            raise Abort("rollout timeout before prefix removal", "installer-restart")
        if not prefix_absent and self.state.get("route_sync_false_after_rollout"):
            for item in self.state["felix"]:
                if item.get("name") == "FELIX_ROUTESYNCDISABLED":
                    item["value"] = "false"

    def calico_install_state(self) -> list[dict[str, Any]]:
        return [dict(pod) for pod in self.state.get("calico_pods", [])]


class Procedure:
    def __init__(
        self,
        cluster: Any,
        execute: bool,
        state_dir: Path | None,
        sleep: Callable[[float], None] | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self.cluster = cluster
        self.execute = execute
        self.state_dir = state_dir
        self.sleep = sleep or time.sleep
        self.now = now or time.monotonic
        self.log: list[str] = []
        self.install_watermark: dict[str, str] | None = None
        self.calico_conflist: dict[str, Any] | None = None
        if hasattr(cluster, "bind_guard"):
            cluster.bind_guard(self.guard_access)

    def note(self, text: str) -> None:
        self.log.append(text)
        print(text, file=sys.stderr)

    def intent_path(self, mutation: str, target: str) -> Path:
        if self.state_dir is None:
            raise Abort("execution requires an explicit --state-dir; no recovery directory was created", "preflight")
        return self.state_dir / INTENT_DIRNAME / f"{mutation_slug(mutation, target)}.json"

    def load_intents(self) -> list[dict[str, Any]]:
        if self.state_dir is None:
            return []
        directory = self.state_dir / INTENT_DIRNAME
        if not directory.exists():
            return []
        found = []
        for path in sorted(directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                found.append({"mutation": path.name, "outcome": None, "malformed": True})
                continue
            if not isinstance(payload, dict) or "outcome" not in payload:
                found.append({"mutation": path.name, "outcome": None, "malformed": True})
                continue
            found.append(payload)
        return found

    def require_no_open_intents(self) -> None:
        open_items = [item for item in self.load_intents() if item.get("outcome") != "verified"]
        if open_items:
            names = [str(item.get("mutation")) for item in open_items]
            raise Abort(
                "intent present without a verified outcome "
                f"({', '.join(names)}); run --audit-recovery before any further execution",
                "interrupted",
            )

    def begin_mutation(self, mutation: str, target: str, intended: dict[str, Any]) -> None:
        self.require_no_open_intents()
        record = {
            "intended": intended,
            "mutation": mutation,
            "outcome": None,
            "prior": self.public_observation(),
            "target": target,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        self.state_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.intent_path(mutation, target), json.dumps(redact_obj(record), indent=2, sort_keys=True))

    def finish_mutation(self, mutation: str, target: str) -> None:
        path = self.intent_path(mutation, target)
        record = json.loads(path.read_text())
        record["after"] = self.public_observation()
        record["outcome"] = "verified"
        record["verified_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        atomic_write_text(path, json.dumps(redact_obj(record), indent=2, sort_keys=True))

    def perform(
        self,
        mutation: str,
        target: str,
        intended: dict[str, Any],
        action: Callable[[], None],
        verify: Callable[[], None],
    ) -> None:
        self._require_execution_state_dir()
        self.guard_access("preflight")
        self.begin_mutation(mutation, target, intended)
        if self._crash_matches("crash_before", mutation, target):
            raise Abort(
                f"simulated crash after the {mutation} intent was written and before the mutation",
                "interrupted",
            )
        action()
        if self._crash_matches("crash_after", mutation, target):
            raise Abort(
                f"simulated crash after {mutation} succeeded and before outcome recording",
                "interrupted",
            )
        verify()
        self.finish_mutation(mutation, target)

    def _crash_matches(self, key: str, mutation: str, target: str) -> bool:
        token = self.cluster_state().get(key)
        return token in {mutation, f"{mutation}:{target}"}

    def cluster_state(self) -> dict[str, Any]:
        state = getattr(self.cluster, "state", None)
        return state if isinstance(state, dict) else {}

    def check_volumes(self, decision: str) -> None:
        self.guard_access(decision)
        baseline = self.load_operator_baseline()
        found = {tuple(item) for item in self.cluster.volumes()}
        expected = {
            (pvc, pv, path, baseline["reclaim_policy"], True) for _, pvc, pv, path in VOLUMES
        }
        if found != expected:
            raise Abort(f"PVC/PV/hostPath binding changed: {sorted(found)}", decision)

    def _require_execution_state_dir(self) -> None:
        if self.state_dir is None:
            raise Abort("execution requires an explicit --state-dir; no recovery directory was created", "preflight")

    def guard_access(self, decision: str) -> None:
        current = self.cluster.context()
        if current != CONTEXT:
            raise Abort(f"context is {current!r}", decision)
        self.assert_cluster_identity(decision)
        self.cluster.resolve_node()

    def load_operator_baseline(self) -> dict[str, str]:
        if self.state_dir is None:
            raise Abort(
                "operator baseline is missing. A person must record server, kube_system_uid, and reclaim_policy "
                "before any migration. This script does not invent those values.",
                "preflight",
            )
        path = self.state_dir / BASELINE_NAME
        if not path.exists():
            raise Abort(
                "operator baseline is missing. A person must record server, kube_system_uid, and reclaim_policy "
                "before any migration. This script does not invent those values.",
                "preflight",
            )
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            raise Abort("operator baseline is unreadable", "preflight") from None
        if not isinstance(payload, dict):
            raise Abort("operator baseline is unreadable", "preflight")
        required = ("server", "kube_system_uid", "reclaim_policy")
        for key in required:
            value = payload.get(key)
            if not isinstance(value, str) or not value.strip():
                raise Abort(f"operator baseline is missing {key}", "preflight")
        if not payload["server"].startswith("https://"):
            raise Abort("operator baseline server is not an https endpoint", "preflight")
        return {key: payload[key] for key in required}

    def assert_cluster_identity(self, decision: str) -> None:
        expected = self.load_operator_baseline()
        live = self.cluster.cluster_identity()
        if live.get("server") != expected["server"] or live.get("kube_system_uid") != expected["kube_system_uid"]:
            raise Abort("cluster identity does not match the operator baseline", decision)

    def _is_survivor(self, pod: dict[str, Any]) -> bool:
        if pod.get("host_network") is not True or pod.get("namespace") != "kube-system":
            return False
        owners = pod.get("owners")
        if not isinstance(owners, list):
            return False
        for owner in owners:
            if not isinstance(owner, dict) or owner.get("controller") is not True:
                continue
            if owner.get("kind") == "DaemonSet" and owner.get("name") in SURVIVOR_DAEMONSETS:
                return True
            if owner.get("kind") == "ReplicaSet" and isinstance(owner.get("name"), str):
                parents = self.cluster.replicaset_parents("kube-system", owner["name"])
                if not isinstance(parents, list):
                    return False
                if any(
                    isinstance(parent, dict)
                    and parent.get("controller") is True
                    and parent.get("kind") == "Deployment"
                    and parent.get("name") == "calico-typha"
                    for parent in parents
                ):
                    return True
        return False

    def assert_no_workload_ifaces(self, decision: str) -> None:
        self.guard_access(decision)
        counts = self.cluster.workload_ifaces()
        results = self.cluster.bad_result_files()
        if counts.get("veth") or counts.get("cali") or results:
            raise Abort(f"unexpected interfaces or result files: {counts} {results}", decision)
        unexpected = []
        allowed = set()
        for pod in self.cluster.cluster_pods():
            if not pod.get("name") or not pod.get("namespace"):
                raise Abort("pod identity is unreadable", decision)
            if self._is_survivor(pod):
                allowed.add((pod["namespace"], pod["name"]))
                continue
            unexpected.append(f"{pod['namespace']}/{pod['name']}")
        if unexpected:
            raise Abort(f"unexpected workload pods remain: {unexpected}", decision)
        for sandbox in self.cluster.sandboxes():
            key = (sandbox["namespace"], sandbox["name"])
            if key in allowed:
                continue
            interface = sandbox.get("interface") or "unreported"
            raise Abort(
                f"workload sandbox remains: {sandbox['namespace']}/{sandbox['name']} interface {interface}",
                decision,
            )

    def poll(self, predicate: Callable[[], bool], timeout: float, decision: str, label: str) -> None:
        deadline = self.now() + timeout
        while True:
            if predicate():
                return
            if self.now() >= deadline:
                raise Abort(f"timeout waiting for {label}", decision)
            self.sleep(2)

    def preflight(self) -> None:
        self.guard_access("preflight")
        if not self.cluster.node_ready() or not self.cluster.readyz():
            raise Abort("node or /readyz is not ready", "preflight")
        self.check_volumes("preflight")
        env = {item["name"]: item for item in self.cluster.felix_env()}
        if env.get("FELIX_ROUTESYNCDISABLED", {}).get("value") != "true":
            raise Abort("FELIX_ROUTESYNCDISABLED is not true", "preflight")
        if "configMapKeyRef" not in json.dumps(env.get("FELIX_TYPHAK8SSERVICENAME", {})):
            raise Abort("FELIX_TYPHAK8SSERVICENAME valueFrom missing", "preflight")
        if "fieldRef" not in json.dumps(env.get("NODENAME", {})):
            raise Abort("NODENAME valueFrom missing", "preflight")
        rules = self.cluster.clusterrole()
        if [canonical_rule(rule) for rule in rules] != [canonical_rule(rule) for rule in ORIGINAL_RULES]:
            raise Abort("ClusterRole does not match the recorded original rules", "preflight")

    def scale_down(self) -> None:
        for kind, namespace, names, expected in SCALE_GROUPS:
            self.guard_access("scale-down")
            self.check_volumes("scale-down")
            recorded = {}
            for name in names:
                current = self.cluster.replicas(kind, namespace, name)
                recorded[name] = current
                if current != expected:
                    raise Abort(
                        f"{kind}/{namespace}/{name} replicas are {current}, expected {expected}",
                        "scale-down",
                    )
            self.note(f"observed {kind}/{namespace} {recorded}")
            if not self.execute:
                self.note(f"DRY-RUN scale {kind}/{namespace} {names} to 0")
                continue
            self._require_execution_state_dir()
            assert self.state_dir is not None
            for name, count in recorded.items():
                atomic_write_text(
                    replica_record_path(self.state_dir, namespace, kind, name),
                    json.dumps({"namespace": namespace, "kind": kind, "name": name, "replicas": count}, sort_keys=True),
                )
            self.perform(
                f"scale-{namespace}-{kind}",
                ",".join(names),
                {"replicas": 0},
                lambda kind=kind, namespace=namespace, names=names: self.cluster.scale_to_zero(kind, namespace, names),
                lambda kind=kind, namespace=namespace, names=names: self.verify_scaled(kind, namespace, names),
            )

    def move_file(self, src: str, dst: str, decision: str) -> None:
        self.assert_no_workload_ifaces(decision)
        if dst == CALICO_RECREATED and self.cluster.cni_exists(dst):
            raise Abort(f"{CALICO_RECREATED} already exists; refusing to overwrite", decision)
        if self.cluster.cni_exists(dst):
            raise Abort(f"destination exists: {dst}", decision)
        if not self.cluster.cni_exists(src):
            raise Abort(f"source missing: {src}", decision)
        if not self.execute:
            self.note(f"DRY-RUN mv -n {CNI_DIR}/{src} {CNI_DIR}/{dst}")
            return
        self.perform(
            "cni-move",
            f"{src}->{dst}",
            {"destination_present": dst, "source_absent": src},
            lambda src=src, dst=dst: self.cluster.move(src, dst),
            lambda src=src, dst=dst, decision=decision: self.verify_move(src, dst, decision),
        )

    def verify_scaled(self, kind: str, namespace: str, names: Sequence[str]) -> None:
        self.poll(
            lambda namespace=namespace, kind=kind, names=names: not any(
                self.cluster.controller_pods(namespace, kind, name) for name in names
            ),
            SCALE_TIMEOUT,
            "scale-down",
            f"pods gone in {namespace}",
        )
        self.check_volumes("scale-down")
        for name in names:
            if self.cluster.replicas(kind, namespace, name) != 0:
                raise Abort(f"{kind}/{namespace}/{name} did not reach 0", "scale-down")

    def verify_move(self, src: str, dst: str, decision: str) -> None:
        if self.cluster.cni_exists(src) or not self.cluster.cni_exists(dst):
            raise Abort(f"mv postcondition failed for {src} -> {dst}", decision)
        self.check_volumes(decision)

    def require_network(self, name: str, plugin: str, decision: str) -> None:
        status, networks = self.cluster.networks()
        names = [item[0] for item in networks]
        match = [plugins for net, plugins in networks if net == name]
        other = "kindnet" if name == "k8s-pod-network" else "k8s-pod-network"
        if status != "OK" or not match or plugin not in match[0] or other in names:
            raise Abort(f"crictl selection is {status} {networks}", decision)

    def poll_network(self, name: str, plugin: str, decision: str) -> None:
        if not self.execute:
            self.note(f"DRY-RUN would poll crictl for {name}/{plugin}")
            return
        self.guard_access(decision)
        self.poll(lambda: self._network_ok(name, plugin), SELECT_TIMEOUT, decision, f"crictl {name}")
        self.require_network(name, plugin, decision)

    def _network_ok(self, name: str, plugin: str) -> bool:
        status, networks = self.cluster.networks()
        names = [item[0] for item in networks]
        match = [plugins for net, plugins in networks if net == name]
        other = "kindnet" if name == "k8s-pod-network" else "k8s-pod-network"
        return status == "OK" and bool(match) and plugin in match[0] and other not in names

    def record_progress(self, mutation: str, detail: str = "") -> None:
        self._require_execution_state_dir()
        assert self.state_dir is not None
        payload = {
            "detail": detail,
            "last_completed_mutation": mutation,
            "observed": self.public_observation(),
        }
        self.state_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.state_dir / PROGRESS_NAME, json.dumps(payload, indent=2, sort_keys=True))
        self.note(f"last completed mutation: {mutation}")
        if detail:
            self.note(detail)

    def public_observation(self) -> dict[str, Any]:
        observed: dict[str, Any] = {}
        try:
            env = self.cluster.felix_env()
            observed["route_sync_true"] = any(
                item.get("name") == "FELIX_ROUTESYNCDISABLED" and item.get("value") == "true" for item in env
            )
            prefix = [item for item in env if item.get("name") == "FELIX_INTERFACEPREFIX"]
            if not prefix:
                observed["interface_prefix"] = "absent"
            elif prefix[0].get("value") == "veth":
                observed["interface_prefix"] = "veth"
            else:
                observed["interface_prefix"] = "present-unprinted"
        except Abort:
            observed["felix_error"] = "unreadable"
        try:
            observed["cni_files"] = {
                name: self.cluster.cni_exists(name)
                for name in (CALICO_ACTIVE, CALICO_HELD, CALICO_RECREATED, KINDNET_ACTIVE, KINDNET_HELD)
            }
        except Abort:
            observed["cni_files_error"] = "unreadable"
        try:
            pods = self.cluster.calico_install_state()
            observed["calico_pods"] = [
                {
                    "exit_code": pod.get("exit_code"),
                    "image": pod.get("image"),
                    "name": pod.get("name"),
                    "terminated": pod.get("terminated"),
                }
                for pod in pods
            ]
        except Abort:
            observed["calico_pods_error"] = "unreadable"
        try:
            status, networks = self.cluster.networks()
            observed["runtime"] = {"networks": [name for name, _plugins in networks], "status": status}
        except Abort:
            observed["runtime_error"] = "unreadable"
        try:
            observed["workload_ifaces"] = self.cluster.workload_ifaces()
            observed["result_files"] = self.cluster.bad_result_files()
        except Abort:
            observed["interface_error"] = "unreadable"
        try:
            observed["rules"] = [canonical_rule(rule) for rule in self.cluster.clusterrole()]
        except Abort:
            observed["rules_error"] = "unreadable"
        try:
            replicas = {}
            for kind, namespace, names, _expected in SCALE_GROUPS:
                for name in names:
                    replicas[f"{kind}/{namespace}/{name}"] = self.cluster.replicas(kind, namespace, name)
            observed["replicas"] = replicas
        except Abort:
            observed["replicas_error"] = "unreadable"
        return observed

    def abort_after_prefix_removal(self, detail: str) -> None:
        try:
            self.check_volumes("felix-rollout")
            volume_note = "PVC/PV/hostPath bindings still match the operator baseline."
        except Abort as exc:
            volume_note = (
                f"Volume evidence failed closed: {exc}. No PVC, PV, or host directory was deleted or recreated."
            )
        self.record_progress("FELIX_INTERFACEPREFIX-removed", detail)
        raise Abort(
            detail
            + " "
            + volume_note
            + " DaemonSet was not changed again. CNI files were not moved. Replicas were not restored. "
            + "This state requires operator-led recovery. Automated rollback is disabled.",
            "felix-rollout",
        )

    def save_snapshot(self) -> None:
        self._require_execution_state_dir()
        assert self.state_dir is not None
        path = self.state_dir / SNAPSHOT_NAME
        rules = self.cluster.clusterrole()
        text = json.dumps(rules, indent=2, sort_keys=True)
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise Abort("ClusterRole snapshot was not saved", "rbac-add") from exc
        if path.exists():
            try:
                current = path.read_text()
                mode = path.stat().st_mode & 0o777
            except OSError as exc:
                raise Abort("existing ClusterRole snapshot cannot be read", "rbac-add") from exc
            if current != text or mode != 0o600:
                raise Abort("existing ClusterRole snapshot was not replaced", "rbac-add")
            return
        try:
            atomic_write_private(path, text)
        except OSError as exc:
            raise Abort("ClusterRole snapshot was not saved", "rbac-add") from exc
        if not path.exists() or (path.stat().st_mode & 0o777) != 0o600 or path.read_text() != text:
            raise Abort("ClusterRole snapshot was not saved", "rbac-add")

    def load_snapshot(self) -> list[dict[str, Any]]:
        path = self.state_dir / SNAPSHOT_NAME
        if not path.exists():
            raise Abort("original ClusterRole snapshot is missing", "rbac-rollback")
        return list(json.loads(path.read_text()))

    def add_rbac(self) -> None:
        self.check_volumes("rbac-add")
        before = self.cluster.clusterrole()
        if [canonical_rule(rule) for rule in before] != [canonical_rule(rule) for rule in ORIGINAL_RULES]:
            raise Abort("refusing to add RBAC; role is not the original snapshot", "rbac-add")
        self.save_snapshot()
        patch = json.dumps([{"op": "add", "path": "/rules/-", "value": ADDED_RULE}])
        if not self.execute:
            self.note("DRY-RUN patch clusterrole calico-cni-plugin add clusterinformations get")
            return
        self.perform(
            "rbac-add",
            "clusterrole/calico-cni-plugin",
            {"added": canonical_rule(ADDED_RULE)},
            lambda patch=patch: self.cluster.patch_role(patch),
            self.verify_rbac_added,
        )

    def verify_rbac_added(self) -> None:
        live = self.cluster.clusterrole()
        original = self.load_snapshot()
        if not sole_added_permission(live, original):
            raise Abort("RBAC diff is not the single added get permission; marker not written", "rbac-add")
        (self.state_dir / MARKER_NAME).write_text("added\n")
        self.check_volumes("rbac-add")

    def rollback_rbac(self, decision: str) -> None:
        snapshot = self.state_dir / SNAPSHOT_NAME
        if not snapshot.exists():
            raise Abort(
                "original ClusterRole snapshot is missing; manual intervention required; role left unchanged",
                decision,
            )
        original = self.load_snapshot()
        live = self.cluster.clusterrole()
        extra, missing = rules_diff(live, original)
        if not extra and not missing:
            self.note("ClusterRole matches the saved snapshot; no RBAC patch")
            marker = self.state_dir / MARKER_NAME
            if marker.exists():
                marker.unlink()
            return
        if not sole_added_permission(live, original):
            raise Abort(
                "RBAC diff is not the single added permission; manual intervention required; role left unchanged",
                decision,
            )
        index = next(i for i, rule in enumerate(live) if canonical_rule(rule) == canonical_rule(ADDED_RULE))
        patch = json.dumps(
            [
                {"op": "test", "path": f"/rules/{index}/resources", "value": ["clusterinformations"]},
                {"op": "test", "path": f"/rules/{index}/verbs", "value": ["get"]},
                {"op": "test", "path": f"/rules/{index}/apiGroups", "value": ["crd.projectcalico.org"]},
                {"op": "remove", "path": f"/rules/{index}"},
            ]
        )
        if not self.execute:
            self.note("DRY-RUN remove the added clusterinformations get")
            return
        self.perform(
            "rbac-remove",
            "clusterrole/calico-cni-plugin",
            {"rules": [canonical_rule(rule) for rule in original]},
            lambda patch=patch: self.cluster.patch_role(patch),
            lambda original=original, decision=decision: self.verify_rbac_removed(original, decision),
        )

    def verify_rbac_removed(self, original: list[dict[str, Any]], decision: str) -> None:
        if [canonical_rule(rule) for rule in self.cluster.clusterrole()] != [canonical_rule(rule) for rule in original]:
            raise Abort("RBAC rollback did not restore the snapshot; manual intervention required", decision)
        marker = self.state_dir / MARKER_NAME
        if marker.exists():
            marker.unlink()

    def assert_felix_preserved(self, decision: str, prefix_present: bool) -> None:
        env = {item["name"]: item for item in self.cluster.felix_env()}
        if env.get("FELIX_ROUTESYNCDISABLED", {}).get("value") != "true":
            raise Abort("FELIX_ROUTESYNCDISABLED changed", decision)
        if "configMapKeyRef" not in json.dumps(env.get("FELIX_TYPHAK8SSERVICENAME", {})):
            raise Abort("Typha valueFrom missing after env change", decision)
        if "fieldRef" not in json.dumps(env.get("NODENAME", {})):
            raise Abort("NODENAME valueFrom missing after env change", decision)
        has_prefix = "FELIX_INTERFACEPREFIX" in env
        if has_prefix != prefix_present:
            raise Abort("FELIX_INTERFACEPREFIX presence does not match the step", decision)

    def forward(self) -> None:
        if self.execute:
            self._require_execution_state_dir()
            self.require_no_open_intents()
        self.preflight()
        self.scale_down()
        if not self.execute:
            self.note("DRY-RUN stopped before cutover. No replicas were changed.")
            return
        self.assert_no_workload_ifaces("interface-gate")
        self.add_rbac()
        self.move_file(CALICO_HELD, CALICO_ACTIVE, "cni-calico-move")
        self.poll_network("k8s-pod-network", "calico", "cni-calico-move")
        self.move_file(KINDNET_ACTIVE, KINDNET_HELD, "cni-kindnet-move")
        self.poll_network("k8s-pod-network", "calico", "cni-kindnet-move")
        self.check_volumes("installer-restart")
        self.calico_conflist = self.cluster.cni_public_config(CALICO_ACTIVE)
        self.assert_calico_conflist(self.calico_conflist, "installer-restart")
        self.install_watermark = self.capture_install_watermark("installer-restart")
        self.assert_forward_cni_files("installer-restart", phase="before the installer restart")
        self.require_network("k8s-pod-network", "calico", "installer-restart")
        self.perform(
            "rollout-restart",
            "daemonset/calico-node",
            {"interface_prefix": "veth", "route_sync": True},
            self.cluster.rollout_restart,
            lambda: self.assert_felix_preserved("installer-restart", prefix_present=True),
        )
        try:
            self.guard_access("installer-restart")
            self.cluster.rollout_status()
        except Abort as exc:
            raise Abort(
                f"installer restart rollout failed before FELIX_INTERFACEPREFIX removal: {exc}",
                "installer-restart",
            ) from exc
        self.assert_ready_for_prefix_removal("felix-rollout")
        proven_install = self.capture_install_watermark("felix-rollout")
        self.perform(
            "set-env",
            "daemonset/calico-node FELIX_INTERFACEPREFIX-",
            {"interface_prefix": "absent", "route_sync": True},
            lambda: self.cluster.set_env("FELIX_INTERFACEPREFIX-"),
            lambda: self.assert_felix_preserved("felix-rollout", prefix_present=False),
        )
        self.record_progress("FELIX_INTERFACEPREFIX-removed")
        try:
            self.guard_access("felix-rollout")
            self.cluster.rollout_status()
        except Abort as exc:
            self.abort_after_prefix_removal(f"rollout failed after FELIX_INTERFACEPREFIX removal: {exc}")
        try:
            self.assert_install_is_new_attempt(proven_install, "felix-rollout")
            self.assert_felix_preserved("felix-rollout", prefix_present=False)
            current = self.cluster.cni_public_config(CALICO_ACTIVE)
            self.assert_calico_conflist(current, "felix-rollout")
            if not isinstance(self.calico_conflist, dict) or current.get("digest") != self.calico_conflist.get("digest"):
                raise Abort(
                    "install-cni changed the Calico conflist bytes after FELIX_INTERFACEPREFIX removal",
                    "felix-rollout",
                )
        except Abort as exc:
            self.abort_after_prefix_removal(str(exc))
        try:
            self.assert_forward_cni_files("felix-rollout")
        except Abort as exc:
            self.abort_after_prefix_removal(str(exc))
        try:
            self.poll_network("k8s-pod-network", "calico", "felix-rollout")
        except Abort as exc:
            self.abort_after_prefix_removal(f"runtime selection failed after FELIX_INTERFACEPREFIX removal: {exc}")
        self.check_volumes("felix-rollout")
        self.record_progress("runtime-selection-verified")
        self.note("cutover commands finished; canary and replica restore are not part of this script")

    def rollback_before_daemonset(self) -> None:
        raise Abort(
            "containment execution is disabled; no cluster call and no CNI rename was made",
            "containment-disabled",
        )

    def parse_finished_at(self, value: Any, decision: str) -> datetime.datetime:
        if not isinstance(value, str) or not value:
            raise Abort("install-cni finishedAt is missing", decision)
        text = value.replace("Z", "+00:00")
        try:
            parsed = datetime.datetime.fromisoformat(text)
        except ValueError as exc:
            raise Abort("install-cni finishedAt is unparseable", decision) from exc
        if parsed.tzinfo is None:
            raise Abort("install-cni finishedAt has no timezone", decision)
        return parsed

    def require_installer_identity(self, decision: str) -> dict[str, Any]:
        self.guard_access(decision)
        pods = self.cluster.calico_install_state()
        where = "before the DaemonSet change"
        if len(pods) != 1 or not pods[0].get("name") or not pods[0].get("uid") or not pods[0].get("container_id"):
            raise Abort(f"calico-node pod identity is ambiguous {where}.", decision)
        pod = pods[0]
        self.parse_finished_at(pod.get("finished_at"), decision)
        if pod.get("image") != INSTALL_CNI_IMAGE or pod.get("terminated") is not True or pod.get("exit_code") != 0:
            raise Abort(f"install-cni identity or termination cannot be proven {where}.", decision)
        if INSTALL_IMAGE_ID.fullmatch(str(pod.get("image_id") or "")) is None:
            raise Abort("install-cni imageID is missing or is not the terminated calico/cni digest.", decision)
        return pod

    def capture_install_watermark(self, decision: str) -> dict[str, str]:
        pod = self.require_installer_identity(decision)
        return {
            "container_id": str(pod["container_id"]),
            "finished_at": self.parse_finished_at(pod.get("finished_at"), decision).isoformat(),
            "image_id": str(pod["image_id"]),
            "uid": str(pod["uid"]),
        }

    def assert_install_is_new_attempt(self, watermark: dict[str, str] | None, decision: str) -> None:
        self.guard_access(decision)
        if not isinstance(watermark, dict):
            raise Abort("pre-mutation install-cni watermark is missing", decision)
        pods = self.cluster.calico_install_state()
        if len(pods) != 1 or not pods[0].get("name") or not pods[0].get("uid") or not pods[0].get("container_id"):
            raise Abort("calico-node pod identity is ambiguous after the DaemonSet change. manual intervention required; CNI files were not moved.", decision)
        pod = pods[0]
        if pod.get("image") != INSTALL_CNI_IMAGE or pod.get("terminated") is not True or pod.get("exit_code") != 0:
            raise Abort("install-cni identity or termination cannot be proven after the DaemonSet change. manual intervention required; CNI files were not moved.", decision)
        if INSTALL_IMAGE_ID.fullmatch(str(pod.get("image_id") or "")) is None or pod.get("image_id") != watermark.get("image_id"):
            raise Abort(
                "install-cni imageID is not the terminated calico/cni digest recorded before this attempt.",
                decision,
            )
        finished = self.parse_finished_at(pod.get("finished_at"), decision)
        previous = self.parse_finished_at(watermark.get("finished_at"), decision)
        if finished <= previous or pod.get("uid") == watermark.get("uid") or pod.get("container_id") == watermark.get("container_id"):
            raise Abort(
                "install-cni evidence is still the pre-mutation watermark. An old exit code 0 is not the current attempt.",
                decision,
            )

    def assert_forward_cni_files(self, decision: str, phase: str = "after FELIX_INTERFACEPREFIX removal") -> None:
        expected = {
            CALICO_ACTIVE: True,
            CALICO_HELD: False,
            CALICO_RECREATED: False,
            KINDNET_ACTIVE: False,
            KINDNET_HELD: True,
        }
        actual = {name: self.cluster.cni_exists(name) for name in expected}
        if actual != expected:
            raise Abort(f"unexpected CNI file state {phase}: {actual}", decision)

    def assert_calico_conflist(self, config: dict[str, Any], decision: str) -> None:
        plugins = config.get("plugins")
        digest = config.get("digest")
        if config.get("name") != "k8s-pod-network" or not isinstance(plugins, list):
            raise Abort("Calico conflist is not the k8s-pod-network document", decision)
        if "calico" not in plugins or "ptp" in plugins:
            raise Abort("Calico conflist plugins are not the Calico CNI", decision)
        if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise Abort("Calico conflist digest is missing", decision)

    def assert_ready_for_prefix_removal(self, decision: str) -> None:
        self.guard_access(decision)
        self.assert_install_is_new_attempt(self.install_watermark, decision)
        current = self.cluster.cni_public_config(CALICO_ACTIVE)
        self.assert_calico_conflist(current, decision)
        previous = getattr(self, "calico_conflist", None)
        if not isinstance(previous, dict) or current.get("digest") != previous.get("digest"):
            raise Abort(
                "install-cni did not preserve the Calico conflist bytes. Exit code 0 is not that proof.",
                decision,
            )
        self.assert_felix_preserved(decision, prefix_present=True)
        self.assert_forward_cni_files(decision, phase="before FELIX_INTERFACEPREFIX removal")
        self.require_network("k8s-pod-network", "calico", decision)

    def containment_rename_plan(self, decision: str) -> list[tuple[str, str]]:
        """Validate both containment renames before either filename changes.

        The two renames are still separate filesystem operations. This only
        refuses to start when the current names already make one of them unsafe.
        """
        if self.cluster.cni_exists(CALICO_RECREATED):
            raise Abort(f"{CALICO_RECREATED} already exists; refusing to overwrite", decision)
        if not self.cluster.cni_exists(CALICO_ACTIVE):
            raise Abort(f"source missing: {CALICO_ACTIVE}; refusing both renames", decision)
        held = self.cluster.cni_exists(KINDNET_HELD)
        active = self.cluster.cni_exists(KINDNET_ACTIVE)
        if held and active:
            raise Abort(
                "second containment move would collide with an existing kindnet conflist; refusing both renames",
                decision,
            )
        if not held and not active:
            raise Abort("kindnet conflist source is missing; refusing both renames", decision)
        moves = [(CALICO_ACTIVE, CALICO_RECREATED)]
        if held:
            moves.append((KINDNET_HELD, KINDNET_ACTIVE))
        return moves

    def before_daemonset_rename_plan(self, decision: str) -> list[tuple[str, str]]:
        if not self.cluster.cni_exists(CALICO_ACTIVE):
            raise Abort(f"source missing: {CALICO_ACTIVE}; refusing both renames", decision)
        if self.cluster.cni_exists(CALICO_HELD):
            raise Abort(f"destination exists: {CALICO_HELD}; refusing both renames", decision)
        if not self.cluster.cni_exists(KINDNET_HELD):
            raise Abort(f"source missing: {KINDNET_HELD}; refusing both renames", decision)
        if self.cluster.cni_exists(KINDNET_ACTIVE):
            raise Abort(f"destination exists: {KINDNET_ACTIVE}; refusing both renames", decision)
        return [(CALICO_ACTIVE, CALICO_HELD), (KINDNET_HELD, KINDNET_ACTIVE)]

    def apply_renames(self, moves: Sequence[tuple[str, str]], decision: str) -> None:
        completed: list[tuple[str, str]] = []
        for src, dst in moves:
            try:
                self.move_file(src, dst, decision)
            except Abort as exc:
                done = ", ".join(f"{src_name}->{dst_name}" for src_name, dst_name in completed) or "none"
                raise Abort(
                    f"{exc} Completed renames: {done}. "
                    "The two renames are not one transaction. "
                    "Do not reverse a completed rename automatically. "
                    "Inspect CNI filenames and crictl info. Do not scale replicas.",
                    decision,
                ) from exc
            completed.append((src, dst))

    def rollback_during_rollout(self) -> None:
        raise Abort(
            "containment execution is disabled; no cluster call and no CNI rename was made",
            "containment-disabled",
        )

    def probe(self, func: Callable[[], Any]) -> tuple[bool, Any]:
        try:
            return True, func()
        except (Abort, OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return False, None

    def audit_item(
        self,
        name: str,
        status: str,
        observed: Any,
        expected: Any,
        next_step: str,
    ) -> dict[str, Any]:
        return {
            "expected": redact_obj(expected),
            "name": name,
            "next_step": "" if status == "consistent" else next_step,
            "observed": redact_obj(observed),
            "status": status,
        }

    def saved_replica(self, namespace: str, kind: str, name: str) -> tuple[str, int | None]:
        if self.state_dir is None:
            return "missing", None
        path = replica_record_path(self.state_dir, namespace, kind, name)
        if not path.exists():
            return "missing", None
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return "malformed", None
        if not isinstance(payload, dict) or payload.get("name") != name or not isinstance(payload.get("replicas"), int):
            return "malformed", None
        return "ok", payload["replicas"]

    def _scale_identity(self, mutation: str) -> tuple[str, str] | None:
        if mutation.endswith("-deploy"):
            return "deploy", mutation[len("scale-") : -len("-deploy")]
        if mutation.endswith("-sts"):
            return "sts", mutation[len("scale-") : -len("-sts")]
        return None

    def _pair_status(self, live: Any, prior: Any, intended: Any) -> str:
        if live == prior == intended:
            return "mixed_or_ambiguous"
        if live == prior:
            return "change_absent"
        if live == intended:
            return "change_present"
        return "mixed_or_ambiguous"

    def _unreadable(self, reason: str) -> dict[str, Any]:
        return {"classification": "unreadable", "live": None, "reason": reason}

    def classify_open_intent(self, intent: dict[str, Any]) -> dict[str, Any]:
        if intent.get("malformed") or not isinstance(intent.get("prior"), dict) or not isinstance(intent.get("intended"), dict):
            return self._unreadable("intent record is incomplete")
        mutation = str(intent.get("mutation") or "")
        prior = intent["prior"]
        intended = intent["intended"]
        if mutation in {"rbac-add", "rbac-remove"}:
            return self._classify_rules(prior, intended, mutation)
        if mutation == "cni-move":
            return self._classify_cni(prior, intended)
        if mutation in {"set-env", "rollout-restart"}:
            return self._classify_env(prior, intended)
        if mutation.startswith("scale-"):
            return self._classify_scale(prior, intended, str(intent.get("target") or ""), mutation)
        return self._unreadable("mutation type is not classified")

    def _classify_rules(self, prior: dict[str, Any], intended: dict[str, Any], mutation: str) -> dict[str, Any]:
        prior_rules = prior.get("rules")
        ok, live_rules = self.probe(lambda: [canonical_rule(rule) for rule in self.cluster.clusterrole()])
        if not ok or not isinstance(prior_rules, list):
            return self._unreadable("ClusterRole evidence is missing")
        if mutation == "rbac-add":
            added = intended.get("added")
            if not isinstance(added, str):
                return self._unreadable("RBAC intent is missing the added rule")
            intended_rules = [*prior_rules, added]
        else:
            intended_rules = intended.get("rules")
            if not isinstance(intended_rules, list):
                return self._unreadable("RBAC intent is missing the intended rules")
        status = self._pair_status(live_rules, list(prior_rules), intended_rules)
        return {"classification": status, "live": live_rules}

    def _classify_cni(self, prior: dict[str, Any], intended: dict[str, Any]) -> dict[str, Any]:
        src = intended.get("source_absent")
        dst = intended.get("destination_present")
        prior_files = prior.get("cni_files")
        if not isinstance(src, str) or not isinstance(dst, str) or not isinstance(prior_files, dict):
            return self._unreadable("CNI intent is missing source or destination")
        if src not in prior_files or dst not in prior_files:
            return self._unreadable("CNI prior observation is missing a filename")
        live: dict[str, bool] = {}
        for name in (src, dst):
            ok, exists = self.probe(lambda name=name: self.cluster.cni_exists(name))
            if not ok or not isinstance(exists, bool):
                return self._unreadable("CNI filename evidence is missing")
            live[name] = exists
        status = self._pair_status(
            (live[src], live[dst]),
            (prior_files[src], prior_files[dst]),
            (False, True),
        )
        return {"classification": status, "live": {"destination": live[dst], "source": live[src]}}

    def _classify_env(self, prior: dict[str, Any], intended: dict[str, Any]) -> dict[str, Any]:
        if "interface_prefix" not in prior or "route_sync_true" not in prior:
            return self._unreadable("DaemonSet prior observation is missing")
        if "interface_prefix" not in intended or "route_sync" not in intended:
            return self._unreadable("DaemonSet intent is missing the intended env")
        ok, env = self.probe(self.cluster.felix_env)
        if not ok or not isinstance(env, list):
            return self._unreadable("DaemonSet env could not be read")
        route_sync = any(item.get("name") == "FELIX_ROUTESYNCDISABLED" and item.get("value") == "true" for item in env)
        prefix_items = [item for item in env if item.get("name") == "FELIX_INTERFACEPREFIX"]
        if not prefix_items:
            prefix = "absent"
        elif prefix_items[0].get("value") == "veth":
            prefix = "veth"
        else:
            prefix = "present-unprinted"
        live = {"interface_prefix": prefix, "route_sync": route_sync}
        status = self._pair_status(
            (prefix, route_sync),
            (prior["interface_prefix"], prior["route_sync_true"]),
            (intended["interface_prefix"], intended["route_sync"]),
        )
        return {"classification": status, "live": live}

    def _classify_scale(self, prior: dict[str, Any], intended: dict[str, Any], target: str, mutation: str) -> dict[str, Any]:
        identity = self._scale_identity(mutation)
        prior_replicas = prior.get("replicas")
        names = [name for name in target.split(",") if name]
        if identity is None or not names or not isinstance(prior_replicas, dict) or intended.get("replicas") != 0:
            return self._unreadable("scale intent is missing controllers or the intended count")
        kind, namespace = identity
        live_counts = []
        prior_counts = []
        for name in names:
            key = f"{kind}/{namespace}/{name}"
            if key not in prior_replicas:
                return self._unreadable("scale prior observation is missing a controller")
            ok, live = self.probe(lambda kind=kind, namespace=namespace, name=name: self.cluster.replicas(kind, namespace, name))
            if not ok:
                return self._unreadable("live replica count could not be read")
            live_counts.append(live)
            prior_counts.append(prior_replicas[key])
        status = self._pair_status(tuple(live_counts), tuple(prior_counts), tuple(0 for _ in names))
        return {"classification": status, "live": dict(zip(names, live_counts, strict=True))}

    def build_audit_report(self) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        hold = (
            "Do not restore replicas automatically. Restoring replicas must wait until "
            "networking is verified and must be done deliberately."
        )
        ok, context = self.probe(self.cluster.context)
        if not ok:
            items.append(self.audit_item("context", "unknown", None, CONTEXT, "Context API failed. Do not execute."))
        elif context != CONTEXT:
            items.append(self.audit_item("context", "inconsistent", context, CONTEXT, "Refuse to continue on this context."))
        else:
            items.append(self.audit_item("context", "consistent", context, CONTEXT, ""))

        ready_ok, ready = self.probe(lambda: (bool(self.cluster.node_ready()), bool(self.cluster.readyz())))
        if not ready_ok:
            items.append(self.audit_item("node-ready", "unknown", None, [True, True], "Node readiness could not be read. Do not execute."))
        elif ready != (True, True):
            items.append(self.audit_item("node-ready", "inconsistent", list(ready), [True, True], "The node or /readyz is not ready. Do not execute."))
        else:
            items.append(self.audit_item("node-ready", "consistent", list(ready), [True, True], ""))

        volume_ok, found = self.probe(self.cluster.volumes)
        baseline_ok, baseline = self.probe(self.load_operator_baseline)
        if not volume_ok or not baseline_ok:
            items.append(self.audit_item("volumes", "unknown", None, "PVC, PV, hostPath, and a confirmed reclaim policy", "PVC, PV, hostPath, or reclaim policy evidence failed. Do not treat a binding as intact."))
        else:
            expected_volumes = sorted((pvc, pv, path, baseline["reclaim_policy"], True) for _, pvc, pv, path in VOLUMES)
            if sorted(found) != expected_volumes:
                items.append(self.audit_item("volumes", "inconsistent", sorted(found), expected_volumes, "A recorded PVC, PV, host directory, or reclaim policy does not match. Do not delete or repair it."))
            else:
                items.append(self.audit_item("volumes", "consistent", expected_volumes, expected_volumes, ""))

        identity_ok, identity = self.probe(self.cluster.cluster_identity)
        if not baseline_ok or not identity_ok:
            items.append(self.audit_item("cluster-identity", "unknown", None, "https server and kube-system uid", "Cluster identity could not be compared. Do not execute."))
        elif identity.get("server") != baseline["server"] or identity.get("kube_system_uid") != baseline["kube_system_uid"]:
            items.append(self.audit_item("cluster-identity", "inconsistent", identity, {"kube_system_uid": baseline["kube_system_uid"], "server": baseline["server"]}, "The API server or kube-system uid does not match the operator baseline. Do not execute."))
        else:
            items.append(self.audit_item("cluster-identity", "consistent", identity, {"kube_system_uid": baseline["kube_system_uid"], "server": baseline["server"]}, ""))

        for kind, namespace, names, baseline in SCALE_GROUPS:
            for name in names:
                label = f"replicas/{namespace}/{kind}/{name}"
                got, live = self.probe(lambda kind=kind, namespace=namespace, name=name: self.cluster.replicas(kind, namespace, name))
                record_state, saved = self.saved_replica(namespace, kind, name)
                if not got:
                    items.append(self.audit_item(label, "unknown", None, baseline, hold))
                elif live != baseline:
                    items.append(self.audit_item(label, "inconsistent", live, baseline, hold))
                else:
                    items.append(self.audit_item(label, "consistent", live, baseline, ""))
                saved_label = f"saved-replicas/{namespace}/{kind}/{name}"
                if record_state != "ok":
                    items.append(
                        self.audit_item(
                            saved_label,
                            "unknown",
                            record_state,
                            "a saved original replica count",
                            "The saved replica file is missing or malformed. Do not invent a count. " + hold,
                        )
                    )
                elif saved != live:
                    items.append(self.audit_item(saved_label, "inconsistent", {"live": live, "saved": saved}, saved, hold))
                else:
                    items.append(self.audit_item(saved_label, "consistent", {"live": live, "saved": saved}, saved, ""))
                pod_label = f"owned-pods/{namespace}/{kind}/{name}"
                pods_ok, pods = self.probe(
                    lambda kind=kind, namespace=namespace, name=name: self.cluster.controller_pods(namespace, kind, name)
                )
                if not pods_ok:
                    items.append(self.audit_item(pod_label, "unknown", None, "owned pod list", "Pod ownership could not be read. Do not scale."))
                elif got and live == 0 and pods:
                    items.append(self.audit_item(pod_label, "inconsistent", pods, [], "Replicas are 0 but owned pods remain. Do not scale another group."))
                elif got and live > 0 and not pods:
                    items.append(self.audit_item(pod_label, "inconsistent", pods, "at least one owned pod", hold))
                else:
                    items.append(self.audit_item(pod_label, "consistent", pods, "ownership readable", ""))

        role_ok, rules = self.probe(self.cluster.clusterrole)
        original = [canonical_rule(rule) for rule in ORIGINAL_RULES]
        if not role_ok:
            items.append(self.audit_item("clusterrole", "unknown", None, original, "ClusterRole could not be read. Do not patch it."))
        else:
            live_rules = [canonical_rule(rule) for rule in rules]
            status = "consistent" if live_rules == original else "inconsistent"
            step = "" if status == "consistent" else "Do not patch the ClusterRole until the saved snapshot and the live diff are reviewed by hand."
            items.append(self.audit_item("clusterrole", status, live_rules, original, step))
        snapshot_path = self.state_dir / SNAPSHOT_NAME
        if not snapshot_path.exists():
            items.append(
                self.audit_item(
                    "rbac-snapshot",
                    "unknown",
                    "missing",
                    "saved original rules",
                    "No local ClusterRole snapshot in this state directory. The clusterrole item is the live comparison. A missing file does not say the live rules changed, and the live comparison does not create the snapshot required before an RBAC rollback. Do not patch.",
                )
            )
        else:
            try:
                saved_rules = json.loads(snapshot_path.read_text())
                saved_canonical = [canonical_rule(rule) for rule in saved_rules]
            except (OSError, json.JSONDecodeError, TypeError, AttributeError):
                saved_canonical = None
            if saved_canonical is None or not role_ok:
                items.append(self.audit_item("rbac-snapshot", "unknown", None, "parseable snapshot", "The snapshot is malformed or the live role is unreadable. Do not patch."))
            elif saved_canonical == [canonical_rule(rule) for rule in rules]:
                items.append(self.audit_item("rbac-snapshot", "consistent", saved_canonical, saved_canonical, ""))
            elif sole_added_permission(rules, saved_rules):
                items.append(
                    self.audit_item(
                        "rbac-snapshot",
                        "inconsistent",
                        [canonical_rule(rule) for rule in rules],
                        saved_canonical,
                        "Live role differs by the single added get permission. Leave it until a deliberate rollback is approved.",
                    )
                )
            else:
                items.append(
                    self.audit_item(
                        "rbac-snapshot",
                        "inconsistent",
                        [canonical_rule(rule) for rule in rules],
                        saved_canonical,
                        "Live role differs from the snapshot by more than the added get permission. Do not patch.",
                    )
                )

        felix_ok, env = self.probe(self.cluster.felix_env)
        if not felix_ok:
            items.append(self.audit_item("daemonset-env", "unknown", None, "route sync true and prefix veth or absent", "DaemonSet env could not be read. Do not run set env."))
            prefix = None
        else:
            route_sync = any(item.get("name") == "FELIX_ROUTESYNCDISABLED" and item.get("value") == "true" for item in env)
            prefix_items = [item for item in env if item.get("name") == "FELIX_INTERFACEPREFIX"]
            if not prefix_items:
                prefix = "absent"
            elif prefix_items[0].get("value") == "veth":
                prefix = "veth"
            else:
                prefix = "present-unprinted"
            status = "consistent" if route_sync and prefix == "veth" else "inconsistent"
            step = "" if status == "consistent" else "Do not change the DaemonSet again. Read progress.json only as a hint and trust this live env summary."
            items.append(self.audit_item("daemonset-env", status, {"interface_prefix": prefix, "route_sync_true": route_sync}, {"interface_prefix": "veth", "route_sync_true": True}, step))

        install_ok, install_pods = self.probe(self.cluster.calico_install_state)
        if not install_ok:
            items.append(self.audit_item("install-cni", "unknown", None, "one terminated install-cni", "Installer state could not be read. Do not run set env."))
        elif (
            len(install_pods) == 1
            and install_pods[0].get("name")
            and install_pods[0].get("image") == INSTALL_CNI_IMAGE
            and install_pods[0].get("terminated") is True
            and install_pods[0].get("exit_code") == 0
        ):
            items.append(self.audit_item("install-cni", "consistent", install_pods, "one terminated install-cni", ""))
        else:
            items.append(self.audit_item("install-cni", "inconsistent", install_pods, "one terminated install-cni", "install-cni identity is ambiguous. Do not run set env or move CNI files."))

        roll_ok, rollout = self.probe(self.cluster.rollout_observed)
        if not roll_ok:
            items.append(self.audit_item("daemonset-rollout", "unknown", None, "complete", "Rollout status could not be read. Do not run set env."))
        else:
            items.append(self.audit_item("daemonset-rollout", "consistent", rollout, "complete", ""))

        expected_files = {
            CALICO_ACTIVE: False,
            CALICO_HELD: True,
            CALICO_RECREATED: False,
            KINDNET_ACTIVE: True,
            KINDNET_HELD: False,
        }
        file_ok = True
        actual_files = {}
        for name in expected_files:
            exists_ok, exists = self.probe(lambda name=name: self.cluster.cni_exists(name))
            actual_files[name] = exists if exists_ok else None
            if not exists_ok:
                file_ok = False
        if not file_ok:
            items.append(self.audit_item("cni-files", "unknown", actual_files, expected_files, "CNI filename evidence failed. Do not move files."))
        elif actual_files != expected_files:
            items.append(self.audit_item("cni-files", "inconsistent", actual_files, expected_files, "CNI filenames do not match the pre-migration layout. Do not move files."))
        else:
            items.append(self.audit_item("cni-files", "consistent", actual_files, expected_files, ""))
        for name, present in actual_files.items():
            if present is not True:
                continue
            digest_ok, digest = self.probe(lambda name=name: self.cluster.cni_digest(name))
            if not digest_ok:
                items.append(self.audit_item(f"cni-digest/{name}", "unknown", None, "sha256", "Digest could not be read. Do not print or move the file."))
            else:
                items.append(self.audit_item(f"cni-digest/{name}", "consistent", digest, "sha256 readable", ""))

        runtime_ok, runtime = self.probe(self.cluster.networks)
        if not runtime_ok:
            items.append(self.audit_item("runtime-selection", "unknown", None, "kindnet/ptp", "crictl info failed. Do not treat the runtime as healthy."))
        else:
            status_text, networks = runtime
            observed_runtime = {"networks": networks, "status": status_text}
            kindnet = any(net == "kindnet" and "ptp" in plugins for net, plugins in networks)
            calico = any(net == "k8s-pod-network" and "calico" in plugins for net, plugins in networks)
            if status_text == "OK" and kindnet and not calico:
                items.append(self.audit_item("runtime-selection", "consistent", observed_runtime, "kindnet/ptp", ""))
            else:
                items.append(self.audit_item("runtime-selection", "inconsistent", observed_runtime, "kindnet/ptp", "Runtime selection is not the pre-migration kindnet network. Do not move CNI files."))

        iface_ok, counts = self.probe(self.cluster.workload_ifaces)
        result_ok, results = self.probe(self.cluster.bad_result_files)
        if not iface_ok or not result_ok:
            items.append(self.audit_item("interfaces", "unknown", None, "a parsed interface list", "Interface or result listing failed. Do not treat that as zero."))
        else:
            items.append(
                self.audit_item(
                    "interfaces",
                    "consistent" if counts.get("veth") or counts.get("cali") or results else "inconsistent",
                    {"counts": counts, "results": results},
                    "pre-migration workload interfaces are still present",
                    "No workload interfaces were observed. Do not assume the cutover gate has passed without a separate execution approval.",
                )
            )

        progress_path = self.state_dir / PROGRESS_NAME
        if not progress_path.exists():
            items.append(
                self.audit_item(
                    "progress.json",
                    "unknown",
                    "missing",
                    "optional local progress",
                    "progress.json is absent from this state directory. It is written only after FELIX_INTERFACEPREFIX is removed, so creating it requires that mutation. Its absence does not prove the DaemonSet is unchanged; daemonset-env is the live observation. Do not invent this file.",
                )
            )
        else:
            try:
                progress = json.loads(progress_path.read_text())
                recorded = progress.get("observed", {}).get("interface_prefix")
            except (OSError, json.JSONDecodeError, AttributeError):
                recorded = None
                progress = None
            if progress is None or prefix is None:
                items.append(self.audit_item("progress.json", "unknown", None, "redacted progress matching live env", "progress.json is malformed or the DaemonSet could not be read."))
            elif recorded != prefix:
                items.append(
                    self.audit_item(
                        "progress.json",
                        "inconsistent",
                        {"live_prefix": prefix, "recorded_prefix": recorded},
                        prefix,
                        "progress.json does not match the live DaemonSet. Do not execute. The live env is authoritative.",
                    )
                )
            else:
                items.append(self.audit_item("progress.json", "consistent", {"interface_prefix": recorded}, prefix, ""))

        intents = self.load_intents()
        open_intents = [item for item in intents if item.get("outcome") != "verified"]
        notes = {
            "change_absent": (
                "Live fields match the recorded prior observation. "
                "Forward execution stays blocked. Leave the intent in place."
            ),
            "change_present": (
                "Live fields match the intended record for this mutation. "
                "Forward execution stays blocked. Later postconditions are not established by this match."
            ),
            "mixed_or_ambiguous": (
                "Live fields conflict with the prior record and the intended record. "
                "Forward execution stays blocked."
            ),
            "unreadable": (
                "Required live evidence is missing. This intent is not classified. "
                "Forward execution stays blocked."
            ),
        }
        for intent in open_intents:
            classified = self.classify_open_intent(intent)
            label = str(classified.get("classification"))
            if label not in notes:
                label = "unreadable"
                classified = self._unreadable("classification was not one of the four observations")
            items.append(
                self.audit_item(
                    "intent-classification/" + mutation_slug(str(intent.get("mutation")), str(intent.get("target"))),
                    label,
                    classified,
                    "no open intent",
                    notes[label],
                )
            )
        if any(item.get("malformed") for item in intents):
            items.append(self.audit_item("intents", "unknown", open_intents, "verified outcomes", "An intent file is malformed. Do not execute."))
        elif open_intents:
            items.append(
                self.audit_item(
                    "intents",
                    "inconsistent",
                    open_intents,
                    "no open intent",
                    "An intent has no verified outcome. The classification items are observations. Do not execute.",
                )
            )
        else:
            items.append(self.audit_item("intents", "consistent", intents, "no open intent", ""))
        return {"items": items}

    def audit_recovery(self) -> dict[str, Any]:
        report = redact_obj(self.build_audit_report())
        print(json.dumps(report, indent=2, sort_keys=True))
        bad = [item["name"] for item in report["items"] if item["status"] != "consistent"]
        if bad:
            raise Abort(
                "recovery audit is inconclusive: " + ", ".join(bad),
                "audit-recovery",
            )
        self.note("recovery audit consistent with the pre-migration baseline")
        return report


def _deployment_pod(name: str) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
    replica_name = f"{name}-rs"
    pod = {
        "metadata": {
            "name": f"{name}-pod",
            "ownerReferences": [
                {"apiVersion": APPS_V1, "kind": "ReplicaSet", "name": replica_name, "controller": True}
            ],
        }
    }
    parents = [{"apiVersion": APPS_V1, "kind": "Deployment", "name": name, "controller": True}]
    return pod, replica_name, parents


def _statefulset_pod(name: str) -> dict[str, Any]:
    return {
        "metadata": {
            "name": f"{name}-0",
            "ownerReferences": [{"apiVersion": APPS_V1, "kind": "StatefulSet", "name": name, "controller": True}],
        }
    }


def default_state() -> dict[str, Any]:
    replicas = {}
    pods: dict[str, list[dict[str, Any]]] = {}
    replica_sets: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for kind, namespace, names, count in SCALE_GROUPS:
        pods.setdefault(namespace, [])
        replica_sets.setdefault(namespace, {})
        for name in names:
            replicas[(kind, namespace, name)] = count
            if kind == "sts":
                pods[namespace].append(_statefulset_pod(name))
            else:
                pod, replica_name, parents = _deployment_pod(name)
                pods[namespace].append(pod)
                replica_sets[namespace][replica_name] = parents
    return {
        "context": CONTEXT,
        "replicas": replicas,
        "pods": pods,
        "replica_sets": replica_sets,
        "calico_pods": [
            {
                "name": "calico-node-5mll4",
                "uid": "pod-uid-before",
                "container_id": "containerd://before",
                "finished_at": "2026-10-16T08:00:00+00:00",
                "image": INSTALL_CNI_IMAGE,
                "image_id": "docker.io/calico/cni@sha256:" + ("ab" * 32),
                "terminated": True,
                "exit_code": 0,
            }
        ],
        "sandboxes": [],
        "files": {CALICO_HELD, KINDNET_ACTIVE, "10-calico.conflist.disabled", "calico-kubeconfig"},
        "ifaces": {"veth": 13, "cali": 0},
        "results": ["kindnet-abc-eth0"],
        "networks": ("OK", [("cni-loopback", ["loopback"]), ("kindnet", ["ptp", "portmap"])]),
        "rules": [dict(rule) for rule in ORIGINAL_RULES],
        "felix": [
            {"name": "FELIX_TYPHAK8SSERVICENAME", "valueFrom": {"configMapKeyRef": {"name": "calico-config", "key": "typha_service_name"}}},
            {"name": "NODENAME", "valueFrom": {"fieldRef": {"fieldPath": "spec.nodeName"}}},
            {"name": "CALICO_NETWORKING_BACKEND", "value": "none"},
            {"name": "FELIX_INTERFACEPREFIX", "value": "veth"},
            {"name": "FELIX_ROUTESYNCDISABLED", "value": "true"},
        ],
        "clear_ifaces_on_scale": True,
        "identity": {"kube_system_uid": "fixture-kube-system-uid", "server": "https://fixture.invalid:6443"},
        "reclaim_policy": "test-reclaim",
        "volumes_ok": True,
        "node_ready": True,
        "readyz": True,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stage 2 Calico cutover. Dry-run unless --execute is set.")
    parser.add_argument("--execute", action="store_true", help="perform mutations; default is dry-run")
    parser.add_argument("--audit-recovery", action="store_true", help="read-only diagnosis; never mutates")
    parser.add_argument(
        "--rollback",
        choices=("containment-before-daemonset", "containment-during-rollout"),
        help="disabled; exits before any cluster call. Containment is not recovery and does not mutate",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        help="explicit directory for --execute and --audit-recovery. Dry-run does not create one",
    )
    parser.add_argument("--fixture", action="store_true", help="use an in-memory cluster instead of kubectl")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    if args.rollback:
        print("DECISION_POINT=containment-disabled", file=sys.stderr)
        print("containment execution is disabled; no cluster call and no CNI rename was made", file=sys.stderr)
        print("replicas were not restored", file=sys.stderr)
        return 2
    if args.execute and args.state_dir is None:
        print("DECISION_POINT=preflight", file=sys.stderr)
        print("execution requires an explicit --state-dir; no recovery directory was created", file=sys.stderr)
        print("replicas were not restored", file=sys.stderr)
        return 2
    if args.audit_recovery and args.state_dir is None:
        print("DECISION_POINT=audit-recovery", file=sys.stderr)
        print("--audit-recovery requires an explicit --state-dir and does not create one", file=sys.stderr)
        return 2
    if args.audit_recovery and args.execute:
        print("DECISION_POINT=audit-recovery", file=sys.stderr)
        print("--audit-recovery refuses --execute and --rollback", file=sys.stderr)
        return 2
    cluster: Any = FixtureCluster(default_state()) if args.fixture else LiveCluster()
    procedure = Procedure(cluster, execute=False if args.audit_recovery else args.execute, state_dir=args.state_dir)
    try:
        if args.audit_recovery:
            procedure.audit_recovery()
        else:
            procedure.forward()
    except Abort as exc:
        print(f"DECISION_POINT={exc.decision}", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        print("replicas were not restored", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
