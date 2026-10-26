# Multi-store acceptance ledger

This is the acceptance record for the bonus store work. It does not replace the historical notes in `docs/verification/repository-findings.md`. A unit pass is not a disposable-cluster pass. No row below is `VERIFIED — DISPOSABLE KUBERNETES` or `VERIFIED — END-TO-END`.

The evening matrix in this file is unchanged. A later readiness pass on 2026-10-24 is in "Readiness pass". Its decision is `NOT READY`. A package pass the same night is in "Disposable package pass". It uses the labels `PASS — END-TO-END`, `PASS — LIVE COMPONENT`, `PASS — UNIT/STATIC`, `FAIL`, `BLOCKED`, and `NOT TESTED`. Those labels do not upgrade any evening row to end-to-end. End-to-end completion remains 0. A later manual-preparation pass is in "Manual demonstration preparation". The package is ready for review and is not ready for the live dashboard demonstration.

**Current status (2026-10-26).** The verdict and counts directly below are the 2026-10-24 evening record, before any cluster work. They are kept as history. The current status is the test-cluster matrix in "Test-cluster live pass", as corrected in "Reconciliation on 2026-10-26" at the end of this file: 48 `PROVEN — TEST CLUSTER`, 23 `PARTIAL`, 14 `UNTESTED` of 85. Overall verdict: `PARTIAL`. MS-06g is `PARTIAL`.

## Verdict (2026-10-24 evening, historical)

`PARTIAL`

The in-process and Helm checks that were run passed. The disposable cluster was not created, so every statement that requires a real namespace, packet, admission decision, or dashboard session on Kubernetes is `BLOCKED`. Zero sub-criteria are fully passed at the level this task required for a `PASS`.

| Bucket | Count |
| --- | --- |
| Parent criteria (MS-01–MS-08) | 8, all `BLOCKED` |
| Sub-criteria | 85 |
| Fully passed at the required live level | 0 |
| Failed | 0 |
| `BLOCKED` | 50 |
| Not tested | 0 |
| `VERIFIED — UNIT` only | 35 |
| `VERIFIED — DISPOSABLE KUBERNETES` | 0 |
| `VERIFIED — END-TO-END` | 0 |

## Initial state, recorded before edits

| Item | Value |
| --- | --- |
| Date | 2026-10-24 |
| Branch | `submission` |
| HEAD | `b4b813c568ca70b45d28944a44de4e8e770f874d` |
| Tracking | `submission...origin/submission [ahead 1]` |
| Staged | nothing |
| Pre-existing tracked modifications | 19 files, `git diff --stat` 991 insertions and 41 deletions |
| Pre-existing untracked | `PROJECT_GAP_TRACKER.md`, `docs/verification/repository-findings.md`, `scripts/image_provenance.py`, `tests/test_image_provenance.py`, `tests/test_utility_mcp.py` |

Those pre-existing files were not reverted, staged, or committed. Later edits in this pass are mixed into some of the same paths (`chat.py`, `cluster.py`, `service.py`, `test_chat.py`, `test_store_kube.py`, `test_store_manifests.py`, `docs/verification/repository-findings.md`, `scripts/disposable-store-acceptance.sh`).

Protected contexts at the start and at the end: current context `kind-urumi-k8s-dev`. Clusters `urumi-k8s-dev` and `urumi-k8s-test`. No Helm release was upgraded. No namespace, PVC, or Secret on those clusters was read for mutation or deleted.

## Environment blocker

Recorded before any cluster create:

| Item | Observation |
| --- | --- |
| Host | WSL2, Docker 29.6.1, 16 CPUs, 7.6GiB RAM |
| Available memory | about 2.6GiB (`free -h`) |
| Swap | 2.0GiB, about 310MiB used |
| Disk | about 895GiB free |
| `urumi-k8s-dev-control-plane` | about 2.107GiB |
| `urumi-k8s-test-control-plane` | about 1.324GiB |
| Tooling | `kind` v0.33.0, `kubectl`, `helm` v4.3.0 |

A third kind node with Calico, ingress, the gateway stack, and two WordPress/MariaDB stores would need memory comparable to one of the existing control planes plus the store requests (WordPress 256Mi, MariaDB 256Mi, MCP 64Mi, and an init Job, twice). That does not fit in the free RAM without pressuring the protected nodes. No disposable cluster was created. No command was sent to the protected contexts.

## Source identity and images

| Item | Value |
| --- | --- |
| Revision tested | dirty working tree on `b4b813c` |
| Images built this pass | none |
| Chart | `helm/urumi`, default `storeProvisioner.enabled=false` and `storeProvisioner.admit=false` |
| Disposable values file | `helm/urumi/values-disposable.yaml` (not installed) |

The running protected cluster images were not retagged and were not used as evidence for this tree.

## Implementation changes in this pass

| Defect | Change | Test |
| --- | --- | --- |
| Reconcile issued a full Job PUT. Kubernetes rejects changes to `spec.template` after it adds a controller selector, which burned provision attempts. | `HttpClusterClient` leaves a succeeded Job alone, leaves a matching running Job alone, and deletes plus recreates a failed or drifted Job. RBAC gains `delete` on `wordpress-init` only. | `tests/test_store_kube.py::test_succeeded_job_is_not_replaced_and_failed_job_is_recreated`, `test_running_job_with_the_same_template_is_left_alone` |
| Namespace deletion does not remove a Retain volume. | After the namespace is gone, the client deletes Released, Failed, or Available volumes whose `claimRef.namespace` is that store. Other namespaces are skipped. A Bound volume keeps the row `deleting`. Admission allows only that delete. | `test_volume_cleanup_deletes_only_released_volumes_for_that_store`, `test_store_stays_deleting_until_volume_cleanup_finishes` |
| Chat instructions always named `woocommerce__list_orders`. | The prompt lists the granted `list_orders` tools and tells the model not to rename a store tool. | `tests/test_chat.py::test_one_request_calls_both_store_list_orders_tools` |
| Init always tried `wp plugin install`, which needs public egress. | Download runs only when `WORDPRESS_PLUGIN_DOWNLOAD=true` (`STORE_ALLOW_PLUGIN_EGRESS`). Otherwise the plugin must already be in the image. | `tests/test_store_manifests.py::test_resources_are_named_and_labeled_for_one_store` |
| MariaDB TCP probes come from the node and would be denied by default-deny. | Database probes are in-container `exec` checks. Probe CIDRs stay off the database policy. | same manifest test; `test_network_policy_does_not_select_the_other_store` still requires the probe CIDR to be absent from the database policy |
| `admit` could be true while `enabled` was false, which renders no policy. | Helm fails that combination. | `test_provisioner_rbac_is_not_cluster_admin` |
| Provisioner had no storage class, ingress class, or probe CIDR settings. | `STORE_STORAGE_CLASS`, `STORE_INGRESS_CLASS`, and `STORE_PROBE_CIDRS` are rendered when both flags are true. Defaults stay empty or `nginx`. | Helm render in the same test |
| `scripts/disposable-store-acceptance.sh` exited 0 after printing. | `--unit` runs Helm and pytest. `--execute` refuses protected names and, only after that, reads with `kubectl --context`. It does not create stores. | Executed this pass. See commands below. |

Files touched for those fixes:

- `apps/gateway/app/chat.py`
- `apps/gateway/app/stores/cluster.py`
- `apps/gateway/app/stores/service.py`
- `apps/gateway/app/stores/manifests.py`
- `apps/gateway/app/stores/init_store.sh`
- `helm/urumi/templates/_helpers.tpl`
- `helm/urumi/templates/store-provisioner-admission.yaml`
- `helm/urumi/templates/store-provisioner-deployment.yaml`
- `helm/urumi/templates/store-provisioner-rbac.yaml`
- `helm/urumi/values.yaml`
- `helm/urumi/values-disposable.yaml` (new, not installed)
- `deploy/wordpress/Dockerfile` (new, not built)
- `docs/multi-store.md`
- `scripts/disposable-store-acceptance.sh`
- `tests/test_chat.py`
- `tests/test_store_kube.py`
- `tests/test_store_manifests.py`

Pre-existing dirty files that this pass did not need to edit were left as they were, including `apps/gateway/Dockerfile`, `admin_api.py`, `chat_provider.py`, `discovery.py`, `dispatch.py`, `routing.py`, `utility_mcp`, `dashboard/Dockerfile`, `docs/threat-model.md`, `tests/store_cluster.py`, `test_chat_provider.py`, `test_discovery_policy.py`, `test_store_routing.py`, and `test_stores.py`.

## Commands and results

All of these were executed on 2026-10-24 against the dirty tree. None selected a protected context for a write.

| Command | Exit | Result |
| --- | --- | --- |
| `uv run pytest -q --tb=line --ignore=tests/test_postgres_permissions.py --ignore=tests/test_reset_admin_password_postgres.py` | 0 | 304 passed, 49.14s. Section F of the findings file had 299 passed before the additional tests. |
| `npm test` in `dashboard/` | 0 | 6 files, 34 tests, Vitest 3.2.7, about 13.6s. |
| `scripts/disposable-store-acceptance.sh` | 2 | Refused kubectl. |
| `scripts/disposable-store-acceptance.sh --execute` with the context variable unset | 2 | Refused before any cluster call. |
| `scripts/disposable-store-acceptance.sh --unit` | 0 | Helm default lint, render refusal for `enabled` without `admit`, disposable render contains `ValidatingAdmissionPolicy` and does not contain `0.0.0.0/0`, then 67 passed in 20.30s. |
| `helm lint helm/urumi --set database.password=lint-placeholder --set store.enabled=false` | 0 | 0 charts failed. |
| `helm template` of `values-disposable.yaml` with a placeholder database password | 0 | Renders the provisioner and admission policy. |
| `kubectl config current-context` after the above | 0 | `kind-urumi-k8s-dev` |
| `kind get clusters` after the above | 0 | `urumi-k8s-dev` and `urumi-k8s-test` only |

Postgres persistence tests were not run, matching the earlier findings. No disposable Kubernetes command was run.

The scripted chat test calls `store_<id>__list_orders` for two stores in one request, records both audit rows with the owner, the key, the upstream id, a duration, and `success`, and the system prompt does not tell the model to use `woocommerce__list_orders`. That is an in-process provider, not a live model and not a live MCP server.

## Criterion matrix

Shared columns for this pass:

- Date/environment: 2026-10-24, no disposable context. Host memory as above.
- Cleanup: no disposable resources were created. Protected clusters were not modified.
- Verification command for unit rows: the 304-pass pytest command, or `--unit` for the 67-pass store/chat/Helm subset.
- Runtime evidence for every row: none on a cluster.

`Change made` is `No change required` when this pass did not edit that behavior and a test already covered it. Initial status for every row was `NOT STARTED` as an acceptance record. The older findings file had already called the bonus runtime `NOT_OBSERVED`.

### MS-01 Dashboard lifecycle

Parent: `BLOCKED`. The section requires browser or API evidence on the disposable environment.

| ID | Requirement | Source | Change | Tests | Result | Level | Gaps | Final |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| MS-01a | Admin creates a store in the dashboard | `StorePanel.tsx`, `POST /admin/stores` | No change required | `StorePanel.test.tsx`, `test_store_api_scopes_owners_and_hides_secrets` | in the 34 and 304 | Unit | No disposable dashboard session | `BLOCKED` |
| MS-01b | List shows name, status, URL, creation time | `public_store`, `StorePanel.tsx` | No change required | same | in the 34 and 304 | Unit | Not observed from a live API | `BLOCKED` |
| MS-01c | Provisioning to Ready, or Failed with a reason | `service.py` | No change required | `test_stores.py` ready and failure cases | in the 304 | Unit | Fake cluster only | `BLOCKED` |
| MS-01d | Admin deletes in the dashboard | `StorePanel.tsx`, `POST .../delete` | No change required | `StorePanel.test.tsx` | in the 34 | Unit | No disposable session | `BLOCKED` |
| MS-01e | Two stores provision concurrently | `service.py` semaphore | No change required | `test_two_stores_provision_concurrently_with_distinct_names` | in the 304 | Unit | No second namespace on a cluster | `VERIFIED — UNIT` |
| MS-01f | Per-user cap is server-side | `repository.py` | No change required | `test_concurrent_creates_stop_at_the_per_user_limit` | in the 304 | Unit | Not repeated on PostgreSQL this pass | `VERIFIED — UNIT` |
| MS-01g | Duplicate requests do not create two stores | `insert_store` | No change required | `test_duplicate_idempotency_key_does_not_create_another_namespace` | in the 304 | Unit | Not repeated on a cluster | `VERIFIED — UNIT` |
| MS-01h | Errors do not leak credentials | `api.py`, `SecretMaterial.__repr__` | No change required | `test_store_api_scopes_owners_and_hides_secrets`, `test_secrets_keep_values_out_of_labels` | in the 304 | Unit | Live response bodies not captured | `VERIFIED — UNIT` |
| MS-01i | Refresh shows lifecycle without a database edit | `StorePanel.tsx` poll | No change required | `StorePanel.test.tsx` | in the 34 | Unit | No live status transition | `BLOCKED` |

