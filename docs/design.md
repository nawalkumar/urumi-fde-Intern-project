# Urumi system design

This note describes the gateway as implemented in this repository. It is not a record of a live cluster, a storefront checkout, or a chat session against a model provider. NetworkPolicy enforcement and encryption of Kubernetes Secrets at rest are not claimed here. `helm/urumi/values-vps.yaml` is a placeholder worksheet, not a production configuration.

`docs/store-access-diagnosis.md` and `docs/store-access-fix-report.md` disagree about whether a storefront routing fix is in effect. This note does not resolve that disagreement.

## System overview

Urumi is a Streamable HTTP gateway. An external MCP client sends a Bearer API key to the gateway. The gateway checks that key, exposes only granted public tool names, calls one upstream MCP server, and writes an audit row. The implementation lives under `apps/gateway`. The HTTP process is `apps/gateway/app/main.py` `create_app`.

| Component | Path | Role |
| --- | --- | --- |
| External MCP clients | Call `POST /mcp` | Not a process in this repo. Tests use `mcp.client.streamable_http.streamable_http_client` in `tests/mcp_session.py`. |
| Authentication and API keys | `apps/gateway/app/keys.py`, `apps/gateway/app/auth.py`, `apps/gateway/app/http_auth.py` | Parse `urk_<key_id>.<secret>`, compare SHA-256, seal a `Principal`. |
| Tool registry | `apps/gateway/app/registry.py` `Registry` | Namespace tools, drop collisions, route calls. |
| Upstream catalog | `apps/gateway/app/catalog.py` `merge_upstreams`, `refresh_registry` | Merge environment upstreams with rows an administrator saved. |
| Support Notes | `apps/support_mcp` | Upstream MCP server. |
| Utility | `apps/utility_mcp` | Upstream MCP server. |
| WooCommerce MCP | `apps/woocommerce_mcp` | Custom MCP server. It calls the WooCommerce REST API when consumer credentials are set. |
| Dashboard and admin API | `dashboard/`, `apps/gateway/app/admin_api.py` | Browser UI and session-authenticated `/admin` mutations. `GET /admin/status` is the API-key route described under authentication. |
| Optional chat | `apps/gateway/app/chat.py`, `apps/gateway/app/chat_api.py`, `dashboard/src/admin/ChatPanel.tsx` | Signed-in user. Tool calls go through `ToolDispatcher`. |
| Gateway state | `apps/gateway/app/store.py` `PostgresStore` | Keys, grants, sessions, upstream rows, audit. Tables are created with `CREATE TABLE IF NOT EXISTS`. |
| Deployment | `compose.yaml`, `helm/urumi` | Compose project and one Helm chart. They do not share volumes. Default chart tags for MariaDB, WordPress, and PostgreSQL match the Compose images. Application tags are not all shared: `values.yaml` uses `local` and `values-dev.yaml` uses `k8s-test`. |

## Request lifecycle

`apps/common/http.py` `build_http_app` mounts the MCP SDK Streamable HTTP app at `/mcp` with `json_response=True` and `stateless_http=True`. `ApiKeyGuard` wraps only that app. `GET /health` is outside the guard.

### Shared authentication

1. `ApiKeyGuard` reads the `Authorization` header and `Authenticator.authenticate` accepts a live key (`apps/gateway/app/http_auth.py`).
2. Missing, malformed, unknown, wrong-secret, and revoked keys produce the same HTTP 401 body, `{"error":"invalid_token","error_description":"Authentication required."}`, and an audit row with actor `unauthenticated`, an empty tool name, and outcome `unauthorized`. The request does not reach an upstream.
3. If the key store raises `StoreUnavailable`, the response is HTTP 503 `{"error":"unavailable","error_description":"Authentication is unavailable."}`.
4. A live key is stored as a sealed `Principal` in a context variable. The raw secret is not kept on that object.

The tests do not include a hand-written `initialize` JSON body. They open the official Streamable HTTP client, which performs the SDK session setup, then call `list_tools` or `call_tool`.

### `tools/list`

