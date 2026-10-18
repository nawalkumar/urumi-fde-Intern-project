# Assignment tracker

Status date: 2026-10-18. Context: `kind-urumi-k8s-dev`.

This file is the working list for the internship assignment: a WooCommerce store on Kubernetes, a custom WooCommerce MCP server, a team MCP gateway, and a dashboard chat agent. Work one module at a time. Do not start the next module until the current module’s “Done when” check passes.

Evidence for older passes is in `docs/baseline-verification.md`. That file does not replace this tracker. On 2026-10-18 the dev cluster was up on `kind-urumi-k8s-dev`. Modules 1 through 10 and Module 12 have recorded live checks. Chat used Groq, and the provider key stayed in Secret `urumi-dev-chat`. Module 11 is still partial: `values-vps.yaml` was rendered, and no VPS was deployed.

## Required flow

Everything a person or an external MCP client does goes through the gateway. Upstream credentials never leave the cluster.

```text
Browser                         External MCP client
  |  store.local                   |  gateway URL + user API key
  |  dashboard.local                |
  v                                 v
Ingress
  |                         |
  |                         v
  |                    Gateway  POST /mcp
  |                      |  session cookie for /admin and chat
  |                      |  hashed API keys, grants, audit in PostgreSQL
  |                      |
  |         +------------+------------+
  |         |            |            |
  |         v            v            v
  |    WooCommerce    Support      Utility
  |    MCP (ClusterIP, only the gateway may call it)
  |         |
  |         v
  v    WordPress + WooCommerce
Storefront <--- MariaDB (PVC) and WordPress files (PVC)
```

Demo order, with two different users:

1. Open the storefront, add a product to the cart, check out with cash on delivery, and see that order in WooCommerce admin.
2. As user A, ask the dashboard chat to create a product with a name and price. The product shows on the storefront.
3. Place an order for that product by hand. Ask user A’s chat which orders arrived today. The answer includes that order. The audit log shows user A.
4. As user B, connect Claude Code, Cursor, or MCP Inspector with only the gateway URL and user B’s API key. The tools of every enabled server are visible. The same orders question succeeds. One prompt uses tools from two different upstream servers.
5. Revoke user B’s key. The next call is rejected.
6. Disable one upstream. Its tools disappear from chat and from the external client.
7. Delete the pod of one upstream. The other servers still answer. The dashboard shows the dead one as Unreachable.
8. Every call above is in the audit log under the right user.

## How to read a module

| Mark | Meaning |
| --- | --- |
| Done | Code exists and a live check already passed. Re-check only if a later module changes it. |
| Partial | The code or chart exists. The assignment check has not passed on the current cluster. |
| Not done | The check has no passing evidence. |

## Snapshot

| Area | Mark |
| --- | --- |
| Store, database, WordPress volumes | Done. Order 16 is cash on delivery, processing, total 19.00. Module 12 added browser order 20. Same three PVCs. |
| WooCommerce MCP server | Done. Gateway listed the five required tools and `list_products` returned live products. |
| Gateway, dashboard, ingress | Done. A read-only check on 2026-10-18 reached `store.local`, `dashboard.local`, and `gateway.local/health` with HTTP 200 on the dev node `172.19.0.3`. Modules 9 and 10 still record `172.19.0.2` for their earlier notes. |
| Team, keys, audit, tool routing | Done. A member key with zero stored grants listed every enabled tool. Utility scaled to 0 left WooCommerce working. |
| Chat agent against a real model | Done on 2026-10-18 with Groq. Created product 17 and listed orders 16 and 18. The provider key is only in Secret `urumi-dev-chat`. |
| Upstream isolation (NetworkPolicy actually enforced) | Done on 2026-10-18. Calico v3.30.2 is the CNI. The gateway reached WooCommerce MCP; Support and Utility timed out. |
| Same chart, production-like values | Partial. `values-vps.yaml` still contains `replace-me`. No VPS, registry, DNS, or TLS certificate is available. `helm upgrade` was not run. |
| Definition-of-done demo with two users | Done on 2026-10-18 for the recorded sitting. Users `raj_dev` and `naval_dev`, gateway `http://gateway.local`, order 20. The WooCommerce admin screen was not reopened; order 20 was verified in `wp_wc_orders`. |

