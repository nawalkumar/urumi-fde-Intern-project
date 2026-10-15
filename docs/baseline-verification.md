# Baseline verification

Checked on 2026-10-14 against context `kind-urumi-k8s-dev`, namespace `default`, Helm release `urumi` revision 1. The test cluster `kind-urumi-k8s-test` was not modified. Compose containers were left stopped.

This file keeps failed and blocked checks. A later pass does not erase them.

## Commands

| Check | Command | Result |
| --- | --- | --- |
| Gateway tests before behavior edits | `uv run pytest -q --tb=line` | 151 passed |
| Gateway tests after the audit-failure test | `uv run pytest -q --tb=line` | 152 passed |
| Focused auth and admin tests | `uv run pytest -q --tb=short tests/test_admin_api.py tests/test_gateway_auth.py` | 15 passed |
| Dashboard tests and build | `npm test` and `npm run build` in `dashboard/` | 23 passed, `tsc --noEmit && vite build` succeeded |
| Helm | `helm lint helm/urumi -f helm/urumi/values-dev.yaml` and the same for `values-vps.yaml` | both linted, 0 failed |
| Helm render | `helm template` for `values-dev.yaml` and `values-vps.yaml` | both rendered. The VPS render contains 33 `replace-me` placeholders |

`values-vps.yaml` is a worksheet. It was not installed.

## Requirement matrix

| ID | Requirement | Status |
| --- | --- | --- |
| R1 | One persistent WooCommerce store on local Kubernetes | PASS — VERIFIED |
| R2 | Custom WooCommerce MCP server, Streamable HTTP | PASS — VERIFIED |
| R3 | Gateway aggregates at least three upstreams | PASS — VERIFIED |
| R4 | React dashboard for servers, members, keys, audit, and chat | PASS — VERIFIED |
| R5 | Admin and member roles, revocable hashed API keys, member self-service | PASS — VERIFIED |
| R6 | Chat agent calls tools as the logged-in user | PARTIAL |
| R7 | External MCP client uses the gateway URL and that user's key | PASS — VERIFIED |
| R8 | One Helm chart for local and production-like values | PASS — VERIFIED |
| R9 | Persistent store database and WordPress content | PASS — VERIFIED |
| R10 | Stable storefront and dashboard URLs through Ingress | PARTIAL |
| R11 | Probes and resource requests/limits | PASS — VERIFIED |
| R12 | Kubernetes Secrets, no hardcoded secrets | PASS — VERIFIED |
| R13 | Server-side upstream credentials | PASS — VERIFIED |
| R14 | Collisions, timeouts, failure isolation, disabled and unreachable upstreams | PASS — VERIFIED |
| R15 | Tool-call audit and dashboard logs | PASS — VERIFIED |
| R16 | README, design, and threat model | PASS — VERIFIED |
| R17 | Definition-of-done workflow | PARTIAL |

## Evidence

### R1 and R9 — store and persistence

WordPress, MariaDB, and the three PVCs were already running. PVC volume IDs stayed the same after the checks:

- `data-urumi-gateway-db-0` `pvc-f77b4777-44e3-4c02-9309-a1cf3c1332cf`
- `data-urumi-store-db-0` `pvc-4283d20f-494e-4a15-81d4-6f514adf028f`
- `urumi-wordpress-data` `pvc-1e0101de-5c7e-4f29-b19c-7a0c25eddbf3`

Cash on delivery was already enabled. Store timezone is `+00:00`. A dummy product `URUMI-BASELINE-STOREFRONT` (id 13, price 19.00 INR) was created. The Store API add-to-cart returned HTTP 201 with one item. Checkout with `payment_method=cod` created order 14, status `processing`, total 19.00. The same order and product 15 (`URUMI-BASELINE-MCP`, price 7.25) were still present after the WordPress pod was recreated and after `urumi-store-db-0` was recreated.

These records were left in place so the check can be repeated. They are labeled test data.

### R2 — WooCommerce MCP

`apps/woocommerce_mcp` is the repository's server. From the gateway pod, the official MCP client listed `create_order`, `create_product`, `get_order`, `list_orders`, `list_products`, and `update_order_status`. `list_products` found product 13. `per_page=0` returned `per_page must be an integer from 1 to 100.` `get_order` and `list_orders` for 2026-10-14 UTC returned order 14. `create_product` created product 15, which the storefront data still contains.

