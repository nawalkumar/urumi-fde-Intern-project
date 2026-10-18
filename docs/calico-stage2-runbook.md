# Stage 2 Calico cutover runbook

The procedure is `scripts/calico_stage2.py`. Without `--execute` it does not change Kubernetes objects or CNI files, and it does not create a recovery directory or write replica, intent, snapshot, or progress files. This runbook does not authorize execution.

The script targets `kind-urumi-k8s-dev` only. It does not delete the cluster, PVCs, PVs, or host directories. It does not change `FELIX_ROUTESYNCDISABLED`. It does not scale any replica back up. The CoreDNS canary and every workload restore need a separate approval and are not implemented here.

## Command

Dry-run, after a person has already written `operator-baseline.json` in an explicit directory. The dry-run only reads that file. It does not create `/tmp/urumi-calico-stage2` or any other directory:

```bash
python3 scripts/calico_stage2.py --state-dir /tmp/urumi-calico-stage2
```

`--execute` and `--audit-recovery` refuse to start without `--state-dir`. There is no default state directory.

Live mutations, only after a fresh approval:

```bash
python3 scripts/calico_stage2.py --execute --state-dir /tmp/urumi-calico-stage2
```

Do not point `--state-dir` at a data directory. Only an execution writes the ClusterRole snapshot, the RBAC marker, the recorded replica counts, intent files, and `progress.json` there.

Read-only diagnosis:

```bash
python3 scripts/calico_stage2.py --audit-recovery --state-dir /tmp/urumi-calico-stage2
```

`--audit-recovery` refuses `--execute` and `--rollback`. It does not scale, patch, move, or change the DaemonSet. A missing or unreadable observation is `unknown`, not zero, absent, or healthy. The command exits non-zero unless every item is `consistent` with the pre-migration baseline. `progress.json` is only a hint. When it disagrees with the DaemonSet, the live env is authoritative.

## Forward order