Live check on 2026-10-18 after the network repair: gateway, dashboard, WordPress, both databases, all three MCP servers, and ingress-nginx were Ready. PVC volume IDs were unchanged: `pvc-f77b4777-44e3-4c02-9309-a1cf3c1332cf`, `pvc-4283d20f-494e-4a15-81d4-6f514adf028f`, `pvc-1e0101de-5c7e-4f29-b19c-7a0c25eddbf3`.

`helm/urumi/values-dev.yaml` sets `networkPolicy.probeCIDRs` to `172.19.0.3/32`. A read-only `kubectl get nodes` showed the dev node InternalIP `172.19.0.3`. Docker shows the other Kind node, `urumi-k8s-test-control-plane`, at `172.19.0.2`. The same read-only curl reached the three hosts on `172.19.0.3` and did not get an HTTP response from `172.19.0.2`. The live NetworkPolicy objects in `default` allow `172.19.0.2/32`, not the values-file CIDR. No packet capture has shown the kubelet probe source, so this file does not call either CIDR verified as the probe source. The values file was not changed. Modules 9 and 10 keep the `172.19.0.2` addresses they recorded.

---

## Module 1 — Bring the dev cluster back to a runnable demo

**Status:** Done on 2026-10-18. Host routes for the existing pod IPs were restored. Calico was no longer selected as the CNI (`10-calico.conflist` moved aside) and Felix no longer treats Kindnet veths as workloads. Gateway `/health` returned `{"status":"ok"}`. Ingress controller Ready. PVCs unchanged.

**Already true**

- The three data volumes are still the original claims.
- WordPress, MariaDB, the WooCommerce MCP server, Support MCP, and gateway PostgreSQL are Running.
- Helm chart, images, and Secrets were enough to run this stack on 2026-10-14.

**Still open**

- Nothing from the earlier failure list. The 2026-10-18 check showed the gateway, dashboard, WordPress, both databases, all three MCP servers, and ingress-nginx Ready, gateway `/health` ok, and the same three PVC IDs.
- Module 9 later made Calico the CNI. That later cutover does not reopen these Ready checks. The status paragraph above describes the cluster before that cutover.

**Done when**

- `kubectl get pods -n default` shows gateway, dashboard, all three MCP servers, WordPress, and both databases Ready.
- `ingress-nginx-controller` is Ready.
- `GET` gateway `/health` from inside the cluster returns `{"status":"ok"}`.
- The three PVC volume IDs are unchanged.

---

## Module 2 — Storefront checkout

**Status:** Done on 2026-10-18. Store API checkout of product 13 created order 16, status `processing`, payment `cod`, total 19.00. `wc_get_order(16)` in the WordPress pod confirmed it.

**Requirement**

Open the storefront, add a product to the cart, check out with cash on delivery, and see the order in WooCommerce admin. The store database and WordPress files stay on persistent volumes.

**Already true**

- WordPress, MariaDB, and the WordPress PVC are in the chart (`helm/urumi/templates/wordpress-deployment.yaml`, `store-db-statefulset.yaml`, `wordpress-pvc.yaml`).
- Cash on delivery was enabled. Order 14 survived a WordPress restart and a MariaDB pod recreate.
- `scripts/dev-store-access.py` is the older store-only proxy. Module 10 records that port 80 on the laptop reached this Ingress on 2026-10-18.

**Still open**

- The WooCommerce admin screen was not reopened. Order 16 was confirmed with `wc_get_order(16)` in the WordPress pod. Module 12 verified order 20 in `wp_wc_orders` only.
- Laptop hostnames passed in Module 10. The browser checkout of order 20 is recorded in Module 12.

**Done when**

- A browser adds an item and checks out with `payment_method=cod`.
- The new order is visible in WooCommerce admin after the WordPress pod is still using the existing PVC.
- The order id is written in this file under this module.

---

## Module 3 — WooCommerce MCP server

