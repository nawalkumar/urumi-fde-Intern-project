# Repository findings

This file is a verification notebook for the Urumi AI FDE checkout. It is a starting point for later sessions, not unquestionable truth. A source reading does not prove a live flow. A passing unit test does not prove Kubernetes networking. A recorded user session does not prove the current checkout behaves the same way.

Legend for statuses used below:

| Field | Values |
| --- | --- |
| Implementation | `IMPLEMENTED`, `PARTIAL`, `MISSING`, `NOT_CONFIRMED` |
| Integration | `CONNECTED_END_TO_END`, `PARTIAL`, `DISCONNECTED`, `NOT_CONFIRMED` |
| Automated test | `PASS`, `FAIL`, `NOT_RUN`, `NOT_COVERED` |
| Runtime | `OBSERVED_WORKING`, `OBSERVED_FAILING`, `NOT_OBSERVED`, `BLOCKED` |
| Manual | `PASS_USER_VERIFIED`, `FAIL_USER_VERIFIED`, `NOT_USER_VERIFIED` |
| Demo | `READY`, `NEEDS_WORK`, `BLOCKED`, `NOT_DEMONSTRABLE` |
| Evidence type | `SOURCE`, `AUTOMATED_TEST`, `RUNTIME`, `MANUAL_USER` |

`NOT_REVALIDATED` means an older written claim was kept and was not checked again against this checkout or this cluster.

No issue in this file is `PASS_USER_VERIFIED`. The 2026-10-23 owner statement in `PROJECT_GAP_TRACKER.md` is `OWNER_REPORTED_MANUAL_TEST` with unnamed pages. It is not owner acceptance of a named checklist.

## A. Repository identity and inspection metadata

| Item | Value |
| --- | --- |
| Repository path | `/home/naval/project/urumi-project` |
| Branch | `submission` |
| HEAD | `b4b813c568ca70b45d28944a44de4e8e770f874d` |
| HEAD subject | `feat: finalize MCP gateway discovery and multi-store submission` |
| Tracking | `git status` reported `submission...origin/submission [ahead 1]`. No fetch was performed. |
| Inspection window | 2026-10-24 14:22–14:26 IST (`2026-10-24T08:52:00Z` at the start of the runtime read) |
| Revalidated against this checkout | Yes for the source paths and tests named in section F. Historical module passes were not re-executed. |
| Findings file before this pass | Absent. `docs/verification/` did not exist. Nothing older was erased from this file. |

### Working tree at inspection

`git status` showed a dirty tree. Nothing was staged. This inspection did not commit, stage, checkout, or discard those changes. The automated tests in section F ran against this dirty tree, not a clean `HEAD`.

Tracked modifications (`git diff --stat`: 19 files, 991 insertions, 41 deletions):

- `apps/gateway/Dockerfile`
- `apps/gateway/app/admin_api.py`
- `apps/gateway/app/chat.py`
- `apps/gateway/app/chat_provider.py`
- `apps/gateway/app/discovery.py`
- `apps/gateway/app/dispatch.py`
- `apps/gateway/app/stores/cluster.py`
- `apps/gateway/app/stores/routing.py`
- `apps/gateway/app/stores/service.py`
- `apps/utility_mcp/app/main.py`
- `dashboard/Dockerfile`
- `docs/threat-model.md`
- `tests/store_cluster.py`
- `tests/test_chat.py`
- `tests/test_chat_provider.py`
- `tests/test_discovery_policy.py`
- `tests/test_store_kube.py`
- `tests/test_store_routing.py`
- `tests/test_stores.py`

Untracked, left untouched:

- `PROJECT_GAP_TRACKER.md`
- `scripts/image_provenance.py`
- `tests/test_image_provenance.py`
- `tests/test_utility_mcp.py`

`PROJECT_GAP_TRACKER.md` says its own pass did not change application code. That statement is historical. The working tree now also contains the application and test diffs listed above. Those diffs were already present when this inspection started.

### Prior documents consulted before source search

- `README.md`
- `docs/assignment-tracker.md`
- `docs/design.md`
- `docs/threat-model.md`
- `docs/multi-store.md` (indexed from the tracker and README; the lifecycle claims used below were checked against source and the gap tracker, not by re-reading every paragraph)
- `docs/baseline-verification.md` (older than the tracker; not re-executed)
- `PROJECT_GAP_TRACKER.md` (untracked audit notebook dated 2026-10-23)
- `helm/urumi/README.md` was not fully re-read. Chart templates and values files were.

## B. Assignment requirements index

No standalone official assignment PDF, brief, or rubric file was found in this repository. The working requirement text is `docs/assignment-tracker.md`. Wording below is taken from that file's Requirement and Done-when lines. It is not an external official document, and it must not be treated as one.

`docs/threat-model.md` section "Assignment questions" answers four questions. Those are recorded as Q1–Q4. They are not extra product modules.

`PROJECT_GAP_TRACKER.md` classifies `VPS-01` as an optional enhancement. That conflicts with the assignment tracker, which lists Module 11 in the required module sequence and marks it Partial. This file follows the assignment tracker for mandatory versus bonus, and records the conflict under `DOC-03`.

### Mandatory modules

| ID | Name | Requirement meaning in the tracker |
| --- | --- | --- |
| M1 | Dev cluster runnable | Pods for gateway, dashboard, three MCP servers, WordPress, and both databases Ready; ingress controller Ready; in-cluster `GET` gateway `/health` returns `{"status":"ok"}`; three PVC volume IDs unchanged. |
| M2 | Storefront checkout | Browser adds an item and checks out with cash on delivery. The order is visible in WooCommerce admin. Store database and WordPress files stay on persistent volumes. |
| M3 | WooCommerce MCP server | A server written in this repo, MCP over Streamable HTTP, not an existing WooCommerce plugin. Tools: list products, create product, list orders, get order, update order status. Only the gateway can call it. |
| M4 | Gateway tool surface | One MCP endpoint. Listing returns every enabled server's tools. A call routes to the right server. Same tool names on two servers stay unambiguous. A slow or dead upstream does not hide the others. At least three upstreams. |
| M5 | Admin servers, members, keys | Admin sees Connected, Unreachable, or Disabled and the tools. Admin can add, edit, disable, or remove a server and can add members and issue or revoke keys. A member can create their own key. Credentials are not echoed. Keys are stored hashed. |
| M6 | Audit log | Every tool call is logged with who, which server, which tool, when, duration, and outcome. The dashboard shows that log. Chat calls are attributed to the logged-in user. |
| M7 | Chat agent | Dashboard chat uses an LLM, calls tools only through the gateway, shows which tools it called, and acts as the logged-in user. The model key is a Kubernetes Secret. |
| M8 | External client and control demo | User B connects with only the gateway URL and their key, sees enabled tools, answers the orders question, and uses two upstreams. Revoke rejects the next call. Disabling a server removes its tools. Killing one upstream pod leaves the others working and marks that server Unreachable. |
| M9 | Upstream network isolation | WooCommerce MCP is not reachable from outside the cluster. Only the gateway can call it. The same rule should hold for Support and Utility. |
| M10 | Stable Ingress URLs | Storefront and dashboard are exposed through Ingress with stable URLs. Local and production differ only by Helm values. Laptop can open the three hosts on port 80. |
| M11 | One chart for a production-like VPS | The same chart installs on local Kubernetes and on a production-like VPS. Differences are values only. Probes, requests, and limits exist on every workload. Secrets are Kubernetes Secrets. |
| M12 | Definition of done, one sitting | The demo order in the tracker: storefront COD checkout, user A chat creates a product and lists today's orders, user B external client, revoke, disable, pod delete, audit attribution. |

### Bonus

| ID | Name | Requirement meaning in the tracker |
| --- | --- | --- |
| BONUS-STORES | Multiple stores | Optional provisioning. The tracker section "Bonus — multiple stores" says it was implemented in the checkout on 2026-10-20, not runtime-verified, and that both provisioner flags stay false. |

### Assignment questions (answers, not build modules)

| ID | Question as headed in `docs/threat-model.md` |
| --- | --- |
| Q1 | What a member can do with shared upstream credentials they never see. |
| Q2 | Why client API tokens are not forwarded to upstream servers. |
| Q3 | How malicious upstream tool descriptions could manipulate the model, and what mitigations exist. |
| Q4 | What one team member can and cannot do to another. |

## C. Component and entry-point map

Verified against the working tree on 2026-10-24 unless a row says otherwise.

| Area | Path | Entry points |
| --- | --- | --- |
| Gateway process | `apps/gateway/app/main.py` | `create_app`. MCP handlers `on_list_tools`, `on_call_tool`. Routes `GET /admin/status`, `GET /admin/discovery`, `POST /admin/discovery/search`, `POST /discovery/search`. |
| MCP HTTP mount | `apps/common/http.py` | `build_http_app` mounts Streamable HTTP at `/mcp` (`json_response=True`, `stateless_http=True`). `GET /health` is outside the API-key guard. |
| Upstream merge | `apps/gateway/app/catalog.py` | `merge_upstreams`, `refresh_registry`. |
| Registry and collisions | `apps/gateway/app/registry.py` | `Registry.set_enabled`, `Registry.call`, `_unique_upstreams`, `_colliding_public_names`. Public name `{upstream_id}__{tool}`. Duplicate message built near the `Duplicate public tool name` return. Disabled call returns `Upstream <id> is disabled.` |
| Discovery | `apps/gateway/app/discovery.py` | Grant-filtered list and lexical search. Ranking does not add a grant. |
| Dispatch | `apps/gateway/app/dispatch.py` | `ToolDispatcher.execute_tool`. Checks `authorize`, `permits`, then `store_call_allowed`, then `Registry.call`. |
| Auth and keys | `apps/gateway/app/keys.py`, `apps/gateway/app/auth.py`, `apps/gateway/app/http_auth.py` | `generate_key`, `hash_key` (SHA-256 of the whole `urk_<id>.<secret>` string), `parse_raw_key`. `Authenticator.authorize`, `permits`, `visible_names`. |
| Grants | `apps/gateway/app/permissions.py`, `apps/gateway/app/store.py` | Exact public names. `KeyStore.revoke`, `revoke_permission`, `permits`. PostgreSQL implementation is `PostgresStore` in `store.py`. |
| Admin API | `apps/gateway/app/admin_api.py` | `POST /admin/login`, `POST /admin/logout`, `GET /admin/me`, user create/enable/disable/role, `GET/POST /admin/keys`, revoke key, grant and revoke permission, upstream list/create/update/delete, `GET /admin/audit`. |
| Admin provision CLI | `apps/gateway/provision.py` | `python -m apps.gateway.provision create-admin` (documented in `README.md`). |
| Audit | `apps/gateway/app/audit.py` | `Auditor.record` writes after the call. Outcomes constrained in `store.py`. |
| Chat | `apps/gateway/app/chat.py`, `apps/gateway/app/chat_api.py`, `apps/gateway/app/chat_provider.py` | `ChatService.answer`. `POST /admin/chat` uses the session user. A body `user_id` is not the selector (`chat.py` module docstring). Missing provider returns HTTP 503. |
| Store ownership gate | `apps/gateway/app/stores/routing.py` | `store_block_reason`, `store_call_allowed`. Empty or mismatched `owner_user_id` returns `store_not_owner`. |
| Store API | `apps/gateway/app/stores/api.py` | `GET/POST /admin/stores`, get, deletion status, `POST .../delete`, `POST .../retry`. |
| Store service | `apps/gateway/app/stores/service.py`, `stores/cluster.py`, `stores/manifests.py`, `stores/provisioner.py` | Row is written before cluster apply. `HttpClusterClient` is namespace-limited to `st-<id>`. Helm renders the provisioner only when both flags are true. |
| Support MCP | `apps/support_mcp/app/main.py` | `create_app`. Tool `search_notes`. |
| Utility MCP | `apps/utility_mcp/app/main.py` | `create_app`. Tool `convert_units`. `_finite_number` rejects non-finite values before conversion. |
| WooCommerce MCP | `apps/woocommerce_mcp/app/main.py`, `app/tools.py`, `app/client.py` | `register_tools`: `list_products`, `create_product`, `list_orders`, `get_order`, `update_order_status`, `create_order`. REST client, not a WooCommerce MCP plugin. |
| Dashboard UI | `dashboard/src/App.tsx`, `dashboard/src/api.ts` | Status view calls `GET /admin/status` with a pasted Bearer key (`fetchStatus`). |
| Dashboard admin | `dashboard/src/admin/AdminPanel.tsx`, `adminClient.ts` | `serverStatus` returns Connected, Unreachable, or Disabled. `adminRequest` talks to `/admin`. |
| Dashboard chat, discovery, stores | `dashboard/src/admin/ChatPanel.tsx`, `DiscoveryPanel.tsx`, `StorePanel.tsx` | Session UI. Chat activity is rendered from the chat response. |
| Status labels | `dashboard/src/status.ts` | `connectionLabel`. |
| Helm chart | `helm/urumi/` | `Chart.yaml`, `values.yaml`, `values-dev.yaml`, `values-vps.yaml`. Templates: `gateway-deployment.yaml` (TCP liveness, HTTP `/health` startup and readiness), `ingress.yaml`, `networkpolicy.yaml`, `secret.yaml`, store and provisioner templates. |
| Compose | `compose.yaml` | Services `db`, `wordpress`, `wordpress-init`, `support-mcp`, `utility-mcp`, `woocommerce-mcp`, `gateway-db`, `gateway`, `gateway-key`. |
| CI | `.github/workflows/ci.yml` | `uv run pytest` including a disposable Postgres image pull, and dashboard `npm test` plus `npm run build`. Not queried for this HEAD. |
| Tests | `tests/` | See section F. `tests/conftest.py` `launch_app` binds `127.0.0.1` on an ephemeral port. |