`create_app` registers `on_list_tools` (`apps/gateway/app/main.py`).

1. `Authenticator.visible_names` returns the exact grants for the sealed key. A failure to read the store returns an empty tool list.
2. `Registry.list_tools_result` refreshes each enabled upstream. An upstream that is disabled, or whose latest list failed or timed out, contributes no tools to this result (`apps/gateway/app/registry.py`). A failed list does not delete a previous successful snapshot. That snapshot can still place a public name in `_collisions`, as described under tool registry.
3. Public names in `Registry._collisions` are omitted.
4. The handler keeps only names that are both granted and still advertised.

A name the key was not granted never appears, even if an upstream registered it. A granted name that collided or whose upstream failed the list does not appear either.

### `tools/call`

`on_call_tool` passes the sealed principal to `ToolDispatcher.execute_tool` (`apps/gateway/app/dispatch.py`).

1. `Authenticator.authorize` checks the seal again. A rejected principal returns the tool error `Authentication required.` and an `unauthorized` audit row.
2. `gateway__read_status` is not a callable tool. A call returns `Permission denied.`
3. `Authenticator.permits` requires the exact public name. There is no wildcard. Failure returns `Permission denied.` and outcome `unauthorized`.
4. `Registry.call` splits the name on the first `__`. A disabled upstream returns `Upstream <id> is disabled.` and is not contacted. A colliding name returns `Duplicate public tool name <name>. Conflicting registration excluded.` The call lists that upstream again before dispatch, so a collision discovered after the client's last `tools/list` is still rejected.
5. Timeouts become outcome `timeout` and the tool error `Upstream timed out while calling <name>.` Other upstream exceptions become outcome `upstream_failure` and a tool error that includes the exception type name, not the upstream body.
6. A normal `CallToolResult` is audited as `success` or `error` from `is_error`, with `duration_ms`. Arguments are not stored.

`Auditor.record` (`apps/gateway/app/audit.py`) writes the row after the upstream call has already returned or failed. If `add_audit` raises, the method logs the exception type and does not change the tool result. The audit insert and the upstream side effect are not one database transaction. That limitation is also stated in `README.md` under "Limitations".

## Tool registry and failure isolation

`Registry._namespace` copies each upstream tool to the public name `{upstream_id}__{tool_name}` (`apps/gateway/app/registry.py`). `_unique_upstreams`, used by `Registry.configure`, rejects an empty id, a duplicate id, or an id containing `__` with `GatewayConfigError` before it replaces the registry. `configure` restores its previous snapshot if configuration raises. Because the id is the prefix before the first `__`, two different upstream ids cannot produce the same public string. The same original tool name on two upstreams stays two public names. `tests/test_upstream_controls.py` `test_shared_original_names_stay_distinct` lists `utility__echo` and `support__echo` and calls each one.

`_colliding_public_names` puts a public name in `_collisions` when that string appears more than once in the combined snapshots. The tested case is one upstream listing `echo` twice, which namespaces to `dup__echo` twice (`test_duplicate_public_tool_names_are_excluded`). Discovery omits `dup__echo`. `Registry.call` returns `Duplicate public tool name dup__echo. Conflicting registration excluded.` and does not invoke that tool. The other upstream's `other__echo` still succeeds. Disabling an upstream drops its registration from collision accounting (`Registry.set_enabled`).

`UPSTREAM_TIMEOUT_SECONDS` bounds an upstream list or call. The process default is 5. Compose and the Helm chart set 20. A timed-out or failed list adds that upstream to `_list_failed`. `list_tools_result` then skips it, so it contributes no tools to the current discovery result. Other upstreams stay in place (`tests/test_gateway_mcp.py` `test_timeout_skips_slow_list_and_fails_slow_call` and `test_unavailable_upstream_is_isolated`). `_refresh_upstream` does not delete `_registrations` on that failure. `_snapshots` still returns the last successful public names for an enabled upstream, and `_refresh_derived_state` rebuilds `_collisions` from those snapshots. An enabled upstream with no successful list yet contributes nothing to that accounting. A later failed list can therefore hide the upstream from discovery while its previous names still count as collisions.