### MS-02 Kubernetes objects

Parent: `BLOCKED`. Manifest shape passed. No store namespace was listed.

| ID | Requirement | Source | Change | Tests | Result | Level | Gaps | Final |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| MS-02a | Namespace and identity labels | `manifests.py` `_namespace` | No change required | `test_resources_are_named_and_labeled_for_one_store` | in the 304 | Unit | No live namespace | `BLOCKED` |
| MS-02b | WordPress workload and Service | `_wordpress_deployment`, `_wordpress_service` | Liveness probe added | same | in the 304 | Unit | No live pod | `BLOCKED` |
| MS-02c | Database workload and Service | `_db_statefulset` | In-container probes; claim retention `whenDeleted: Delete` | same | in the 304 | Unit | No live StatefulSet | `BLOCKED` |
| MS-02d | Independent Secrets | `secret_resources` | No change required | `test_secrets_keep_values_out_of_labels` | in the 304 | Unit | No live Secret | `BLOCKED` |
| MS-02e | WordPress and database PVCs | `wordpress-data`, `volumeClaimTemplates` `data` | Retention policy | same manifest test | in the 304 | Unit | No live PVC | `BLOCKED` |
| MS-02f | Ingress host | `_ingress` | No change required | host assertion in the manifest test | in the 304 | Unit | No live Ingress | `BLOCKED` |
| MS-02g | Store MCP workload and Service | `_mcp_deployment` | Liveness probe added | same | in the 304 | Unit | No live endpoint | `BLOCKED` |
| MS-02h | ResourceQuota and LimitRange | `_quota`, `_limit_range` | No change required | quota assertion | in the 304 | Unit | Not enforced on a cluster | `BLOCKED` |
| MS-02i | NetworkPolicies | `_network_policies` | Database still excludes probe CIDRs | `test_network_policy_does_not_select_the_other_store` | in the 304 | Unit | CNI not exercised | `BLOCKED` |
| MS-02j | Probes, requests, and limits | workload containers | Liveness added; database probe is exec | manifest test | in the 304 | Unit | Probes not observed on pods | `BLOCKED` |
| MS-02k | Store A and B distinct, demo unused | namespace `st-<id>` | No change required | manifest test refuses `store.local` | in the 304 | Unit | Demo PVC identities were not re-read, on purpose | `BLOCKED` |

### MS-03 Registration and authorization

Parent: `BLOCKED`.

| ID | Requirement | Source | Change | Tests | Result | Level | Gaps | Final |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| MS-03a | Ready store registers its MCP server | `service.py` `_register` | No change required | `test_ready_registers_one_upstream_without_changing_grants` | in the 304 | Unit | No live upstream row | `BLOCKED` |
| MS-03b | No manual upstream registration | provisioner, not the admin form | No change required | same | in the 304 | Unit | Provisioner pod not run | `BLOCKED` |
| MS-03c | Tool name `store_<id>__list_orders` | `public_tool` | No change required | routing and chat tests | in the 304 | Unit | Live discovery not listed | `VERIFIED — UNIT` |
| MS-03d | Registry has only live registrations | `_drop_upstream`, drift pin | No change required | `test_missing_namespace_removes_a_stale_registration` | in the 304 | Unit | No live registry | `BLOCKED` |
| MS-03e | Gateway connects to the store MCP | gateway egress `urumi.dev/store=true` | No change required | Helm disposable render contains the egress rule | `--unit` exit 0 | Unit | No connection attempt | `BLOCKED` |
| MS-03f | Discovery uses grants and ownership | `routing.py`, `discovery.py` | No change required | `test_discovery_policy.py`, routing tests | in the 304 | Unit | Not a live tools/list | `VERIFIED — UNIT` |
| MS-03g | Calls recheck authorization | `dispatch.py` | No change required | `test_deleting_store_is_not_called_and_another_store_cannot_be_selected` | in the 304 | Unit | Not a live tools/call | `VERIFIED — UNIT` |
| MS-03h | Create does not grant every user | grants are explicit | No change required | `test_ready_registers_one_upstream_without_changing_grants` | in the 304 | Unit | Not a live grant table | `VERIFIED — UNIT` |
| MS-03i | A member cannot use a store they do not own | `store_block_reason` | No change required | `test_chat_rejects_another_users_store_tool_when_the_key_has_a_grant` | in the 304 | Unit | Not a live member | `VERIFIED — UNIT` |
| MS-03j | Delete removes registration and grants | `request_delete`, `revoke_store_grants` | Volume sweep does not restore them | `test_delete_revokes_only_that_stores_grants` | in the 304 | Unit | Not a live delete | `VERIFIED — UNIT` |
| MS-03k | Old tool name is rejected | state must be Ready | No change required | deleting-store routing test | in the 304 | Unit | No live stale call | `VERIFIED — UNIT` |

### MS-04 Deletion

Parent: `BLOCKED`.

| ID | Requirement | Source | Change | Tests | Result | Level | Gaps | Final |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| MS-04a | Only that store's registration and grants | `revoke_store_grants` | No change required | `test_delete_revokes_only_that_stores_grants` | in the 304 | Unit | Not live | `VERIFIED — UNIT` |
| MS-04b | Namespace removed | `delete_namespace` | No change required | `test_namespace_absence_completes_a_held_deletion` | in the 304 | Unit | No live namespace | `BLOCKED` |
| MS-04c | PVC and PV cleanup is deterministic | `release_store_volumes` | Sweeper added; Retain documented | `test_volume_cleanup_deletes_only_released_volumes_for_that_store` | in the 304 | Unit | No StorageClass was inspected. Scripted HTTP is not an apiserver | `BLOCKED` |
| MS-04d | No orphans after success | sweeper plus namespace delete | Sweeper added | volume tests | in the 304 | Unit | No live listing | `BLOCKED` |
| MS-04e | Repeated delete is safe | `request_delete` | No change required | `test_deletion_is_idempotent_and_drops_registration_immediately` | in the 304 | Unit | Not live | `VERIFIED — UNIT` |
| MS-04f | Interrupted delete can resume | row stays `deleting` until absence | Also waits for the volume sweep | `test_store_row_stays_deleting_after_http_202_until_the_namespace_is_gone`, `test_store_stays_deleting_until_volume_cleanup_finishes` | in the 304 | Unit | No provisioner process was restarted | `VERIFIED — UNIT` |
| MS-04g | Failed delete reports a reason and can retry | `delete_blocked` | No change required | `test_failed_namespace_delete_retries_without_exposing_tools_or_the_other_store` | in the 304 | Unit | Not live | `VERIFIED — UNIT` |
| MS-04h | Store B and the demo stay | namespace guard | Sweeper ignores other claim namespaces | `test_absent_namespace_delete_does_not_touch_the_other_store`, volume test | in the 304 | Unit | Demo PVCs were not re-read | `VERIFIED — UNIT` |

### MS-05 Network isolation

Parent: `BLOCKED`. Policy YAML is not a packet test.

| ID | Requirement | Source | Change | Tests | Result | Level | Gaps | Final |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| MS-05a | Default deny in each store namespace | `default-deny` | No change required | network manifest test | in the 304 | Unit | CNI not installed for this pass | `VERIFIED — UNIT` |
| MS-05b | Selectors for WordPress, database, init, MCP, gateway | `_network_policies`, gateway chart policy | Database probes no longer need a node CIDR | network manifest test; disposable Helm egress to `urumi.dev/store=true` | `--unit` exit 0 | Unit | Selectors not proven with traffic | `VERIFIED — UNIT` |
| MS-05c | Store A cannot reach Store B database or WordPress | per-namespace policies, no namespace selector on those peers | No change required | manifest test shows no cross-namespace peer | in the 304 | Unit | No connection attempt | `BLOCKED` |
| MS-05d | Store B cannot reach Store A | same | No change required | same | in the 304 | Unit | No connection attempt | `BLOCKED` |
| MS-05e | Store MCP cannot reach another store | MCP egress is in-namespace WordPress plus DNS | No change required | manifest test | in the 304 | Unit | No connection attempt | `BLOCKED` |
| MS-05f | Gateway reaches each store MCP | gateway egress when both flags are true | No change required | disposable template contains the rule | `--unit` exit 0 | Unit | No connection attempt | `BLOCKED` |
| MS-05g | Unrelated pods cannot reach private services | default deny plus peer selectors | No change required | manifest test | in the 304 | Unit | No connection attempt | `BLOCKED` |
| MS-05h | Public egress stays off | `allowPluginEgress` default false | Init no longer downloads unless the flag is set. Disposable render has no `0.0.0.0/0` | `--unit` | exit 0 | Unit | Not enforced by a CNI | `VERIFIED — UNIT` |
| MS-05i | Required egress is narrow | DNS to `kube-system`; database and WordPress peers in-namespace | Plugin download is opt-in | manifest test | in the 304 | Unit | DNS path not proven with a packet | `VERIFIED — UNIT` |

### MS-06 Guardrails

Parent: `BLOCKED`.

| ID | Requirement | Source | Change | Tests | Result | Level | Gaps | Final |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| MS-06a | ResourceQuota created and enforced | `_quota` | No change required | manifest hard limits | in the 304 | Unit | Enforcement not attempted | `BLOCKED` |
| MS-06b | LimitRange created and enforced | `_limit_range` | No change required | manifest max CPU | in the 304 | Unit | Enforcement not attempted | `BLOCKED` |
| MS-06c | Cap holds under concurrent requests | advisory lock and in-memory lock | No change required | `test_concurrent_creates_stop_at_the_per_user_limit` | in the 304 | Unit | PostgreSQL suite not run | `VERIFIED — UNIT` |
| MS-06d | Provisioning timeout | `_expired` | No change required | `test_timeout_records_a_safe_reason_and_deletes_the_owned_namespace` | in the 304 | Unit | Not a live deadline | `VERIFIED — UNIT` |
| MS-06e | Timeout and failure text is safe | `ERRORS` | No change required | same timeout test | in the 304 | Unit | Live message not captured | `VERIFIED — UNIT` |
| MS-06f | Unauthorized create and delete denied | `get_owned` | No change required | `test_another_user_cannot_read_or_delete_a_store` | in the 304 | Unit | Not live | `VERIFIED — UNIT` |
| MS-06g | Admission denies cross-namespace writes | `store-provisioner-admission.yaml` | PersistentVolume delete limited to `st-<16 hex>` claim namespaces. `admit` without `enabled` fails Helm | `test_provisioner_rbac_is_not_cluster_admin` | in the 304 and `--unit` | Unit | Policy not installed. No allow and deny request was sent | `BLOCKED` |
| MS-06h | Provisioner RBAC is least privilege | `store-provisioner-rbac.yaml` | Job delete and volume list/delete added, both narrowed | same test rejects cluster-admin and `*` | in the 304 | Unit | A live ServiceAccount was not used | `VERIFIED — UNIT` |
| MS-06i | Provisioner cannot target protected namespaces | `assert_store_namespace` | Volume list cannot delete `default` | `test_client_refuses_protected_namespaces_and_hides_the_token`, volume test | in the 304 | Unit | Not a live forbidden apply | `VERIFIED — UNIT` |
| MS-06j | Secrets do not leak | API, audit, repr | No change required | secret and API tests | in the 304 | Unit | Live logs not collected | `VERIFIED — UNIT` |
| MS-06k | Guessing an id or tool name does not grant access | routing and 404 | No change required | `test_cross_owner_store_tool_is_denied_without_an_upstream_call` | in the 304 | Unit | Not live | `VERIFIED — UNIT` |

### MS-07 Idempotency, concurrency, recovery

Parent: `BLOCKED`. Job behavior was not run against a Kubernetes API server.

| ID | Requirement | Source | Change | Tests | Result | Level | Gaps | Final |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| MS-07a | Same key and name returns the same store | `insert_store` | No change required | duplicate idempotency test | in the 304 | Unit | Not live | `VERIFIED — UNIT` |
| MS-07b | Same key and other name conflicts | `insert_store` | No change required | covered by the store insert tests in `test_stores.py` | in the 304 | Unit | Not live | `VERIFIED — UNIT` |
| MS-07c | Concurrent duplicates do not double-create | locks | No change required | cap and idempotency tests | in the 304 | Unit | No live Job or namespace count | `VERIFIED — UNIT` |
| MS-07d | Different stores can run together | concurrency setting | No change required | `test_two_stores_provision_concurrently_with_distinct_names` | in the 304 | Unit | Not live | `VERIFIED — UNIT` |
| MS-07e | Provisioning survives a restart | `worker` reloads provisioning rows | No change required | `test_restart_continues_the_same_namespace` | in the 304 | Unit | No provisioner pod was killed | `BLOCKED` |
| MS-07f | Deletion survives a restart | deleting rows are resumed | Volume sweep is part of resume | held-deletion tests | in the 304 | Unit | No process was killed | `BLOCKED` |
| MS-07g | Immutable Job fields reconcile | `cluster.py` | Replace instead of PUT | the two Job tests | in the 304 | Unit | Scripted HTTP, not an apiserver | `BLOCKED` |
| MS-07h | Failed attempts reach a terminal state | `max_attempts` | No change required | timeout and attempt paths in `test_stores.py` | in the 304 | Unit | Not live | `VERIFIED — UNIT` |
| MS-07i | Ready requires workloads and registration | `_provision` then `_register` | No change required | `test_ready_registers_one_upstream_without_changing_grants` | in the 304 | Unit | Ready was not observed on pods | `VERIFIED — UNIT` |
| MS-07j | Partial failure is cleaned up or tracked | `_fail` | Also sweeps volumes once the namespace is already gone | `test_deployment_failure_cleans_only_that_store` | in the 304 | Unit | Not live | `VERIFIED — UNIT` |