## D. Requirement verification matrix

Demo `READY` is not used for a module whose live proof is an older image, an older date, or an in-process test. The running cluster can be shown as itself. It is not this checkout.

### M1 — Dev cluster runnable

- Mandatory.
- Implementation: `IMPLEMENTED` (chart and probes exist).
- Integration: `PARTIAL`. The running stack is up. It is not built from this checkout (`K8S-01`).
- Automated test: `NOT_COVERED` for live pods. Chart probe shape is source, not a cluster test.
- Runtime: `OBSERVED_WORKING` for laptop-path health, Ready pods, Ready ingress controller, and the three historical PVC IDs. In-cluster `GET /health` was `NOT_RUN`.
- Manual: `NOT_USER_VERIFIED`. Tracker pass on 2026-10-18 is `NOT_REVALIDATED` as a user demo.
- Demo: `NEEDS_WORK` for a demo of this checkout. The already-running cluster can show health and Ready pods.
- Evidence: section G. Safest next step: from a gateway pod, `GET` its own `/health`, without changing pods.

### M2 — Storefront checkout

- Mandatory.
- Implementation: `IMPLEMENTED` in the chart (WordPress, MariaDB, PVC templates) and historical store data.
- Integration: `PARTIAL`. `http://store.local/` returned HTTP 200 on 2026-10-24. No cart or checkout was performed.
- Automated test: `NOT_COVERED` for a browser checkout.
- Runtime: `NOT_OBSERVED` for checkout. Storefront HTTP 200 only.
- Manual: `NOT_USER_VERIFIED`. Order 20 on 2026-10-18 is `NOT_REVALIDATED`. The tracker says the WooCommerce admin screen was not reopened.
- Demo: `NEEDS_WORK`.
- Safest next step: read-only confirmation of existing order rows, only with explicit approval. Do not place a new order as part of inventory.

### M3 — WooCommerce MCP server

- Mandatory.
- Implementation: `IMPLEMENTED`. `apps/woocommerce_mcp/app/tools.py` `register_tools` includes the five required tools plus `create_order`.
- Integration: `PARTIAL` in source (gateway namespaces those tools). Live tool execution was not called.
- Automated test: `PASS` in `tests/test_woocommerce.py` as part of the 2026-10-24 in-memory run. Those tests use scripted HTTP, not the live store.
- Runtime: `NOT_OBSERVED`. WooCommerce MCP pod is Running with image `urumi-woocommerce-mcp:k8s-test`. `GET /health` on that upstream was `NOT_RUN`.
- Manual: `NOT_USER_VERIFIED`. 2026-10-18 tool list is `NOT_REVALIDATED`.
- Demo: `NEEDS_WORK`.
- Safest next step: read-only `GET /health` from the gateway pod to the WooCommerce MCP Service. Do not call `create_product` or `create_order`.

### M4 — Gateway tool surface

- Mandatory.
- Implementation: `IMPLEMENTED`. `POST /mcp`, namespaced tools, collision exclusion, per-upstream timeout and list failure.
- Integration: `PARTIAL`. In-process tests connect list and call. The live gateway image is not this tree, and the Utility database flag was not re-read (`K8S-03`).
- Automated test: `PASS` for `tests/test_gateway_mcp.py`, `tests/test_upstream_controls.py`, and collision cases in `tests/test_tool_discovery.py` and `tests/test_discovery_policy.py` (included in the 299).
- Runtime: `NOT_OBSERVED` for `tools/list` or `tools/call`.
- Manual: `NOT_USER_VERIFIED`. Module 4's 2026-10-18 three-upstream pass is `NOT_REVALIDATED`.
- Demo: `NEEDS_WORK`.
- Safest next step: do not enable Utility and do not call tools until a separate approval. Re-read only the `utility` enabled flag if a read-only query is approved.

### M5 — Admin servers, members, keys

- Mandatory.
- Implementation: `IMPLEMENTED` for roles, hashed keys, revoke, upstream CRUD, and the three status words (`serverStatus` in `dashboard/src/admin/AdminPanel.tsx`).
- Integration: `PARTIAL`. Admin routes call the gateway store in-process. The cluster dashboard bundle is not this checkout's `dashboard/dist` (`RUNTIME-01`). Browser add, edit, remove, and invite remain historically unconfirmed.
- Automated test: `PASS` for gateway admin tests in the 299, and dashboard `AdminPanel.test.tsx` (4 tests) in the Vitest run. Vitest mocks HTTP.
- Runtime: `NOT_OBSERVED` for a logged-in admin session.
- Manual: `NOT_USER_VERIFIED`. Owner statement does not name pages.
- Demo: `NEEDS_WORK`.
- Safest next step: owner writes which admin pages were used on 2026-10-23, against which URL.

### M6 — Audit log

- Mandatory.
- Implementation: `IMPLEMENTED`. `Auditor.record` and `GET /admin/audit`. Columns described in the tracker exist in `store.py` (`occurred_at`, `actor`, `tool_name`, `outcome`, `upstream_id`, `duration_ms`, `user_id`, `target`).
- Integration: `PARTIAL`. Dispatch writes a row after the call in source. Live rows were not read on 2026-10-24.
- Automated test: `PASS` where permission and chat tests assert audit outcomes (inside the 299). Not a live PostgreSQL audit read.
- Runtime: `NOT_OBSERVED`.
- Manual: `NOT_USER_VERIFIED`. The tracker says the audit screen was not reopened after Module 1.
- Demo: `NEEDS_WORK`.
- Known limit: `RES-03`. A failed audit insert does not roll back the tool result.
- Safest next step: owner opens the audit screen, or a read-only row listing that does not print secrets.

### M7 — Chat agent

- Mandatory.
- Implementation: `IMPLEMENTED`. `ChatService.answer` lists tools for the signed-in user's live keys and executes through `ToolDispatcher`. `POST /admin/chat` returns 503 when no provider is configured.
- Integration: `PARTIAL`. Source path is one dispatcher. Live Groq was not called. Secret `urumi-dev-chat` is present by name only.
- Automated test: `PASS`. `tests/test_chat.py` and `tests/test_chat_provider.py` in the 299. `ChatPanel.test.tsx` (5 tests) mocks the API.
- Runtime: `NOT_OBSERVED` for a model call.
- Manual: `NOT_USER_VERIFIED`. 2026-10-18 Groq products 17 and 19 are `NOT_REVALIDATED`. The 2026-10-19 partial-response check was scripted inside an image, not Groq.
- Demo: `NEEDS_WORK`.
- Safest next step: none until the owner wants a live model call. Do not print the Secret.

### M8 — External client and control demo

- Mandatory.
- Implementation: `IMPLEMENTED` for SDK Streamable HTTP, revoke-to-401, disable, and upstream isolation in source and tests.
- Integration: `PARTIAL`. Tests use `tests/mcp_session.py` against an ephemeral local app. No external client was connected on 2026-10-24. Host `127.0.0.1:8000` refused connections (`RUNTIME-02`).
- Automated test: `PASS` for auth, revoke, disable, and isolation tests in the 299.
- Runtime: `NOT_OBSERVED`.
- Manual: `NOT_USER_VERIFIED`. Module 8 and Module 12 client sessions are `NOT_REVALIDATED`. The tracker says Claude Code, Cursor, and MCP Inspector were not the recorded client.
- Demo: `NEEDS_WORK`.
- Safest next step: do not replay a stored bearer. A new demo needs a key issued on purpose.

### M9 — Upstream network isolation

- Mandatory.
- Implementation: `IMPLEMENTED` as chart policy text. Services are ClusterIP. `helm/urumi/templates/ingress.yaml` does not target the MCP servers. `networkpolicy.yaml` allows MCP ingress from the gateway plus probe CIDRs.
- Integration: `PARTIAL`. Policy objects exist on the cluster. Packet enforcement was not repeated. The Calico cutover is not in the chart.
- Automated test: `PASS` for `tests/test_networkpolicy_intent.py` inside the 299. That test reads policy text. It does not send packets.
- Runtime: `NOT_OBSERVED` for a cross-pod timeout. Policy objects and their `ipBlock` values were read (`K8S-02`). CNI plugin was not inspected.
- Manual: `NOT_USER_VERIFIED`. Module 9 on 2026-10-18 is `NOT_REVALIDATED`.
- Demo: `NEEDS_WORK`.
- Safest next step: read-only CNI identity (`crictl` or the loaded conflist) with no file moves. Packet tests need explicit approval.

### M10 — Stable Ingress URLs

- Mandatory.
- Implementation: `IMPLEMENTED`. `values-dev.yaml` hosts `store.local`, `dashboard.local`, `gateway.local`. Forwarder script `scripts/dev-port80-forward.py`.
- Integration: `CONNECTED_END_TO_END` for the laptop HTTP path to the running Ingress. Not proof that the served apps are this checkout.
- Automated test: `NOT_COVERED` for live DNS and port 80. `tests/test_baseline_smoke.py` uses a local fake server and was part of the 299.
- Runtime: `OBSERVED_WORKING` for status codes only: `store.local/` 200, `dashboard.local/` 200, `gateway.local/` 401, `gateway.local/health` 200 with body `{"status":"ok"}`.
- Manual: `NOT_USER_VERIFIED` for a GUI click-through.
- Demo: `NEEDS_WORK` for a source-faithful demo. URL reachability of the current cluster can be shown.
- Safest next step: none for status codes. A browser walkthrough is a separate manual session.

### M11 — Production-like VPS chart

- Mandatory in the assignment tracker. The gap tracker calls the same gap optional (`DOC-03`).
- Implementation: `PARTIAL`. `values-vps.yaml` is a worksheet. This pass did not re-count `replace-me`. The tracker says the 2026-10-18 render still contained placeholders and that no VPS was deployed.
- Integration: `DISCONNECTED`. No VPS, registry, DNS, or TLS install is recorded, and this pass did not deploy.
- Automated test: `NOT_RUN` for `helm lint` and `helm template` of `values-vps.yaml` in this inspection. `tests/test_store_manifests.py` does run `helm template` for store RBAC and was inside the 299. That is not a VPS install.
- Runtime: `NOT_OBSERVED`.
- Manual: `NOT_USER_VERIFIED`.
- Demo: `NOT_DEMONSTRABLE`.
- Safest next step: `helm lint` and `helm template` locally, without `helm upgrade`.

### M12 — Definition of done, one sitting

- Mandatory.
- Implementation: `IMPLEMENTED` as the composition of M2–M10 in source.
- Integration: `PARTIAL`. Historical sitting was 2026-10-18 on images that are not this tree. Today's cluster is up and the URLs answer. The control steps were not repeated.
- Automated test: `NOT_COVERED` as one sitting. Pieces have in-process tests.
- Runtime: `NOT_OBSERVED` for the demo script.
- Manual: `NOT_USER_VERIFIED`. The 2026-10-18 table is `NOT_REVALIDATED`.
- Demo: `NEEDS_WORK`.
- Safest next step: do not start a two-user demo until image provenance and the Utility flag are decided.

### BONUS-STORES — Multiple stores

- Bonus.
- Implementation: `IMPLEMENTED` in source, with flags off. `helm/urumi/values.yaml` `storeProvisioner.enabled` and `storeProvisioner.admit` are both `false`.
- Integration: `DISCONNECTED` from the live cluster. No `st-` namespace exists. The public gateway does not apply store objects unless it is the cluster reconciler.
- Automated test: `PASS` for in-process store tests inside the 299 (`tests/test_stores.py`, `tests/test_store_kube.py`, `tests/test_store_routing.py`, `tests/test_store_manifests.py`). `tests/store_cluster.py` is a fake, not a Kubernetes API server.
- Runtime: `NOT_OBSERVED`. `BLOCKED` for a real lifecycle on the existing Kind contexts.
- Manual: `NOT_USER_VERIFIED`. The owner said store creation was not manually tested.
- Demo: `BLOCKED`.
- Safest next step at the time of this section: do not create a store. A later pass on 2026-10-24 is recorded in `docs/verification/multi-store-acceptance.md`. The statuses in this section were not rewritten. That later pass did not create a store and did not use `kind-urumi-k8s-dev` or `kind-urumi-k8s-test`.

### Q1–Q4 — Assignment answers