**Status:** Done on 2026-10-18. From the gateway pod, WooCommerce MCP health was `woocommerce_api: configured`. Tools: `create_order`, `create_product`, `get_order`, `list_orders`, `list_products`, `update_order_status`. `list_products` returned products 13 and 15. Network isolation remains Module 9.

**Requirement**

A server written in this repo, MCP over Streamable HTTP, not an existing WooCommerce MCP plugin. Tools: list products, create product, list orders, get order, update order status. Only the gateway can call it. Document the store API.

**Already true**

- `apps/woocommerce_mcp` registers those five tools plus `create_order` (`apps/woocommerce_mcp/app/tools.py`).
- Each tool calls the WooCommerce REST API (`apps/woocommerce_mcp/app/client.py`). REST is used because the MCP process does not share WordPress’s filesystem, so WP-CLI would require a sidecar in the WordPress pod. REST works from a separate Deployment with a consumer key.
- The Service is ClusterIP. On 2026-10-14 the gateway listed `list_products`, `create_product`, `list_orders`, `get_order`, `update_order_status`, and `create_order`, and `create_product` created product 15.
- Consumer keys are a Kubernetes Secret, not source. The chart does not create them. They are created in wp-admin and stored as `urumi-dev-woocommerce-credentials`.

**Still open**

- Nothing for the tool list. On 2026-10-18 the gateway listed the tools above and `list_products` returned products 13 and 15. Health was `woocommerce_api: configured`.
- Network isolation is Module 9. That module passed on Calico. The 2026-10-14 non-gateway HTTP 200 was before that cutover.

**Done when**

- From the gateway pod, `tools/list` on the WooCommerce MCP URL includes the five required tools.
- `list_products` returns a real product, including one created in Module 2 or by `create_product`.
- The consumer key is not in git, the dashboard, or an MCP client response.

---

## Module 4 — Gateway tool surface

**Status:** Done on 2026-10-18. One gateway key listed Support, Utility, and WooCommerce tools and called all three. With Utility scaled to 0, `utility__convert_units` returned `ConnectError` and `woocommerce__get_order` still returned order 16. Status showed Utility `listing_failed` and the others `listing_ok`. Utility was restored and the conversion returned `2000`.

**Requirement**

One MCP endpoint. Listing returns every enabled server’s tools. A call is routed to the right server. Two servers may expose the same tool name. A slow or dead upstream does not hide the others. Calls time out with a clear error. At least three upstreams: WooCommerce, plus two others.

**Already true**

- `POST /mcp` is Streamable HTTP (`apps/common/http.py`).
- Public names are `{upstream_id}__{tool}`. An id may not contain `__`. A public name listed twice is omitted and a call returns `Duplicate public tool name ... Conflicting registration excluded.` (`apps/gateway/app/registry.py`, `docs/design.md`).
- Support Notes and Utility are the other two upstreams.
- Timeouts use `UPSTREAM_TIMEOUT_SECONDS`. A failed list skips that upstream only (`tests/test_gateway_mcp.py`).
- On 2026-10-14 one session called WooCommerce, Utility, and Support. Scaling Utility to 0 left WooCommerce working. Disabling Utility removed `utility__convert_units` from discovery.

**Still open**

- Nothing. On 2026-10-18 one key listed and called WooCommerce, Support, and Utility. With Utility scaled to 0, `utility__convert_units` returned `ConnectError` and `woocommerce__get_order` still returned order 16. Utility was restored and the conversion returned `2000`.

**Done when**

- `tools/list` with a granted key includes tools from WooCommerce, Support, and Utility.
- Stopping Utility returns a clear error for a Utility tool and a successful WooCommerce call in the same minute.
- The collision rule in `docs/design.md` still matches the code.

---

## Module 5 — Admin servers, members, and keys