### MS-08 Two-store chat and delete

Parent: `BLOCKED`. None of the 16 steps were run on a dashboard or a cluster. The in-process analogue of steps 7–10 is `test_one_request_calls_both_store_list_orders_tools` (in the 304). It is not MS-08.

| ID | Requirement | Final |
| --- | --- | --- |
| MS-08-1 | Create Store A and Store B from the dashboard | `BLOCKED` |
| MS-08-2 | Both reach Ready | `BLOCKED` |
| MS-08-3 | Distinct namespaces, resources, URLs, and MCP endpoints | `BLOCKED` |
| MS-08-4 | Automatic registration and discovery | `BLOCKED` |
| MS-08-5 | Explicit grants for both `list_orders` tools | `BLOCKED` |
| MS-08-6 | Identifiable test orders in both stores | `BLOCKED` |
| MS-08-7 | One dashboard chat asks for today's orders across both | `BLOCKED` |
| MS-08-8 | Both store `list_orders` tools are actually called | `BLOCKED` |
| MS-08-9 | The answer distinguishes the stores | `BLOCKED` |
| MS-08-10 | Audit attributes actor, server, tool, time, duration, outcome | `BLOCKED` |
| MS-08-11 | Delete Store A from the dashboard | `BLOCKED` |
| MS-08-12 | Store A tools disappear from discovery | `BLOCKED` |
| MS-08-13 | The old tool name is rejected and the upstream is not called | `BLOCKED` |
| MS-08-14 | Store A namespace, workloads, Secrets, Services, volumes, registry, and grants match the cleanup rules | `BLOCKED` |
| MS-08-15 | Store B stays Ready and its `list_orders` tool still runs | `BLOCKED` |
| MS-08-16 | Audit rows exist for create, chat, delete, and the denied stale call | `BLOCKED` |

Source for the unimplemented live path remains `dashboard/src/admin/StorePanel.tsx`, `dashboard/src/admin/ChatPanel.tsx`, `apps/gateway/app/chat.py`, and `apps/gateway/app/stores/service.py`. No live command was run, so there is no exit code for these rows.

## Cleanup

No disposable cluster, namespace, PVC, PV, host port, or image was created by this pass. `kind get clusters` still lists only `urumi-k8s-dev` and `urumi-k8s-test`. Current context is still `kind-urumi-k8s-dev`. Nothing was staged, committed, or pushed.

## What remains

1. A machine with enough free RAM, or a window where the protected clusters can stay up while a memory-capped disposable node is proven not to disturb them.
2. Create a kind cluster whose context name contains `disposable`, install a CNI that enforces NetworkPolicy, and prove a denied connection before creating a store.
3. Build `deploy/wordpress/Dockerfile`, the gateway, the dashboard, and the WooCommerce MCP from this tree. Install only with `helm/urumi/values-disposable.yaml` on that context.
4. Run `scripts/disposable-store-acceptance.sh --execute` after `URUMI_DISPOSABLE_KUBE_CONTEXT` matches that context, then perform MS-08 from the dashboard.
5. Record a fresh matrix. Do not copy the unit results above forward as cluster results.

## Demo script, after the cluster exists

Do not record this against `kind-urumi-k8s-dev` or `kind-urumi-k8s-test`.

1. Show `kubectl config current-context` containing `disposable`, and `kind get clusters` still listing the two protected names.
2. Show the admission policy and a denied write outside `st-<id>`.
3. In the dashboard, create Store A and Store B. Show both rows move from Provisioning to Ready with different URLs.
4. Show two namespaces, two PVCs each, and two MCP Services.
5. Grant only `store_<A>__list_orders` and `store_<B>__list_orders` to the admin key.
6. Send one chat asking for today's orders in both stores. Show the activity list naming both tools and the audit rows for both.
7. Delete Store A in the dashboard. Show its tools gone, a rejected call to the old name, its namespace gone, and any Released volume for that namespace gone.
8. Show Store B still Ready and one successful `list_orders` call.
9. Delete the disposable cluster by its exact name only. Show the protected clusters and the original git dirty files still present.

## Readiness pass (2026-10-24, after 21:50 IST)

Decision: `NOT READY`.

No Kind cluster was created. No Helm release was installed. Current context stayed `kind-urumi-k8s-dev`. `kind get clusters` stayed `urumi-k8s-dev` and `urumi-k8s-test`. No application code was edited in this pass. The evening status vocabulary is left in place. For this pass, every evening `VERIFIED — UNIT` row is `PASS — UNIT/STATIC`, and every evening `BLOCKED` row stays `BLOCKED`. Count: 35 `PASS — UNIT/STATIC`, 50 `BLOCKED`, 0 `PASS — END-TO-END`, 0 `FAIL`, 0 `NOT TESTED` among the 85 sub-criteria. Parents MS-01 through MS-08 stay `BLOCKED`.

One live component check was added and is not one of those 85 completions: WooCommerce 9.4.3 activated inside a disposable local Docker pair, then that pair was removed.

### Git safety

| Item | Value |
| --- | --- |
| Branch | `submission` |
| HEAD | `b4b813c568ca70b45d28944a44de4e8e770f874d` |
| Staged | nothing |
| Tracked diff | 29 files, 1609 insertions, 85 deletions |
| Untracked | `PROJECT_GAP_TRACKER.md`, `deploy/wordpress/Dockerfile`, `docs/verification/`, `helm/urumi/values-disposable.yaml`, `scripts/image_provenance.py`, `tests/test_image_provenance.py`, `tests/test_utility_mcp.py` |

Nothing was staged, committed, pushed, reset, or cleaned.

### Five fixes

| Fix | Location | This pass | What it proves | Still unproven |
| --- | --- | --- | --- | --- |
| Job reconciliation | `apps/gateway/app/stores/cluster.py` `_reconcile_existing_job` | `tests/test_store_kube.py::test_succeeded_job_is_not_replaced_and_failed_job_is_recreated` and `test_running_job_with_the_same_template_is_left_alone`, inside the 8-pass run below | A scripted HTTP client does not PUT a succeeded or matching Job. A failed Job is DELETE then POST. | A real Job controller, immutable-field 422, and a restarted provisioner pod. MS-07e, MS-07g stay `BLOCKED`. |
| Volume cleanup | `release_store_volumes`, admission expression for `persistentvolumes`, teardown in `service.py` | `test_volume_cleanup_deletes_only_released_volumes_for_that_store`, `test_store_stays_deleting_until_volume_cleanup_finishes` | Scripted list deletes one Released volume for that namespace and skips `default`, the other store, and a Bound volume. The row stays `deleting` until the sweep returns clean. | StorageClass reclaim on a cluster. MS-04b, MS-04c, MS-04d stay `BLOCKED`. |
| Cross-store chat | `chat.py` `_system_prompt` | `tests/test_chat.py::test_one_request_calls_both_store_list_orders_tools` | An in-process provider is offered both `store_<id>__list_orders` names, both calls run, and each gets an audit row with actor, user, upstream id, duration, and `success`. | A real model and a real MCP server. MS-08-7 through MS-08-10 stay `BLOCKED`. |
| WooCommerce image | `deploy/wordpress/Dockerfile`, `init_store.sh` | Image build and local activation below | The image contains plugin 9.4.3. The entrypoint copies it onto an empty volume. `wp plugin activate` then reports `active` and version 9.4.3. | Kubernetes init Job, REST key insert, `install_pages`, and Ready. MS-02 and MS-08-2 stay `BLOCKED`. |
| Helm flag guard | `helm/urumi/templates/_helpers.tpl` | Renders below, exit 1 for both invalid pairs | `enabled` without `admit`, and `admit` without `enabled`, fail the render. Default-safe render has no admission policy. Disposable render has the policy and no `0.0.0.0/0`. | The policy has not denied a live request. MS-06g stays `BLOCKED`. |

Command for the eight tests:

`uv run pytest -q --tb=line tests/test_store_kube.py::test_succeeded_job_is_not_replaced_and_failed_job_is_recreated tests/test_store_kube.py::test_running_job_with_the_same_template_is_left_alone tests/test_store_kube.py::test_volume_cleanup_deletes_only_released_volumes_for_that_store tests/test_store_kube.py::test_store_stays_deleting_until_volume_cleanup_finishes tests/test_chat.py::test_one_request_calls_both_store_list_orders_tools tests/test_store_manifests.py::test_resources_are_named_and_labeled_for_one_store tests/test_store_manifests.py::test_provisioner_rbac_is_not_cluster_admin tests/test_store_manifests.py::test_network_policy_does_not_select_the_other_store`

Result: 8 passed, exit 0, 6.56s. Status of that command: `PASS — UNIT/STATIC`.

### WordPress image

| Item | Value |
| --- | --- |
| Build | `docker build -t urumi-wordpress-woo:disposable-readiness-20261024 deploy/wordpress` |
| Exit | 0 |
| Image id | `sha256:a9137b31865af63b773bade56cba470f52fd70b24715bc83dfeb915c8c9c19ab` |
| Created | 2026-10-24T21:53:44+05:30 |
| Inspect size field | 291187495 bytes |
| Base | `wordpress:6.8-php8.2-apache` at `sha256:1e6215749283955d5c9ffea6c297651ed23cdfdbb91677ad7abd705b2682f2cf` |
| Plugin pin | WooCommerce 9.4.3 from `https://downloads.wordpress.org/plugin/woocommerce.9.4.3.zip` |
| Header in the image | Plugin Name WooCommerce, Version 9.4.3 |
| Bootstrap file sha256 | `62c8a201c84657decf6c14fad64683fe728373c0f5d57d4189bef4fc840227ec` (1845 bytes, the main plugin file only) |

The Dockerfile does not pin a zip digest. The build used the public network for the plugin zip and Debian `unzip`. The zip is not left in the image. Unzip stays installed. WooCommerce is GPL. The chart value `urumi-wordpress-woo:disposable` was not created and was not retagged. The demo workload image `wordpress:6.8-php8.2-apache` was not retagged.

Activation evidence, local Docker only, memory-capped (`512m` database, `768m` WordPress, `256m` wp-cli):

| Attempt | Result |
| --- | --- |
| First | Exit 1. The plugin file was already on the volume (`plugin_copied_to_volume=yes`). `wp` in a CLI container without `WORDPRESS_DB_*` read `wp-config-docker.php`, which defaults the host to `mysql`, and could not connect. |
| Second | Exit 0. Same copy step (`plugin_copied_after_1`, `wp_config=getenv_docker`). With the database environment present, output included `Plugin 'woocommerce' activated.`, `plugin_active=yes`, version `9.4.3`, status `active`. |

Those containers, the volume, the network, and the env file were removed. Names contained `disposable-readiness`. Protected containers were not stopped.

This does not run `init_store.sh`, so permalinks, WooCommerce pages, and the REST key insert are `NOT TESTED`. Runtime plugin download stays off when `WORDPRESS_PLUGIN_DOWNLOAD` is not `true`. A store using this image does not need public HTTPS to fetch WooCommerce. It still needs cluster DNS, MariaDB, and the init Job.

### Helm

| Render | Exit | Observed |
| --- | --- | --- |
| `helm lint helm/urumi --set database.password=lint-placeholder` | 0 | `1 chart(s) linted, 0 chart(s) failed` |
| `helm template` with `store.enabled=false` and the placeholder password | 0 | No `ValidatingAdmissionPolicy`, no `urumi-store-provisioner`. Two `0.0.0.0/0` rules because `networkPolicy.allowExternalEgress` is true in `values.yaml`. |
| `helm template` of `values.yaml` with only the placeholder password | 1 | `Set store.existingSecret, or set the MariaDB and WordPress admin values in a gitignored values file.` Provisioner still absent from the partial output. |
| `storeProvisioner.enabled=true`, `admit=false` | 1 | `The store provisioner is non-deployable until storeProvisioner.admit is true...` |
| `storeProvisioner.enabled=false`, `admit=true` | 1 | `storeProvisioner.admit cannot be true while storeProvisioner.enabled is false.` |
| `helm lint` and `helm template` with `helm/urumi/values-disposable.yaml` plus the placeholder password | 0 | Chart version in labels `urumi-0.3.0`. Provisioner command `python -m apps.gateway.app.stores.provisioner`, image `urumi-gateway:local`, `STORE_CLUSTER_RECONCILER=true` on that Deployment only, `STORE_ALLOW_PLUGIN_EGRESS=false`, `STORE_STORAGE_CLASS=local-path`, `STORE_PROBE_CIDRS` empty, `STORE_IMAGE_WORDPRESS=urumi-wordpress-woo:disposable`. Admission `failurePolicy: Fail`, `validationActions: [Deny]`, persistent-volume delete only when `claimRef.namespace` matches `st-[0-9a-f]{16}`. Gateway, dashboard, and MCP images are `urumi-*:local`. `0.0.0.0/0` count 0. `kind: ResourceQuota` count 0 because per-store quotas are applied by Python, and `store.enabled` is false so the demo store is not rendered. |