The WooCommerce MCP Service is ClusterIP. A disposable non-gateway pod still received HTTP 200 from `http://urumi-woocommerce-mcp.default.svc.cluster.local:8000/health`. The NetworkPolicy objects are present, and Kind's kindnet does not enforce them. That isolation gap is R17, not a missing server.

### R3, R7, and R14 — gateway, external client, isolation

A temporary member and hashed API key were inserted, used, revoked, and then removed. The raw key was not printed. The official Streamable HTTP client (`mcp` Python SDK) connected to `svc/urumi-gateway` through a localhost port-forward with only that key.

`list_tools` returned `support__search_notes`, `utility__convert_units`, `woocommerce__get_order`, and `woocommerce__list_orders`. One session called WooCommerce, Utility, and Support. Audit rows for user `ab989f49b4db7bc7` recorded the tool, upstream, outcome, and a duration.

Scaling `urumi-utility-mcp` to 0 replicas made `utility__convert_units` return `Upstream call failed for utility__convert_units: ConnectError.` `woocommerce__get_order` still returned order 14. `GET /admin/status` showed utility `listing_failed` and the other two upstreams `listing_ok`. The deployment was scaled back to 1 and `utility__convert_units` returned `2000`.

An existing `gateway_upstream_flags` row for utility was `true`. It was set to `false`, the gateway pod was recreated, and `utility__convert_units` disappeared from discovery. A direct call returned `Upstream utility is disabled.` Support still answered. The flag was restored to `true` and the tools returned. The revoked key, a missing key, and a malformed key each received HTTP 401.

Timeout behavior is covered by `tests/test_gateway_mcp.py` `test_timeout_skips_slow_list_and_fails_slow_call`. The live failure above was a refused connection, audited as `upstream_failure`, not a timeout. Collision exclusion remains the unit test `test_duplicate_public_tool_names_are_excluded`.

### R4 and R5 — dashboard, roles, and keys

`POST /admin/keys` now lets a member issue a key only for that member. A different `user_id` returns HTTP 403 and no key. `tests/test_admin_api.py` and `dashboard/src/admin/AdminPanel.test.tsx` cover it. The dashboard card shows Connection as Connected, Unreachable, or Disabled from the listing state.

The dev deployments were updated to `urumi-gateway:baseline-20261014` and `urumi-dashboard:baseline-20261014`. Previous images, still present locally, were `urumi-gateway:reset-admin-20261013` and `urumi-dashboard:k8s-test`. Gateway `/health` returned `{"status":"ok"}`. The served dashboard script `/assets/index-tLg0ie7m.js` contains `Issue my API key` and `Connection:`.

Against that gateway, a temporary member logged in, received 403 when issuing a key for the administrator, received 201 for their own key, did not see the raw key on the following list, received 403 from `GET /admin/users`, and revoked the key. The temporary user, session, and key were deleted.

### R6 — chat

`tests/test_chat.py` mocks the provider and passed inside the 152-test run. The live deployment references Secret `urumi-dev-chat`, and that Secret does not exist. `CHAT_API_KEY` is optional and unset. No live model call was made.

### R8, R11, and R12 — Helm, probes, secrets

The same chart rendered for `values-dev.yaml` and `values-vps.yaml`. Workloads in the dev render include WordPress, MariaDB, gateway PostgreSQL, gateway, dashboard, and the three MCP servers. Live containers for those workloads have startup, liveness, and readiness probes plus requests and limits. The WordPress MariaDB wait container is an init container with requests and limits. The store init Job is a finite script with requests and limits and no probe: a liveness failure would abort installation, and it does not serve traffic.

Database, store, and WooCommerce credentials are Kubernetes Secrets referenced by the deployment. `values-vps.yaml` does not contain usable production values.

### R13 — upstream credentials

`tests/test_secretsbox.py` covers tampering and a missing key. The dev cluster had no `urumi-dev-gateway-keys` Secret, so `GATEWAY_UPSTREAM_KEY` was unset. A generated Secret was created with that name and the gateway was restarted. The value was not printed and was not written into the repository.