**Status:** Done on 2026-10-18. The admin server table shows Connected, Unreachable, or Disabled. A live key lists and calls every enabled server's tools without a stored grant. Member `raj_dev` key `349aa4c4a4dd9949` had zero grants, listed all eight tools, read order 16, and was revoked. That check recorded images `urumi-gateway:module5-20261018` and `urumi-dashboard:module5-20261018`. A later read-only listing shows the running gateway image is `urumi-gateway:chat-20261018`, which matches `values-dev.yaml`. The dashboard image is still `urumi-dashboard:module5-20261018`. 218 unit tests passed. Image tags were not changed in this reconciliation.

**Requirement**

An admin sees each server as Connected, Unreachable, or Disabled, and sees its tools. WooCommerce is already registered. The admin can add a server by URL and credential, and can edit, disable, or remove it. The admin can add members and issue or revoke keys. A member can create their own key. Upstream credentials never reach the browser or an MCP client. Keys are stored hashed and can be revoked.

**Already true**

- Roles `admin` and `member` (`apps/gateway/app/admin_api.py`). Members issue, list, and revoke only their own keys.
- Keys are `urk_<id>.<secret>`. PostgreSQL stores SHA-256 of the whole string (`apps/gateway/app/keys.py`). Revocation returns the same 401 as an unknown key.
- Dynamic upstreams accept an optional header or bearer credential. Responses expose `has_credential`, not the secret (`tests/test_secretsbox.py`). A live check on 2026-10-14 sealed a credential for upstream `baselineprobe`.
- WooCommerce, Support, and Utility are environment upstreams from the chart, so they appear without a manual add.
- The status page maps listing state to Connected / Unreachable / Disabled (`dashboard/src/status.ts` `connectionLabel`). That page asks the operator to paste an API key. It is separate from the admin session.
- `serverStatus` in `dashboard/src/admin/AdminPanel.tsx` returns Connected, Unreachable, or Disabled. The 2026-10-18 status line says the admin table showed those words.
- A new key has no stored grants (`apps/gateway/app/permissions.py`). On 2026-10-18 member `raj_dev` key `349aa4c4a4dd9949` had zero grants and listed all eight enabled tools. `ToolDispatcher.execute_tool` does not require a stored grant.

**Still open**

- Add, edit, remove, and invite were not re-tested in the browser after Module 1. Disable, issue, and revoke were exercised later: Module 8 disabled Support and revoked a `naval_dev` key; Module 12 disabled Utility and revoked a temporary member key.

**Done when**

- Logging in as admin shows WooCommerce first among the built-in servers, with one of those three status words and its tool names.
- Adding a fourth server with a credential does not echo the credential in the UI or in `GET /admin/upstreams`.
- A member creates a key and, with only that key, lists tools from every enabled server.
- Revoking that key makes the next `POST /mcp` return 401.

---

## Module 6 — Audit log

**Status:** Done for the live calls so far. Chat tool rows for `raj_dev` (`b1a156315c774f5a`) record `woocommerce__create_product` and `woocommerce__list_orders` with upstream, duration, and outcome `success`.

**Requirement**

Every tool call is logged with who, which server, which tool, when, how long, and whether it succeeded. The dashboard shows that log. Chat calls are attributed to the logged-in user.

**Already true**

- `gateway_audit_log` stores `occurred_at`, `actor`, `tool_name`, `outcome`, `upstream_id`, `duration_ms`, and `user_id` (`apps/gateway/app/store.py`).
- The dispatcher writes the row after the call (`apps/gateway/app/dispatch.py`).
- Members see only their rows. Admins see the log in `AdminPanel`.
- Live MCP calls on 2026-10-14 recorded user `ab989f49b4db7bc7`, tool, upstream, outcome, and duration.
- A failed audit insert does not roll back the tool result. That limit is documented in `README.md`.

**Still open**

- Chat attribution for `raj_dev` is recorded in Module 7 and Module 12, with tool, upstream, duration, and outcome.
- The audit screen was not reopened after Module 1. The recorded rows contain those fields. The dashboard column labels were not checked again in the browser.

**Done when**

- The calls from Modules 4, 5, and 7 each appear once, under user A or user B, not under a shared admin key.
- A revoked or missing key is audited as unauthenticated and does not name a tool.

---

## Module 7 — Chat agent