1. Preflight. The current context must be `kind-urumi-k8s-dev`, the node must be Ready, and `/readyz` must be `ok`. The three PVC-to-PV bindings and host directories must match the recorded values. `FELIX_ROUTESYNCDISABLED` must be `true`, and the Typha and node-name `valueFrom` entries must still be present. The `calico-cni-plugin` rules must be exactly get on pods, nodes, and namespaces, plus patch on `pods/status`.
2. Scale to zero, one group at a time, after checking the current replica count: the six `default` Deployments at 1, `ingress-nginx-controller` at 1, `coredns` at 2, `calico-kube-controllers` at 1, `local-path-provisioner` at 1, then `urumi-gateway-db` and `urumi-store-db` at 1. On `--execute` only, each controller is stored in its own file, `replicas-<namespace>-<kind>-<name>.json`, written with `os.replace`. A dry-run prints the counts and does not write them. Typha, `calico-node`, kindnet, and kube-proxy are not scaled. A Deployment pod counts only when its ReplicaSet owner is `apps/v1` and that ReplicaSet is owned by that Deployment. A StatefulSet pod counts only when its `apps/v1` owner is that StatefulSet. `urumi-gateway-db-0` is not a pod of Deployment `urumi-gateway`. A name that resembles a controller but has no proven owner stops the sequence. Each group has 300 seconds to lose its proven pods. Volumes are checked before and after the group. A failure does not scale later groups and does not scale anything back.
3. Require zero `veth` interfaces, zero `cali` interfaces, and no workload CNI result files. A result file whose name is not `loopback-*`, `kindnet-*`, or `k8s-pod-network-*` is unreadable evidence and stops the run. The same gate lists every pod in the cluster. The only pods allowed to remain are host-network pods owned by DaemonSet `calico-node`, `kindnet`, or `kube-proxy`, or by Deployment `calico-typha`, all in `kube-system`. Any other pod is an unexpected workload. `crictl pods` must not show a sandbox outside that allowlist. A sandbox whose interface is `wg0` or any other non-`veth` name is still a sandbox. `ip -o link` and the result-directory listing must exit 0. A node that cannot be reached, a non-zero command, or a line that is not a real interface or filename stops the run. `ip -o link` is counted only for names that start with `veth` or `cali`. An interface with another name, and with no pod, no `crictl` sandbox, and no CNI result file, is not detected. The script does not keep an allowlist of host device names. That gap blocks execution. Closing it needs a separately approved read-only listing of every interface name, with each name tied to a host device or to a sandbox.
4. Save the ClusterRole snapshot. Add get on `clusterinformations.crd.projectcalico.org`. Write the marker only after the live rules differ from that snapshot by exactly that rule.
5. Move `10-calico.conflist.held` to `10-calico.conflist` with `mv -n`. The source must exist and the destination must be absent beforehand. Afterward the source must be absent and the destination must exist. `mv` exiting 0 is not enough. Before the move, the sandbox gate in step 3 must pass. After the move, read the conflist and require network name `k8s-pod-network`, a `calico` plugin, no `ptp` plugin, and a sha256 digest. Poll `crictl info` for up to 60 seconds until `lastCNILoadStatus` is `OK`, the loaded network is `k8s-pod-network`, and its plugins include `calico`. `lastCNILoadStatus=OK` without that document is not proof. A Ready `calico-node` pod is not this check.
6. Move `10-kindnet.conflist` to `10-kindnet.conflist.held-migration` with the same source, destination, and post-move checks, then poll `crictl info` again.
7. Record the current `install-cni` identity from the terminated container status, not from the pod spec: status image `docker.io/calico/cni:v3.30.2`, status `imageID` `docker.io/calico/cni@sha256:` plus 64 hex characters, pod UID, container ID, timezone-aware `finishedAt`, and exit code 0. Record the sha256 of `10-calico.conflist`. Then `kubectl rollout restart daemonset/calico-node` without changing environment variables. `install-cni` is an init container, so it runs only when a new pod starts. The restart annotation starts that pod while `FELIX_INTERFACEPREFIX` stays `veth`. After that rollout, require a new UID, a new container ID, the same `imageID`, a later `finishedAt`, and exit code 0. Re-read the conflist. Its digest must equal the digest recorded before the restart, and `crictl info` must still show `k8s-pod-network` and `calico`. Exit code 0 alone does not prove the bytes. If the digest changes, the check fails and the prefix is not removed. Re-read the DaemonSet and require `FELIX_ROUTESYNCDISABLED=true` and the prefix still present. Any failure stops before `kubectl set env`. This repository does not contain the Calico installer source. `docs/baseline-verification.md` records that `install-cni` rewrote `10-calico.conflist` at 08:20 UTC on 2026-10-16, and it does not record the digest before and after that write. Exact-byte equality is therefore unverified. The equality check stays in place. It is not a claim that a correct install preserves bytes. That comparison needs a separately approved read-only digest immediately before and after one installer run. Execution stays blocked on that point.
8. Remove only `FELIX_INTERFACEPREFIX` after that new installer proof. The script records that mutation in `progress.json` with the observed Felix prefix state, CNI filenames, `calico-node` installer state, and runtime network names. It does not write Secret data or the contents of `calico-kubeconfig`. It then waits up to 180 seconds for the DaemonSet rollout. `FELIX_ROUTESYNCDISABLED` must still be `true`, and both `valueFrom` entries must remain. The installer started by this second rollout must again be a new UID, a new container ID, a later `finishedAt`, the same image, and exit code 0. The Calico conflist must be active, the kindnet conflist must be held, and `10-calico.conflist.recreated` must be absent. Only then does it poll `crictl info` again.

The script then stops. It does not start the canary. The plan below is not authorized and is not implemented in the script.

## Execution package

These commands are for a later approval. They are not approved now. Do not run the `--execute` lines from this review.

Read-only commands, which do not change the cluster:

```bash
python3 scripts/calico_stage2.py --state-dir /tmp/urumi-calico-stage2
python3 scripts/calico_stage2.py --audit-recovery --state-dir /tmp/urumi-calico-stage2
```

The audit exits 2 while `progress.json` is absent. That is expected before the prefix is removed. Do not create the file.

Stage 2 execution is one command, and only after its own approval. There is no second script flag for the canary:

```bash
python3 scripts/calico_stage2.py --execute --state-dir /tmp/urumi-calico-stage2
```

Inside that command, each mutation writes `intents/<mutation>.json` first and sets `outcome` to `verified` only after the postcondition. If the process stops with `outcome` null, the next `--execute` refuses to continue. Do not delete the intent file. Do not scale any replica back. Do not write `progress.json` by hand. Read the live object named in the intent and stop.