`urumi-gateway:local` inspect created time is 2026-10-10. It is older than HEAD and older than the uncommitted provisioner fixes. The live demo gateway is `urumi-gateway:partial-20261019` on `kind-urumi-k8s-dev`. Neither image is this dirty tree.

### Blockers

| Blocker | Criterion IDs | Next action |
| --- | --- | --- |
| No disposable cluster, and this host has about 2.5GiB available while the two protected control planes already occupy about 2.1GiB and 1.3GiB | MS-01a–d, MS-01i, MS-02a–k, MS-03a, MS-03b, MS-03d, MS-03e, MS-04b–d, MS-05c–g, MS-06a, MS-06b, MS-06g, MS-07e–g, MS-08-1–16 | Use a separate machine. Do not add a third Kind node here. |
| Disposable values point at `urumi-gateway:local`, `urumi-dashboard:local`, and `urumi-*-mcp:local`, which are not this checkout | MS-03a, MS-03e, MS-08 | Rebuild those images from this tree with disposable tags before any install. |
| `values-disposable.yaml` names `urumi-wordpress-woo:disposable`, which does not exist. The built tag is `urumi-wordpress-woo:disposable-readiness-20261024` | MS-02b, MS-08-2 | After approval, point the disposable values at the built tag or retag only that disposable name. |
| `STORE_PROBE_CIDRS` is empty. WordPress and MCP HTTP probes come from the node and default-deny will drop them. Database probes are in-container exec | MS-02b, MS-02c, MS-02g, MS-02j, MS-08-2 | Set a narrow node CIDR at install time, or change probes only after an approved code change. |
| The chart does not install a StorageClass or local-path provisioner. Reclaim policy is unknown until that class exists | MS-04c, MS-04d | On the future cluster, install a provisioner, record `reclaimPolicy`, then run deletion. |
| Admission, NetworkPolicy, and quota enforcement have not been executed | MS-05c–g, MS-06a, MS-06b, MS-06g | On the future cluster, deny one cross-namespace write and one cross-store connection before creating stores. |
| Chat evidence is an in-process provider | MS-08-7–10 | One real dashboard chat against both Ready stores. |
| `init_store.sh` REST key and page setup were not run | MS-02, MS-08-6 | Run the init Job on the disposable cluster and call `list_orders`. |
| Original assignment VPS and live demo image drift | separate from MS-* | `values-vps.yaml` is still not a verified install. Do not treat `partial-20261019` as this checkout. |

### Disposable environment recommendation

Do not create it on this WSL host.

| Item | Recommendation |
| --- | --- |
| Where | A separate VM or machine, not this host |
| CPU | 4 vCPU |
| RAM | 12 GiB for the VM, with no copy of the two protected clusters |
| Disk | 40 GiB |
| Kubernetes | kind v0.33-compatible node, context name containing `disposable` and neither protected name |
| CNI | Calico, matching the already-enforcing plugin on `kind-urumi-k8s-dev` (`calico/kube-controllers:v3.30.2` is what that cluster runs). Prove one denied packet before any store create |
| Ingress | ingress-nginx in `ingress-nginx` |
| Storage | local-path provisioner and a StorageClass named `local-path`, after recording `reclaimPolicy` |
| Images | Load this checkout's gateway, dashboard, three MCP images, and `urumi-wordpress-woo:disposable-readiness-20261024`. `mariadb:11.4`, `wordpress:cli-php8.2`, and `postgres:16-alpine` are already local |
| Install | `helm/urumi/values-disposable.yaml` only on that context, after the image tags and probe CIDR match the build |
| Cleanup | Delete stores through the API, then `kind delete cluster` only for the disposable name |

Estimated footprint if it were forced onto this host: control plane plus Calico about 1.5–2.5 GiB, ingress and gateway stack about 1 GiB, two stores about 1.2 GiB of requests before limits and page cache. Available memory now is about 2.5 GiB. That is not enough, and taking it would press the protected nodes.

### Proposed implementation sequence, not started

1. Rebuild gateway, dashboard, and MCP images from this dirty tree with disposable tags. Affected: MS-03, MS-08. No protected retag.
2. Point `values-disposable.yaml` at those tags and at `urumi-wordpress-woo:disposable-readiness-20261024`. Keep both provisioner flags false in `values.yaml`.
3. Leave probe CIDRs as an install-time value, documented, not a broad default.
4. Add the StorageClass install to the disposable procedure, not to the protected clusters.
5. After a separate approval, create the VM cluster and run MS-08.

No code change was made for that sequence in this pass.

## Disposable package pass (2026-10-24, after 22:10 IST)

Review decision: the artifacts are ready for a human review. Provisioning decision: `NOT READY`.

No cluster was created. No Helm release was installed. No VM was created. Current context stayed `kind-urumi-k8s-dev`. `kind get clusters` stayed `urumi-k8s-dev` and `urumi-k8s-test`. Nothing was staged or committed. End-to-end completion of the 85 sub-criteria stays 0. This pass does not move any evening row to `PASS — END-TO-END`.

### Git safety

Recorded before the image build. The later chart edit and new files are listed after it.

| Item | Value |
| --- | --- |
| Branch | `submission` |
| HEAD | `b4b813c568ca70b45d28944a44de4e8e770f874d` |
| Ahead | `origin/submission` by 1. No fetch. |
| Staged | nothing |
| Tracked diff before this pass | 29 files, 1609 insertions, 85 deletions |

Those 29 paths are the same set recorded in the readiness pass. This pass then edited `helm/urumi/templates/_helpers.tpl` again and added `helm/urumi/values-disposable-proposed.yaml` plus `docs/verification/evidence/multi-store/gateway-image-provenance.json`. `values.yaml`, `values-disposable.yaml`, `values-dev.yaml`, and `values-vps.yaml` were not edited. No existing image tag was retagged.

### Gateway image

| Item | Value |
| --- | --- |
| Command | `docker build -f apps/gateway/Dockerfile -t urumi-gateway:disposable-pkg-20261024 --build-arg URUMI_SOURCE_REVISION=b4b813c568ca70b45d28944a44de4e8e770f874d --build-arg URUMI_SOURCE_DIRTY=true .` |
| Exit | 0 |
| Local image id | `sha256:f1f10ad1e6f31e1009c744a31281a33069bfe8ca1dd2c0dd3489e5a821a5a1d3` |
| Created | 2026-10-24T22:13:36+05:30 |
| Labels | `io.urumi.source.revision=b4b813c568ca70b45d28944a44de4e8e770f874d`, `io.urumi.source.dirty=true` |
| Base | `python:3.10-slim` at `sha256:6ff506466f8b1e981719b468cc0023b8ef000b983cdec0eafe119d01f379e31d` |
| Registry digest | none. The image was not pushed. |

`urumi-gateway:local` stayed `sha256:01b9a7b174e19084d894314b0c435f14399fb0502a2867a333e721bdfa84a6fd`. `urumi-gateway:partial-20261019` stayed `sha256:ce7818a6c752269f3efcacd5a38aba557e0dd095d120312f8af3c16b9ba8f0d6`. The new tag is the only tag on the new id.

The Dockerfile copies the working tree `apps/` directory and installs it with `uv sync --frozen --no-editable`. `.dockerignore` excludes `tests/` and `.git`. Helm files are not copied. Nine files were hashed before the build and again from `/app/.venv/lib/python3.10/site-packages` inside the image. The hashes match, and they still match the working tree after the later chart edit:

| File | sha256 |
| --- | --- |
| `apps/gateway/app/stores/cluster.py` | `07392ceb2bfd50cebfc65fbf03b90eb974a1ee2d854beecc64db8bafa529fef9` |
| `apps/gateway/app/stores/service.py` | `0fc1b444abb379cea2b53f20fe0792726cc649ac4209d047bfcdf813ea0e0150` |
| `apps/gateway/app/stores/manifests.py` | `c9c9a3b62525b4bce13a3d55ef710084fffce259e0f0976950b8eb37f36057f7` |
| `apps/gateway/app/stores/init_store.sh` | `2e6187e47ff11dc896597cf084fe02a5c30d238ce8732e696353f17804e5bcfd` |
| `apps/gateway/app/chat.py` | `f1315f7b0ccf1979f6d20449e64573457e4c10e4e2ec254ede52a5b4a50bbe1b` |
| `apps/gateway/app/dispatch.py` | `5301cabddd79bcb4fc06062d68b757ad4608b662d25ca3656988ef30657acfb1` |
| `apps/gateway/app/stores/routing.py` | `6eb247389026b25d5b11fd69f4f55d5f5c0519878b4ac633986efe8e6e067e18` |
| `apps/utility_mcp/app/main.py` | `1b2c7e075e1b80ea404743dcb0ef886ca667cc3c49097e24db249465f4cede00` |
| `apps/woocommerce_mcp/app/tools.py` | `ddbdf853853493d2d81cdfbd66b9ac1c7c670b7f4e3557121f54cafdec67b19e` |

`HttpClusterClient.release_store_volumes` and `_reconcile_existing_job` are present on the packaged class. That proves this image contains the uncommitted gateway, store, chat, utility, and WooCommerce MCP sources that were hashed. It does not prove the separate dashboard or MCP images were rebuilt. Those processes use their own images. The provenance file `docs/verification/evidence/multi-store/gateway-image-provenance.json` records `dirty: true` and `context_sha256=031006798c21ae9bc805eb004a5be695e1de744a0fe97aba743407d33c626aa7`. Its `tag` field is the provenance script's identity string, `urumi-gateway:b4b813c568ca70b45d28944a44de4e8e770f874d-dirty`. That Docker tag was not created. The context hash was taken before the probe-gate edit and before the new values file, so it is not a fingerprint of the tree after this pass.

Local startup, not Kubernetes: `docker run --memory 256m -p 127.0.0.1:18080:8000` with no database URL. Uvicorn logged `Application startup complete`. `GET /health` returned 503 `{"status":"unavailable"}` because PostgreSQL is unset. The container `urumi-disposable-pkg-gateway-start` was removed. Status of this check: `PASS — LIVE COMPONENT` for process start only.

### WordPress image

`urumi-wordpress-woo:disposable-readiness-20261024` is still local. `docker image inspect` id `sha256:a9137b31865af63b773bade56cba470f52fd70b24715bc83dfeb915c8c9c19ab`, created 2026-10-24T21:53:44+05:30, size field 291187495. A container `cat` of `/usr/src/wordpress/wp-content/plugins/woocommerce/woocommerce.php` returned 1845 bytes, Plugin Name WooCommerce, Version 9.4.3. The image was not rebuilt and was not retagged. Status: `PASS — LIVE COMPONENT` for presence of that plugin file. Activation evidence remains the readiness pass. `init_store.sh` was not executed.

### Proposed values

`helm/urumi/values-disposable-proposed.yaml` is the review file. It does not replace `values-disposable.yaml`.

| Setting | Proposed value | Why |
| --- | --- | --- |
| Gateway image | `urumi-gateway:disposable-pkg-20261024` | Matches the image inspected above. The provisioner Deployment uses the gateway image. |
| WordPress image | `urumi-wordpress-woo:disposable-readiness-20261024` | Matches the image that contains WooCommerce 9.4.3. |
| Dashboard, Support, Utility, chart WooCommerce MCP, store MCP | tag `disposable-pkg-20261024` | These images were not built. A missing tag fails closed. `:local` and `:k8s-test` are not used. |
| `store.enabled` | false | The demo store is not rendered. |
| Provisioner `enabled` and `admit` | both true | Same pair the chart already requires. |
| `allowExternalEgress` and `allowPluginEgress` | false | No public 443 rule from this overlay. |
| `probeCIDRs` and `probeCidrs` | empty | No address was guessed. |
| `requireCapturedProbeCidrs` | true | `helm template` fails until both probe lists are single-host routes. |
| Ingress | `gateway.stores.local`, `dashboard.stores.local` | Not `gateway.local`, `dashboard.local`, or `store.local`. |
| `database.persistence.storageClass` and provisioner `storageClass` | `local-path` | Names the class. Does not install it and does not set `reclaimPolicy`. |

Probe path, from the chart and `manifests.py`, not from a guessed address:

- This chart does not assign node or pod CIDRs. Kind and the CNI do.
- Kubelet probes the pod IP directly. The packet source has to be captured on the disposable node. The protected nodes' addresses are not that source.
- Chart `networkPolicy.probeCIDRs` is added to gateway, dashboard, and in-namespace MCP ingress. Gateway liveness is `tcpSocket`; gateway readiness, dashboard, and MCP probes are `httpGet`.
- `storeProvisioner.probeCidrs` is copied to `STORE_PROBE_CIDRS`. Store WordPress allows it on TCP 80. Store MCP allows it on TCP 8000. Store MariaDB does not. Its probes are `exec` to `127.0.0.1:3306`.
- Gateway-to-store MCP is not a CIDR. The gateway pod gets `urumi.dev/gateway: allowed` only when both provisioner flags are true. Its egress selects namespaces labeled `urumi.dev/store: "true"` on TCP 8000. The store MCP allows that label from the gateway namespace. Default-deny stays on the store namespace. Store-to-store traffic is not in either allow list.
- PostgreSQL probes are `pg_isready` exec. The chart still adds `probeCIDRs` to `gateway-db` ingress on 5432, and to the unused release-namespace WordPress and MariaDB policies. A captured `/32` therefore also allows that host to open TCP 5432 to gateway PostgreSQL. That chart behavior was not changed.

`_cidrs` in `config.py` still drops any probe prefix shorter than IPv4 `/24` or IPv6 `/120`. The new Helm gate is stricter: with `requireCapturedProbeCidrs` true, only a `/32` or `/128` renders. `helm lint` prints the empty-probe failure as info and still exits 0. `helm template` is the gate.

### Helm and tests

| Command | Exit | Result |
| --- | --- | --- |
| `helm template` of `values-disposable-proposed.yaml` with a placeholder database password | 1 | `storeProvisioner.probeCidrs is empty...` |
| Same, with `0.0.0.0/0` on both probe settings | 1 | `storeProvisioner.probeCidrs rejects a default route.` |
| Same, with `192.0.2.0/24` on both | 1 | wider prefix rejected |
| Same, with documentation prefix `192.0.2.1/32` on both | 0 | Shape only. That prefix is not a captured source and is not in the values file. |
| Same, with `requireCapturedProbeCidrs=false` | 0 | Shape render used to inspect images and RBAC. Not an install command. |
| `helm template` of unchanged `values-disposable.yaml` | 0 | The old file still renders. It is still the wrong image set. |
| `enabled=false`, `admit=true` | 1 | admit-without-enabled rejected |
| `enabled=true`, `admit=false` | 1 | enabled-without-admit rejected |
| `helm lint` default values with `store.enabled=false` | 0 | 0 charts failed |
| `helm lint` of the old disposable file | 0 | 0 charts failed |
| `helm lint` of the proposed file | 0 | Lint does not fail the empty probe list. Template does. |
| `uv run pytest -q --tb=line` on the eight store/chat tests plus `tests/test_image_provenance.py` | 0 | 14 passed, 5.55s |

Gate-off render counts: gateway image `urumi-gateway:disposable-pkg-20261024` twice and `urumi-gateway:local` zero; WordPress image the readiness tag twice; `STORE_CLUSTER_RECONCILER` once, value `"true"`, on the provisioner; `STORE_PROBE_CIDRS` value `""`; `STORE_ALLOW_PLUGIN_EGRESS` `"false"`; `STORE_STORAGE_CLASS` `local-path`; `0.0.0.0/0` zero; `172.19.0.2` and `172.19.0.3` zero; `store.local` zero; admission policy and binding present; `failurePolicy: Fail`; `validationActions: [Deny]`; persistent-volume delete still requires `claimRef.namespace` `st-[0-9a-f]{16}`; NetworkPolicy count 9; `kind: ResourceQuota` count 0 because store quotas are applied by the provisioner. Gateway PostgreSQL `storageClassName` is `"local-path"`.

These results are `PASS — UNIT/STATIC`. They are not a cluster install.

### Blockers

| Blocker | Criterion IDs | Correction in this pass | Still required |
| --- | --- | --- | --- |
| No disposable cluster. This host has about 2.6GiB available | MS-01a–d, MS-01i, MS-02, MS-03a, MS-03b, MS-03d, MS-03e, MS-04b–d, MS-05c–g, MS-06a, MS-06b, MS-06g, MS-07e–g, MS-08 | None. No cluster was created | Approved VM |
| Dashboard and MCP images are not this tree | MS-01, MS-02g, MS-03e, MS-08 | Proposed tags do not point at `:local` | Build `urumi-dashboard`, `urumi-support-mcp`, `urumi-utility-mcp`, and `urumi-woocommerce-mcp` as `disposable-pkg-20261024` |
| Probe source is unknown | MS-02b, MS-02g, MS-02i, MS-02j, MS-05, MS-08-2 | Helm template fails closed while the lists are empty or wider than one host | Capture one kubelet source on the new node and supply it in a gitignored file |
| Chart `probeCIDRs` also opens gateway PostgreSQL 5432 | MS-05, MS-06 | Documented. Not changed | Separate approval before removing `probeFrom` from the gateway-db policy |
| StorageClass and `reclaimPolicy` are not installed by the chart | MS-04c, MS-04d | Values name `local-path` | Install the provisioner on the new cluster and read `reclaimPolicy` before creating a store |
| Admission, NetworkPolicy, and quota have not been executed | MS-05c–g, MS-06a, MS-06b, MS-06g | Render only | One denied write and one denied cross-store connection before any store |
| Chat proof is an in-process provider | MS-08-7–10 | Gateway image contains the current `chat.py` | One real dashboard chat |
| `init_store.sh` has not been run | MS-02, MS-08-6 | The script bytes in the gateway image match the working tree | Init Job on the disposable cluster |
| VPS and the live demo images | separate from MS | Not changed | Do not claim either from this package |

### Runbook for a later approved VM

Do not run this runbook until the approval list below is accepted. Do not point it at `kind-urumi-k8s-dev` or `kind-urumi-k8s-test`.

1. Use a separate machine with 4 vCPU, 12 GiB RAM, and 40 GiB disk. Do not copy the protected clusters onto it. App requests are about 700m CPU and 1.8 GiB for the chart plus two stores. Kind, Calico, and ingress need about 1.5–2.5 GiB more. Container limits can add up to more than 4 vCPU if every container bursts at once, so the VM is sized for the requests plus a small burst.
2. Create a kind cluster whose context name contains `disposable` and contains neither protected name. Install Calico and ingress-nginx in `ingress-nginx`. Record the context, Kubernetes version, node InternalIP, and CNI.
3. Capture the kubelet source of one HTTP probe. Put that single `/32` or `/128` in a gitignored file as both `networkPolicy.probeCIDRs` and `storeProvisioner.probeCidrs`. Do not commit it. Do not use `0.0.0.0/0`.
4. Install a local-path provisioner that creates StorageClass `local-path`. Read `.reclaimPolicy` and write it down. `Delete` means the volume goes away with the claim. `Retain` means the provisioner sweeper must delete the Released volume after namespace deletion.
5. Load `urumi-gateway:disposable-pkg-20261024` and `urumi-wordpress-woo:disposable-readiness-20261024`. Build the four missing images from this tree with tag `disposable-pkg-20261024` and load those too. `mariadb:11.4`, `wordpress:cli-php8.2`, and `postgres:16-alpine` are already local on this host; copy them if the VM does not have them.
6. From a gitignored password file, run `helm template` with `values-disposable-proposed.yaml` plus the probe file. Continue only if it exits 0. `helm lint` exiting 0 is not that check. Install only with `kubectl --context` equal to the disposable context.
7. Before any store: show the admission policy, the gateway label, gateway egress to `urumi.dev/store`, one denied cross-namespace write, and one denied connection between store namespaces.
8. Create two stores from the dashboard, compare today's orders in one chat, delete one store, and show the other still serves. Then delete the remaining store and delete only that kind cluster.

### Approval still required

- Creating the VM, the kind cluster, or any cloud resource.
- `kind load`, `docker build` of the dashboard and MCP images, and any tag other than the two images already built.
- `helm install` or `helm upgrade` on any context.
- Choosing the probe CIDR, including copying either protected node's address.
- Installing a StorageClass or local-path provisioner.
- Editing the gateway-db NetworkPolicy to stop adding the probe CIDR to PostgreSQL.
- Creating a store, deleting a store, or calling a write provisioning API.
- Staging, committing, or pushing.

## Manual demonstration preparation (2026-10-24, after 22:28 IST)

The manual dashboard flow has not been performed. End-to-end completion of the 85 sub-criteria stays 0. No cluster, VM, Helm release, or store was created. Context stayed `kind-urumi-k8s-dev`. Nothing was staged or committed.

### What changed

| Change | Why |
| --- | --- |
| `helm/urumi/templates/networkpolicy.yaml` | Kubelet probe routes are no longer added to PostgreSQL or chart MariaDB ingress. Those probes are `exec`. Gateway, dashboard, MCP, and chart WordPress policies still include the probe helper. |
| `apps/gateway/app/stores/init_store.sh` | Wait for MariaDB before `wp core install`. Store the WooCommerce consumer secret as generated. WooCommerce hashes only the consumer key; hashing the secret made the insert fail because `consumer_secret` is `char(43)`. |
| `tests/test_networkpolicy_intent.py`, `tests/test_store_manifests.py` | Regression checks for both defects. |
| Source labels on the three MCP Dockerfiles | The disposable images record revision `b4b813c` and `dirty=true`. |
| `helm/urumi/values-disposable-proposed.yaml` | Image tags now match the inspected builds. Gateway tag is `disposable-manual-20261024`. |

`values.yaml`, `values-disposable.yaml`, `values-dev.yaml`, and `values-vps.yaml` were not edited. Existing demo tags were not retagged. `urumi-gateway:disposable-pkg-20261024` was left in place and is not the install tag: it does not contain the init-script fix.

### Dependency map for the manual flow

| Step | Path | What this pass showed |
| --- | --- | --- |
| Create and delete | `StorePanel.tsx` calls `POST /admin/stores`, `POST /admin/stores/<id>/delete`, and `POST /admin/stores/<id>/retry`. The panel polls every 2 seconds while a row is Provisioning or Deleting, shows `error_description`, and does not treat Failed as Ready. | Dashboard unit tests. Not a live session. |
| Ready | `service.py` `_provision` calls `workloads_ready`, which requires WordPress, MariaDB, MCP, and a succeeded `wordpress-init` Job before `_register` sets `ready`. | Unit tests. A local init run is not a pod. |
| Init | ConfigMap is the packaged `init_store.sh`. It installs WordPress, activates WooCommerce, sets `/%postname%/`, runs `wp wc tool run install_pages`, and inserts the REST key. | Local containers, below. |
| Tools | Ready registration uses `store_<id>__list_orders`. Discovery and dispatch recheck the grant, the owner, and Ready. | Unit tests. |
| One chat, two tools | `POST /admin/chat` → `ChatService.answer`. The prompt lists granted `__list_orders` tools. Each dispatch writes its own audit row. The chat panel shows activity. Audit history is `GET /admin/audit`. | In-process provider test. Not a model call. |
| Missing model | If no provider is configured, chat returns 503 `The chat provider is not configured.` | Source. Not a live 503 from the dashboard. |
| Delete Store A | `request_delete` drops that registration and grants. The namespace is deleted. Released volumes for that namespace are swept. Store B is a different namespace. | Unit tests. Not a live delete. |
| Isolation | Store default-deny. MariaDB allows only that store's WordPress and init pods. MCP allows the gateway label on TCP 8000. | Templates and unit tests. No packet. |

### Images

| Tag | Id | Check |
| --- | --- | --- |
| `urumi-gateway:disposable-manual-20261024` | `sha256:be4630516c3d6dbd0c1a7386fb40ff3d7e6f0f5cb1f32cb5b43408a00f4a1332` | Packaged `init_store.sh` sha256 `f3c00f0dc7ff91106a141c2731402e87cfea08ca7070d86fa53185edd01d5ac1`, same as the working tree. Secret is not hashed. Database wait is before install. |
| `urumi-dashboard:disposable-pkg-20261024` | `sha256:72c3233564d8ac84920e76748534cc5439d270b2505c58b08a305ebdc567436a` | Contains `index.html` and `assets`. |
| `urumi-support-mcp:disposable-pkg-20261024` | `sha256:e1f8735456f9f1528b3d768945785874a37191ac1a934c1b08924cfdf9d223b3` | Command is Support's app. |
| `urumi-utility-mcp:disposable-pkg-20261024` | `sha256:aa458e9a694bd7dc38e5c4ec515ecb752371716306e2b5e4055cb4fb953bdb19` | Packaged `utility_mcp/app/main.py` matches the dirty tree. |
| `urumi-woocommerce-mcp:disposable-pkg-20261024` | `sha256:f5100088180f29b8b83257cdeeaadd1a1772e19074795645191d4b7a19ae809f` | `tools.py` matches the tree and defines `list_orders`. |
| `urumi-wordpress-woo:disposable-readiness-20261024` | `sha256:a9137b31865af63b773bade56cba470f52fd70b24715bc83dfeb915c8c9c19ab` | Unchanged. WooCommerce 9.4.3. |

Provenance JSON files are in `docs/verification/evidence/multi-store/`. Their `tag` field is the provenance script's `revision-dirty` string. The Docker tags above are the ones to load. No image was pushed.