- Implementation: `IMPLEMENTED` as prose in `docs/threat-model.md`. Q1's current paragraph matches `ToolDispatcher.execute_tool` and `store_block_reason` as read on this tree. The file is modified and uncommitted (`DOC-01`).
- Integration: `NOT_CONFIRMED` as a live member demonstration.
- Automated test: `PASS` for the grant and discovery tests cited by Q1, except `tests/test_postgres_permissions.py`, which was `NOT_RUN`.
- Runtime: `NOT_OBSERVED`.
- Manual: `NOT_USER_VERIFIED`.
- Demo: `NEEDS_WORK`.

## E. End-to-end flow map

### 1. Admin registers an upstream and discovers its tools

- Source: `POST /admin/upstreams` and `GET /admin/upstreams` in `admin_api.py`. Registry refresh compensation is described in `docs/design.md` and covered by `tests/test_admin_api.py`. Admin discovery is `GET /admin/discovery`.
- Integration: wired in the gateway process. Not exercised on the live admin session.
- Automated test: `PASS` inside the 299 for admin API and discovery policy tests. Not re-listed test-by-test here.
- Runtime: `NOT_OBSERVED`.
- Manual: `NOT_USER_VERIFIED`. Browser add/edit/remove were left open in the tracker.

### 2. Authenticated external MCP client through the gateway

- Source: `ApiKeyGuard` plus `POST /mcp` via `build_http_app`. Tests open `mcp.client.streamable_http.streamable_http_client` from `tests/mcp_session.py`.
- Integration: in-process only during this inspection. `127.0.0.1:8000` was down. `http://gateway.local/mcp` was not called.
- Automated test: `PASS` for gateway MCP and auth tests in the 299.
- Runtime: `NOT_OBSERVED`.
- Manual: `NOT_USER_VERIFIED`. `README.md` says no live external client was exercised for that document. The tracker records an official MCP client on 2026-10-18 (`NOT_REVALIDATED`).

### 3. `tools/list` returns only tools authorized for that key

- Source: `on_list_tools` calls `discover`, which uses `Authenticator.visible_names` and drops names the registry is not advertising. Store tools also pass `store_block_reason`. There is no wildcard.
- Integration: source and in-process tests agree. The 2026-10-18 zero-grant listing in Module 5 is an older behavior. The current `execute_tool` and list path require an exact grant. That older sitting is `CONFLICTING` with current source if someone treats it as the current rule.
- Automated test: `PASS`. `tests/test_tool_permissions.py` and `tests/test_tool_discovery.py` were in the 299.
- Runtime: `NOT_OBSERVED`. The 2026-10-19 "Permission denied" check is `NOT_REVALIDATED`.
- Manual: `NOT_USER_VERIFIED`.

### 4. `tools/call` routes to the intended upstream and enforces permissions

- Source: `on_call_tool` delegates to `ToolDispatcher.execute_tool`, which checks the seal, exact grant, and store ownership before `Registry.call`.
- Integration: one dispatcher is also what chat uses (`CHAT-01`).
- Automated test: `PASS` in the 299, including `tests/test_chat.py::test_chat_rejects_another_users_store_tool_when_the_key_has_a_grant`.
- Runtime: `NOT_OBSERVED`.
- Manual: `NOT_USER_VERIFIED`.

### 5. Tool-name collisions cannot route ambiguously

- Source: `_colliding_public_names` omits a public name that appears more than once. `Registry.call` returns `Duplicate public tool name <name>. Conflicting registration excluded.` Different upstream ids keep different public names. An id containing `__` is rejected by `_unique_upstreams`.
- Integration: in-process. No live duplicate was created.
- Automated test: `PASS` for the collision tests in `tests/test_upstream_controls.py`, `tests/test_discovery_policy.py`, and `tests/test_tool_discovery.py`.
- Runtime: `NOT_OBSERVED`. `BLOCKED` on the protected Kind clusters.
- Manual: `NOT_USER_VERIFIED`. Absence of a live collision test is not a defect by itself (`COLL-01`).

### 6. Chat uses the signed-in user and the gateway's authorized tools

- Source: `POST /admin/chat` resolves the session user and calls `ChatService.answer`. The module docstring says a user id in the request is ignored. Tool execution goes through `ToolDispatcher`.
- Integration: source path is connected. Live provider path was not used. Vite on port 5173 is this checkout's dashboard source (`RUNTIME-01`), and it was not driven through a chat turn.
- Automated test: `PASS` for `tests/test_chat.py` in the 299 and `ChatPanel.test.tsx` in Vitest.
- Runtime: `NOT_OBSERVED`.
- Manual: `NOT_USER_VERIFIED`.

### 7. WooCommerce product and order tools

- Source: six tools in `register_tools`. `create_order` creates an unpaid pending order in that process. Idempotency state is process memory (`docs/design.md`).
- Integration: tools call the WooCommerce REST client when consumer credentials are configured. Live REST was not called.
- Automated test: `PASS` for `tests/test_woocommerce.py` in the 299, with scripted HTTP.
- Runtime: `NOT_OBSERVED`.
- Manual: `NOT_USER_VERIFIED`.

### 8. API keys issued, stored hashed, authenticated, and revoked

- Source: `generate_key` returns the raw key once. `hash_key` stores SHA-256 hex of the whole string. Revoked, unknown, and malformed keys share one 401 body (`README.md`, `docs/design.md`). This pass confirmed `hash_key` and `generate_key` in source and did not issue a key.
- Integration: admin issue/revoke routes exist. Live issue/revoke was not performed.
- Automated test: `PASS` for `tests/test_gateway_auth.py` and `tests/test_tool_permissions.py` in the 299. Disposable Postgres persistence tests were `NOT_RUN`.
- Runtime: `NOT_OBSERVED`.
- Manual: `NOT_USER_VERIFIED`.

### 9. Disabling an upstream removes or blocks its tools

- Source: `Registry.set_enabled(False)` drops that registration. A later call returns `Upstream <id> is disabled.` and does not contact it. Discovery omits it.
- Integration: in-process tests. Live Utility flag is a separate database override (`K8S-03`, `NOT_REVALIDATED`). ConfigMap `UTILITY_MCP_ENABLED` was `true` on 2026-10-24.
- Automated test: `PASS` in `tests/test_upstream_controls.py` inside the 299.
- Runtime: `NOT_OBSERVED` for a disable call. The stored flag was not queried.
- Manual: `NOT_USER_VERIFIED`.

### 10. Upstream failure is isolated and visible

- Source: a failed or timed-out list skips that upstream only. `serverStatus` and `connectionLabel` map listing failure to Unreachable.
- Integration: in-process. Live pod deletion was not repeated.
- Automated test: `PASS` for `tests/test_gateway_mcp.py` timeout and unavailable-upstream cases, and dashboard status tests that mock a listing failure.
- Runtime: `NOT_OBSERVED`. All three MCP pods were Ready, which does not prove isolation.
- Manual: `NOT_USER_VERIFIED`. Module 4 and Module 12 pod-delete notes are `NOT_REVALIDATED`.

### 11. Calls and authorization failures in the audit log

- Source: `execute_tool` writes `success`, `error`, `timeout`, `upstream_failure`, or `unauthorized` with key id and user id. Authentication failures use actor `unauthenticated` (`docs/design.md`). Arguments are not audit fields.
- Integration: in-process assertions. Live audit table was not opened.
- Automated test: `PASS` inside the permission and chat tests in the 299.
- Runtime: `NOT_OBSERVED`.
- Manual: `NOT_USER_VERIFIED`.
- Limit: `RES-03`.

### 12. Optional multi-store provisioning, ownership, routing, and cleanup

- Source: create persists a provisioning row before cluster apply. Ownership checks deny another user. Deletion drops that store's grants. `namespace_absent` is HTTP 404 only; HTTP 202 leaves the row deleting. This matches the current `routing.py` and the store tests.
- Integration: fake cluster only. Helm will not render the provisioner while `admit` is false. No store namespace is on the dev cluster.
- Automated test: `PASS` in the 299 for the store files. Not a Kind proof.
- Runtime: `NOT_OBSERVED`. `BLOCKED` on `kind-urumi-k8s-dev` and `kind-urumi-k8s-test`.
- Manual: `NOT_USER_VERIFIED`.

## F. Test inventory and results

### Commands actually run on 2026-10-24

Working directory `/home/naval/project/urumi-project` unless noted. Both commands executed the dirty working tree.

1. `uv run pytest -q --tb=line --ignore=tests/test_postgres_permissions.py --ignore=tests/test_reset_admin_password_postgres.py`

   Result: 299 passed, exit 0, 49.02 seconds. Failed 0. This count is the current result. It is not a copy of an older 68-pass or 218-pass note.

2. `npm test` in `dashboard/` (`vitest run`, Vitest 3.2.7).

   Result: 6 files, 34 tests, all passed, started 14:24:40 local time, duration 14.38 seconds.

3. `uv run pytest --collect-only -q tests/test_postgres_permissions.py tests/test_reset_admin_password_postgres.py`

   Result: 17 tests collected, exit 0. They were not executed.

### Intentionally not run

| Command or file | Why |
| --- | --- |
| `tests/test_postgres_permissions.py` (9 tests) | Starts a new `postgres:16-alpine` container. Not run, to avoid creating containers. |
| `tests/test_reset_admin_password_postgres.py` (8 tests) | Same Docker fixture pattern. Not run. |
| `uv run pytest` with no ignores | Would include those 17 tests. |
| `npm run build` | Not required to inventory, and it writes `dashboard/dist`. |
| `helm lint`, `helm template`, `helm upgrade` | Not run as standalone commands. One store-manifest test inside the 299 calls `helm template` locally and passed. It does not install. |
| `scripts/disposable-store-acceptance.sh` | Would target a cluster. Not run. |
| Live `tools/call`, login, key issue, store create, pod delete | Mutations or secret-bearing calls. Not run. |
| Dashboard browser click-through | Not run. Curl status codes only. |

`tests/test_calico_stage2.py` was included in the 299. It mocks process execution. It was not pointed at the live cluster. `tests/test_baseline_smoke.py` uses a local fake HTTP server. `tests/test_image_provenance.py` uses temporary git directories.

### What the suites cover

Gateway pytest files under `tests/`:

| File | Coverage |
| --- | --- |
| `test_gateway_mcp.py` | Namespaced list, dispatch, timeout isolation, unavailable upstream. |
| `test_gateway_auth.py` | Rejected keys do not reach an upstream. |
| `test_tool_permissions.py` | Exact grants, aliases, empty grants, revoked key, colliding name. |
| `test_tool_discovery.py` | List follows current grants. Collisions, disabled upstreams, and failures are omitted. |
| `test_discovery_policy.py` | Search policy, admin collision naming, two store names stay distinct. |
| `test_upstream_controls.py` | Enable flags, disable, duplicate ids, duplicate public names, shared original names, held snapshots. |
| `test_admin_api.py` | Admin mutations and failed registry-refresh reporting. |
| `test_admin_status.py` | Open versus required status permission. |
| `test_chat.py` | Scripted chat turns, two upstreams, partial and timeout results, cross-user store tool denial. |
| `test_chat_provider.py` | Provider failures and redaction. |
| `test_woocommerce.py` | Tool registration, validation, unpaid pending `create_order`, scripted REST. |
| `test_utility_mcp.py` | Finite-number rejection for `convert_units`. Untracked. Included in the 299. |
| `test_ssrf.py` | Untrusted URL policy. |
| `test_secretsbox.py` | Upstream credential sealing. Responses expose presence, not the secret. |
| `test_stores.py` | Create, idempotency, ownership, retry, delete, gateway process does not apply cluster objects. |
| `test_store_kube.py` | Scripted HTTP cluster client, 202 versus 404, protected namespaces. |
| `test_store_routing.py` | Store tool routing and grant denial, including a key with no owner. |
| `test_store_manifests.py` | Rendered store object shape and provisioner RBAC via local `helm template`. |
| `test_networkpolicy_intent.py` | Chart policy text. |
| `test_reset_admin_password.py` | In-memory admin password reset. Included in the 299. |
| `test_provision_output.py` | Provision command output handling. |
| `test_verify_member_mcp.py` | Member MCP verification helper. |
| `test_baseline_smoke.py` | Local fake health and storefront checks for `scripts/baseline_smoke.py`. |
| `test_calico_stage2.py` | Stage 2 planner refusals. No live mutation. |
| `test_image_provenance.py` | Provenance record hashing. Untracked. Included in the 299. |
| `test_postgres_permissions.py` | Real Postgres grants. `NOT_RUN`. |
| `test_reset_admin_password_postgres.py` | Real Postgres password reset. `NOT_RUN`. |
| `conftest.py`, `mcp_session.py`, `slow_mcp.py`, `store_cluster.py` | Fixtures and doubles. |

Dashboard Vitest, all `PASS` on 2026-10-24:

| File | Tests | Coverage |
| --- | --- | --- |
| `dashboard/src/App.test.tsx` | 13 | Status rendering, disabled versus unreachable, schema and proxy errors. |
| `dashboard/src/admin/AdminPanel.test.tsx` | 4 | Admin controls, empty user list, collision diagnostics, member key scope. |
| `dashboard/src/admin/ChatPanel.test.tsx` | 5 | Loading, tool activity, retry. Mocked HTTP. |
| `dashboard/src/admin/StorePanel.test.tsx` | 5 | List error, create, validation. Mocked HTTP. Does not provision a cluster. |
| `dashboard/src/admin/DiscoveryPanel.test.tsx` | 3 | Collision display and search explanation. |
| `dashboard/src/proxyError.test.ts` | 4 | Proxy error mapping. |