Admin create, update, and delete of an upstream call `_refresh_committed` after the store write commits (`apps/gateway/app/admin_api.py`). The function snapshots the registry, calls `refresh_registry`, and on any exception restores that snapshot and undoes the write.

- Undo succeeds: HTTP 503, `persisted: false`, no audit row. The response error is `registry_refresh_failed`.
- Undo fails: the saved change remains, `_reconcile_saved_change` tries to align the in-memory registry with that one change, and the response is HTTP 500 with `persisted: true`. The audit outcome is `error`, not `success`.

`tests/test_admin_api.py` `test_failed_registry_refresh_is_not_reported_as_success` covers those branches. Compensation is not a single transaction around the in-process refresh. A crash after the commit and before the undo can leave a stored change with no audit row. `GET /admin/upstreams` catches a refresh exception other than `StoreUnavailable` and still returns the current registry report. That path does not undo a write.

## Authentication and authorization

`generate_key` in `apps/gateway/app/keys.py` builds `urk_` plus 16 hexadecimal characters, a dot, and a URL-safe secret. `hash_key` stores SHA-256 of the whole string. `KeyStore.issue` returns the raw key once. The store keeps the key id and the hash.

`revoke` sets revocation on that key. `tests/test_gateway_auth.py` `test_rejected_keys_do_not_reach_upstream_and_a_live_key_still_works` shows a revoked key receives the same 401 as an unknown key and does not reach an upstream. A different live key still calls its granted tool.

Grants are exact public names. `tests/test_tool_permissions.py` and `tests/test_postgres_permissions.py` cover independent grants and revocation of a stored permission. Discovery and `tools/call` both consult those grants, as described above. A tool grant does not authorize admin mutations.

`GET /admin/status` is a separate route (`create_app` `register_status` in `apps/gateway/app/main.py`). It accepts a live API key through `require_live_principal`. It does not accept the dashboard session. `status_permission_mode` in `apps/gateway/app/config.py` maps an unset, empty, or `open` `GATEWAY_STATUS_PERMISSION` to `open`. In that mode the handler does not check `gateway__read_status`, so any live key can read the status report. `required` calls `Authenticator.permits` for `gateway__read_status`. A live key without that grant gets HTTP 403 `{"error":"forbidden","error_description":"Status access was denied."}`. Any other value raises `StatusPermissionConfigError` at startup. This grant does not make the name an MCP tool, and it does not apply to the other `/admin` routes.

The dashboard session is a username and password exchanged for an HttpOnly `urumi_session` cookie (`apps/gateway/app/admin_api.py`). `_session_token` also accepts an `Authorization` bearer token that starts with `urs_`. Members can issue, list, and revoke only their own keys and can read only their own audit rows. `POST /admin/keys` for a member ignores a missing owner and rejects a different `user_id`. An `urk_` API key is not a session token, so it cannot perform those mutations. Disabling a user revokes that user's sessions. The implementation does not make the cookie and the API key interchangeable, and it does not promise that a failed audit insert rolls back a completed admin write. `Auditor.record` swallows an insert failure after the mutation has succeeded.

## Data and persistence

Gateway PostgreSQL holds key hashes, grants, sessions, dynamic upstream rows, upstream enablement flags, and `gateway_audit_log` (`apps/gateway/app/store.py`). Audit columns include `occurred_at`, `actor`, `tool_name`, `outcome`, `upstream_id`, `duration_ms`, `user_id`, and `target`. Outcomes are constrained to `success`, `error`, `timeout`, `upstream_failure`, and `unauthorized`.

WooCommerce product and order records are in the store database, MariaDB, behind WordPress. The WooCommerce MCP process does not own that database. It calls the REST API. `_OrderReservations` in `apps/woocommerce_mcp/app/tools.py` is created when `register_tools` runs and lives in that process. `claim`, `release`, and `mark` update an in-memory dictionary. That reservation state is not in gateway PostgreSQL. `_order_body` also sends `meta_data` key `urumi_idempotency_key` on the WooCommerce order POST, so a stored order can carry the key in MariaDB. `tools.py` does not read that metadata back and does not rebuild `_OrderReservations` after a restart. A new process starts with an empty dictionary.