`urumi-gateway:local` stayed `sha256:01b9a7b174e19084d894314b0c435f14399fb0502a2867a333e721bdfa84a6fd`. `urumi-dashboard:partial-20261019`, `urumi-woocommerce-mcp:local`, and `urumi-woocommerce-mcp:k8s-test` were not retagged.

### Initialization

Disposable containers only, memory-capped at 512 MiB, removed afterward. Names started with `urumi-init-pkg`. The first attempt failed because MariaDB was not accepting connections when `wp-config.php` already existed. The second attempt reached WordPress install and then `WooCommerce credential insert failed` because the secret was hashed to 64 characters. After the script fix, the third attempt exited 0.

| Check | Result |
| --- | --- |
| Plugin status | `active` |
| Permalink | `/%postname%/` |
| Pages | `shop`, `cart`, `checkout`, `my-account`, `refund_returns` |
| API key row | description `urumi-gateway`, permissions `read_write`, key length 64, secret length 43 |

No credential was printed. This is `PASS — LIVE COMPONENT` for the script. It is not a Kubernetes Job and it does not mark a store Ready. Status of MS-02 and MS-08-2 stays `BLOCKED`.

### Probe policy and Helm

`helm template` of `values-disposable-proposed.yaml` with an empty probe list exited 1. The same file with documentation prefix `192.0.2.1/32`, which is not a captured source and is not saved in the values file, exited 0. That render contains `urumi-gateway:disposable-manual-20261024` twice, the readiness WordPress image twice, and no `0.0.0.0/0`. The documentation prefix appears on gateway, upstream, dashboard, and chart WordPress policies. It does not appear on `pkg-urumi-gateway-db-net` or `pkg-urumi-store-db-net`.

`tests/test_networkpolicy_intent.py` and `tests/test_store_manifests.py` cover the same rule. Level: `PASS — UNIT/STATIC`. A CNI has not enforced it.

### Tests

| Command | Exit | Result |
| --- | --- | --- |
| `uv run pytest -q --tb=line tests/test_networkpolicy_intent.py tests/test_store_manifests.py tests/test_store_kube.py tests/test_stores.py tests/test_chat.py tests/test_store_routing.py tests/test_discovery_policy.py` | 0 | 71 passed, 21.80s |
| `npm test` in `dashboard/` | 0 | 6 files, 34 tests |

These do not replace the dashboard demonstration.

### Remaining blockers

| Blocker | IDs |
| --- | --- |
| No disposable cluster and no captured probe `/32` | MS-01a–d, MS-01i, MS-02, MS-03a/b/d/e, MS-04b–d, MS-05c–g, MS-06a/b/g, MS-07e–g, MS-08 |
| `CHAT_API_KEY` is not installed; a real model call has not happened | MS-08-7–10 |
| StorageClass `reclaimPolicy` has not been read back | MS-04c, MS-04d |
| Admission and NetworkPolicy have not denied a live request | MS-05c–g, MS-06a, MS-06b, MS-06g |
| Provisioner egress allows TCP 443 to any destination, because that rule has no `to` | MS-06 |
| The manual walkthrough below has not been done | MS-08 |
| VPS and the protected demo | separate |

### Manual walkthrough

Perform this only on the approved disposable context. Stop if `kubectl config current-context` is `kind-urumi-k8s-dev` or `kind-urumi-k8s-test`.

1. Preflight. Context name contains `disposable`. `helm template` of `values-disposable-proposed.yaml` plus the gitignored probe file exits 0. Pods for the gateway, dashboard, Support, Utility, and the provisioner are Ready. Admission policy `urumi-store-provisioner-scope` exists. One cross-namespace write and one cross-store connection have already been denied.
2. Dashboard. Open `http://dashboard.stores.local` after the hosts entry points at the disposable node. Sign in as the admin created on that cluster. Do not reuse a demo password or paste a key into the notes.
3. Create Store A. In Stores, enter a name such as `Store A`, set a new idempotency key, and submit. Leave the row on Provisioning until it says Ready. Record the store id, URL, and tool name `store_<id>__list_orders`.
4. Create Store B the same way, without waiting for Store A to finish if you want the concurrency check. Both rows must say Ready, with different ids, URLs, and tool names.
5. Orders. In each store's WordPress admin, add one order dated today with a distinct line, for example `order-a` and `order-b`. Do not use the protected demo store.
6. Chat prompt, as one message: `Compare today's orders from both of my stores. Call each store list_orders tool separately and base the comparison only on those results.`
7. Evidence. The chat activity lists both `store_<id>__list_orders` calls. Refresh Audit history. There are two rows, with different upstream ids, the tool name, actor, user, time, duration, and outcome. The answer matches the two orders. If chat says the provider is not configured, stop. That is a failure, not an order comparison.
8. Delete Store A from the Stores table and confirm the prompt. The row moves to Deleting and then disappears or shows Deleted. Refresh audit after the delete.
9. Store A is gone. Its tool is absent from discovery. A direct call with the old tool name is rejected. Its namespace `st-<id>` is gone. Record the PersistentVolume reclaim result for that namespace only.
10. Store B still says Ready. Its URL still shows `order-b`. A second chat that asks only for Store B's orders returns `order-b` and writes one new audit row.
11. Save screenshots of both Ready rows, the chat activity, the two audit rows, the delete result, and Store B after the delete. Save redacted kubectl listings for namespaces, jobs, and persistent volumes. Do not save Secret values or the REST keys.
12. Delete Store B through the dashboard. After its namespace is gone, delete only the disposable kind cluster. Confirm the two protected cluster names still exist.

### What still prevents the demonstration

I cannot hand you a dashboard URL yet. The images and the proposed values are built, and the init script succeeds in local containers, but the disposable cluster does not exist, the probe `/32` has not been captured, and the chart correctly refuses to render until that capture exists. Creating the VM remains waiting on approval. The manual flow is not a pass.

## Test-cluster live pass (2026-10-25)

The owner authorized `kind-urumi-k8s-test` as the implementation cluster for this pass, replacing the disposable-context rule above for this run only. `kind-urumi-k8s-dev` was not touched. Every command passed `--context kind-urumi-k8s-test` or `--kube-context kind-urumi-k8s-test`. The rows above are left as they were. This section is a separate matrix.

Evidence files under `docs/verification/evidence/multi-store/`:

- `cluster-repair-and-probes-20261025T030622Z.json`: Calico repair, DNS, NetworkPolicy allow and deny, captured probe source `172.19.0.2/32`, storage
- `baseline-install-20261025T035841Z.json`: release installed, baseline Ready
- `provisioner-enabled-20261025T053720Z.json`: provisioner and admission policy installed, out-of-scope ConfigMap create denied
- `first-store-20261025T071904Z.json`: `acceptance-store-a` Ready, isolation probes, ungranted call denied
- `post-provisioning-audit-20261025T073704Z.json`: read-only audit of revision 6, the store, the registry, audit rows, and memory

State at the audit: release `urumi` revision 6, gateway and provisioner image `urumi-gateway:job-stable-20261025T071558Z`, `storeProvisioner.concurrency` 1, `allowExternalEgress` and `allowPluginEgress` false, no chat Secret, no ingress controller. One store, `c4421147ea825bbc` in `st-c4421147ea825bbc`. No second store, no delete, no order, no chat.

Three defects were found on the cluster and fixed in this pass, with tests:

| Defect | Change | Test |
| --- | --- | --- |
| Provisioner could not reach the API. Calico matches the destination after kube-proxy rewrites `10.96.0.1:443` to the node on `6443`. | `urumi-store-provisioner-net` also allows TCP 6443. | `tests/test_store_manifests.py::test_provisioner_rbac_is_not_cluster_admin` |
| Reconcile sent PUT for an existing Namespace and PVC. The apiserver rejects both, which spent the attempt limit. | `HttpClusterClient.apply` leaves an existing owned Namespace or PVC in place. | `tests/test_store_kube.py::test_existing_namespace_and_pvc_are_not_replaced` |
| A running init Job was deleted every pass because the controller adds template labels. That hit the pod quota and never finished. | Only a failed Job is replaced. A running Job is left alone. | `tests/test_store_kube.py::test_running_job_with_the_same_template_is_left_alone` |

Open finding, not repaired: the MariaDB claim `data-store-db-0` comes from the StatefulSet claim template and carries only `app.kubernetes.io/component` and `app.kubernetes.io/name`. It has no `urumi.dev/store-id` or `urumi.dev/owner-id`. The namespace still bounds it.

Follow-up the same day (`pvc-labels-and-store-auth-*.json`): the claim template now carries the store labels, and an existing StatefulSet keeps its live claim template on update because that field cannot change after create. The tests are `tests/test_store_manifests.py::test_resources_are_named_and_labeled_for_one_store` and `tests/test_store_kube.py::test_existing_statefulset_keeps_its_live_claim_template`. The source change has not been built or deployed. The live claim for `acceptance-store-a` was not patched and is still unlabeled. Deletion and volume cleanup do not read PVC labels: they use the namespace and `claimRef.namespace`. No matrix status changed.

Store tool access was traced the same day without a live write. The only key, `40ab7159b6c06f90`, has no owner. The admin grant endpoint refuses a store tool for a key without the store's owner, and the dispatcher denies a store tool unless the caller owns a Ready store. The six tools are registered but not usable by that key. Proving an allowed call needs a key issued to `acceptance-admin` and an explicit grant. MS-03f and MS-03g stay `PARTIAL`.

Seven row statuses were superseded by the reconciliation dated 2026-10-26; see the reconciliation section below for the current evidence-based counts.

Status labels: `PROVEN — TEST CLUSTER` means observed on that cluster in this pass. `PARTIAL` means some of the requirement was observed and the rest was not. `UNTESTED` means no live evidence in this pass; earlier unit rows still apply.