CI on `.github/workflows/ci.yml` runs the full pytest suite, including disposable Postgres, plus dashboard test and build. The public result for this HEAD was not queried. `docs/design.md` cites an older successful run for commit `d7ddff1d26cd6ff9e6b96da4282bfc51cf93b178`. That run is `NOT_REVALIDATED` and is not this HEAD.

## G. Runtime and deployment provenance

Read-only commands only. No pod was deleted, scaled, or exec'd. No Secret body was printed. No database query was issued.

### What was read on 2026-10-24

Current kubectl context: `kind-urumi-k8s-dev`. Other context present and not used for pods: `kind-urumi-k8s-test`.

Nodes:

| Context | Node | InternalIP |
| --- | --- | --- |
| `kind-urumi-k8s-dev` | `urumi-k8s-dev-control-plane` | `172.19.0.2` |
| `kind-urumi-k8s-test` | `urumi-k8s-test-control-plane` | `172.19.0.3` |

Namespaces on the dev context: `default`, `ingress-nginx`, `kube-node-lease`, `kube-public`, `kube-system`, `local-path-storage`. No `st-` namespace.

Ready workloads in `default` (1/1):

| Workload | Image | Pod imageID |
| --- | --- | --- |
| `urumi-gateway-74647f87fc-tggrx` | `urumi-gateway:partial-20261019` | `docker.io/library/import-2026-10-19@sha256:21f1e50011666fff63ec12230dff1868006030cd0f6217fe07804974e7e781a4` |
| `urumi-dashboard-858fbb8c4c-qmbx4` | `urumi-dashboard:partial-20261019` | `docker.io/library/import-2026-10-19@sha256:99979e69c5d6697669276fa333aed782a39edd2dcda9d289bf1c2953a1014016` |
| `urumi-support-mcp-7799ddfb6f-sn2jq` | `urumi-support-mcp:k8s-test` | `import-2026-10-11@sha256:9ca27c930070ec473f298d80fcc28f6cabf753791654e5cac906f302778cd764` |
| `urumi-utility-mcp-759554d66b-66pmj` | `urumi-utility-mcp:k8s-test` | `import-2026-10-11@sha256:8b5281f80d9c5e49af515173bcbdb2389c9b8f7092a993b8f25cc372dbaba9fe` |
| `urumi-woocommerce-mcp-7fcc7d8c5d-zs77h` | `urumi-woocommerce-mcp:k8s-test` | `import-2026-10-11@sha256:8ee8a11de43f7b59952d24a9475aac61f13008d515c2935f056129409bebd7cf` |
| `urumi-wordpress-694c69d4b4-dlb4x` | `wordpress:6.8-php8.2-apache` | `sha256:b1c4ef27a7a85b2e8ec87218bbf83e05bd0d0ae70fa6205737ae2a631bb116fe` |
| `urumi-gateway-db-0` | `postgres:16-alpine` | `sha256:81bd698b4594e751a3269e4dcd3e03a4a0ec0daf7b72e7aa1abd43cce9887542` |
| `urumi-store-db-0` | `mariadb:11.4` | `sha256:ab3dff582d246358b6f6a1f6212f29ac64609fad883eb3dcfb0a89f6f7d0700e` |

`ingress-nginx-controller-6dfcd5998d-649sz` was Running and ready. Completed node-debugger and admission jobs were present. They were not changed.

PVCs still Bound:

- `data-urumi-gateway-db-0` `pvc-f77b4777-44e3-4c02-9309-a1cf3c1332cf`
- `data-urumi-store-db-0` `pvc-4283d20f-494e-4a15-81d4-6f514adf028f`
- `urumi-wordpress-data` `pvc-1e0101de-5c7e-4f29-b19c-7a0c25eddbf3`

Helm release `urumi` in `default`: revision 1, chart `urumi-0.3.0`, status `deployed`, updated 2026-10-12 07:39:17 +0530. Stored user values set gateway and dashboard tags to `k8s-test` and the three MCP tags to `k8s-test`. Stored values include `existingSecret` names and do not include `networkPolicy.probeCIDRs`. Secret values were not printed.

`values-dev.yaml` still sets gateway tag `chat-20261018`, dashboard tag `module5-20261018`, and `networkPolicy.probeCIDRs` `172.19.0.3/32`. That file was not applied.

NetworkPolicy `ipBlock` values read from `default`:

| Policy | Generation | Blocks |
| --- | --- | --- |
| `urumi-dashboard-net` | 2 | ingress `172.19.0.2/32` |
| `urumi-gateway-db-net` | 2 | ingress `172.19.0.2/32` |
| `urumi-gateway-net` | 2 | ingress `172.19.0.2/32`, egress `0.0.0.0/0` |
| `urumi-store-db-net` | 2 | ingress `172.19.0.2/32` |
| `urumi-upstream-net` | 2 | ingress `172.19.0.2/32` |
| `urumi-wordpress-net` | 2 | ingress `172.19.0.2/32` |
| `urumi-woocommerce-mcp-net` | 1 | no ipBlock |
| `urumi-wordpress-init-net` | 1 | egress `0.0.0.0/0` |

No policy contained `172.19.0.3/32`. The kubelet probe source IP was not captured. `172.19.0.2/32` is the applied ingress block and is also today's dev-node InternalIP. That coincidence is not packet proof.

ConfigMap `urumi-config` key `UTILITY_MCP_ENABLED` is `true`. The database row in `gateway_upstream_flags` was not read.

Secrets present by name only: `urumi-dev-chat`, `urumi-dev-gateway-credentials`, `urumi-dev-gateway-keys`, `urumi-dev-store-credentials`, `urumi-dev-woocommerce-credentials`, plus the Helm release secret. Presence is not proof the chat provider still works.

Laptop HTTP GET:

| URL | Result |
| --- | --- |
| `http://store.local/` | 200 |
| `http://dashboard.local/` | 200 |
| `http://gateway.local/` | 401 |
| `http://gateway.local/health` | 200, body `{"status":"ok"}` |
| `http://127.0.0.1:8000/health` | connection refused |

`dashboard.local` HTML references `/assets/index-DienbTDh.js` and `/assets/index-DzZS3LZd.css`. This checkout's `dashboard/dist/index.html` references `/assets/index-Db_F4In1.js` and `/assets/index-CtuYfo3v.css`. The served cluster bundle is not the dist file in this tree.

Vite is listening on `127.0.0.1:5173`. Process cwd is `/home/naval/project/urumi-project/dashboard`. Command is `vite --host 127.0.0.1 --port 5173 --strictPort`. A GET returned the Vite client preamble. That process serves this checkout's dashboard source. Dashboard `src/` is not in the uncommitted diff. `dashboard/Dockerfile` is. The Vite server was not driven through login or chat.

Docker, read with `docker ps` and narrow inspects:

| Container | Image | Notes |
| --- | --- | --- |
| `urumi-k8s-dev-control-plane` | local Kind node id `44e222ee2132` | API `127.0.0.1:42439` |
| `urumi-k8s-test-control-plane` | `kindest/node:v1.34.11` | Publishes host ports 80 and 443 |
| `urumi-dev-port80` | `urumi-gateway:chat-20261018` | Shares the test node's network namespace. Still running. |
| `urumi-dash01-062b4b1c` | `postgres:16-alpine` | Publishes `127.0.0.1:15432`. Not connected. Not identified as Compose. Left untouched. |

Compose services from `compose.yaml` were not in `docker ps`. The Compose stack was not running.

Local image `urumi-gateway:partial-20261019` id `sha256:ce7818a6c752269f3efcacd5a38aba557e0dd095d120312f8af3c16b9ba8f0d6`, created `2026-10-19T18:21:37+05:30`, `Config.Labels` null. Local image `urumi-dashboard:partial-20261019` id `sha256:defdf9596281aa8dadb91bbe58175602f980da563bc531097b0d5ed54c1def69`, created `2026-10-19T18:22:31+05:30`. Its only revision label is the nginx base image, not a Urumi commit. Neither local id equals the cluster `imageID`. A difference between a Kubernetes `imageID` and a local Docker id does not by itself prove the bytes differ. It also does not prove they match. No image was built.

HEAD `b4b813c` is dated 2026-10-21 in the gap tracker. Both local `partial-20261019` images were created on 2026-10-19, before that commit. The running images cannot be a build of current `HEAD` plus the dirty tree. The Dockerfiles in the working tree add `URUMI_SOURCE_REVISION` labels. Those labels are absent on the local gateway tag (`Config.Labels` null). The cluster image was not inspected for labels.

### Feature flags and mismatches

| Source | Gateway tag | Dashboard tag | Probe CIDR | Utility |
| --- | --- | --- | --- | --- |
| Running pods | `partial-20261019` | `partial-20261019` | policies allow `172.19.0.2/32` | ConfigMap `true`; database flag `NOT_REVALIDATED` |
| Helm release revision 1 | `k8s-test` | `k8s-test` | not stored | not stored; chart default remains |
| `values-dev.yaml` | `chat-20261018` | `module5-20261018` | `172.19.0.3/32` | not overridden in the lines read |
| This checkout | uncommitted Dockerfiles | uncommitted Dockerfile | chart default empty | source `convert_units` finite check is in the dirty tree, not in the `k8s-test` image by any proof gathered here |

The running stack is suitable for showing the already deployed demo image. It is not suitable for proving this checkout. Do not `helm upgrade` `values-dev.yaml` to "align" it. That would change tags and the probe CIDR.

### Not verified

- In-cluster `/health`.
- CNI plugin and whether Calico still enforces policy.
- Kubelet probe source IP.
- `gateway_upstream_flags` for `utility`.
- Live tool calls, chat, audit rows, and WooCommerce admin.
- That local Docker ids equal cluster image bytes.
- Compose.
- VPS.
- The unidentified Postgres container on port 15432.
- GitHub Actions for `b4b813c`.

## H. Known gaps and next steps

### P0 — security, correctness, or mandatory demo blockers

| ID | Gap | Evidence | Safest next action |
| --- | --- | --- | --- |
| `K8S-01` | The demo pods are not this checkout. Helm revision 1 still stores `k8s-test`. Running tags are `partial-20261019`. `values-dev.yaml` names older tags. | Section G, 2026-10-24. | Do not upgrade or retag. Decide in a later review which image a demo is allowed to claim. |
| `K8S-03` | Utility may still be disabled in the database while the ConfigMap says enabled. A granted Utility call would then fail closed if the 2026-10-19 flag is unchanged. | ConfigMap re-read as `true`. Database flag `NOT_REVALIDATED`. | Read-only `SELECT` of the boolean only, with approval. Do not enable it in this inventory. |
| `DEMO-M12` | The two-user definition of done has not been repeated on the current images or on this tree. | No tool call, no chat, no revoke on 2026-10-24. | Do not start the sitting until `K8S-01` and `K8S-03` are explicit. |

### P1 — mandatory behavior not proven end to end on this checkout

| ID | Gap | Evidence | Safest next action |
| --- | --- | --- | --- |
| `M2-ADMIN` | WooCommerce admin screen for the historical order was never recorded as opened. | Assignment tracker Module 2 and Module 12. `NOT_REVALIDATED`. | Owner opens the existing admin screen. Do not create an order for the inventory. |
| `M5-BROWSER` | Add, edit, remove, and invite were not retested in the browser after Module 1. | Assignment tracker. Owner statement does not name pages. | Write the pages already tried. |
| `M6-UI` | Audit column labels were not reopened. | Assignment tracker. | Open the audit view, or a read-only query that omits secrets. |
| `M7-GROQ` | Live model chat is 2026-10-18 evidence. Secret `urumi-dev-chat` exists. That does not show a current completion. | Secret name only. No chat request on 2026-10-24. | A later approved chat turn. Do not print the key. |
| `M9-PACKET` | NetworkPolicy objects exist. Enforcement was not repeated. `urumi-woocommerce-mcp-net` still has no probe `ipBlock`. | Section G. Module 9 is `NOT_REVALIDATED`. | Read-only CNI check, then an approved packet test. |
| `K8S-02` | `values-dev.yaml` probe CIDR is `172.19.0.3/32`. Live policies and today's dev node are `172.19.0.2`. The test node is `172.19.0.3`. Probe source is still uncaptured. | Section G. | Do not apply the values file. Capture the kubelet source before any edit. |
| `M11` | VPS worksheet is not a deployment. | Tracker Module 11. `helm` of `values-vps.yaml` was `NOT_RUN` today. | Local `helm lint` and `helm template` only. |
| `AUTH-POSTGRES` | Grant persistence on real Postgres was not re-run. | 17 Docker-backed tests collected and not executed. | A later run that is allowed to start a disposable Postgres container. |

### P2 — usability, documentation, and demo polish