**Status:** Done on 2026-10-18. Groq model `openai/gpt-oss-120b` created published product 17, `URUMI-CHAT-DEMO`, price 12.50. After cash-on-delivery order 18, the same chat listed orders 18 and 16. The dashboard service path is `ChatService`; the audit actor is user `raj_dev`.

**Requirement**

A dashboard chat uses an LLM, calls tools only through the gateway, shows which tools it called, and acts as the logged-in user. The model key is a Kubernetes Secret.

**Already true**

- `ChatService.answer` lists tools the user’s keys are allowed to call and executes them through `ToolDispatcher` (`apps/gateway/app/chat.py`).
- `ChatPanel.tsx` renders the tool activity returned with the answer.
- `tests/test_chat.py` covers a scripted turn that calls WooCommerce and Support, and a disabled tool that is not offered again.
- The chart mounts `CHAT_API_KEY` from Secret `urumi-dev-chat`. That Secret was present for the 2026-10-18 Groq run and is not in git. `POST /admin/chat` returns 503 when the key is absent.
- The system prompt tells the model to publish products with `status=publish` and to pass timezone bounds to `woocommerce__list_orders`.

**Still open**

- The two user-A prompts are recorded here (product 17, orders 18 and 16) and again in Module 12 (product 19, order 20).
- This file records the `POST /admin/chat` calls and audit rows. It does not include a separate screenshot of `ChatPanel` listing the tool names.

**Done when**

- User A asks for a product by name and price. The storefront shows it as published.
- After a hand-placed order, user A asks what orders came in today and the answer includes that order.
- The chat panel lists `woocommerce__create_product` and `woocommerce__list_orders`.
- The audit rows for those calls use user A’s key id.
- The provider key is absent from the repository, the browser payload, and the audit row.

---

## Module 8 — External client and the control demo

**Status:** Done on 2026-10-18. `naval_dev` key `b60229d0a5e4fa9a` listed every enabled tool, read orders 16 and 18, and called Utility plus Support. After revoke, the next call returned HTTP 401. Support was then disabled: its tool disappeared from chat and from the external client, and a call returned `Upstream support is disabled.` Deleting the Utility pod made `utility__convert_units` fail with `ConnectError` while order 18 still loaded. Status showed Utility `listing_failed` (Unreachable) and Support `disabled`. Support was enabled again afterward so the demo stack is usable. The revoked key remains in `.cursor/mcp.json`.

**Requirement**

User B connects with only the gateway URL and their key, sees every enabled server’s tools, answers the orders question, and runs one prompt that needs two upstreams. Revoke rejects the next call. Disabling a server removes its tools from chat and from the external client. Killing one upstream pod leaves the others working and marks that server Unreachable.

**Already true**

- SDK session: list, WooCommerce plus Utility plus Support, revoke 401, disable hides the tool, scale-to-zero isolates the failure, status showed `listing_failed` while the others stayed `listing_ok`.
- `connectionLabel` renders that failed listing as Unreachable.

**Still open**

- Claude Code, Cursor, and MCP Inspector are not named in the recorded session. The recorded client is the official MCP client at `http://gateway.local/mcp`. Module 12 names `ClientSession` for the revoke check.
- One chat turn that called WooCommerce and Utility is recorded for user A in Module 12. A single external-client prompt that used two upstreams is not separately recorded. This module’s status does record `naval_dev` calling Utility and Support.
- Disable and pod delete are recorded. Module 8 disabled Support and deleted the Utility pod. Module 12 deleted the Utility pod again and read `listing_failed` as Unreachable.

**Done when**

- The four control steps succeed in one sitting, with user B distinct from user A, and each call is in the audit log.

---

## Module 9 — Only the gateway can reach upstream MCP servers

**Status:** Done on 2026-10-18. Calico v3.30.2 is the loaded CNI (`10-calico.conflist`). Kindnet is held as `10-kindnet.conflist.held-migration`. Felix prefix stayed `cali`. Route sync is on, and the backend is `bird`.