A temporary administrator then created disabled upstream `baselineprobe` at `https://example.com/mcp` with a credential. The create and list responses did not contain the credential, `has_credential` was true, and the database value was sealed rather than the plaintext. The upstream, the administrator, and the session were deleted. `remaining` for that upstream id was 0.

### R15 — audit

The live rows above include actor key id, user id, upstream, tool, outcome, and duration. `tests/test_gateway_auth.py` `test_audit_write_failure_keeps_the_completed_tool_result` shows that a failed audit insert does not change a completed tool result and logs `Audit write failed: RuntimeError` without the exception message. That is the existing tradeoff: the gateway does not share a transaction with the upstream. The dashboard audit component is covered by `AdminPanel.test.tsx`. Those live rows remained after the gateway process was recreated. The gateway database pod was not recreated in this pass; its PVC was left in place.

### R10 and R16 — Ingress and docs

`ingress-nginx` controller `v1.12.1` was installed in namespace `ingress-nginx`. The dev node was labeled `ingress-ready=true` so the Kind manifest could schedule. `curl --resolve store.local:80:172.19.0.2` returned HTTP 200 and the test product page. The same method returned the dashboard title and gateway `{"status":"ok"}`. `curl http://127.0.0.1/` to host port 80 is still `Connection reset by peer` because that port belongs to the test cluster.

README, `docs/design.md`, and `docs/threat-model.md` describe this behavior. The threat model answers the four assignment questions in "Assignment questions".

## Definition of done

| Step | Result |
| --- | --- |
| Storefront product visible through dev Ingress | PASS. Product 13 page HTTP 200 via the dev node |
| Add to cart and COD checkout | PASS. Order 14, status processing, payment `cod` |
| Order present in store data | PASS. `wc_get_order(14)` after pod and database restarts |
| User A chat creates a product | NOT TESTED live. No chat provider credential. Product 15 was created by the WooCommerce MCP tool instead |
| User A asks for today's orders | NOT TESTED as a model answer. `list_orders` for 2026-10-14 UTC returned order 14 |
| Audit attributed to User A | PASS for the temporary MCP user, not for a chat session |
| User B external client, two upstreams, revoke, disable | PASS with the MCP Python SDK |
| One model prompt calling two upstreams | NOT TESTED. No provider credential |
| Unreachable upstream leaves the others working | PASS. Utility scaled to 0, then restored |
| Dashboard shows the failed upstream | PASS. Status listing state `listing_failed` while the others were `listing_ok` |
| Browser on `http://store.local/` reaches the dev pod | FAIL — REPRODUCED. Port 80 is the test cluster |

## Remaining blockers

1. Host port 80/443 is published by `urumi-k8s-test-control-plane`. Moving it means recreating that Kind node or changing host firewall or hosts files. Those actions were not taken. The dev Ingress works on the dev node IP.
2. NetworkPolicy is not enforced by kindnet. A non-gateway pod reached WooCommerce MCP. Replacing the CNI on the live cluster was not attempted because a failed swap can disconnect the store. Recreating the cluster was not allowed.
3. Secret `urumi-dev-chat` is absent, so live chat and a single model prompt that calls two upstreams were not run.
4. `helm upgrade` and rollback were not executed. The chart README already says they have not been run in a cluster. The image update was `kubectl set image` and `kubectl rollout restart`, not a Helm upgrade.
5. Git push of this work needs GitHub credentials. `git push` over HTTPS failed with `could not read Username`. `gh` is not installed and no token was present in the environment.

## Continuation — 2026-10-15

Rechecked context `kind-urumi-k8s-dev`. The node `urumi-k8s-dev-control-plane` is Ready at `172.19.0.3`. The test cluster was not modified. The three PVC bindings are unchanged:

- `data-urumi-gateway-db-0` → `pvc-f77b4777-44e3-4c02-9309-a1cf3c1332cf`
- `data-urumi-store-db-0` → `pvc-4283d20f-494e-4a15-81d4-6f514adf028f`
- `urumi-wordpress-data` → `pvc-1e0101de-5c7e-4f29-b19c-7a0c25eddbf3`