| Order | Precondition | Intended change | Postcondition | Abort | If the outcome was not recorded |
| --- | --- | --- | --- | --- | --- |
| 1–6 | Context, node, `/readyz`, three volume bindings, route sync `true`, Typha and node `valueFrom`, original ClusterRole, and the expected replica count for that group | Scale to zero, in order: six `default` Deployments, `ingress-nginx-controller`, `coredns`, `calico-kube-controllers`, `local-path-provisioner`, then both StatefulSets | Owned pods for that group are gone, the live replica count is 0, and the three volumes still match | Count mismatch, unproven pod owner, volume change, or timeout | Leave every replica where it is. Later groups must not be scaled. |
| 7 | Zero workload `veth` and `cali`, and no workload CNI result files, from commands that exited 0 | Add get on `clusterinformations` to `calico-cni-plugin` | Live rules differ from the saved snapshot by only that rule, then the marker is written | Role was not the original two rules, or the diff is anything else | Compare the live rules with the snapshot. Leave the role unchanged. Do not run either containment command. |
| 8 | Snapshot and marker written; held Calico conflist present; active name absent | `mv -n` `10-calico.conflist.held` to `10-calico.conflist` | Source gone, destination present, then `crictl info` shows `k8s-pod-network` and `calico` | `mv` postcondition fails or the 60 second poll fails | Do not move kindnet and do not change the DaemonSet. Filenames and `crictl info` are the evidence. |
| 9 | The Calico poll passed; kindnet conflist present; held-migration name absent | `mv -n` `10-kindnet.conflist` to `10-kindnet.conflist.held-migration` | Source gone, destination present, and the same Calico poll passes again | Same `mv` and poll failures | Do not remove `FELIX_INTERFACEPREFIX`. |
| 10 | Volumes still match. One pre-restart `install-cni` has image `docker.io/calico/cni:v3.30.2`, a pod UID, a container ID, a timezone-aware `finishedAt`, and exit code 0. The post-move CNI filenames and Calico `crictl info` already match. That record is only the watermark | `kubectl rollout restart` on `daemonset/calico-node`. No Felix environment variable changes | After rollout, UID and container ID differ, `finishedAt` is later, the image and exit code 0 match, `FELIX_ROUTESYNCDISABLED` is still `true`, and `FELIX_INTERFACEPREFIX` is still `veth` | Missing identity, same UID, same container ID, stale `finishedAt`, wrong image, missing or non-zero exit, rollout failure, or route sync no longer `true` | Do not run `set env`. Do not remove the prefix. |
| 11 | The step 10 postcondition passed, including the same conflist digest and the same terminated `imageID` | Remove only `FELIX_INTERFACEPREFIX` | After the second rollout, the installer is newer than the step 10 pod, route sync is still `true`, the conflist digest is unchanged, CNI filenames still match, and `crictl info` still shows Calico | The prefix is already absent. Do not run `set env` again. Do not move a CNI file. An old exit code 0 is not the new attempt | This is not an automated recovery. See the second-rollout section. Do not restore replicas. |

The command then exits. It does not start CoreDNS. It does not restore replicas.

Do not run either containment command. The containment execution is disabled. `--rollback` returns before any cluster call or CNI rename. The two renames are not one transaction. The executable path no longer attempts them. Kindnet containment is the known-broken network, not recovery. The phrase for that path is kindnet containment.

If filenames were already changed by an earlier attempt, inspect them with read-only commands. Do not run a reverse `mv` from this script. It will not do that.

Approval boundaries, each separate from the others:

1. Stage 2 `--execute`, the command above.
2. CoreDNS from zero replicas to one, after the canary evidence below. This scale is not in the script.
3. Each later workload group, one group at a time. The script has no scale-up command.
4. The connectivity matrix, after the pods those tests need are up.
5. Any change to `172.19.0.2/32` or another NetworkPolicy field, only after a packet capture shows the kubelet probe source.

Before the canary, require the postcondition of step 11 plus zero workload `veth`, zero `cali`, and no workload result files. After the one CoreDNS pod is Running, require the same `crictl info`, the same CNI filenames, route sync still `true`, the prefix still absent, one installer still exited 0, a new `cali` interface, workload `veth` still zero, a `k8s-pod-network-*` result for that sandbox, and the same three PVC, PV, and hostPath identities. CoreDNS to `10.96.0.1:443` is a dataplane check. CoreDNS is not selected by a NetworkPolicy, so that connection is not an enforced allow.