WordPress content and the store database are separate volumes from the gateway database.

| Environment | Gateway data | Store data |
| --- | --- | --- |
| Compose | Named volume `gateway_db` on `gateway-db` (`compose.yaml`) | Named volumes `wordpress_db` and `wordpress_html` |
| Helm | StatefulSet claim at `/var/lib/postgresql/data`, default `1Gi`, `retain: true` (`helm/urumi/values.yaml` `database.persistence`, `templates/gateway-db-statefulset.yaml`) | MariaDB StatefulSet claim and WordPress PVC (`templates/store-db-statefulset.yaml`, `templates/wordpress-pvc.yaml`) |

The chart README says not to mount the Compose volume names into the cluster. Nothing in the chart migrates data from one to the other. Deleting a PVC, or using a StorageClass whose reclaim policy is `Delete`, can destroy a Helm volume even when `helm.sh/resource-policy: keep` is set. That limitation is stated in `helm/urumi/README.md` under "Persistence" and "Rollback".

## Deployment model

Compose (`compose.yaml`) runs WordPress, MariaDB, gateway PostgreSQL, the gateway, Support, Utility, and the WooCommerce MCP server. Published host ports are WordPress 8080 and the four HTTP services 8000–8003. The databases are not published. `gateway` uses `urumi-gateway:local` from `apps/gateway/Dockerfile`. `support-mcp`, `utility-mcp`, and `woocommerce-mcp` use `urumi-support-mcp:local`, `urumi-utility-mcp:local`, and `urumi-woocommerce-mcp:local`, from `apps/support_mcp/Dockerfile`, `apps/utility_mcp/Dockerfile`, and `apps/woocommerce_mcp/Dockerfile`. `db` uses `mariadb:11.4`. `wordpress` uses `wordpress:6.8-php8.2-apache`, and `wordpress-init` uses `wordpress:cli-php8.2`. `gateway-db` uses `postgres:16-alpine`. `GATEWAY_UPSTREAM_KEY` and `CHAT_API_KEY` are not passed into the gateway service. Dynamic upstream credentials and chat stay off until an operator adds them. WooCommerce tools stay unregistered until `WOOCOMMERCE_BASE_URL`, `WOOCOMMERCE_CONSUMER_KEY`, and `WOOCOMMERCE_CONSUMER_SECRET` are set.

The Helm chart `helm/urumi` packages the same processes plus a dashboard Deployment and a store init Job. Services are ClusterIP. Ingress, when enabled, is only the storefront, dashboard, and gateway. The chart does not install an ingress controller, build images, or issue gateway API keys.

`values.yaml` tags the gateway, dashboard, and three MCP images `*:local`. `urumi.validate` in `helm/urumi/templates/_helpers.tpl` fails unless the gateway database sets `database.existingSecret` or `database.password`, and not both. When the store is enabled, it fails unless the store sets `store.existingSecret` or the MariaDB and WordPress administrator values `store.mariadb.password`, `store.mariadb.rootPassword`, `store.wordpress.adminUser`, `store.wordpress.adminPassword`, and `store.wordpress.adminEmail`. Those three passwords cannot be combined with `store.existingSecret`. The store must also set `store.publicUrl`. That URL does not replace the store credentials. A Kubernetes Secret is not the only accepted form. The gateway database can use `database.password` instead, and the store can use those administrator values instead. `values-dev.yaml` sets tag `k8s-test` and HTTP hosts `store.local`, `gateway.local`, and `dashboard.local`. `values-vps.yaml` uses `replace-me` for image names, hosts, storage class, TLS, and Secret names. Rendering that file is not a production deploy.