| ID | Gap | Evidence | Safest next action |
| --- | --- | --- | --- |
| `DOC-01` | Threat-model answer 1 is an uncommitted correction. | `docs/threat-model.md` is modified. Current Q1 text matches `execute_tool` as read today. | Owner reviews that diff. It is not `PASS_USER_VERIFIED`. |
| `DOC-02` | `docs/design.md` says `values-dev.yaml` probe CIDR `172.19.0.3/32` matches the current dev node InternalIP, and that `172.19.0.2` is the other Kind node. | Today's dev node is `172.19.0.2` and the test node is `172.19.0.3`. `CONFLICTING` with section G. | Correct the sentence only when a documentation edit is requested. |
| `DOC-04` | `values-dev.yaml` header says the gateway Deployment was set to `urumi-gateway:grants-msg-20261019` and that the dashboard tag still matches `module5-20261018`. | Running images are both `partial-20261019`. `CONFLICTING` with section G. | Do not apply the file to "fix" the comment. |
| `DOC-05` | Store API comment versus `public_store` (`DOC-02` in the gap tracker). | Gap tracker, `NOT_REVALIDATED` line by line in this pass. | Re-read `stores/api.py` and `public_store` before editing the comment. |
| `RUNTIME-01` | Three dashboard surfaces differ: cluster bundle `index-DienbTDh.js`, checkout dist `index-Db_F4In1.js`, Vite source on port 5173. | Section G. | State which surface a screenshot came from. |
| `RUNTIME-02` | No host gateway on port 8000. | Curl connection refused. | Use `gateway.local` for the cluster, or start a host process only when that is the intended target. |
| `CHAT-SCREEN` | No saved ChatPanel screenshot in the tracker. | Assignment tracker remaining issues. | A later manual session. |

### Bonus

| ID | Gap | Evidence | Safest next action |
| --- | --- | --- | --- |
| `STORE-01` | Provisioned store lifecycle is source and in-process tests only. Flags are false. No `st-` namespace. | Section D `BONUS-STORES`. | Keep it off. Disposable cluster plus admission proof before any create. |
| `COLL-01` | Live collision path was not exercised. In-process policy tests passed. | Section E flow 5. | Do not create a colliding upstream on the dev cluster. |
| `VPS-01` | Same as M11. Classified optional only in the gap tracker. | `DOC-03`. | Treat Module 11 as mandatory-partial until the owner says otherwise. |
| `OPT-OAUTH` | No MCP OAuth authorization server was re-audited. WooCommerce OAuth 1.0a is a different mechanism. | Gap tracker, `NOT_REVALIDATED`. | Leave it out of the mandatory demo. |
| `OPT-IMAGE` | Arbitrary MCP image run was not re-audited. | Gap tracker, `NOT_REVALIDATED`. | Leave it out of the mandatory demo. |
| `OPT-METRICS` | Discovery diagnostics exist. A Prometheus endpoint was not sought in this pass. | `GET /admin/discovery` exists. Metrics endpoint `NOT_CONFIRMED`. | Do not claim a metrics stack. |

### Accepted residuals (not scheduled as defects)

| ID | Residual | Evidence |
| --- | --- | --- |
| `RES-01` | Unset or `open` `GATEWAY_STATUS_PERMISSION` lets any live key read `GET /admin/status`, including upstream ids. | `main.py` `register_status`. `gateway__read_status` is not a tool. |
| `RES-02` | Utility MCP process has no API-key check. Isolation depends on network policy. | `apps/utility_mcp/app/main.py` as read. Enforcement `NOT_REVALIDATED`. |
| `RES-03` | Audit insert is not in the same transaction as the tool side effect. | `Auditor.record`. `README.md` Limitations. |

### Working-tree fixes the gap tracker already described

These were not re-derived line by line beyond the functions read in section C. The 2026-10-24 pytest run included their test files and passed. They are uncommitted. They are not in the running `partial-20261019` or `k8s-test` images by any proof in section G. They are not `PASS_USER_VERIFIED`.

| ID | Claim | This pass |
| --- | --- | --- |
| `SEC-01` | Store tools require the key's user. | `store_block_reason` returns `store_not_owner` when the caller id is empty or different. Tests in the 299. |
| `SEC-02` | `deleted` waits for namespace HTTP 404. | Not re-read in `service.py` line by line. Store tests passed. Live namespace `NOT_OBSERVED`. |
| `SEC-03` | Chat provider errors redact credential-shaped text. | `tests/test_chat_provider.py` passed. Implementation not quoted here. |
| `SEC-04` | `convert_units` rejects non-finite values. | `_finite_number` in `apps/utility_mcp/app/main.py`. `tests/test_utility_mcp.py` passed. |
| `SEC-05` | A key with no owner cannot receive a store-tool grant. | Test name `test_admin_cannot_grant_a_store_tool_to_a_key_without_an_owner` is in `tests/test_store_routing.py`, which was in the 299. |
| `AUTH-02` | HTTP chat test denies another user's store tool even if a grant was stored directly. | `tests/test_chat.py` includes `test_chat_rejects_another_users_store_tool_when_the_key_has_a_grant` and the file passed inside the 299. |

## I. Search index for future sessions

### Requirement ids

`M1` cluster health, `M2` checkout COD, `M3` WooCommerce MCP, `M4` gateway surface, `M5` admin keys, `M6` audit, `M7` chat, `M8` external client, `M9` NetworkPolicy, `M10` Ingress, `M11` VPS, `M12` demo sitting, `BONUS-STORES`, `Q1` `Q2` `Q3` `Q4`.

### Audit ids

`K8S-01` image drift, `K8S-02` probe CIDR, `K8S-03` Utility flag, `RUNTIME-01` dashboard bundle mismatch, `RUNTIME-02` host port 8000 down, `DOC-01` threat-model diff, `DOC-02` design.md node IP sentence, `DOC-03` VPS optional-versus-mandatory conflict, `DOC-04` values-dev header versus running tags, `DOC-05` store API comment, `STORE-01`, `COLL-01`, `SEC-01` through `SEC-05`, `AUTH-02`, `RES-01` through `RES-03`, `OPT-OAUTH`, `OPT-IMAGE`, `OPT-METRICS`.

### Paths

`apps/gateway/app/main.py`, `dispatch.py`, `registry.py`, `discovery.py`, `auth.py`, `keys.py`, `admin_api.py`, `audit.py`, `chat.py`, `chat_api.py`, `stores/routing.py`, `stores/api.py`, `stores/service.py`, `apps/woocommerce_mcp/app/tools.py`, `apps/utility_mcp/app/main.py`, `apps/common/http.py`, `dashboard/src/api.ts`, `dashboard/src/admin/AdminPanel.tsx`, `helm/urumi/values.yaml`, `values-dev.yaml`, `values-vps.yaml`, `templates/networkpolicy.yaml`, `templates/gateway-deployment.yaml`, `compose.yaml`, `docs/assignment-tracker.md`, `PROJECT_GAP_TRACKER.md`.

### Symbols

`create_app`, `on_list_tools`, `on_call_tool`, `ToolDispatcher.execute_tool`, `Registry.call`, `Registry.set_enabled`, `_colliding_public_names`, `hash_key`, `generate_key`, `Authenticator.permits`, `visible_names`, `store_block_reason`, `ChatService.answer`, `register_tools`, `convert_units`, `_finite_number`, `serverStatus`, `fetchStatus`.

### Routes

`POST /mcp`, `GET /health`, `POST /discovery/search`, `GET /admin/status`, `GET /admin/discovery`, `POST /admin/chat`, `GET /admin/audit`, `POST /admin/keys`, `POST /admin/keys/{key_id}/revoke`, `POST /admin/upstreams/{upstream_id}`, `GET/POST /admin/stores`.

### Tests to reopen first

`tests/test_tool_permissions.py`, `tests/test_tool_discovery.py`, `tests/test_discovery_policy.py`, `tests/test_upstream_controls.py`, `tests/test_chat.py`, `tests/test_store_routing.py`, `tests/test_woocommerce.py`, `tests/test_gateway_mcp.py`. Postgres files stay excluded until Docker creation is allowed.

### Revalidate before a demo

1. Which image the speaker will claim (`K8S-01`). The running pods are `partial-20261019` and `k8s-test`, not this dirty tree.
2. Utility database flag (`K8S-03`).
3. Whether the demo URL is `dashboard.local` (old bundle) or Vite `:5173` (this dashboard source) (`RUNTIME-01`).
4. Whether Utility must be part of the script. The tracker says a three-upstream claim needs Utility enabled.
5. Do not apply `values-dev.yaml` (`K8S-02`).
6. Do not create a store on the dev or test Kind context (`STORE-01`).
7. Owner acceptance is still unset for every gap-tracker issue.

### Unresolved questions

- Are the cluster `imageID`s byte-for-byte the local `partial-20261019` tags? Not proven.
- Is Utility disabled in `gateway_upstream_flags` today? Not re-read.
- Does Calico still enforce the policies? Not re-read. Section J found both `calico-node` and `kindnet` Running on 2026-10-24. That is not packet proof.
- What is container `urumi-dash01-062b4b1c` on port 15432? Not identified. It was not opened.
- Did the owner use the cluster dashboard or Vite on 2026-10-23? The statement does not say.
- Is Module 11 mandatory? The assignment tracker says yes and Partial. The gap tracker calls `VPS-01` optional (`DOC-03`).

### Four-layer pass index

Section J, dated 2026-10-24, is the four-layer architecture and same-chart check. Search: `LAYER-1` store, `LAYER-2` WooCommerce MCP, `LAYER-3` gateway and dashboard, `LAYER-4` chat, `BOUNDARY` upstream bypass, `CHART-SAME` Helm local versus VPS, `VPS_DEPLOYMENT_NOT_VERIFIED`.

Symbols added there: `ChatService.answer`, `register_chat_routes`, `Registry._connect`, `WooClient._request`, `urumi.wooBaseUrl`, `list_products`, `list_orders`.

Tests executed in that pass, not the earlier 299: `tests/test_woocommerce.py`, `tests/test_chat.py`, `tests/test_gateway_mcp.py`, `tests/test_upstream_controls.py`, `tests/test_networkpolicy_intent.py` (55 passed).

Chart commands: `helm lint` and `helm template` for `values-dev.yaml` and `values-vps.yaml`. Bare `values.yaml` template failed closed on missing public URL and Secrets. No `helm upgrade`.

## J. Four-Layer Architecture and Same-Helm-Chart Deployment Verification

Inspection date: 2026-10-24, after the inventory in sections A–I. The user request timestamp was 14:58 IST. This section re-read the current dirty tree and repeated read-only runtime checks. It did not treat section F or section G as proof. No application source, chart, test, cluster object, container, or application record was changed.

Verdict for this requirement: **partially satisfied**. Source and the local chart shape implement the four layers, and one chart renders for both values files. The running cluster is not this checkout. Live product and order tool calls, live chat, WooCommerce admin, checkout, pod-recreation persistence, network-policy enforcement, and any VPS install were not demonstrated in this pass.

### Repository state at this pass

| Item | Value |
| --- | --- |
| Branch | `submission` |
| HEAD | `b4b813c568ca70b45d28944a44de4e8e770f874d` (`feat: finalize MCP gateway discovery and multi-store submission`, 2026-10-21 13:49:42 +0530) |
| Tracking | `submission...origin/submission [ahead 1]`. No fetch. |
| Working tree | Same dirty set as section A. `git diff --stat` still 19 files, 991 insertions, 41 deletions. Helm templates are not in that diff. `apps/gateway/app/chat.py` and `dispatch.py` each change one call so store ownership receives `owner_user_id`. Those lines are uncommitted and are not in the running gateway image. |
| Findings file | Already present. Sections A–I were preserved. |
| kubectl context | `kind-urumi-k8s-dev`. Other context `kind-urumi-k8s-test` was used only for its node address. |
| Dashboard identity | Cluster `http://dashboard.local/` serves `/assets/index-DienbTDh.js`. This tree's `dashboard/dist/index.html` references `/assets/index-Db_F4In1.js`. Vite pid 33450 cwd `/home/naval/project/urumi-project/dashboard`, `vite --host 127.0.0.1 --port 5173 --strictPort`, HTTP 200. Vite was not driven through login or chat. |
| Gateway identity | Pod `urumi-gateway-74647f87fc-tggrx` image `urumi-gateway:partial-20261019`, imageID `docker.io/library/import-2026-10-19@sha256:21f1e50011666fff63ec12230dff1868006030cd0f6217fe07804974e7e781a4`. Host `127.0.0.1:8000` connection refused. `http://gateway.local/health` returned 200 `{"status":"ok"}`. `http://gateway.local/` returned 401. |

Historical manual results in `docs/assignment-tracker.md` (2026-10-18 and 2026-10-19) were re-read as history. They were not repeated. They stay `NOT_REVALIDATED` and `NOT_USER_VERIFIED`.

### LAYER-1 — WooCommerce store on Kubernetes