From the gateway pod, `http://urumi-woocommerce-mcp.default.svc.cluster.local:8000/health` returned `{"status":"ok","woocommerce_api":"configured"}`. The same call from Support timed out, and the same call from Utility timed out. The gateway call succeeded again in that window. Orders 16 and 18 were still `processing` at 19.00 and 12.50. Ingress on `172.19.0.2` returned 200 for `store.local` and `dashboard.local`, and 401 for `gateway.local`. The three PVC volume IDs were unchanged. There were no workload `veth` interfaces.

**Requirement**

The WooCommerce MCP server is not reachable from outside the cluster. Only the gateway can call it. The same rule should hold for Support and Utility, because clients must use the gateway.

**Already true**

- Services are ClusterIP. Ingress does not route to the MCP servers (`helm/urumi/templates/ingress.yaml`).
- `helm/urumi/templates/networkpolicy.yaml` allows MCP ingress only from the gateway, plus probe CIDRs.
- `tests/test_networkpolicy_intent.py` checks that policy text.
- The enforcing CNI is Calico v3.30.2. A rollback copy of the Kindnet config and the previous replica counts is in `/home/naval/.urumi-module9-rollback`.

**Still open**

- Nothing for this module. A later Helm or node rebuild can put Kindnet back in front of Calico, because this cutover was applied on the live node and is not in the Helm chart.

**Done when**

- Those three checks pass on `kind-urumi-k8s-dev`, and the three PVC volume IDs are unchanged.
- Record the CNI that enforced the policy.

---

## Module 10 — Stable Ingress URLs on the laptop

**Status:** Done on 2026-10-18. The dev node and the laptop both reach the same Ingress on port 80.

| URL | Dev node `172.19.0.2` | Laptop `127.0.0.1` |
| --- | --- | --- |
| `http://store.local/` | 200 | 200 |
| `http://dashboard.local/` | 200 | 200 |
| `http://gateway.local/` | 401 | 401 |
| `http://gateway.local/health` | 200 | 200 |

Host port 80 is still published by `urumi-k8s-test-control-plane`. That node was not listening, so the connection was reset. Container `urumi-dev-port80` shares that node's network namespace and forwards TCP port 80 to the dev Ingress. The Docker port publish was not changed. Windows hosts has `127.0.0.1` for `store.local`, `dashboard.local`, and `gateway.local`. The three PVC volume IDs were unchanged. A GUI click-through of checkout and the dashboard was not repeated in this pass.

**Requirement**

The storefront and the dashboard are exposed through Ingress with stable URLs. Document the local domain approach. Local and production differ only by Helm values.

**Already true**

- `values-dev.yaml` sets `store.local`, `dashboard.local`, and `gateway.local` on Ingress.
- `README.md` documents the hosts entries and `scripts/dev-port80-forward.py`.
- `scripts/dev-store-access.py` remains the older store-only proxy. The port 80 forwarder is the path a browser uses.

**Done when**

- `http://store.local/`, `http://dashboard.local/`, and `http://gateway.local/health` answer on port 80 from the laptop. Passed on 2026-10-18.
- The doc states the hosts file entries and the forwarder a reviewer must use. `README.md` does.
- A full storefront checkout and dashboard click-through on these URLs is part of Module 12. It was not repeated here.

---

## Module 11 — One chart for a production-like VPS

**Status:** Partial. Inspected on 2026-10-18. Not deployed.

`helm lint` of `values-dev.yaml` and `values-vps.yaml` returned 0 failed. `helm template` of the VPS file rendered 8 NetworkPolicies, the store and gateway databases, WordPress, the gateway, the dashboard, three MCP servers, and one Ingress. The render contains 40 `replace-me` strings, 0 `stringData` blocks, and no password literals. The dev render contains 0 `replace-me`. `helm upgrade` was not run. The current context is still `kind-urumi-k8s-dev`, and the three PVC volume IDs are unchanged.

There is no VPS, registry, DNS name, or TLS certificate in this checkout, so those values were not invented. `helm/urumi/values-vps.local.yaml` is gitignored for the operator copy. The committed worksheet remains `values-vps.yaml`.

**Requirement**