`urumi-gateway-db-0` and `urumi-store-db-0` are Running. Gateway, dashboard, WordPress, the MCP servers, CoreDNS, and ingress-nginx are not Ready. The host route table has no `10.244` routes, so those pod addresses follow the default route via `172.19.0.1`. Each sandbox from `10.244.0.2` through `10.244.0.14` still has its address on `eth0` inside its network namespace, with an up host `veth`. kindnet's daemon log is an nftables sync loop and does not install those routes. The `ptp` plugin in `10-kindnet.conflist` installs them when a sandbox is created. `10-calico.conflist` sorts first. The Calico CNI log shows failed `CNI DEL` calls and no `CNI ADD` entries. Felix ran one IPv4 route resync at startup and later reconciles IP sets.

A policy-only Calico install is present (`calico-node` DaemonSet desired 1, plus Typha and kube-controllers). It is not a healthy replacement dataplane. Removing it would leave kindnet, which does not enforce NetworkPolicy. Isolation is not met. The manual `/32` route restore was preflighted and has not been applied.

`values-dev.yaml` now records gateway and dashboard tag `baseline-20261014`, matching the images loaded for those Deployments, and probe source `172.19.0.3/32`. Helm lint of `values-dev.yaml` and `values-vps.yaml` returned 0 failed. The VPS render still contains `replace-me` and is not deploy-ready. `tests/test_networkpolicy_intent.py` checks the WooCommerce policy text. `tests/test_chat.py` includes a scripted provider test that one prompt calls WooCommerce and support tools and that a disabled support tool is not offered again. That test does not call a live model. Secret `urumi-dev-chat` is still absent.

Local checks on 2026-10-15: `uv run pytest -q --tb=line` passed (155). Focused chat, policy, admin, and auth tests passed (25). Dashboard `npm test` passed (23) and `npm run build` succeeded. Host port 80 remains the test cluster. Helm upgrade and rollback were not run while pod routes are missing. `GIT_TERMINAL_PROMPT=0 git push origin main` failed with `could not read Username`. No token was read or printed.

On 2026-10-15 the approved `/32` restore was applied on `urumi-k8s-dev-control-plane` after the 13 veth mappings were read again and still matched. `ip route get 10.244.0.10` and `10.244.0.13` used the expected veths, and 13 routes were present. TCP from the store namespace to gateway PostgreSQL, and from the gateway namespace to store MariaDB, timed out while those routes still existed. A second check found the route count back at 0. The same 13 veths were still up with the same addresses. Felix's summaries in that minute listed IP-set resync only, and kindnet logged nftables errors rather than a route change. The routes were not re-added. Calico, kindnet, pods, and the three PVCs were not deleted. Node stayed Ready. Isolation is still unmet.

A later read of the live DaemonSet shows `FELIX_INTERFACEPREFIX=veth`, `CALICO_NETWORKING_BACKEND=none`, and `CALICO_MANAGE_CNI=false` only on the main container. The `install-cni` init container still has `CNI_CONF_NAME=10-calico.conflist` and writes that file on every start. The node has 13 `veth` interfaces and no `cali` interfaces. Changing the prefix to `cali` would not create routes, would not stop that init container from winning CNI selection, and would point Felix at interfaces that are not present. That change was not applied.

A one-route monitor on 2026-10-15 added only `10.244.0.3/32` via `veth4d384102` after that mapping was rechecked. `ip monitor` recorded `Deleted 10.244.0.3 dev veth4d384102 scope link` 47 seconds later. At `15:14:42` Felix logged `Removing conntrack flows ip=10.244.0.3`. kindnet's netlink socket is protocol 12. `calico-node` holds the route netlink sockets. The Felix binary defaults `RemoveExternalRoutes` to true. The test route was not left in place. No DaemonSet or CNI file was changed.

Calico 3.30 documents `FELIX_REMOVEEXTERNALROUTES` default true, and says Felix still removes routes that use `DeviceRouteProtocol` (default 3, proto boot) unless those routes use a different protocol. `FELIX_ROUTESYNCDISABLED` is the documented switch for policy-only mode. Neither setting was applied. `10-kindnet.conflist` sha256 `effdf84a35f889af9218fe827e03dd668f05c8ec3ff9e26304a5084d120371b8`. `10-calico.conflist` sha256 `3cae50fd8c4b04bca0eedc29b01423a90997fe3c1abbfb0f028be6639e66c86e`. The WordPress container was not running, so order 14 was not re-read. The three PVCs were unchanged.