| Field | Status |
| --- | --- |
| Implementation | `IMPLEMENTED` |
| Integration | `PARTIAL` |
| Automated test | `NOT_COVERED` for a live store. Chart shape is source. |
| Runtime | `OBSERVED_WORKING` for Ready pods, Bound PVCs, and `http://store.local/` HTTP 200. Checkout, admin, and pod recreation were not run. |
| Manual | `NOT_USER_VERIFIED`. Tracker orders 16 and 20 on 2026-10-18 are `NOT_REVALIDATED`. |
| Demo | `NEEDS_WORK` |

Chart: one chart, `helm/urumi` (`Chart.yaml` version `0.3.0`). Store objects are templates, not a second chart.

| Concern | Path and live name |
| --- | --- |
| WordPress | `helm/urumi/templates/wordpress-deployment.yaml`. Live Deployment `urumi-wordpress`, pod `urumi-wordpress-694c69d4b4-dlb4x`, image `wordpress:6.8-php8.2-apache`, Ready 1/1. |
| MariaDB | `helm/urumi/templates/store-db-statefulset.yaml`. Live StatefulSet `urumi-store-db`, pod `urumi-store-db-0`, image `mariadb:11.4`, Ready 1/1. |
| Services | `wordpress-service.yaml` and `store-db-service.yaml`. Live `urumi-wordpress` ClusterIP port 80, `urumi-store-db` ClusterIP port 3306. |
| WordPress files | `wordpress-pvc.yaml`. Live PVC `urumi-wordpress-data` Bound, volume `pvc-1e0101de-5c7e-4f29-b19c-7a0c25eddbf3`, StorageClass `standard`, annotation `helm.sh/resource-policy=keep`. Mount `/var/www/html`. |
| Database files | StatefulSet `volumeClaimTemplates` name `data`, mount `/var/lib/mysql`. Live PVC `data-urumi-store-db-0` Bound, volume `pvc-4283d20f-494e-4a15-81d4-6f514adf028f`, `helm.sh/resource-policy=keep`. StatefulSet `persistentVolumeClaimRetentionPolicy` is `whenDeleted=Retain`, `whenScaled=Retain`. |
| Database connection | ConfigMap `urumi-store-config`: `WORDPRESS_DB_HOST=urumi-store-db`, `WORDPRESS_DB_NAME=wordpress`, `WORDPRESS_DB_USER=wordpress`, `WORDPRESS_URL=http://store.local`. Password comes from Secret `urumi-dev-store-credentials` key `WORDPRESS_DB_PASSWORD`. The value was not read. |
| Ingress | `ingress.yaml` store host. Live Ingress `urumi-ingress` class `nginx`, hosts `store.local`, `gateway.local`, `dashboard.local`. |
| Probes and resources | Chart `values.yaml` `store.probes` and resource blocks. Live WordPress startup and readiness HTTP `/`, liveness TCP port name `http`, requests `100m`/`256Mi`, limits `1`/`768Mi`. Pod Ready means those probes are currently succeeding. |
| Init | `wordpress-init-job.yaml` plus `helm/urumi/scripts/init-store.sh`. Not re-run. |
| Helm release | `urumi` namespace `default`, revision 1, chart `urumi-0.3.0`, status `deployed`, updated 2026-10-12 07:39:17 +0530. Stored `store.publicUrl` is `http://store.local`. |

`http://store.local/` returned HTTP 200, 60844 bytes, and the body contained the strings `woocommerce`, `wp-content`, and `wordpress`. That is a storefront response. It is not a cart, cash-on-delivery checkout, or wp-admin session.

Persistence is configured on the live PVCs and StatefulSet. This pass did not delete or recreate a Pod, so data survival after recreation was not verified. The tracker statement that order 14 survived a WordPress restart and a MariaDB recreate, and that order 20 was read from `wp_wc_orders` on 2026-10-18, remains historical. The WooCommerce admin screen was never recorded as opened.

Local cluster: context `kind-urumi-k8s-dev`, node `urumi-k8s-dev-control-plane` InternalIP `172.19.0.2`. `kind-urumi-k8s-test` node InternalIP `172.19.0.3`. No `st-` namespace.

Production-like values: the same templates render with `values-vps.yaml` (see `CHART-SAME`). That file does not by itself deploy a VPS.

### LAYER-2 — Custom WooCommerce MCP server

| Field | Status |
| --- | --- |
| Implementation | `IMPLEMENTED` |
| Integration | `PARTIAL` |
| Automated test | `PASS` for scripted HTTP in this pass. Not the live store. |
| Runtime | `NOT_OBSERVED` for a tool call. The pod is Ready and its non-secret env points at WordPress. |
| Manual | `NOT_USER_VERIFIED`. 2026-10-18 `list_products` for products 13 and 15 is `NOT_REVALIDATED`. |
| Demo | `NEEDS_WORK` |

Implementation: `apps/woocommerce_mcp/app/main.py` `create_app` builds `MCPServer("woocommerce")` and `build_http_app` (`apps/common/http.py`) mounts Streamable HTTP at `/mcp` with `json_response=True` and `stateless_http=True`. `GET /health` reports `woocommerce_api` `configured` or `not_configured`. This is not a WooCommerce plugin.

`register_tools` in `apps/woocommerce_mcp/app/tools.py` exposes:

| Public tool on the WooCommerce server | REST path in `WooClient` |
| --- | --- |
| `list_products` | `GET /wp-json/wc/v3/products` via `get_list("/products")` |
| `create_product` | `POST /wp-json/wc/v3/products` |
| `list_orders` | `GET /wp-json/wc/v3/orders` |
| `get_order` | `GET /wp-json/wc/v3/orders/{id}` |
| `update_order_status` | `PUT /wp-json/wc/v3/orders/{id}` |
| `create_order` | `POST /wp-json/wc/v3/orders` after catalog checks. Extra beyond the five required tools. |

`WooClient._request` in `apps/woocommerce_mcp/app/client.py` uses HTTP basic auth when the base URL is `https`, and OAuth 1.0a HMAC-SHA256 in the Authorization header when the scheme is `http`. Credentials are not placed in the URL. `load_settings` in `config.py` reads `WOOCOMMERCE_BASE_URL`, `WOOCOMMERCE_CONSUMER_KEY`, `WOOCOMMERCE_CONSUMER_SECRET`, optional `WOOCOMMERCE_SITE_URL`, and `WOOCOMMERCE_TIMEOUT_SECONDS`. Missing or invalid settings leave tools unregistered.

Read-only traces in source:

- `list_products` → `_list_products` → `client.get_list("/products")` → `GET {base}/wp-json/wc/v3/products`.
- `list_orders` → `_list_orders` → `client.get_list("/orders")` → `GET {base}/wp-json/wc/v3/orders`.

Kubernetes: Deployment `urumi-woocommerce-mcp`, pod `urumi-woocommerce-mcp-7fcc7d8c5d-zs77h`, image `urumi-woocommerce-mcp:k8s-test`, imageID `docker.io/library/import-2026-10-11@sha256:8ee8a11de43f7b59952d24a9475aac61f13008d515c2935f056129409bebd7cf`, Ready 1/1. Service `urumi-woocommerce-mcp` ClusterIP port 8000. Env names present: `WOOCOMMERCE_MCP_ENABLED`, `WOOCOMMERCE_TIMEOUT_SECONDS`, `WOOCOMMERCE_BASE_URL=http://urumi-wordpress`, `WOOCOMMERCE_SITE_URL=http://store.local`, plus Secret refs for the consumer key and secret. Secret `urumi-dev-woocommerce-credentials` has keys `WOOCOMMERCE_CONSUMER_KEY` and `WOOCOMMERCE_CONSUMER_SECRET`. Values were not read. Chart helper `urumi.wooBaseUrl` in `helm/urumi/templates/_helpers.tpl` sets that in-cluster URL when `woocommerce.baseUrl` is empty and the store is enabled.

Gateway registration: ConfigMap `urumi-config` has `WOOCOMMERCE_MCP_URL=http://urumi-woocommerce-mcp:8000/mcp` and `WOOCOMMERCE_MCP_ENABLED=true`. The gateway Deployment has env name `WOOCOMMERCE_MCP_URL` from that ConfigMap. Static upstream id `woocommerce` comes from `apps/gateway/app/config.py`. A database flag can override the ConfigMap enabled bit in `merge_upstreams`. That row was not read. This pass did not call `tools/list` or `tools/call`. Upstream `/health` from inside the cluster was not called.

Tests this pass: `tests/test_woocommerce.py` (23 collected, included in the 55). `test_configured_tools_are_namespaced_and_isolated` expects the six namespaced names `woocommerce__list_products`, `woocommerce__create_product`, `woocommerce__list_orders`, `woocommerce__get_order`, `woocommerce__update_order_status`, and `woocommerce__create_order` against a scripted HTTP peer. `test_publish_is_visible_and_order_bounds_keep_the_offset` covers list/query shaping without a live store.

The image tag `k8s-test` was imported 2026-10-11. It is older than HEAD `b4b813c` (2026-10-21) and older than the dirty tree. A passing test in this checkout does not prove the running image.

### LAYER-3 — Gateway and React dashboard

| Field | Status |
| --- | --- |
| Implementation | `IMPLEMENTED` |
| Integration | `PARTIAL` |
| Automated test | `PASS` for the gateway files run this pass. Dashboard Vitest was `NOT_RUN` this pass. The earlier same-day Vitest result in section F was not repeated. |
| Runtime | `OBSERVED_WORKING` for gateway `/health` and dashboard HTTP 200. `tools/list`, admin login, and upstream CRUD were not called. The served dashboard bundle is not this checkout. |
| Manual | `NOT_USER_VERIFIED` |
| Demo | `NEEDS_WORK` |

Dashboard routes and client:

- `dashboard/src/App.tsx` hosts the admin panel. `dashboard/src/api.ts` `fetchStatus` calls `GET /admin/status` with a pasted Bearer key.
- `dashboard/src/admin/AdminPanel.tsx` calls `GET/POST /admin/upstreams`, user, key, permission, and `GET /admin/audit` through `adminRequest` in `dashboard/src/admin/adminClient.ts`.
- `dashboard/src/admin/ChatPanel.tsx` posts to `/admin/chat`.
- `dashboard/src/admin/DiscoveryPanel.tsx` and `StorePanel.tsx` call `/admin/discovery` and `/admin/stores`.
- Vite `dashboard/vite.config.ts` proxies `/admin` to `GATEWAY_ORIGIN`, default `http://127.0.0.1:8000`. No upstream MCP host appears under `dashboard/src`.
- Chart Nginx `helm/urumi/templates/dashboard-configmap.yaml` proxies `/admin/` to the gateway Service. It does not proxy `/mcp`.

Backend admin API: `apps/gateway/app/admin_api.py` has `GET /admin/upstreams`, `POST /admin/upstreams`, `POST /admin/upstreams/{upstream_id}`, and `POST /admin/upstreams/{upstream_id}/delete`. Session routes include login, logout, and `GET /admin/me`.

Upstream MCP client: `apps/gateway/app/registry.py` class `_connect` opens `mcp.client.streamable_http.streamable_http_client` to `upstream.url`. `Registry.call` lists tools on that session, rejects a duplicate public name, then `client.call_tool(tool_name, arguments)`. `catalog.merge_upstreams` merges static env upstreams with admin-saved rows. Enabled flags from the database override static flags.

Gateway MCP server: `apps/gateway/app/main.py` `on_list_tools` calls `discover`. `on_call_tool` calls `ToolDispatcher.execute_tool`. `build_http_app` mounts that server at `/mcp`. External clients are told, in `README.md`, to use `<GATEWAY_URL>/mcp` with a Bearer key. Live `POST /mcp` was not sent.

Public names are `{upstream_id}__{tool}`. `_unique_upstreams` rejects an id containing `__` or a repeated id. `_colliding_public_names` omits a public name that appears more than once. `Registry.call` returns `Duplicate public tool name <name>. Conflicting registration excluded.` A disabled upstream is not contacted and the error is `Upstream <id> is disabled.`

Auth: `ToolDispatcher.execute_tool` checks `Authenticator.authorize`, exact `permits`, then `store_call_allowed`, then `Registry.call`. Chat uses the same dispatcher. `POST /admin/chat` uses the session user (`chat_api.py`), not a gateway API key. `GET /admin/status` can be opened by any live key when `GATEWAY_STATUS_PERMISSION` is unset or `open` (`RES-01`). That was not re-tested live.

Kubernetes: gateway pod Ready, Service `urumi-gateway` ClusterIP 8000, Ingress host `gateway.local`. Dashboard pod `urumi-dashboard-858fbb8c4c-qmbx4` image `urumi-dashboard:partial-20261019`, Ready. Service ClusterIP port 80. Ingress host `dashboard.local`.

Upstream Services are ClusterIP and are not Ingress backends (`ingress.yaml` only selects wordpress, gateway, and dashboard). `networkpolicy.yaml` `urumi-upstream-net` allows MCP port ingress from the gateway pod and probe CIDRs. `urumi-woocommerce-mcp-net` adds egress to WordPress and DNS. Live policies exist (section G and this pass). `kube-system` has both `calico-node-w6xct` Running and `kindnet-69wgb` Running. Which plugin enforces packets was not tested. `tests/test_networkpolicy_intent.py` reads template text only. Its own docstring says it does not prove CNI enforcement.