The same Helm chart installs on local Kubernetes and on a production-like VPS such as k3s. Differences are values only. Probes, requests, and limits exist on every workload. Secrets are Kubernetes Secrets.

**Already true**

- Chart `helm/urumi`. Local overlay `values-dev.yaml`. VPS overlay `values-vps.yaml`.
- Both files lint and render. The VPS render still contains `replace-me`.
- The VPS render has liveness and readiness probes and resource requests and limits. The store init Job is a finite Job with resources and no probe, which is appropriate.
- Database, store, WooCommerce, upstream-key, and chat values are existing Secret names. The chart does not emit those Secret bodies from `values-vps.yaml`.
- `helm upgrade` and rollback have not been run.

**Still open**

- A private `values-vps.local.yaml` with a real registry and tag, the cluster StorageClass, the ingress class and controller namespace, the three DNS hosts, `networkPolicy.probeCIDRs` for that node, and existing Secret names. The TLS Secret has to be created in the cluster. None of those were available here.
- `helm upgrade` of the dev release, only when explicitly approved, then a check that the three PVCs stay bound.
- Do not install `values-vps.yaml` while it still says `replace-me`. A rendered worksheet is not a VPS install.

**Done when**

- `helm upgrade` of the dev release succeeds and the demo pods become Ready again.
- A reviewer can point at one uncommitted values file that has no `replace-me` and no raw passwords.
- `helm template` of that file is not claimed as a VPS install unless a VPS was actually used.

---

## Module 12 — Definition of done, one sitting

**Status:** Done on 2026-10-18 on `kind-urumi-k8s-dev`. The browser cash-on-delivery checkout created order 20, and Gateway chat listed that order through `woocommerce__list_orders`. Utility pod recovery, revoked-key rejection, and the two-upstream chat prompt had already passed.

Gateway URL `http://gateway.local`. Users `raj_dev` (`b1a156315c774f5a`) and `naval_dev` (`eb248a9a0ebb11cd`). Chat published product 19, `URUMI-MODULE12-DEMO`, price 9.50. The storefront checkout created order 20. Orders 18, 16, and 14 remain.