For the matrix, an unselected pod must open TCP 5432 to the gateway-db pod IP and to `10.96.178.93` before any deny is believed. Gateway must succeed to both while support MCP fails to both, and the gateway call must still succeed in that window. Repeat the pair for WordPress versus support MCP on TCP 3306 to the store-db pod IP and `10.96.52.15`. Gateway DNS and the same lookup from the unselected pod must both succeed. If an allowed call and a denied call both fail, the dataplane has failed. Do not record a policy deny.

The probe capture, after the canary path works and only with its own approval, is a read of the host side of that pod's `cali` interface for the kubelet packet to the probe port. Record the source address. Do not change `172.19.0.2/32` from that address until a later approval. The node InternalIP `172.19.0.3` is not itself the capture.

## Canary plan

This plan starts only after a reviewed Stage 2 execution has stopped on its own, or `--audit-recovery` plus live reads show the same post-cutover state. Do not create `progress.json` to satisfy the audit. Before the prefix is removed, a missing `progress.json` is expected. After the prefix is removed, the pre-migration audit will correctly call the DaemonSet, CNI files, and runtime `inconsistent` with the old baseline. That exit code is not a canary failure and it is not permission to continue. The canary gate is the live checklist below.

Do not start a workload until every line is true:

- Context is `kind-urumi-k8s-dev`. The three PVC, PV, and hostPath bindings still match. No open intent.
- `crictl info` has `lastCNILoadStatus` `OK`, the loaded network is `k8s-pod-network`, its plugins include `calico`, and `kindnet` is absent. A Ready `calico-node` pod does not prove this.
- `10-calico.conflist` is present, `10-kindnet.conflist` is absent, `10-kindnet.conflist.held-migration` is present, and `10-calico.conflist.recreated` is absent.
- `FELIX_INTERFACEPREFIX` is absent, so Felix uses its compiled `cali` default. `FELIX_ROUTESYNCDISABLED` is still `true`. The Typha and node-name `valueFrom` entries are still present.
- Exactly one `calico-node` pod has `install-cni` `docker.io/calico/cni:v3.30.2` terminated with exit code 0.
- A successful `ip -o link` shows zero workload `veth` and zero `cali` interfaces. The result directory has no `kindnet-*` or `k8s-pod-network-*` files. Empty output from a failed command is not this proof.
- Every application replica is still the scaled-down value recorded for that run. Nothing in this plan scales a replica up except the single canary below, and only after a separate approval.

Containerd selection and Felix readiness are different from attachment and from enforcement:

- Selection is the `crictl info` check above. It is true before any new pod exists.
- Attachment is a new sandbox whose host interface is `cali` plus a hash, with a `k8s-pod-network-<sandbox>-eth0` result file. A `veth` interface means the sandbox did not attach through this Calico setup.
- Enforcement is an allow and a deny in the same window. A `cali` interface, a pod IP, or a passing CoreDNS lookup does not prove it. If the allowed call and the denied call both fail, the network is down. Do not record that as a policy deny.

The minimum canary is one CoreDNS pod, from zero to one replica. Leave the second CoreDNS replica and every application, database, ingress, and local-path replica at zero. Required evidence after it is Running: the pod's owner is the CoreDNS ReplicaSet whose parent is Deployment `coredns`; the new host interface is `cali*`; workload `veth` count stays zero; `crictl info` still shows Calico and not kindnet; the volume bindings are unchanged. CoreDNS is not selected by a NetworkPolicy. A connection from that pod to `10.96.0.1:443` shows that the new path can reach the API. It is not an enforced allow rule.

Stop the canary, without scaling anything else and without moving CNI files, if any `veth` appears, `crictl` returns to kindnet, the pod is up without a `cali` interface, more than one CoreDNS pod appears, route sync is no longer `true`, or a volume binding changes. Human approval is required at four points: before `--execute`, before changing CoreDNS from zero to one, before any replica after that canary, and before the connectivity matrix or any probe-CIDR edit. A failed canary does not make kindnet containment safe. Containment returns the known-broken network and does not restore data or replicas. Leave the observed replica count in place until a separate decision.