Helm release stored tags are `k8s-test` for gateway and dashboard. Running tags are `partial-20261019`. `values-dev.yaml` still says gateway `chat-20261018` and dashboard `module5-20261018`. Three different identities. Do not `helm upgrade` to reconcile them.

### LAYER-4 — Dashboard chat agent

| Field | Status |
| --- | --- |
| Implementation | `IMPLEMENTED` |
| Integration | `PARTIAL` |
| Automated test | `PASS` for scripted provider tests. No live model. `list_products` is not asserted in `tests/test_chat.py`. |
| Runtime | `NOT_OBSERVED`. Secret `urumi-dev-chat` exists. Its values were not read. No chat request was sent. |
| Manual | `NOT_USER_VERIFIED`. 2026-10-18 Groq products 17 and 19 and orders 16, 18, and 20 are `NOT_REVALIDATED`. |
| Demo | `NEEDS_WORK` |

Path in the current dirty tree:

1. `ChatPanel.onSubmit` in `dashboard/src/admin/ChatPanel.tsx` sends the trimmed message.
2. `adminRequest("/admin/chat", "POST", { messages })` goes to the dashboard origin. Nginx or the Vite proxy forwards `/admin` to the gateway. The panel does not call WooCommerce or an upstream MCP URL.
3. `register_chat_routes` in `apps/gateway/app/chat_api.py` resolves the user with cookie `urumi_session` or a Bearer token that starts with `urs_`, via `store.authenticate_session`. A body user id is not the selector (`chat.py` module docstring). A missing session is HTTP 401. A missing provider returns HTTP 503 before `answer`.
4. `ChatService.answer` loads the user's live keys and exact grants, then `registry.list_tools_result()` for tools that `permits` and `store_block_reason` allow. The model sees those names only.
5. `provider_from_env` in `apps/gateway/app/chat_provider.py` requires `CHAT_API_KEY`. Optional `CHAT_API_URL` defaults to `https://api.openai.com/v1/chat/completions`. Optional `CHAT_MODEL` defaults to `gpt-4o-mini`. The chart injects `CHAT_API_KEY`, `CHAT_API_URL`, and `CHAT_MODEL` from `gateway.chatApiSecret` when that name is set. Live Secret `urumi-dev-chat` has those three keys. Values were not read, so this pass does not confirm Groq or any other host.
6. `HttpChatProvider.complete` parses `tool_calls`. The system prompt names `woocommerce__list_orders` and `woocommerce__create_product`. That text is not a client.
7. `_run_call` picks a key that holds the grant and calls `ToolDispatcher.execute_tool`. It does not import `WooClient` and does not open `streamable_http_client` itself.
8. `execute_tool` authorizes, then `Registry.call`, which is the only production `streamable_http_client` (`apps/gateway/app/registry.py`). The other import is the test helper `tests/mcp_session.py`.
9. The tool text is appended as untrusted tool data. A later provider turn becomes `ChatAnswer.message`. `POST /admin/chat` returns `message` and `activity`. The panel renders `tool: outcome`.

Critical check: the chat agent does not call WooCommerce or an upstream MCP server directly. It calls the gateway's in-process `ToolDispatcher`, the same function `on_call_tool` uses for external MCP clients. It does not make a second HTTP call to the gateway's own `POST /mcp`. The upstream hop is `Registry._connect` inside the gateway process.

Read-only order evidence in tests: `tests/test_chat.py::test_read_only_chat_hides_upstream_text_and_ungranted_tools` uses a scripted provider call to `woocommerce__list_orders` and asserts activity `success` plus an audit row. `test_one_prompt_calls_two_upstreams_and_a_disabled_tool_stays_hidden` calls `woocommerce__list_orders` and `support__search_notes` through the dispatcher.

Read-only product evidence: not in the chat tests. `list_products` is covered at the WooCommerce server and gateway namespace layer by `tests/test_woocommerce.py` and `tests/test_gateway_mcp.py::test_dispatch_through_gateway`, with scripted HTTP. The chat product test `test_product_creation_arguments_reach_the_dispatcher` is a write (`woocommerce__create_product`), also scripted. No live read-only product query and no live read-only order query were run.

Provider failures: missing provider is HTTP 503 in `chat_api.py`. `ProviderError` before any tool is HTTP 502. `ProviderError` after a tool returns HTTP 200 with `partial: true`. Provider timeout is HTTP 504. Upstream timeout and call-limit cases are in `test_malformed_tool_call_provider_timeout_upstream_timeout_and_call_limit`. This pass did not call a model.

### BOUNDARY — shared gateway boundary

The requirement is that dashboard chat and external clients reach every MCP upstream through the gateway, and that nothing else talks directly to upstream MCP servers.

| Finding | Classification | Evidence |
| --- | --- | --- |
| Chat and `POST /mcp` both use `ToolDispatcher.execute_tool` | Intended gateway path | `apps/gateway/app/chat.py` `_run_call`; `apps/gateway/app/main.py` `on_call_tool`; `apps/gateway/app/dispatch.py` |
| Only the gateway registry opens Streamable HTTP to upstreams in application code | Intended gateway path | `streamable_http_client` appears in `apps/gateway/app/registry.py` and `tests/mcp_session.py` only |
| WooCommerce REST is called only by the WooCommerce MCP process | Intended layer-2 path, not a gateway bypass | `WooClient` is constructed in `apps/woocommerce_mcp/app/main.py` and tests. Chat and the dashboard do not import it. |
| Dashboard has no upstream MCP URL | No dashboard bypass | Search of `dashboard/src` found no `woocommerce-mcp`, `support-mcp`, or `utility-mcp` host |
| Ingress does not target MCP Services | No external Ingress bypass | `helm/urumi/templates/ingress.yaml`. Live hosts are only `store.local`, `gateway.local`, `dashboard.local`. Live Services for the three MCP servers are ClusterIP. |
| NetworkPolicy text limits MCP ingress to the gateway | Intended control, enforcement not proven now | `helm/urumi/templates/networkpolicy.yaml`. Live objects exist. Both Calico and kindnet pods are Running. No packet test this pass. |
| `compose.yaml` publishes Support `8001`, Utility `8002`, and WooCommerce MCP `8003` on the host | Real bypass when that Compose project is running | Ports `8001:8000`, `8002:8000`, `8003:8000`. Compose services were not in `docker ps`. Host ports 8000–8003 were not listening. |
| `README.md` local setup runs the three upstreams on `127.0.0.1:8001`–`8003` and points the gateway process at those URLs | Documented host-dev path. A client on that host could call the upstreams directly. | `README.md` around the uvicorn commands. Not running now (`127.0.0.1:8000` refused; 8001–8003 not listening). |
| `helm/urumi/README.md` lists `http://urumi-woocommerce-mcp:8000/mcp` and the Support and Utility equivalents | Operator documentation of the gateway's in-cluster upstream URLs, not an external-client instruction | Same file says external traffic uses Ingress and MCP Services have no Ingress or NodePort. |
| Bonus store manifests set `WOOCOMMERCE_BASE_URL` to `http://wordpress` inside a store namespace | Not a chat bypass. Provisioner flags are false and no `st-` namespace exists. | `apps/gateway/app/stores/manifests.py`. `storeProvisioner.enabled` and `admit` are false in `values.yaml`. |

The "nothing else talks directly" claim is **not** fully satisfied. The application path is correct. The Kubernetes network boundary is configured and not re-proven while two CNI-related pods are Running. The Compose file and the host-run README are real alternate ways to reach upstream MCP ports. They are not the current Kind deployment, and they are not unused dead code.

### CHART-SAME — local Kubernetes and VPS/k3s

| Marker | Result |
| --- | --- |
| `SAME_CHARTS_CONFIRMED_BY_SOURCE` | Yes. The only chart is `helm/urumi`. `deploy/wordpress/init-store.sh` is the Compose init script, not a second chart. Templates contain no `k3s` or `values-dev` branch. |
| `SAME_CHARTS_RENDERED_FOR_BOTH_CONFIGURATIONS` | Yes, on this machine, without contacting a cluster. |
| `VPS_DEPLOYMENT_OBSERVED` | No. |
| `VPS_DEPLOYMENT_NOT_VERIFIED` | Yes. |

Commands, Helm v4.3.0 (`/home/naval/.local/bin/helm`), repository root, no cluster contact:

| Command | Result |
| --- | --- |
| `helm lint helm/urumi` | Exit 0. Helm v4 logged `funcMap fail` INFO lines for missing `store.publicUrl` and Secrets, then `1 chart(s) linted, 0 chart(s) failed`. |
| `helm template urumi helm/urumi` | Exit 1. `upstream-deployment.yaml`: `Set database.existingSecret, or set database.password in a gitignored values file.` Stdout empty. No Secret body printed. |
| `helm lint helm/urumi -f helm/urumi/values-dev.yaml` | Exit 0. Icon INFO only. |
| `helm lint helm/urumi -f helm/urumi/values-vps.yaml` | Exit 0. Icon INFO only. |
| `helm template urumi helm/urumi -f helm/urumi/values-dev.yaml` | Exit 0. 2137 lines. 31 kind/name objects. |
| `helm template urumi helm/urumi -f helm/urumi/values-vps.yaml` | Exit 0. 2134 lines. The same 31 kind/name objects in the same order. |

Rendered kinds for both files: ConfigMap 3, Deployment 6, Ingress 1, Job 1, NetworkPolicy 8, PersistentVolumeClaim 1, Service 8, ServiceAccount 1, StatefulSet 2. No workload document lacked `resources:` or a liveness/readiness probe. Service types were ClusterIP only. `WOOCOMMERCE_BASE_URL` rendered as `http://urumi-wordpress` in both. Image pull policy `IfNotPresent` in both.

Differences are values, not templates:

| Setting | `values-dev.yaml` render | `values-vps.yaml` render |
| --- | --- | --- |
| Gateway image | `urumi-gateway:chat-20261018` | `replace-me/urumi-gateway:replace-me` |
| Dashboard image | `urumi-dashboard:module5-20261018` | `replace-me/urumi-dashboard:replace-me` |
| MCP images | `*:k8s-test` | `replace-me/urumi-*:replace-me` |
| Store and database images | `mariadb:11.4`, `wordpress:6.8-php8.2-apache`, `wordpress:cli-php8.2`, `postgres:16-alpine` | Same public tags |
| Hosts | `store.local`, `gateway.local`, `dashboard.local` | `store.replace-me.invalid`, `gateway.replace-me.invalid`, `dashboard.replace-me.invalid` |
| TLS | absent | Secret name `replace-me-tls` |
| Cookie | `GATEWAY_COOKIE_SECURE` false | true |
| StorageClass | empty, so cluster default | `replace-me` |
| `replace-me` count | 0 | 40 |
| Ingress class | `nginx` | `nginx` |
| Probe CIDR | `172.19.0.3/32` | none |
| Policy ingress namespace | `ingress-nginx` | `replace-me` |

`values-vps.yaml` says it is not a production configuration. k3s Traefik needs class `traefik` and namespace `kube-system`. The rendered file still has class `nginx` and namespace `replace-me` until an operator overrides them. Empty `probeCIDRs` adds no kubelet allow rule. The chart does not create the TLS certificate, DNS, registry images, or the existing Secrets. `helm/urumi/README.md` says the VPS install command was not run for that document. No VPS, registry, or DNS evidence was found in this pass.

The running Kind release is the same chart name and version, revision 1 from 2026-10-12, with stored tags `k8s-test`. It is not a render of today's `values-dev.yaml`, and it is not this checkout's images. Local deployment of the chart is historical. A current `helm upgrade` was not performed and must not be inferred.

`SAME_CHARTS_CONFIRMED_BY_SOURCE` and `SAME_CHARTS_RENDERED_FOR_BOTH_CONFIGURATIONS` stand. `VPS_DEPLOYMENT_NOT_VERIFIED` stands. The requirement "deployable with configuration changes only" is supported for the templates. It is not supported by an observed VPS install, and `values-vps.yaml` as committed is not install-ready.

### End-to-end flow table