| ID | Status | Live evidence or gap |
| --- | --- | --- |
| MS-01a | `PARTIAL` | Created through `POST /admin/stores`, the dashboard's endpoint. The dashboard form was not used. |
| MS-01b | `PARTIAL` | API list returned name, state, URL, and creation time. The dashboard table was not viewed. |
| MS-01c | `PROVEN — TEST CLUSTER` | Failed with `Provisioning stopped after the retry limit.`, then Ready after retry. |
| MS-01d | `PARTIAL` | Store A deleted through `POST /admin/stores/c4421147ea825bbc/delete`, the dashboard's endpoint. The dashboard button was not used. |
| MS-01e | `UNTESTED` | Concurrency held at 1. One store. |
| MS-01f | `UNTESTED` | Cap not exercised. |
| MS-01g | `UNTESTED` | No duplicate request sent. |
| MS-01h | `PARTIAL` | Live failure body carried no credential. Only that error path was seen. |
| MS-01i | `PARTIAL` | API polling showed Provisioning, Failed, and Ready without a database edit. The dashboard poll was not watched. |
| MS-02a | `PROVEN — TEST CLUSTER` | Namespace `st-c4421147ea825bbc` with matching `urumi.dev/store-id` and `urumi.dev/owner-id`. |
| MS-02b | `PROVEN — TEST CLUSTER` | WordPress Deployment Ready and Service present. |
| MS-02c | `PROVEN — TEST CLUSTER` | MariaDB StatefulSet Ready and Service present. |
| MS-02d | `PARTIAL` | `store-db` and `woocommerce-credentials` exist in the store namespace with the expected key names. No second store to compare. Values were not read. |
| MS-02e | `PROVEN — TEST CLUSTER` | Both claims Bound on `standard` for each store. Store B's two claims, created on revision 7, carry `urumi.dev/store-id` and `urumi.dev/owner-id`. Store A's database claim still lacks them, as noted above. |
| MS-02f | `PARTIAL` | Ingress `store` has host `c4421147ea825bbc.stores.local`. No controller, so it is not reachable. |
| MS-02g | `PROVEN — TEST CLUSTER` | MCP Deployment Ready, Service present, gateway lists 6 tools from it. |
| MS-02h | `PROVEN — TEST CLUSTER` | ResourceQuota and LimitRange present with the manifest values. |
| MS-02i | `PROVEN — TEST CLUSTER` | Five policies present. Allow and deny connections below. |
| MS-02j | `PROVEN — TEST CLUSTER` | WordPress probes `/wp-includes/images/blank.gif`, MCP `/health`. Quota usage shows requests and limits on every pod. |
| MS-02k | `PROVEN — TEST CLUSTER` | Store A `st-c4421147ea825bbc` and Store B `st-174c75b6c0ef57e5` have different ids, namespaces, volumes, URLs, and MCP endpoints. The three baseline claims are the same volumes from install. |
| MS-03a | `PROVEN — TEST CLUSTER` | Dynamic upstream `store_c4421147ea825bbc`, `listing_ok`. |
| MS-03b | `PROVEN — TEST CLUSTER` | No upstream was added through the admin form. The provisioner wrote the row. |
| MS-03c | `PROVEN — TEST CLUSTER` | Registered names include `store_c4421147ea825bbc__list_orders`. |
| MS-03d | `PARTIAL` | The only dynamic row matches the Ready store. Stale-row removal was not exercised. |
| MS-03e | `PROVEN — TEST CLUSTER` | Gateway listing of the store MCP returned 6 tools. |
| MS-03f | `PROVEN — TEST CLUSTER` | An ungranted key's `tools/list` omitted every store tool. Key `03ab04ae83ba0191`, owned by the store owner and granted only `store_c4421147ea825bbc__list_orders`, listed exactly that one tool. |
| MS-03g | `PROVEN — TEST CLUSTER` | That key's `list_orders` call succeeded through the store upstream. Its `get_order` and `utility__convert_units` calls returned `Permission denied.` An ownerless key and an invalid key were denied. |
| MS-03h | `PROVEN — TEST CLUSTER` | The only key has no permissions after the store became Ready. |
| MS-03i | `UNTESTED` | No member user. |
| MS-03j | `PROVEN — TEST CLUSTER` | After delete, upstream `store_c4421147ea825bbc` is gone and key `03ab04ae83ba0191` lost its `store_c4421147ea825bbc__list_orders` grant. |
| MS-03k | `PROVEN — TEST CLUSTER` | `store_c4421147ea825bbc__list_orders` returned `Permission denied.` with no upstream in the audit row. The calling key never held that grant; the key that did had it revoked. |
| MS-04a | `PROVEN — TEST CLUSTER` | Only Store A's upstream and grant were removed. Store B's upstream and its grant on `013b6aeafe9ea69c` stayed. |
| MS-04b | `PROVEN — TEST CLUSTER` | `st-c4421147ea825bbc` went Terminating, then absent, in about 37 s. |
| MS-04c | `PROVEN — TEST CLUSTER` | StorageClass `standard` reclaim `Delete`. Store A's two volumes were removed. The other five volumes kept the same uid and claim. |
| MS-04d | `PROVEN — TEST CLUSTER` | No volume references `st-c4421147ea825bbc` after completion. |
| MS-04e | `UNTESTED` | No delete. |
| MS-04f | `UNTESTED` | No delete. |
| MS-04g | `PARTIAL` | While the namespace was terminating, the row stayed `Deleting` with `That store could not be deleted.` and retried to `Deleted`. That was normal termination, not an induced failure. A source fix (`final-audit-*.json`) stops counting normal termination as an attempt. It is unit-tested and not deployed. |
| MS-04h | `PROVEN — TEST CLUSTER` | All 28 Store B objects kept their uids, Store B stayed Ready, and the default-namespace objects and baseline volumes were unchanged. |
| MS-05a | `PROVEN — TEST CLUSTER` | `default-deny` present. A busybox pod in `default` could not open the store database. |
| MS-05b | `PROVEN — TEST CLUSTER` | Store WordPress reached its database. The gateway reached the store MCP. |
| MS-05c | `PROVEN — TEST CLUSTER` | From Store A WordPress and MCP pods, Store B `store-db:3306`, `wordpress:80`, and `woo-mcp:8000` timed out at 3 s. Intra-store paths connected. |
| MS-05d | `PROVEN — TEST CLUSTER` | From Store B WordPress and MCP pods, the same three Store A services timed out at 3 s. Intra-store paths connected. |
| MS-05e | `PROVEN — TEST CLUSTER` | Each store's MCP pod reached its own WordPress and timed out on the other store's WordPress, database, and MCP. |
| MS-05f | `PROVEN — TEST CLUSTER` | Both dynamic upstreams report `listing_ok` with 6 tools through the gateway. |
| MS-05g | `PROVEN — TEST CLUSTER` | Default-namespace busybox and baseline WordPress were both denied to the store database. |
| MS-05h | `PARTIAL` | No public CIDR in the store policies and the init Job finished without a download. Outbound denial was not packet-tested. |
| MS-05i | `PARTIAL` | DNS resolved from every store pod. Required intra-store paths connected and every cross-namespace path tried was closed. Public egress was not packet-tested. |
| MS-06a | `PROVEN — TEST CLUSTER` | Quota present and enforced: init pods were refused with `exceeded quota` at 8 pods. |
| MS-06b | `PARTIAL` | LimitRange present. An oversized container was not submitted. |
| MS-06c | `UNTESTED` | No concurrent creates. |
| MS-06d | `UNTESTED` | The attempt limit fired. The 600 s deadline did not. |
| MS-06e | `PARTIAL` | Attempt-limit text was safe. Timeout text not seen. |
| MS-06f | `UNTESTED` | No second user. |
| MS-06g | `PARTIAL` | Server dry-run of a ConfigMap create in `default` as the provisioner ServiceAccount was denied by `urumi-store-provisioner-scope` and not stored. One kind and one verb. |
| MS-06h | `PARTIAL` | The live ServiceAccount provisioned inside its namespace. Each RBAC rule was not probed. |
| MS-06i | `PARTIAL` | Admission denied a write to `default`. Other protected namespaces were not tried. |
| MS-06j | `PARTIAL` | API bodies, audit rows, and provisioner logs read in this pass showed no secret values. Not exhaustive. |
| MS-06k | `PARTIAL` | Store B's key was denied on Store A's `list_orders` and `get_order` and on ungranted Store B and static tools. A store owned by another user was not tried. |
| MS-07a | `UNTESTED` | No repeated key. |
| MS-07b | `UNTESTED` | No conflicting key. |
| MS-07c | `UNTESTED` | No concurrent duplicates. |
| MS-07d | `PARTIAL` | Two stores are Ready at the same time. They were provisioned one after the other with concurrency 1. |
| MS-07e | `PARTIAL` | Revision 6 replaced the provisioner pod while the store was Provisioning. The new pod continued the same namespace to Ready. That was a rollout, not a crash. |
| MS-07f | `UNTESTED` | No delete. |
| MS-07g | `PARTIAL` | After the fix, the running Job was left alone and succeeded. Recreating a failed Job was not observed live. |
| MS-07h | `PROVEN — TEST CLUSTER` | Four attempt-limit failures became `Failed`. |
| MS-07i | `PROVEN — TEST CLUSTER` | The row stayed Provisioning until the Job succeeded and the upstream was registered, then became Ready. |
| MS-07j | `PROVEN — TEST CLUSTER` | Failed attempts deleted `st-c4421147ea825bbc`. The only volumes left for that namespace belong to the current Ready store. |
| MS-08-1 | `PARTIAL` | Store A and Store B created through `POST /admin/stores`. The dashboard form was not used. |
| MS-08-2 | `PROVEN — TEST CLUSTER` | Store A and Store B are both Ready, each at attempt 0 on its last run. |
| MS-08-3 | `PROVEN — TEST CLUSTER` | Separate namespaces, 22 owned objects each, separate volumes, URLs `http://<id>.stores.local`, and MCP URLs `http://woo-mcp.st-<id>.svc.cluster.local:8000/mcp`. |
| MS-08-4 | `PROVEN — TEST CLUSTER` | Both stores registered automatically. An owner key granted only that store's `list_orders` listed exactly that tool, for each store. |
| MS-08-5 | `PARTIAL` | Both `list_orders` tools are explicitly granted, on two separate owner keys. Not yet on one chat key. |
| MS-08-6 | `UNTESTED` | No orders. |
| MS-08-7 | `UNTESTED` | No chat Secret, egress off. |
| MS-08-8 | `UNTESTED` | No chat. |
| MS-08-9 | `UNTESTED` | No chat. |
| MS-08-10 | `PARTIAL` | The audit row for a direct MCP `list_orders` call has actor, upstream, tool, time, duration, and outcome. No chat. |
| MS-08-11 | `PARTIAL` | Store A deleted through the API, not the dashboard button. |
| MS-08-12 | `PROVEN — TEST CLUSTER` | No `store_c4421147ea825bbc__` tool remains in the registry. The owner key lists only Store B's tool. |
| MS-08-13 | `PROVEN — TEST CLUSTER` | Old tool name denied, audit `unauthorized` with no upstream, so the upstream was not called. |
| MS-08-14 | `PROVEN — TEST CLUSTER` | Namespace and all objects in it (workloads, Secrets, Services, claims) gone, both volumes gone, registry row gone, grant gone. |
| MS-08-15 | `PROVEN — TEST CLUSTER` | Store B Ready with 0 restarts. Its `list_orders` succeeded before (08:50Z) and after (08:52Z) the delete. |
| MS-08-16 | `PARTIAL` | Audit rows exist for create, delete, and the denied stale call. No chat row. |

| Status | Count |
| --- | --- |
| `PROVEN — TEST CLUSTER` | 42 |
| `PARTIAL` | 26 |
| `UNTESTED` | 17 |
| Total | 85 |

The counts above include the owner-key pass at 2026-10-25 08:01Z (`store-tool-authorization-*.json`). That pass issued one key to `acceptance-admin` and granted one store tool. The raw key was not printed or stored.

Revision 7 at 08:08Z (`helm-rev7-*.json`, image `urumi-gateway:pvc-labels-20261025T080649Z`) deploys the PVC-label and claim-template fixes. Store A was not reprovisioned: its StatefulSet is still generation 1, and its database claim still has only the two original labels. The key and its single grant are unchanged in the database. The authorized call was not repeated after the upgrade. No matrix status changed.

Store B at 08:18Z (`store-b-preflight-*.json`, `store-b-*.json`): `acceptance-store-b`, id `174c75b6c0ef57e5`, was created once through `POST /admin/stores` and reached Ready in about 90 s on attempt 0. Store A's objects, pods, and volumes and the baseline volumes are unchanged. The only new namespace is `st-174c75b6c0ef57e5` and the only new volumes are its two claims. No key or grant was created. The admin session was renewed with `provision reset-admin-password`.

Isolation at 08:28Z (`store-isolation-*.json`): one new owner key `bfca9a84d7b4380d` was granted only `store_174c75b6c0ef57e5__list_orders`. It listed and called that tool. It was denied on Store B `get_order`, both Store A tools, and `woocommerce__list_orders`. Invalid tokens got 401. Connection probes ran from existing pods with a 3 s timeout, and no diagnostic pods were created. Every source that was denied also has a restrictive egress policy, so the probes show each path is closed without separating source and destination rules. Cross-owner denial is still unit-only, because both stores have one owner.

Store A deletion at 08:51Z (`store-a-delete-*.json`): with approval, one replacement Store B key `013b6aeafe9ea69c` was issued with only Store B's `list_orders`, and unusable key `bfca9a84d7b4380d` was revoked. Store B's `list_orders` succeeded before the delete. One `POST /admin/stores/c4421147ea825bbc/delete` returned 202, and the row reached `Deleted` about 37 s later. Store A's namespace, both volumes, registry row, and grant are gone. Store B's objects and volumes kept their uids, and its `list_orders` still succeeded. The removed tool name was denied. Finding: during normal namespace termination the row showed `That store could not be deleted.`, because every pass counted as an attempt.

Final audit at 09:08Z (`delete-retry-diagnosis-*.json`, `final-audit-*.json`):

- The deletion counter was counting reconcile passes. `_note_delete_incomplete` added one on every pass where the namespace was still terminating, and set `delete_blocked` once it reached `max_attempts`. Retries never stopped.
- The source fix in `apps/gateway/app/stores/service.py` counts only failed API calls, stops the counter at `max_attempts`, and reports a terminating namespace as blocked only after a deadline set at delete time. Four regression tests were added and one test was updated. The suites passed: 43 store, 63 focused, 312 wider, and 34 dashboard tests. Not built or deployed.
- The temporary key file `/tmp/urumi-store-b-key` was removed from the gateway pod without reading it. Key `013b6aeafe9ea69c` stays active with one grant.
- Dashboard store and chat panels pass component tests only. Live create and delete are still API-only.
- Chat is blocked. There is no `urumi-test-chat` Secret, `allowExternalEgress` is false, no store has an order, and Store A is deleted, so a comparison needs a new store.

No matrix status changed.

Parents MS-01 through MS-08 are not passed. No row is `VERIFIED — END-TO-END`.

### Gateway schema-hook fix, revision 10 (2026-10-25, 15:03–15:31Z)

Evidence: `negative-permission-tests-20261025T134737Z.json` (before the fix), `gateway-image-mcp-schema-hook-20261025T145951Z-provenance.json`, `gateway-schema-hook-rollout-20261025T151552Z.json`.

Before the fix, MCP SDK 2.3.0 ran the gateway's `tools/list` handler before dispatching a 2026-07-28 `tools/call` with arguments. That refreshed every enabled upstream before the grant check, so denied calls still contacted upstreams. `apps/gateway/app/main.py` now sets `get_tool_input_schema=_no_input_schema`. `tests/test_call_upstream_contact.py` adds 8 tests. Helm revision 10 deploys `urumi-gateway:mcp-schema-hook-20261025T145951Z` to the gateway and provisioner. The installed `main.py` hash is `fb6f70fe10236ec3` in both pods. Only the two image lines changed in the render, and Store B and Store C kept their pods, generations, and seven volumes.

Live checks after the rollout, key `d81bf7a2acd3750f` (Store B and Store C `list_orders` only):