The connectivity matrix waits until that later approval starts the needed pods. Run an unselected pod to the gateway-db pod IP and to `10.96.178.93` on TCP 5432 first. That call must succeed before any deny is believed. Then gateway must succeed to both destinations while support MCP fails to both, and the gateway call must still succeed in that same window. Repeat the pair for WordPress versus support MCP on TCP 3306 to the store-db pod IP and `10.96.52.15`. Gateway DNS through CoreDNS must succeed, and the same lookup from the unselected pod must succeed; if both fail, it is not a policy result. CoreDNS to the API remains the unselected dataplane check, not an allow rule.

Do not change `172.19.0.2/32` until a capture on the host side of one running pod's `cali` interface shows the source address of a kubelet probe to that pod's probe port. The live node InternalIP is `172.19.0.3`, and `values-dev.yaml` names that address, but the probe source has not been captured. A capture is allowed only after the canary path works. Editing the policy or the chart is a separate approval. Do not edit either while the only traffic path is the Felix `veth` drop, because a probe failure then does not identify the source.

## Second-rollout failure

If `FELIX_INTERFACEPREFIX` has already been removed and the second rollout, installer, conflist digest, or `crictl` check fails, the script stops. Automated rollback is disabled. This is operator-led recovery, and it is not implemented. Execution stays blocked until that recovery is reviewed separately.

The state that may remain is:

- `10-calico.conflist` active and `10-kindnet.conflist` held as `10-kindnet.conflist.held-migration`.
- `FELIX_INTERFACEPREFIX` absent, so Felix is on its compiled `cali` default. `FELIX_ROUTESYNCDISABLED` is whatever the live DaemonSet still shows. The script does not change it again.
- Workloads already scaled to zero. They must stay at zero. Do not scale any workload, including CoreDNS, back up until an operator has reviewed the live CNI filenames and digest, the Felix environment (`FELIX_INTERFACEPREFIX` and `FELIX_ROUTESYNCDISABLED`), `crictl info`, and the three PVC, PV, and hostPath bindings. Starting a pod before that review would attach it to this unfinished network. That review is not `--rollback` and is not implemented in this script.
- The three PVC, PV, and hostPath bindings are read again on this abort. A mismatch is reported and is not repaired. Nothing in this path deletes or recreates a PVC, PV, or host directory.

Before any manual recovery, record read-only evidence: the DaemonSet env, the five CNI filenames, the conflist digest, `crictl info`, `install-cni` status image and `imageID`, pod UID, container ID, `finishedAt`, exit code, every `ip -o link` name, the three volume bindings, and whether any non-survivor pod or sandbox remains. Do not print Secrets or `calico-kubeconfig`.

Until that operator review of the live CNI, Felix, runtime, and persistent-volume state, do not run `kubectl set env`, do not move a CNI file, do not scale any replica up, do not delete the cluster, and do not delete or recreate persistent storage. Kindnet containment is not that review. `--rollback` stays disabled.

## Containment rollback

Containment execution is disabled. `python3 scripts/calico_stage2.py --rollback ...` exits before it constructs a cluster client. It does not scale, patch, set env, or rename files. Returning to kindnet would be the known-broken network, not recovery. Do not run either containment command.

If a previous attempt already renamed a conflist, read the five filenames and `crictl info`. Do not invent a reverse `mv`. Do not delete a file. Leave replicas where they are.

Before `FELIX_INTERFACEPREFIX` is removed, the forward path records an install-cni watermark and then restarts the DaemonSet without changing Felix environment. `install-cni` runs only as an init container of a new pod, so the proof has to be a pod started by that restart, not by the prefix change. Missing, unparseable, or contradictory identity aborts before the restart. After the restart rollout, a new UID, a new container ID, a later timezone-aware `finishedAt`, image `docker.io/calico/cni:v3.30.2`, and exit code 0 are required, and `FELIX_ROUTESYNCDISABLED=true` is read again. The prefix is still `veth` at that point. Any of those checks failing aborts before `kubectl set env`. An old exit code 0 is not the new attempt. The script does not treat the pre-restart exit code as proof that the new install succeeded.