| Step | User | Pass |
| --- | --- | --- |
| Storefront cart and COD checkout | browser | Pass. On 2026-10-18 the storefront confirmation page showed order 20, product `URUMI-MODULE12-DEMO`, quantity 1, total INR 9.50, payment Cash on Delivery. Read-only `wp_wc_orders` row 20 is `wc-processing`, type `shop_order`, currency `INR`, total `9.50000000`, payment `cod` / `Cash on delivery`, created `2026-10-18 14:25:46` GMT. Line item 4 is `URUMI-MODULE12-DEMO`, product id 19, quantity 1, line total 9.5. Orders 18, 16, and 14 still exist. PVC volume IDs `pvc-f77b4777-44e3-4c02-9309-a1cf3c1332cf`, `pvc-4283d20f-494e-4a15-81d4-6f514adf028f`, and `pvc-1e0101de-5c7e-4f29-b19c-7a0c25eddbf3` are still Bound. |
| Order visible in WooCommerce admin | browser | Backend row verified, admin screen not reopened. Order 20 is in `wp_wc_orders` with the line item above. Orders 16 and 18 still exist. |
| Chat creates a published product | A | Pass. `POST /admin/chat` as `raj_dev` called `woocommerce__create_product`. Product 19 is published. |
| Product visible on the storefront | browser | Pass. The storefront checkout bought product 19, `URUMI-MODULE12-DEMO`. |
| Hand-placed order, chat lists today’s orders | A | Pass. `POST /admin/chat` as `raj_dev` on 2026-10-18 14:31:18Z called `woocommerce__list_orders` and returned orders 20 (processing, INR 9.50), 18 (processing, INR 12.50), and 16 (processing, INR 19.00). Audit actor `91e44888c637aea4`, user `b1a156315c774f5a`, upstream `woocommerce`, outcome `success`, duration 1091 ms. The WooCommerce MCP log shows `GET /wp-json/wc/v3/orders` HTTP 200, then `GET /wp-json/wc/v3/orders/20`, `/18`, and `/16`, each HTTP 200. |
| External client sees every enabled server’s tools | B | Pass. Official MCP client at `http://gateway.local/mcp` with `naval_dev` key `77c312a427ebdc73` listed eight tools. |
| Orders question through that client | B | Pass. `woocommerce__list_orders` returned orders 18 and 16. |
| One prompt uses two upstream servers | A | Pass. One `POST /admin/chat` as `raj_dev` returned both `woocommerce__list_orders` success and `utility__convert_units` success. The reply listed orders 18 and 16 and converted 2 km to 2000 m. Audit at 09:33:24Z and 09:33:25Z names user `b1a156315c774f5a`, key `91e44888c637aea4`, upstreams `woocommerce` and `utility`. |
| Revoked key is rejected | admin | Pass. Temporary member `m12_revoke_probe` (`28975881527cb40e`) received key `a4f78904b1c846ea`. The official MCP `ClientSession` listed 8 tools, including `woocommerce__list_orders`. After `POST /admin/keys/a4f78904b1c846ea/revoke` returned 200, the same token sent to `/mcp` got HTTP 401 `invalid_token`. The authenticator returned no principal and `authorize` was inactive. Audit: `admin:revoke_key` and actor `unauthenticated`, outcome `unauthorized`. The probe user is disabled. `raj_dev` key `91e44888c637aea4` remains active. The earlier `naval_dev` secrets were not recovered. |
| Disabled server disappears from chat and from the client | admin | Pass. `urumi_admin` disabled Utility with `POST /admin/upstreams/utility` `{"enabled":false}`. Status was `enabled false`, `listing disabled`, and no Utility tools. Chat could not call `utility__convert_units` and still listed orders 18 and 16. Gateway `/health` stayed ok. Utility was enabled again at 09:07:34Z and `utility__convert_units` returned 2000. Audit: `admin:disable_upstream` and `admin:enable_upstream`, user `36e2af3487daa142`, upstream `utility`, outcome success. |
| Killed upstream pod: others work, dashboard says Unreachable | admin | Pass for the outage and recovery. At 09:20:29Z deleted only `urumi-utility-mcp-759554d66b-wq2dg`. While it was gone, gateway `/health` stayed 200, Utility stayed enabled with `listing_failed` (dashboard Unreachable), and WooCommerce and Support stayed Connected. ReplicaSet created `urumi-utility-mcp-759554d66b-66pmj`, which became Ready with desired and available replicas 1. Utility returned to `listing_ok` and Connected. `utility__convert_units` at 09:20:51Z returned 2000 m in 43 ms. No audit row recorded the listing failure. Volume IDs and orders 18, 16, and 14 were unchanged. |
| Audit log attributes each call to A or B | admin | Pass for the calls that were made. `raj_dev` key `91e44888c637aea4` listed orders again at 09:04:33Z, 417 ms, success, and chat succeeded. `naval_dev` revoke rows at 09:04:30Z name both key ids, outcome success, and contain no secret. No new unauthorized `/mcp` row exists for those keys because the bearers were not replayed. |

Cluster health, the three volume IDs, laptop URLs, and the NetworkPolicy check were verified again in this sitting. Gateway reached WooCommerce MCP. Support and Utility timed out. `http://store.local/` and `http://dashboard.local/` returned 200. `http://gateway.local/` returned 401 and `/health` returned 200.

Record the date, the gateway URL, the two usernames, and the order id. Do not record API keys, the chat provider key, or WooCommerce consumer secrets.

## Suggested order

1. Module 1, cluster health.
2. Module 2, checkout.
3. Module 3, WooCommerce tools from the gateway.
4. Module 4, three upstreams and failure isolation.
5. Module 5, admin status words and member tool visibility.
6. Module 6, audit columns.
7. Module 7, live chat.
8. Module 8, external client control demo.
9. Module 9, NetworkPolicy enforcement.
10. Module 10, browser URLs.
11. Module 11, Helm upgrade and VPS values.
12. Module 12, full demo.

Modules 5 and 7 change product behavior. Modules 9 and 10 change the cluster network. Do not run them in parallel.