Gateway liveness in `templates/gateway-deployment.yaml` uses `urumi.tcpProbe` on the HTTP port (`_helpers.tpl`). Startup and readiness use `urumi.httpProbe` against `/health` (`values.yaml` `probes.gateway`). `/health` returns 503 when PostgreSQL does not answer, so a database outage fails the HTTP probes and does not, by itself, fail the TCP liveness probe. Those probes are not interchangeable. The Compose gateway service healthcheck calls `http://127.0.0.1:8000/health` (`compose.yaml`). A non-success response, including that 503, fails the Compose check. `apps/gateway/Dockerfile` defines no image `HEALTHCHECK` for this reason. The Compose check is a file-level healthcheck, not the chart's TCP liveness probe. Upstream MCP chart probes use `/health` for startup, liveness, and readiness. The chart README states that Kubernetes has not been used to validate the runtime user, probes, or NetworkPolicy. `templates/networkpolicy.yaml` is rendered only when `networkPolicy.enabled` is true. Kind's default networking does not enforce those objects. This repository has not shown that a policy-enforcing CNI is installed.

Tests in `tests/` cover the gateway with an in-memory store or a disposable `postgres:16-alpine` container. They do not install the chart. `helm/urumi/README.md` "Rollback" says the chart has not been upgraded in a cluster.

## Design decisions and trade-offs

**Namespaced names and collision exclusion.** The public name is `{upstream_id}__{tool_name}` so a call can be routed by splitting on `__` (`Registry.call`). Ids that contain `__` are rejected so the split stays unambiguous. Different upstream ids therefore stay distinct even when the original tool names match. `_collisions` excludes a public name that one registration lists more than once, instead of choosing one copy. The alternative of serving one copy would hide which listing won. The trade-off is that the duplicated public name disappears from discovery and from calls until that listing no longer repeats it. That behavior is implemented and tested. A written comparison with other naming schemes is not in the source.

**TCP liveness versus `/health` readiness.** `apps/gateway/Dockerfile` states that `/health` returns 503 when PostgreSQL is unavailable, so a container health check on that URL would treat a database outage as a dead process. The chart uses TCP for gateway liveness and `/health` for startup and readiness. The alternative, using `/health` for liveness, would restart the pod during a database outage. The documented chart trade-off is that a process can stay up while it is not ready. Compose does not use that split: its gateway healthcheck requests `/health`, so the same database outage fails the Compose check. Liveness and readiness are not the same probe.

**Trusted environment upstreams versus dynamic upstreams.** `merge_upstreams` sets `trusted=trust_environment` on static upstreams. The default of `UPSTREAM_TRUST_ENVIRONMENT` is true (`apps/gateway/app/config.py`). Dynamic rows are saved with `trusted=False` (`apps/gateway/app/catalog.py`). `Registry._connect` calls `validate_upstream_url` only when `trusted` is false, and `upstream_http_client` installs `PolicyTransport` only for those untrusted clients. A trusted environment upstream skips that check. README "Security" states the same default: environment upstreams are trusted unless `UPSTREAM_TRUST_ENVIRONMENT` is false. It does not say the flag exists to allow Compose hostnames. `compose.yaml` points the gateway at `support-mcp`, `utility-mcp`, and `woocommerce-mcp`. `ssrf.py` comments that local Compose uses loopback and that trust keeps that stack working. That comment is not what this Compose file configures, so it is not used here as the rationale.

For an untrusted URL, `validate_upstream_url` rejects the metadata hostnames in `ssrf.py` (`localhost`, `metadata`, `metadata.google.internal`, `metadata.google.com`, and a name ending in `.localhost`). `_blocked` always rejects loopback, link-local, multicast, reserved, and unspecified addresses, and the metadata addresses listed in that module. Private addresses are rejected only when `allow_private` is false. `UPSTREAM_ALLOW_PRIVATE` supplies that flag. README "Security" says private addresses are allowed unless that variable is false. Setting `UPSTREAM_TRUST_ENVIRONMENT` false applies this check to environment URLs. It does not preserve the trusted bypass. An inference, not a statement in the source, is that the bypass exists so an operator can point a static upstream at an address the policy would reject. Tests in `tests/test_ssrf.py` cover the policy. They do not show a live cluster blocking a metadata address.