A person must write `operator-baseline.json` in the explicit state directory before any migration. The keys are `server` (the context's `https://` API endpoint), `kube_system_uid` (the `kube-system` namespace UID), and `reclaim_policy` (the human-confirmed PV reclaim policy). The repository does not contain those three values, and the script does not invent them. A missing or changed value aborts before the first scale. Every later cluster or node operation compares the live endpoint and UID again, then resolves the node from that context. Docker exec uses the single Ready node name returned by the API only when `docker inspect` prints that same name. The historical node name is not sufficient. The stored current context is read with `kubectl config current-context` and no `--context` flag. That output must be exactly `kind-urumi-k8s-dev`. Empty, failed, or different output aborts before any mutation. The script does not change the current context. Every other kubectl command must include exactly `--context kind-urumi-k8s-dev`. `--rollback` is disabled and exits before a cluster client is constructed. Passing `--execute` does not enable it.

Volume checks still compare PVC name, PV name, hostPath, Bound phase, and directory presence. They also require the reclaim policy in that file. They do not delete or recreate PVCs, PVs, or host directories. The prefix-removal abort reads those three bindings again before it returns. A failed read does not start a repair. Storage safety on that path is the read, not a rollback.

The ClusterRole snapshot is written with `os.replace` and mode `0600`. An existing snapshot is left untouched unless its bytes and mode are already exactly the rules about to be saved. A write failure aborts before the RBAC patch.

## Failure decisions

| Failure | State to report | Manual intervention |
| --- | --- | --- |
| Before RBAC | Which scale group stopped, each per-controller replica file, and the volume names | Do not scale anything up and do not start a CNI move. Scaling back is a separate approval. |
| RBAC patch saved, marker missing | The snapshot file and the live rules | Leave the role unchanged. Do not run either containment command to remove the rule. A later reviewed edit may remove the rule only when the snapshot exists and the sole added rule is that get permission. If the snapshot is missing or any other rule differs, edit it by hand only after that review. |
| After the Calico move | `crictl info` network names and whether `10-calico.conflist` exists | Do not treat a Ready Felix pod as selection and do not continue to the kindnet move until the poll passes. |
| After the kindnet move | The same selection evidence and whether the kindnet file is held | Do not start the DaemonSet change. |
| After `FELIX_INTERFACEPREFIX` is removed | `progress.json`: last completed mutation, route sync, whether the prefix is absent, CNI filename presence, `calico-node` count, `install-cni` image and exit code, and `crictl` network names | This is the decision tree below. Do not run another `set env`, do not move a CNI file, and do not restore replicas. |

### After the prefix is removed

Read the live DaemonSet before reading `progress.json`. The sentence below is true only when that file exists and its prefix matches the live env. A missing file does not name a last completed mutation.

When `progress.json` records `FELIX_INTERFACEPREFIX-removed` and the live prefix agrees, do not repeat the DaemonSet change while any of these is unresolved.

- Rollout times out or `kubectl rollout status` fails. The env change may already be saved. Leave the DaemonSet alone and do not move CNI files.
- More than one `calico-node` pod is present, or the pod name is missing. Installer completion cannot be assigned to one pod. Leave both the DaemonSet and the CNI directory unchanged.
- The single `install-cni` container is not `docker.io/calico/cni:v3.30.2`, has not terminated, or exited non-zero. The installer may still be writing `10-calico.conflist`. Do not move that file and do not change `CNI_CONF_NAME`.
- `10-calico.conflist` is absent, `10-kindnet.conflist` is still active, both kindnet names exist, or `10-calico.conflist.recreated` exists. The file state is ambiguous. Do not run `mv`.
- `crictl info` does not show `k8s-pod-network` with the `calico` plugin, or the command fails. A Ready Felix pod is not selection. Do not scale any application back up.

Returning to kindnet is containment of the known-broken network. It is not successful recovery. If a rollback postcondition cannot be proven, stop and report that state. Do not invent another filename, scale a controller, or delete a volume. None of these states restores replicas.

## Interrupted mutations

Before each live scale, ClusterRole patch, CNI move, and `kubectl set env`, the script writes `intents/<mutation>.json` with the mutation name, target, prior redacted observation, intended state, and timestamp. `outcome` is null until the postcondition is checked. It then rewrites that file with `outcome` `verified` and a redacted after-observation. `progress.json` is updated only after that for the DaemonSet step.

If the process stops after the apiserver or node accepts the change and before the outcome rewrite, the intent stays open. The next `--execute` or containment command stops immediately and tells you to run `--audit-recovery`. The audit classifies that open intent from live objects as `change_absent`, `change_present`, `mixed_or_ambiguous`, or `unreadable`. Those words are observations. `change_absent` means the live fields match the recorded prior observation. `change_present` means they match the intended record. Neither one proves that the mutation never ran, and neither one proves that later postconditions passed. `unreadable` means the audit refused to choose one of the other three because the required live evidence is missing. Every classification blocks the next forward run. The audit does not delete the intent, create `progress.json`, or repair the cluster.

This does not close every crash window. The intent file is atomic only on the local filesystem (`os.replace`). A crash before that replace leaves no intent and no mutation. A crash after the cluster accepts the mutation and before the outcome file is replaced is detectable only when the intent file survived. A crash that loses the state directory, or a mutation made outside this script, is invisible to the intent log. Those cases depend on live Kubernetes and node observations: replica counts, pod owners, ClusterRole rules, DaemonSet env, CNI filenames and sha256 digests, `crictl info`, and interface listings. `progress.json` is not one of those observations.

Replica files record the original count. They are not a restore trigger. If live replicas differ from the baseline or from a saved file, the audit names that controller and says to leave it until networking is verified. Restoring replicas is a separate deliberate action.

## Why the audit exits 2

`audit_recovery` exits 2 when any item is `unknown` or `inconsistent`. That is fail-closed for the report as a whole. It is not, by itself, a finding that a live precondition failed.

On a state directory where Stage 2 has never written its journal, two items stay `unknown` even when every live read matches the pre-migration baseline:

- `rbac-snapshot` is the local file `calico-cni-plugin-original.json`. The audit does not create it. The separate `clusterrole` item compares the live rules with the rules compiled into the script. A missing file does not mean the live rules changed. A matching `clusterrole` item does not replace the snapshot that an RBAC rollback requires. A snapshot may be saved without changing the cluster by writing only those rules, with mode `0600`, after a read-only `kubectl get` shows they are exactly the script's original two rules. Do not write it when the comparison is ambiguous. `progress.json` is a different file and is still absent until the prefix mutation.
- `progress.json` is written only after `FELIX_INTERFACEPREFIX` is removed. Creating it requires that DaemonSet mutation, so it must not be invented to obtain exit code 0. Before any execution its absence is expected. The `daemonset-env` item is the live observation of route sync and the interface prefix. Absence of this file in one directory does not prove that no other directory recorded a mutation.

Live observations can establish the current context, replica counts, pod ownership, ClusterRole rules, DaemonSet env, installer state, CNI filenames, digests, runtime selection, interface counts, and the three PVC/PV/hostPath bindings. They cannot establish that this state directory contains a rollback snapshot, that `progress.json` recorded a prefix change, or that NetworkPolicy allow and deny have been demonstrated. Interface status `consistent` means workload `veth` interfaces are still present. That matches the pre-migration network. It is the opposite of the cutover gate, which requires zero `veth` and zero `cali` interfaces.

## Readiness checklist

Stage 2 stays NO-GO until every line below is true. The audit's exit code is not a substitute for this list.

- Context is exactly `kind-urumi-k8s-dev`, the node is Ready, and `/readyz` is `ok`.
- Every scale target still has its baseline replica count, owned by the expected controller. `urumi-gateway-db-0` is not a Deployment `urumi-gateway` pod.
- The live `calico-cni-plugin` rules are the original two rules, and a local snapshot of those rules exists before any patch. A missing snapshot blocks rollback.
- `FELIX_ROUTESYNCDISABLED` is `true`, `FELIX_INTERFACEPREFIX` is `veth`, and exactly one `calico-node` pod has `install-cni` `docker.io/calico/cni:v3.30.2` terminated with exit code 0.
- `10-calico.conflist.held` and `10-kindnet.conflist` are the selected files. There is no active `10-calico.conflist` and no `10-calico.conflist.recreated`. `crictl info` shows kindnet and ptp. Digests are recorded without printing `calico-kubeconfig`.
- Workload `veth` interfaces are still present. The cutover itself must later prove they are gone. The audit does not make that claim.
- The three PVC, PV, and hostPath bindings match the recorded identities. The script refuses to delete them. It has not been executed.
- NetworkPolicy enforcement is not checked by this audit. Allow and deny tests remain a separate approval after the dataplane is Calico-managed. Kindnet with the Felix unknown-interface drop is not successful enforcement.
- No open intent. `progress.json` is not required before the first mutation and must not be fabricated.

## Probe CIDR

The live policies allow `172.19.0.2/32`. `values-dev.yaml` says `172.19.0.3/32`. Helm release `urumi` is still revision 1, and its user values do not set `probeCIDRs`, so the chart default is an empty list. The gateway-db policy is generation 2, so its spec changed once after Helm created it. The script does not read or edit this CIDR.

Kubelet on the node sends liveness and readiness probes to the pod IP and the container port. The gateway liveness probe is `tcpSocket` on the named port `http`; the readiness probe is `httpGet` `/health` on that same port. That path is not a Service, so kube-proxy does not DNAT it. An `ipBlock` rule matches the source address of the packet as it reaches the pod. The chart comment treats that source as the node InternalIP. The current node InternalIP is `172.19.0.3`. No packet capture has shown whether the host SNATs that probe. The CIDR is for kubelet probes after a policy-enforcing CNI is active. It is not one of the six connectivity cases and it is not a step in this script. Changing it is a separate edit. Do it only with a new approval, and do not do it while Felix is dropping `veth` traffic, because a probe failure then would not identify the CIDR.

## Cutover points

Workloads are scaled to zero only inside `--execute`, one recorded group at a time, after the replica file is written. A failed group leaves an open intent and does not scale anything back. Manual recovery is to stop, run `--audit-recovery`, and leave replicas at zero until a later approval. Do not scale up from the exception handler.

CNI selection changes only at the two `mv -n` steps. After the Calico conflist is restored, `crictl info` must show `k8s-pod-network` and `calico` before kindnet is held. If that poll fails, the kindnet file stays active and the DaemonSet is not changed. Manual recovery is to inspect the CNI directory and `crictl info`. Do not move files by hand unless the source and destination are unambiguous.

Calico `cali` interfaces are created only after the prefix override is removed and a later workload is started. This script removes the prefix and then stops. It does not start the canary. `progress.json` is written after that env change is verified. If the process dies before the outcome file, the intent stays open and the next execute refuses to continue. Read the live DaemonSet. Do not run `set env` again while the pod count or `install-cni` exit is ambiguous.

NetworkPolicy enforcement can begin only when Felix is matching `cali` interfaces and containerd has selected the Calico CNI. Returning to kindnet does not restore enforcement. The kindnet containment path is the known-broken network. It is not a successful rollback.

## State directory

A missing state directory makes the snapshot, saved replica counts, and `progress.json` unknown. The audit does not create the directory. A malformed snapshot stays unknown and RBAC rollback does not patch. A snapshot copied from another run is applied only when the live rules differ from it by exactly the one added `clusterinformations` get. Any other difference leaves the ClusterRole unchanged. A `progress.json` whose prefix does not match the live DaemonSet is inconsistent. The live env is authoritative.

## What is proven, observed, or assumed

Proven by mocked tests: a crash after a successful scale, RBAC add, either CNI move, or prefix removal leaves an open intent and blocks the next execute; a crash after the intent is written and before the API call does the same, and the audit reports `change_absent` or `change_present` from live objects without calling either result a success; missing live evidence stays `unreadable`; stale or missing `progress.json` does not override the live prefix; a missing snapshot and a missing `progress.json` stay `unknown` and make the audit exit 2 even when the live ClusterRole and DaemonSet match the baseline; observation and parse failures are `unknown`; a partial scale is reported and not restored; a kindnet-name collision aborts before either containment rename; a failed second rename does not reverse the first or restore replicas; `--audit-recovery` does not call scale, patch, set-env, move, or rollout mutation, including when `execute` is true and those methods raise if reached; kubeconfig and secret strings are redacted.

These mocked tests do not authorize Stage 2. The verdict remains NO-GO until a separate review of this script, this runbook, and live recovery evidence.

Observed on the live cluster only by the read-only commands in the latest report. Those commands are not proof that a later `--execute` is safe.

Assumed, not proven: that the local state directory survives a crash, and that a future execution will leave the three data directories unchanged. The script checks the PVC, PV, and hostPath bindings, and it refuses to delete them. It has not been executed against the cluster.