| # | Flow | Source | Integration evidence | Automated test | Current runtime | Previous manual | Status and gap |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Storefront → database | WordPress env to `urumi-store-db`; PVC and MariaDB volume | Live ConfigMap host and Ready pods | `NOT_COVERED` | Storefront HTTP 200. PVC volume IDs unchanged from the tracker. No checkout. | 2026-10-18 orders 16 and 20. `NOT_REVALIDATED`. Admin screen not opened. | `PARTIAL`. Persistence configured, survival after recreate not retested. |
| 2 | WooCommerce MCP → WooCommerce | `WooClient` REST | Live `WOOCOMMERCE_BASE_URL=http://urumi-wordpress` | `PASS` scripted in `tests/test_woocommerce.py` | Pod Ready. No REST call. | 2026-10-18 `list_products` products 13 and 15. `NOT_REVALIDATED`. | `PARTIAL` |
| 3 | Gateway → WooCommerce MCP | `WOOCOMMERCE_MCP_URL` and `Registry._connect` | ConfigMap URL matches the Service | `PASS` `test_configured_tools_are_namespaced_and_isolated`, `test_dispatch_through_gateway` | URL configured. No `tools/call`. | 2026-10-18 tool list and `list_products`. `NOT_REVALIDATED`. | `PARTIAL` |
| 4 | Gateway → other upstreams | Support and Utility URLs in the same ConfigMap | Both Services ClusterIP and pods Ready. Database enabled flag not read. | `PASS` `tests/test_upstream_controls.py`, `tests/test_gateway_mcp.py` | ConfigMap `UTILITY_MCP_ENABLED=true` and `SUPPORT_MCP_ENABLED=true`. Not a successful call. Utility DB flag `NOT_REVALIDATED` (`K8S-03`). | 2026-10-18 three-upstream calls. `NOT_REVALIDATED`. | `PARTIAL` |
| 5 | External client → gateway → upstream | `POST /mcp` → `on_call_tool` → dispatcher | In-process client in `tests/mcp_session.py` | `PASS` gateway MCP tests this pass | `POST /mcp` not sent. Host port 8000 down. `gateway.local` health only. | 2026-10-18 official client. `NOT_REVALIDATED`. | `NOT_OBSERVED` live |
| 6 | Chat → gateway → product tool | Chat → dispatcher → `woocommerce__*` | Source path connected. Chat tests do not call `list_products`. | `PASS` for scripted `create_product` only. Read-only product chat `NOT_COVERED`. | No chat request | 2026-10-18 Groq `create_product` products 17 and 19. `NOT_REVALIDATED`. | `PARTIAL` |
| 7 | Chat → gateway → order tool | Same path, `woocommerce__list_orders` | `test_read_only_chat_hides_upstream_text_and_ungranted_tools` | `PASS` scripted | No chat request | 2026-10-18 list of orders 16, 18, 20. `NOT_REVALIDATED`. | `PARTIAL` |
| 8 | Admin dashboard → upstream management | `AdminPanel.tsx` and `admin_api.py` | Routes exist. Browser not driven. | Dashboard Vitest `NOT_RUN` this pass. Section F same-day result not repeated. | Dashboard HTTP 200 from an older bundle. No login. | Owner statement does not name pages. `NOT_USER_VERIFIED`. | `PARTIAL` |
| 9 | Local Kubernetes → all four layers | Chart release `urumi` | Pods Ready and Ingress answers | No live-cluster test | Ready pods and HTTP codes. Images are not this checkout. | 2026-10-18 Ready check. `NOT_REVALIDATED` as a demo of this tree. | `PARTIAL` |
| 10 | Same chart → VPS/k3s | `values-vps.yaml` overlay | Render matches dev kind/name set | `helm lint` and `helm template` exit 0 | No VPS | None | `VPS_DEPLOYMENT_NOT_VERIFIED` |

### Tests actually run in this pass

Working directory `/home/naval/project/urumi-project`. Dirty tree. No Docker containers were created by the tests.

```text
uv run pytest -q --tb=line tests/test_woocommerce.py tests/test_chat.py tests/test_gateway_mcp.py tests/test_upstream_controls.py tests/test_networkpolicy_intent.py
```

Result: 55 passed, exit 0, 15.28 seconds. Then `uv run pytest --collect-only -q` on the same files collected those 55 tests, exit 0.

Not run this pass: the rest of the 299 from section F, `tests/test_postgres_permissions.py`, `tests/test_reset_admin_password_postgres.py`, dashboard `npm test`, `npm run build`, `helm install`, `helm upgrade`, and any live tool or chat call.

### Runtime checks actually performed

Read-only. No pod delete, scale, exec, Secret decode, database query, or Helm mutation.

- Context `kind-urumi-k8s-dev`. Namespaces had no `st-` prefix. Workload pods listed in section G were still the same names and images, all Ready. Ingress controller `ingress-nginx-controller-6dfcd5998d-649sz` Ready.
- PVC volume IDs and `helm.sh/resource-policy=keep` as listed under LAYER-1. Both database StatefulSets retain PVCs on delete and scale.
- Services ClusterIP. Ingress hosts unchanged.
- NetworkPolicy generations and `ipBlock` values matched section G. No `172.19.0.3/32` on the live policies. Dev node is `172.19.0.2`. Test node is `172.19.0.3`.
- `kube-system` pods include `calico-node-w6xct`, `calico-kube-controllers`, `calico-typha`, and `kindnet-69wgb`, all Running. Enforcement not tested.
- ConfigMap and WooCommerce non-secret env values quoted above. Secret key names only.
- HTTP: `store.local/` 200, `dashboard.local/` 200 with `index-DienbTDh.js`, `gateway.local/` 401, `gateway.local/health` 200 `{"status":"ok"}`, `127.0.0.1:8000` connection refused, `127.0.0.1:5173/` 200.
- `docker ps` names: Kind nodes, `urumi-dev-port80` (`urumi-gateway:chat-20261018`), `urumi-dash01-062b4b1c` (`postgres:16-alpine` on `127.0.0.1:15432`). Compose project not running. Ports 8000–8003 not listening on the host. Port 80 is published by the test Kind node.

### Critical blockers

1. `K8S-01`. The running gateway, dashboard, and MCP images are not this checkout. Tool and chat behavior in the dirty tree is not what the cluster is running.
2. Live read-only `list_products` and `list_orders`, and a live chat turn, were not observed. The last recorded successes are 2026-10-18.
3. `CHART-SAME` / Module 11. Templates match. `values-vps.yaml` still has 40 `replace-me` placeholders and ingress class `nginx`. No VPS was deployed.
4. Network isolation is not currently proven. Policy objects exist, and both Calico and kindnet pods are Running.
5. Storefront health is not a checkout or a wp-admin check. Persistence after Pod recreation was not retested.

`K8S-03` (Utility database flag) still blocks a three-upstream claim. It is not required to prove that the WooCommerce tools exist in source.

### Recommended next action

Do not mark this requirement complete. Do not `helm upgrade`, retag, or apply `values-dev.yaml` or `values-vps.yaml`.

The next useful step is an explicit choice of which image a demo may claim. After that, a separately approved read-only gateway call to `list_products` and `list_orders` can test the running image without creating store data. A VPS install is a later values-and-cluster task, not a chart-template rewrite.

## H. Multi-store acceptance pass on 2026-10-24 evening

This section was appended. Sections A–G were not rewritten. Their baseline results stay historical.

The detailed ledger is `docs/verification/multi-store-acceptance.md`.

| Item | Result |
| --- | --- |
| Verdict | `PARTIAL`. No disposable cluster was created. Live store, packet, admission, and chat evidence is `BLOCKED`. |
| HEAD | Still `b4b813c568ca70b45d28944a44de4e8e770f874d` on `submission`. Nothing was committed. |
| In-process pytest | `uv run pytest -q --tb=line --ignore=tests/test_postgres_permissions.py --ignore=tests/test_reset_admin_password_postgres.py` — 304 passed, exit 0, 49.14s. The earlier section F result was 299 passed. The difference is tests added in the dirty tree, including this pass. |
| Dashboard | `npm test` in `dashboard/` — 6 files, 34 tests, exit 0. |
| Helm | Default lint exit 0. `enabled` without `admit` fails the render. `values-disposable.yaml` renders the admission policy and does not contain `0.0.0.0/0`. |
| Cluster | `kubectl config current-context` remained `kind-urumi-k8s-dev`. `kind get clusters` remained `urumi-k8s-dev` and `urumi-k8s-test`. No third cluster was created. |
| Why live acceptance stopped | About 2.6GiB RAM was available. The two protected control planes were already using about 2.1GiB and 1.3GiB. A NetworkPolicy-enforcing cluster plus two stores would need memory on that scale. Creating it could put the protected clusters under host memory pressure. |

## I. Readiness pass on 2026-10-24 night

This section was appended. Sections A–H were not rewritten.

Decision: `NOT READY` for disposable deployment and for the end-to-end scenario. Details and the new status labels are in `docs/verification/multi-store-acceptance.md` under "Readiness pass".

| Check | Result |
| --- | --- |
| Git | `submission` at `b4b813c568ca70b45d28944a44de4e8e770f874d`, dirty, nothing staged or committed |
| Five-fix pytest | 8 passed, exit 0, 6.56s. `PASS — UNIT/STATIC` |
| Helm invalid flags | both invalid combinations exit 1 with the expected fail text |
| Helm disposable render | exit 0, admission present, `0.0.0.0/0` absent, provisioner image `urumi-gateway:local` |
| WordPress image | Built `urumi-wordpress-woo:disposable-readiness-20261024`, id `sha256:a9137b31865af63b773bade56cba470f52fd70b24715bc83dfeb915c8c9c19ab`. Local activation of WooCommerce 9.4.3 exited 0 after one failed attempt that lacked database environment. Containers removed |
| Cluster | Context still `kind-urumi-k8s-dev`. Clusters still the two protected names. No third cluster |
| Host | About 2.5GiB available of 7.6GiB. Recommendation is a separate 12 GiB VM, not this machine |

## J. Disposable package pass on 2026-10-24 night

This section was appended. Sections A–I were not rewritten.

The package is ready for human review. It is `NOT READY` to provision. End-to-end completion stays 0. The ledger section is "Disposable package pass" in `docs/verification/multi-store-acceptance.md`.

| Check | Result |
| --- | --- |
| Git | `submission` at `b4b813c568ca70b45d28944a44de4e8e770f874d`, dirty, nothing staged or committed. `values.yaml` and `values-disposable.yaml` were not edited |
| Gateway image | Built `urumi-gateway:disposable-pkg-20261024`, id `sha256:f1f10ad1e6f31e1009c744a31281a33069bfe8ca1dd2c0dd3489e5a821a5a1d3`, dirty label true. Nine packaged source hashes match the working tree, including `cluster.py`, `chat.py`, and `init_store.sh`. `urumi-gateway:local` and `urumi-gateway:partial-20261019` were not retagged |
| Gateway startup | Local container only. Uvicorn started. `GET /health` returned 503 `{"status":"unavailable"}` with no database. Container removed. `PASS — LIVE COMPONENT` |
| WordPress image | Still `sha256:a9137b31865af63b773bade56cba470f52fd70b24715bc83dfeb915c8c9c19ab`. Plugin header WooCommerce 9.4.3. Not rebuilt |
| Proposed values | `helm/urumi/values-disposable-proposed.yaml`. Gateway and WordPress tags match the inspected images. Probe lists are empty on purpose. Dashboard and MCP tags are reserved and were not built |
| Helm template of that file | Exit 1 until a captured `/32` or `/128` is supplied. `0.0.0.0/0` and a `/24` also exit 1. `helm lint` still exits 0, so lint is not the gate |
| Gate-off shape render | Exit 0. No `0.0.0.0/0`, no protected node address, provisioner reconciler once, admission fail-closed. `PASS — UNIT/STATIC` |
| Tests | 14 passed, exit 0, 5.55s. The eight store/chat tests plus `tests/test_image_provenance.py` |
| Cluster | Context `kind-urumi-k8s-dev`. Clusters `urumi-k8s-dev` and `urumi-k8s-test` only |

The gateway image does not contain the Helm chart. The probe-gate edit in `_helpers.tpl` was made after the image build and does not change that image. Dashboard, Support, Utility, and WooCommerce MCP images were not built. Store NetworkPolicy, admission, and quota remain unexecuted.

## K. Manual demonstration preparation on 2026-10-24 night

This section was appended. Sections A–J were not rewritten.

The manual dashboard flow was not performed. End-to-end completion stays 0. No cluster or store was created. Details are in `docs/verification/multi-store-acceptance.md` under "Manual demonstration preparation".

| Check | Result |
| --- | --- |
| Probe policy | PostgreSQL and chart MariaDB ingress no longer include `probeFrom`. HTTP and TCP probe policies still do. Render with a documentation `/32` confirms the split. `PASS — UNIT/STATIC` |
| Init script | Local containers exited 0 after two defects were fixed: wait for MariaDB, and store the WooCommerce secret unhashed. Pages `shop`, `cart`, `checkout`, `my-account`. Key row lengths 64 and 43. Containers removed. `PASS — LIVE COMPONENT`. Not a Ready store |
| Gateway image | `urumi-gateway:disposable-manual-20261024` id `sha256:be4630516c3d6dbd0c1a7386fb40ff3d7e6f0f5cb1f32cb5b43408a00f4a1332`. Init script hash matches the tree. The older `disposable-pkg` gateway tag was not overwritten |
| Dashboard and MCP images | Tags `disposable-pkg-20261024` for dashboard, Support, Utility, and WooCommerce MCP. Ids are in the ledger. Demo tags were not retagged |
| Tests | Store/chat/policy pytest 71 passed, exit 0, 21.80s. Dashboard `npm test` 34 passed, exit 0 |
| Helm | Proposed values still fail closed while probe lists are empty. Install tag is the manual gateway image |
| Demonstration | Blocked until an approved disposable cluster, a captured probe `/32`, and a configured chat provider exist |