**In-memory order idempotency.** `_OrderReservations` stops a second `create_order` with the same key and the same line items from posting twice in one process. A different line-item fingerprint for that key is rejected. The reservation dictionary is process-local. `_order_body` sends `urumi_idempotency_key` as WooCommerce order metadata, and `tools.py` never loads it back into `_OrderReservations`. README "Security" states that this does not survive a restart and is not safe across multiple WooCommerce MCP processes. A restart can submit the order again. `create_order` creates an unpaid pending order. It is not a card payment and not a completed storefront checkout.

**Audit writes after side effects.** The dispatcher calls the upstream first and then `Auditor.record`. Admin success rows are written after the store reports success. A failed insert does not change the HTTP result. The alternative, one transaction for the side effect and the audit row, is not what the code does. The gateway does not own the upstream's database, so a single transaction across an MCP call and `gateway_audit_log` is not available in this design. That last sentence is a constraint inferred from the process boundary. The non-transactional behavior itself is in the source.

**Mocked tests versus live integration.** Most tests use an in-memory store and scripted HTTP. Dashboard tests mock HTTP. `README.md` "Limitations" states that chat was not run against a live model provider and that the tests do not prove a running cluster serves this source. The disposable PostgreSQL tests do start a real database process. They do not start Compose, Kind, or WooCommerce.

**Compose versus Kubernetes.** Compose volumes and Helm PVCs are different disks. Building `urumi-gateway:local` does not change a node that runs `k8s-test`. The chart README states that a pod runs the loaded image tag, not the working tree. The trade-off of two deployment paths is that a reviewer can run either stack, and a change to one is not a change to the other.

## Limitations and operational risks

These limits are stated in `README.md` or `helm/urumi/README.md`, or they are the source behavior cited above.

- An audit insert can fail, or the process can crash, after a tool call or admin write has committed. The caller can still see success, and the audit row can be missing.
- Registry-refresh compensation undoes an upstream write when the undo succeeds. It is not one transaction with the refresh.
- `GET /admin/upstreams` continues after a non-availability refresh error.
- `woocommerce__create_order` reservation state is in one process's memory. Order metadata may store `urumi_idempotency_key`, and the MCP server does not use that metadata to restore the reservation.
- Chart resource requests are placeholders, not a measured capacity plan.
- `values-vps.yaml` is not production-ready.
- Helm upgrade and rollback have not been executed in a cluster, according to the chart README. Volume deletion can still destroy data.
- NetworkPolicy objects do not restrict traffic on a CNI that does not enforce them. Enforcement was not verified.
- A Kubernetes Secret is base64, not encryption. Encryption at rest was not verified.
- The storefront documents disagree, as noted at the top of this file. This note does not say which access path works today.

## Verification status

**Source.** The behaviors above are taken from the files and functions named in each section.

**Automated tests.** Protocol, auth, grants, collisions, timeouts, SSRF, admin compensation, WooCommerce tool handlers, and disposable PostgreSQL persistence are under `tests/`. Dashboard Vitest tests mock HTTP. The workflow that runs them is `.github/workflows/ci.yml`: `uv run pytest` for the gateway, and `npm test` plus `npm run build` for the dashboard.

**CI.** The public check-runs API for commit `d7ddff1d26cd6ff9e6b96da4282bfc51cf93b178` reported workflow run `38537165909` completed with `gateway` and `dashboard` success. That run does not include this file or the uncommitted README edit. Job logs were not re-read for this note.

**Not verified live at the time this note was first written.** Storefront reachability, a browser checkout, order lookup against a real store, the image tag running in Kind, ingress, NetworkPolicy enforcement, PVC retain behavior under delete, Helm upgrade and rollback, and chat against a live model provider. Unit and chart-template success do not establish those.

Later checks are recorded in `docs/baseline-verification.md` and do not replace this source description. On 2026-10-14 the dev cluster's Ingress answered `store.local`, `dashboard.local`, and `gateway.local` on the dev node IP. Host port 80 still belongs to the other Kind node. A non-gateway pod could still reach WooCommerce MCP, so NetworkPolicy was not enforced. No live model-provider chat was run.