| Window | Protocol | Audit row | Upstream requests in the window |
| --- | --- | --- | --- |
| Denied Store B `get_order` | 2026-07-28 | 54 `unauthorized`, no upstream | none on Store B, Store C, utility, support, or baseline WooCommerce MCP; none in Store B WordPress |
| Denied `utility__convert_units` | 2026-07-28 | 55 `unauthorized`, no upstream | none |
| Granted Store B `list_orders` | 2026-07-28 | 56 `success`, `store_174c75b6c0ef57e5`, 2645 ms | Store B MCP only: 3 POSTs and one WooCommerce `GET /wp-json/wc/v3/orders` |
| Denied Store B `get_order` | 2025-11-25 (legacy) | 57 `unauthorized`, no upstream | none |

The client's own `tools/list` returned only the two granted tools and contacted all five upstreams, by design.

Rows this supports, with no status change: MS-03f and MS-03g (`PROVEN — TEST CLUSTER`), MS-08-15 (`PROVEN — TEST CLUSTER`, Store B `list_orders` still runs on revision 10), MS-06k (stays `PARTIAL`: no cross-owner key was tried).

Limits: an authorized `tools/list` still contacts every enabled upstream. HTTP logs carry no MCP method or correlation id, so method names are inferred from source and tests. WordPress request times are to the second. The gateway no longer checks `Mcp-Param-*` headers against arguments; no tool here declares `x-mcp-header`.

The temporary key was revoked at 15:31:26Z with `python -m apps.gateway.provision revoke` in the gateway pod. That command writes no audit row. The literal key was removed from the user-level Cursor configuration.

Not reconciled into this ledger yet: `synthetic-orders-20261025T114938Z.json`, `gateway-multistore-verification-20261025T122221Z.json`, and `cursor-mcp-client-verification-20261025T133049Z.json` bear on MS-08-5 through MS-08-10 and MS-08-16. Those rows were not re-reviewed in this pass.

No matrix status changed.

### Dashboard disposable-store lifecycle, revision 10 (2026-10-25)

Evidence: `dashboard-disposable-store-lifecycle-20261025T171638Z.json`. Recorded from manually observed dashboard and terminal checks on `kind-urumi-k8s-test` (context `kind-urumi-k8s-test`), Helm revision 10. Raw command output and exact observation times were not captured.

- The dashboard at `http://127.0.0.1:18081/` returned HTTP 200.
- Store `acceptance-disposable-20261025` was created through the dashboard form with idempotency key `disposable-20261025-01`. The dashboard showed id `d8cd81554d850255`, URL `http://d8cd81554d850255.stores.local`, and Ready. Its namespace was `st-d8cd81554d850255`.
- Before deletion, both claims were Bound at 2Gi: `data-store-db-0` on `pvc-b17bdec0-7333-4d3e-bae5-7a2390bb3347` and `wordpress-data` on `pvc-5136e706-5875-48be-8b0e-be5fbdf0b0d8`.
- The store was deleted with the dashboard Delete action. The dashboard briefly showed Deleting, and the store was gone after refresh.
- Afterwards, namespace `st-d8cd81554d850255` and both volumes returned NotFound.
- Store B (`174c75b6c0ef57e5`, `st-174c75b6c0ef57e5`) and Store C (`5bc7dc01da3ccf38`, `st-5bc7dc01da3ccf38`) namespaces stayed Active. Their claims stayed Bound at 2Gi: Store B `pvc-4ae7dc69-2ae0-45cd-93a9-70b2029fccb7` and `pvc-9cc41993-f39d-4f92-8204-7974b4546771`; Store C `pvc-da403201-662a-4320-a0d8-6ab86528c40e` and `pvc-5d211c94-7e52-4752-a983-c51d2e64b5ef`.
- The final pod check showed the running application pods Ready with zero restarts. `urumi-wordpress-init` was Completed, as expected for the one-time init pod.

This supports the dashboard lifecycle and storage-cleanup demonstration: create, Ready, and delete driven from the dashboard UI rather than direct API calls, with the store's namespace and both volumes removed and Store B and Store C claims preserved. It bears on MS-01a, MS-01b, MS-01d, MS-01i, MS-04b, MS-04c, MS-04d, MS-04h, MS-08-1, and MS-08-11.

It does not by itself establish that MS-01 through MS-08 pass. It does not prove:

- audit-row completeness (no audit rows were read),
- stale-tool rejection (no call with the removed tool name),
- registry and grant cleanup for this store (not checked),
- wrong-owner denial (not attempted),
- Retain reclaim-policy behaviour (not exercised),
- interrupted-deletion recovery (no restart during delete).

Store B and Store C object uids and pod generations were not recorded in this pass, so preservation is shown for their namespaces and claims only.

No matrix status changed. Rows that mention the dashboard form or button not being used are not re-graded here; that needs a separate review against each row's exact definition.

### Deployed state and provenance limits, revision 11 (2026-10-26 read-only audit)

Recorded from read-only `kubectl --context kind-urumi-k8s-test`, `helm history`, and `docker image inspect` output during the audit. No cluster object was created, changed, or deleted, and `kind-urumi-k8s-dev` was not contacted. Raw command output was not saved as an evidence file.

Observed:

- Helm release `urumi`, namespace `default`, revision 11 (`deployed`).
- The test cluster is configured with the gitignored overlay `helm/urumi/values-kind-urumi-k8s-test.local.yaml`. The live image tags, provisioner flags, and admission policy match that overlay. That overlay sets `storeProvisioner.enabled: true` and `storeProvisioner.admit: true`. The defaults in `helm/urumi/values.yaml` remain `false`. The values stored for revision 11 were not read, because `helm get values` can print secrets.
- The `urumi-store-provisioner` Deployment has `STORE_PROVISIONER_ENABLED=true` and `STORE_CLUSTER_RECONCILER=true`.
- Image tags: gateway and provisioner `urumi-gateway:discovery-import-20261026T082546Z`; dashboard `urumi-dashboard:disposable-pkg-20261024`.
- Both images carry `io.urumi.source.revision=b4b813c568ca70b45d28944a44de4e8e770f874d` and `io.urumi.source.dirty=true`.
- The ValidatingAdmissionPolicy and binding `urumi-store-provisioner-scope` matched `helm template` of the current chart with that overlay (expression, match conditions, policy name, `validationActions: [Deny]`). The `matchConstraints` difference was API-server defaulting only (`scope: "*"`, `matchPolicy: Equivalent`).
- The policy status reports CEL type-check warnings because the `persistentvolumes` branch names `spec.claimRef`, which other matched kinds do not have. Type-check warnings do not stop enforcement. The provisioner log since its pod started was empty, so no admission failure was logged.
- Store B and Store C namespaces, Deployments, StatefulSets, init Jobs, and four claims were present and Bound. The read-only audit did not change them.

Not verified:

- The exact source tree behind either image. `dirty=true` means the build context had uncommitted changes; it does not identify them. The gateway image relationship to the current tree is not hash-proven.
- The live dashboard as evidence of current dashboard source. The dashboard image predates the latest dashboard redesign in `dashboard/src`.
- A new denied admission request in this audit. None was sent. The earlier denied dry-run in `provisioner-enabled-20261025T053720Z.json` still stands.
- Any data-safety guarantee beyond the objects listed above.

Correction recorded 2026-10-26: this section originally said MS-06g "stays `BLOCKED`" and that no denied admission request was recorded. Both statements were wrong. `provisioner-enabled-20261025T053720Z.json` records a server dry-run ConfigMap create in `default` as the provisioner ServiceAccount, denied by `urumi-store-provisioner-scope` and not stored, and the test-cluster matrix above already grades MS-06g `PARTIAL`. MS-06g is `PARTIAL`. No other matrix status changed in this section.

## Reconciliation on 2026-10-26

Documentation only. No cluster, Docker, key, store, or order was touched to write this section. It applies evidence files that were already in the staged set but had not been folded into the test-cluster matrix.

### Status changes

| ID | Was | Now | Evidence | Note |
| --- | --- | --- | --- | --- |
| MS-01a | `PARTIAL` | `PROVEN — TEST CLUSTER` | `dashboard-disposable-store-lifecycle-20261025T171638Z.json` | Store `acceptance-disposable-20261025` created through the dashboard form on revision 10 and shown Ready. |
| MS-01d | `PARTIAL` | `PROVEN — TEST CLUSTER` | same | Deleted with the dashboard Delete action; namespace and both volumes NotFound afterwards; Store B and Store C claims stayed Bound. |
| MS-08-5 | `PARTIAL` | `PROVEN — TEST CLUSTER` | `gateway-multistore-verification-20261025T122221Z.json` | One live key, `013b6aeafe9ea69c`, holds exactly both store `list_orders` grants. Store C's grant was added in the dashboard (audit row 39). |
| MS-08-6 | `UNTESTED` | `PROVEN — TEST CLUSTER` | `synthetic-orders-20261025T114938Z.json` | One pending order per store, totals 11.00 and 22.00 USD. Created with a fee-only REST POST from each store's MCP pod, not through the storefront or the `create_order` tool. |
| MS-08-7 | `UNTESTED` | `PARTIAL` | `gateway-multistore-verification-20261025T122221Z.json` | Audit row 42 is one successful dashboard chat by the owner at 12:15Z. The prompt text was not captured. |
| MS-08-8 | `UNTESTED` | `PROVEN — TEST CLUSTER` | same | Rows 40 and 41 call both store `list_orders` tools within 3 s, immediately before chat row 42, same key. Attribution to the chat is by timing and key. |
| MS-08-10 | `PARTIAL` | `PROVEN — TEST CLUSTER` | same | Rows 40 and 41 carry key, user, upstream, tool, time, duration, and outcome. |

Rows reviewed and left unchanged:

- MS-08-9 stays `UNTESTED`. The chat answer text was never observed.
- MS-08-16 stays `PARTIAL`. Create, chat, delete, and denied stale-call rows exist, but across separate runs; `deletion-gap-report-20261025T153542Z.json` also grades it partial.
- MS-01b and MS-01i stay `PARTIAL`. The dashboard showed id, URL, Ready, and Deleting, but creation time and the Provisioning transition were not recorded.
- MS-08-1 and MS-08-11 stay `PARTIAL`. The dashboard create and delete were for the disposable store, not Store A and Store B.
- MS-06g stays `PARTIAL`: one dry-run denial, one kind, one verb.

### Counts

| Status | Before | After |
| --- | --- | --- |
| `PROVEN — TEST CLUSTER` | 42 | 48 |
| `PARTIAL` | 26 | 23 |
| `UNTESTED` | 17 | 14 |
| Total | 85 | 85 |

Parents MS-01 through MS-08 are still not passed as wholes. Overall verdict: `PARTIAL`.

### Supporting evidence outside the matrix

`cursor-mcp-client-verification-20261025T133049Z.json` records an external MCP client (Cursor) listing exactly the two granted store tools and calling both `list_orders` tools through the gateway. The responses showed order 10 at 11.00 USD in Store B and order 10 at 22.00 USD in Store C, matching audit rows 50 and 51 and each store database. The key was owned by the administrator, not by a member. This is a cross-store order comparison through an external client. It is not the dashboard chat comparison (MS-08-9).

### Read-only audit observations on 2026-10-26

Observed with read-only `kubectl --context kind-urumi-k8s-test`, `helm history`, `docker image inspect`, and read-only `psql`, before this documentation pass. Raw output was not saved as an evidence file. No status above depends only on these observations.

- Release `urumi` revision 11 deployed 2026-10-26. Gateway and provisioner `urumi-gateway:discovery-import-20261026T082546Z`. Dashboard `urumi-dashboard:disposable-pkg-20261024`. Support, Utility, and WooCommerce MCP `disposable-pkg-20261024`.
- SHA-256 of all 38 Python and shell files under `apps/` in the running gateway pod matched this tree. The Support, Utility, WooCommerce, and Store B MCP pods' application files also matched. Image labels still say `io.urumi.source.dirty=true`, so the builds are not reproducible from a commit.
- The dashboard image was created 2026-10-24 22:32 IST. The staged dashboard files were modified 2026-10-26 between 14:18 and 16:30 IST. The running dashboard is not the staged dashboard source.
- Audit row 73 (2026-10-26) is a successful `woocommerce__list_products` on the baseline store. Audit row 76 (2026-10-26) is a `chat` success with no tool row around it. Chat tool calling has not been demonstrated on revision 11.
- `gateway_upstreams` held only the two Ready stores' rows. `gateway_stores` showed Store A and the disposable store as `deleted`. No PersistentVolume referenced either deleted namespace. StorageClass `standard` reclaim policy is `Delete`.
- Users: one administrator and one member; the member has no keys. No live key holds WooCommerce, Support, or Utility grants.
- No ingress controller or IngressClass is installed on the test cluster. Access is through port-forwards.
- The gateway reads its chat settings from Secret `urumi-test-chat` (values not read). Gateway egress for chat is limited to `104.18.38.236/32` and `172.64.149.20/32` on TCP 443, and `api.groq.com` resolved to those two addresses on 2026-10-26. If the provider's addresses change, chat egress will fail.
- Live store NetworkPolicies in both store namespaces matched the intent in `docs/multi-store.md`, including the node `172.19.0.2/32` allowance on WordPress port 80 and MCP port 8000.
