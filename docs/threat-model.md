# Urumi threat model

This note records assets, trust boundaries, and residual risks for the gateway as implemented in this repository. It is not a penetration test, a live-cluster review, or a severity ranking. A behavior is called a confirmed vulnerability only when a test or a code path demonstrates an unintended breach. Risks inferred from a normal control boundary are labeled as inferences.

`docs/store-access-diagnosis.md` and `docs/store-access-fix-report.md` disagree about whether a storefront routing fix is in effect. This note does not resolve that disagreement.

The public check-runs result for commit `d7ddff1d26cd6ff9e6b96da4282bfc51cf93b178` is workflow run `38537165909`. That run does not include this file.

## Scope and method

The claims below were checked against the cited functions and tests. `docs/design.md` was used as context and was not treated as proof by itself.

Evidence labels used in the table:

- **Implemented.** The behavior is in the cited source.
- **Directly tested.** A cited test asserts that behavior.
- **Inferred.** The residual risk follows from the control boundary. No exploit was run.
- **Not verified live.** The cited test or code path was not run on a cluster. Separate local-cluster checks are in `docs/assignment-tracker.md`.

Encryption of Kubernetes Secrets or databases at rest is not claimed. NetworkPolicy enforcement on `kind-urumi-k8s-dev` is recorded in that tracker: Calico v3.30.2, with the gateway reaching WooCommerce MCP and Support and Utility timing out. That cutover is not in the Helm chart. No VPS, TLS certificate, or production deploy is claimed.

## Assets

| Asset | Where it lives | What the application stores |
| --- | --- | --- |
| Gateway API key | Caller, once, at issue | SHA-256 of the whole `urk_` string (`apps/gateway/app/keys.py` `hash_key`). Not the raw key. |
| Dashboard password | Operator, outside this process | PBKDF2-HMAC-SHA256 verifier, 200,000 iterations (`apps/gateway/app/passwords.py` `hash_password`). |
| Dashboard session | HttpOnly `urumi_session` cookie, or an `urs_` bearer token | SHA-256 of the whole `urs_` string (`apps/gateway/app/directory.py` `new_session_token`). |
| Upstream credential | Gateway PostgreSQL ciphertext | AES-GCM sealed with a key derived from `GATEWAY_UPSTREAM_KEY` (`apps/gateway/app/secretsbox.py` `seal_secret`). |
| Tool grants | `gateway_key_permissions` | Exact public tool names. |
| Audit rows | `gateway_audit_log` | Actor, tool name, outcome, time, upstream id, duration, user id, and target. Not arguments or upstream bodies. |
| WooCommerce orders | Store MariaDB, through the REST API | Orders the WooCommerce MCP process creates. The gateway database does not hold them. |
| Deployment configuration | `compose.yaml`, `helm/urumi` | Image tags, probes, and Secret or values-file references. No committed secret values. |

## Trust boundaries

An external MCP client presents a Bearer API key to `POST /mcp`. The dashboard presents a session to `/admin`. Those credentials are not interchangeable.

The gateway process is the boundary that checks the key or session, filters tool names, and opens upstream HTTP. Support, Utility, and WooCommerce MCP are separate processes. WooCommerce MCP then calls the store REST API. Gateway state is in PostgreSQL. Store content is in MariaDB and the WordPress volume. Compose volumes and Helm claims are different disks.

A trusted environment upstream skips URL validation. An untrusted dynamic upstream does not. That split is the SSRF boundary.

## Authentication and authorization

`generate_key` builds `urk_` plus 16 hexadecimal characters, a dot, and a URL-safe secret. `KeyStore.issue` returns the raw key once. `Authenticator.authenticate` accepts the key only when the hash matches a live row whose user, if any, is not disabled (`apps/gateway/app/auth.py`). `ApiKeyGuard` returns one HTTP 401 body for a missing, malformed, unknown, wrong-secret, or revoked key, writes an `unauthorized` audit row, and does not call an upstream (`apps/gateway/app/http_auth.py`).

`tools/list` returns the enabled upstream tools for a live key (`create_app` `on_list_tools` calls `registry.list_tools_result`). `tools/call` checks the seal in `authorize`, then accepts an exact public tool name without reading stored grants (`ToolDispatcher.execute_tool`). There is no wildcard. `gateway__read_status` is not a callable tool. `Authenticator.permits` and `PostgresStore.permits` still look up a stored row. Dispatch does not call them.

`GET /admin/status` accepts a live API key through `require_live_principal` and does not accept the dashboard session (`main.py` `register_status`). Unset, empty, or `open` `GATEWAY_STATUS_PERMISSION` does not check `gateway__read_status`. `required` does, and a missing grant returns HTTP 403. Any other value raises `StatusPermissionConfigError` at configuration (`config.py` `status_permission_mode`). This grant does not authorize other `/admin` routes.

Admin mutations use `_session_token`: the `urumi_session` cookie or an `Authorization` bearer token that starts with `urs_`. An `urk_` key is not a session. Members issue, list, and revoke only their own keys and read only their own audit rows. A member `POST /admin/keys` with another user's id returns 403 and no key. `_same_site_request` requires `x-urumi-request: 1` and rejects `Sec-Fetch-Site: cross-site`.

The login cookie is HttpOnly and `SameSite=lax`. `secure` follows `GATEWAY_COOKIE_SECURE`, which defaults to false. `LoginLimiter` counts failures in this process only.

## Upstream trust and SSRF

`merge_upstreams` sets `trusted=trust_environment` on static upstreams. The default of `UPSTREAM_TRUST_ENVIRONMENT` is true. Dynamic rows are saved with `trusted=False` (`apps/gateway/app/catalog.py`).

`Registry._connect` calls `validate_upstream_url` only when `trusted` is false. `upstream_http_client` installs `PolicyTransport` only for those clients and sets `follow_redirects=False`. A trusted client uses neither the check nor that transport.

For an untrusted URL, `validate_upstream_url` rejects the metadata hostnames in `ssrf.py`, including `localhost` and a name ending in `.localhost`. `_blocked` always rejects loopback, link-local, multicast, reserved, and unspecified addresses, and the metadata addresses listed in that module. Private addresses are rejected only when `allow_private` is false. `UPSTREAM_ALLOW_PRIVATE` defaults to true.

`PolicyTransport.handle_async_request` calls `destination_addresses` on that request and connects to the first permitted address. If that address differs from the original host, the request URL host is replaced and `sni_hostname` keeps the original name. The class docstring says this avoids a second, unchecked DNS lookup. The test `test_connection_uses_the_validated_address_and_original_hostname` uses a stub resolver and a recording transport. It shows the recorded host is the stub address and the Host and SNI stay the original name. It does not show that every socket the MCP SDK might open uses this transport. That remaining gap is not a demonstrated bypass.

`ssrf.py` comments that local Compose uses loopback and that trust keeps that stack working. `compose.yaml` points the gateway at `support-mcp`, `utility-mcp`, and `woocommerce-mcp`. That comment is not used here as the rationale for the trust default.

## Tool execution

`Registry._namespace` publishes `{upstream_id}__{tool_name}`. `_unique_upstreams` rejects a duplicate id or an id containing `__`, so two different upstream ids do not produce the same public string. `_colliding_public_names` excludes a public name that appears more than once in the combined snapshots. The tested case is one upstream listing the same tool twice. The other upstream stays callable.

A disabled upstream is not contacted. A failed or timed-out list omits that upstream from the current discovery result and leaves other upstreams in place. The last successful snapshot can still contribute to `_collisions`.

`execute_tool` forwards arguments to `Registry.call` and returns the `CallToolResult`. Arguments are not written to the audit log. A timeout becomes outcome `timeout`. Another upstream exception becomes `upstream_failure` with the exception type name, not the upstream body.

Chat passes tool text back to the model as `Untrusted tool data` (`apps/gateway/app/chat.py`). The MCP result path does not add that label. A granted tool on an upstream the operator does not control can perform that tool's side effect. That is the intended grant boundary, not a demonstrated escape.

## WooCommerce orders

`create_order` posts `status: pending` and `set_paid: false` (`apps/woocommerce_mcp/app/tools.py` `_order_body`). The tool docstring says it does not take payment or complete checkout. This is an unpaid pending order, not a completed storefront checkout.

`_OrderReservations` is created in `register_tools` and lives in that process. The same idempotency key and line-item fingerprint does not post twice while the process keeps the reservation. A different fingerprint is rejected. `_order_body` also sends `urumi_idempotency_key` as order metadata. `tools.py` does not read that metadata back and does not rebuild the dictionary after a restart. A second process starts empty. A restart can submit the order again. That limit is implemented and is covered for the in-process cases. A restart was not tested.

## Audit and persistence

`Auditor.record` runs after the upstream call has returned or failed (`dispatch.py` `execute_tool`). It bounds the stored fields and inserts one row. If `add_audit` raises, `record` logs the exception type and does not re-raise (`audit.py`). The caller then returns the tool result it already has. The insert and the upstream side effect are not one database transaction. A crash after the side effect and before the insert can leave no row. The swallow behavior is implemented. No cited test forces `add_audit` to fail during `execute_tool` and asserts that the tool result is unchanged.

The application statements found for `gateway_audit_log` are `CREATE`, `ALTER`, `INSERT`, and `SELECT` (`store.py`, `directory.py`). No application `UPDATE` or `DELETE` of that table was found. A database role that can write the table can still change rows. That is an operational inference, not an application path.

Gateway PostgreSQL and the store MariaDB are separate. Compose named volumes and Helm PVCs do not share data. Deleting a PVC, or a StorageClass whose reclaim policy is `Delete`, can destroy a Helm volume even when the chart sets a keep annotation. That limitation is stated in `helm/urumi/README.md`. It was not exercised on a cluster.

## Deployment

Compose application images are `urumi-gateway:local`, `urumi-support-mcp:local`, `urumi-utility-mcp:local`, and `urumi-woocommerce-mcp:local`, built from the Dockerfiles under `apps/`. MariaDB is `mariadb:11.4`. WordPress is `wordpress:6.8-php8.2-apache` and `wordpress:cli-php8.2`. Gateway PostgreSQL is `postgres:16-alpine`. `GATEWAY_UPSTREAM_KEY` and `CHAT_API_KEY` are not passed into the Compose gateway service.

`urumi.validate` fails unless the gateway database sets `database.existingSecret` or `database.password`, and not both. When the store is enabled, it fails unless the store sets `store.existingSecret` or the MariaDB and WordPress administrator values, and it still requires `store.publicUrl`. That URL does not replace store credentials. A Kubernetes Secret is not the only accepted form.

Gateway chart liveness is TCP. Startup and readiness use `/health`. A PostgreSQL outage fails the HTTP probes and does not, by itself, fail TCP liveness. The Compose gateway healthcheck requests `/health`, so the same outage fails that check. `docs/assignment-tracker.md` records gateway `/health` returning ok on the dev cluster. That is not a probe-failure test.

`templates/networkpolicy.yaml` is rendered when `networkPolicy.enabled` is true. Kindnet does not enforce NetworkPolicy. On 2026-10-18 Calico v3.30.2 did enforce it on `kind-urumi-k8s-dev`, as recorded in the tracker. The chart does not install that CNI. `helm/urumi/values-dev.yaml` sets the gateway image to `chat-20261018`, the dashboard image to `module5-20261018`, the three MCP images to `k8s-test`, and `networkPolicy.probeCIDRs` to `172.19.0.3/32`. The running pods match those tags. The dev node InternalIP is `172.19.0.3`. The applied NetworkPolicies allow `172.19.0.2/32`, the other Kind node's address. The tracker keeps `172.19.0.2` in the Module 9 and 10 notes. The probe source was not captured, and the values file was not changed.

`values-vps.yaml` uses `replace-me` placeholders. It is not a production configuration and has not been deployed. Rendering it is not a VPS install and does not create TLS.

AES-GCM in `secretsbox.py` encrypts dynamic upstream credentials in the application. A Kubernetes Secret is base64, as stated in the chart README. Anyone who can read the Secret can decode it. Database encryption at rest is not implemented in this repository. Those are three different controls. This note does not treat any of them as verified encryption at rest.

## Threats

| Threat | Existing mitigation | Residual risk | Evidence |
| --- | --- | --- | --- |
| Stolen or guessed API key | Implemented. `generate_key` builds a high-entropy `urk_` key and `issue` returns the raw key once (`keys.py`). Directly tested: the stored `secret_hash` equals `hash_key` of that string, and the raw key is not in the stored row. Rejected keys share one 401 body and do not reach an upstream. | A captured raw key works until revoke. SHA-256 is a fast hash. The 401 body does not identify which check failed. | `keys.py` `generate_key`, `hash_key`. `tests/test_gateway_auth.py` `test_issue_stores_only_the_hash`, `test_rejected_keys_do_not_reach_upstream_and_a_live_key_still_works`. |
| Use after revoke or user disable | Implemented. `authenticate` and `authorize` reject `revoked_at` and a disabled user (`auth.py`). `set_user_disabled` calls `revoke_user_sessions` (`directory.py`). Directly tested for keys: a revoked key and a disabled user's key fail `authenticate`, and `authorize` is inactive. Directly tested for the session: after disable, `GET /admin/me` and a new login return 401. That login test does not assert that session rows were deleted. | A call already in flight is not cancelled. The next check is the next `authenticate` or `authorize`. Inferred from the request path. | `auth.py`. `directory.py` `revoke_user_sessions`. `tests/test_admin_api.py` `test_revoked_and_disabled_callers_cannot_use_tools`, `test_login_throttle_csrf_expiry_and_disabled_session`. `tests/test_postgres_permissions.py` `test_revoked_key_cannot_use_a_stored_permission`. |
| Forged in-process principal | Implemented. `seal_principal` is an HMAC with a process-local key (`keys.py`). `execute_tool` calls `authorize` before the registry. Directly tested: a `Principal` whose seal does not match is not dispatched, and the audit actor is `unauthenticated`. | Any code in the gateway process can call `seal_principal`. Chat does that after it selects a key id. That is implemented, not an external forge. | `keys.py` `seal_principal`. `dispatch.py` `execute_tool`. `tests/test_gateway_auth.py` `test_execute_tool_rejects_forged_and_revoked_principals`. `chat.py` `_run_call`. |
| Tool grant bypass | `ToolDispatcher.execute_tool` allows a live key to call an exact public tool name. It does not call `permits` (`dispatch.py`). `permissions.py` states that stored grants are not required. Directly tested older cases still show that a stored name is exact and that `utility__*` is not a wildcard. On 2026-10-18 a member key with zero stored grants listed every enabled tool (`docs/assignment-tracker.md` Module 5). `PostgresStore.permits` still returns true only when a stored row exists. That function is not the dispatch gate. | Any enabled upstream tool is reachable with a live key. There is no per-argument policy. Inferred. | `dispatch.py` `execute_tool`. `permissions.py`. `store.py` `PostgresStore.permits`. `docs/assignment-tracker.md` Module 5. |
| Status read by any live key | Implemented in `register_status` and `status_permission_mode`. Directly tested: with the default open mode, a live key that has no tool grant still receives HTTP 200. With `required`, a key that lacks `gateway__read_status` receives HTTP 403, and calling that name as a tool returns `Permission denied.` Unrecognized values raise `StatusPermissionConfigError` from `status_permission_mode` and from `Settings.from_env`. `status_report` documents that the report omits URLs and secrets. The cited status tests hide raw keys. They do not assert that upstream URLs are absent. | Unset, empty, or `open` lets any live key read upstream ids, listing state, and diagnostics without that grant. | `main.py` `register_status`. `registry.py` `status_report`. `config.py` `status_permission_mode`. `tests/test_admin_status.py` `test_status_requires_a_live_key_and_ignores_tool_grants`, `test_required_status_permission_is_not_a_tool`, `test_status_permission_flag_defaults_open_and_rejects_typos`. |
| Dashboard session theft or cross-site mutation | Implemented. `hash_password` uses PBKDF2-HMAC-SHA256 with 200,000 iterations (`passwords.py`). `new_session_token` stores SHA-256 of the `urs_` token (`directory.py`). The login cookie is HttpOnly, `SameSite=lax`, and `max_age` is `SESSION_MAX_AGE_SECONDS`, 12 hours (`admin_api.py`). `secure` follows `GATEWAY_COOKIE_SECURE`, default false (`config.py`). Directly tested in `test_login_throttle_csrf_expiry_and_disabled_session`: eight failures then HTTP 429, logout without `x-urumi-request` returns 403, `Sec-Fetch-Site: cross-site` returns 403, an expired session returns 401, and a disabled user is rejected on `GET /admin/me` and on login. That test does not assert the hash, cookie flags, or the 12-hour age. | The cookie is not marked Secure unless `GATEWAY_COOKIE_SECURE` is set. Another worker has its own `LoginLimiter` counters. Stated in `login_limit.py`. Not a multi-process test. | `passwords.py` `hash_password`. `directory.py` `new_session_token`, `SESSION_MAX_AGE_SECONDS`. `admin_api.py`. `config.py`. `login_limit.py`. `tests/test_admin_api.py` `test_login_throttle_csrf_expiry_and_disabled_session`. |
| Member acting as admin | Implemented in `admin_api.py` `_admin`. Directly tested: a signed-in member receives 403 for `GET /admin/users`, `POST /admin/upstreams`, and `POST /admin/keys/{id}/permissions`. Separately, `GET /admin/audit` for that member returns only that member's rows. The audit test does not prove the 403s. | An admin session can perform those mutations. Session fixation is not a cited test. | `admin_api.py` `_admin`, `_owns_key`. `tests/test_admin_api.py` `test_admin_session_manages_members_keys_and_upstreams`, `test_audit_is_limited_to_the_signed_in_member`. |
| SSRF through a dynamic upstream | Implemented. Dynamic rows are `trusted=False`. `_blocked` rejects loopback, link-local, multicast, reserved, unspecified, and the metadata addresses in `ssrf.py`. Directly tested URL cases are loopback, link-local, metadata addresses, `metadata.google.internal`, and `printer.localhost`. A DNS answer of loopback is rejected. `PolicyTransport` records the stub address while Host and SNI stay the original name. That pin test uses a stub resolver. It does not prove every MCP SDK socket or every network configuration. Redirects are off on the upstream client. | Private addresses are allowed when `UPSTREAM_ALLOW_PRIVATE` is true, the default. Trusted environment upstreams skip the check. Both are implemented. `test_environment_upstreams_stay_trusted_until_the_operator_changes_policy` covers the trust flag. Multicast, reserved, and unspecified rejection is implemented in `_blocked` and is not in the parametrized URL list. A live metadata block was not run. | `catalog.py` `merge_upstreams`. `registry.py` `_connect`, `PolicyTransport`. `ssrf.py` `_blocked`. `tests/test_ssrf.py` `test_unsafe_urls_are_rejected`, `test_dns_answer_pointing_at_loopback_is_rejected`, `test_connection_uses_the_validated_address_and_original_hostname`, `test_environment_upstreams_stay_trusted_until_the_operator_changes_policy`. |
| Compromised or confused upstream | Implemented in `registry.py` and `dispatch.py`. Directly tested, each for one behavior: `utility__echo` and `support__echo` both stay callable. One upstream listing `echo` twice excludes `dup__echo` and leaves `other__echo` callable. After that duplicate list, a later failed list still rejects `dup__echo`. Duplicate upstream ids, and an id containing `__`, raise `GatewayConfigError`. Disabling `support` returns `Upstream support is disabled.` and `utility` still succeeds. A slow list is omitted and a slow call is audited as `timeout`, while another upstream still succeeds. | Arguments are forwarded and the result is returned to the MCP client. A granted tool can perform its side effect. Chat marks tool text untrusted. The MCP path does not. The hostile-upstream outcome is inferred, not an exploit test. | `registry.py` `call`. `dispatch.py` `execute_tool`. `tests/test_upstream_controls.py` `test_shared_original_names_stay_distinct`, `test_duplicate_public_tool_names_are_excluded`, `test_partial_list_failure_does_not_dispatch_a_held_collision`, `test_duplicate_upstream_ids_are_rejected`, `test_disabled_tools_disappear_and_failures_stay_isolated`. `tests/test_gateway_mcp.py` `test_timeout_skips_slow_list_and_fails_slow_call`. |
| Order replay or a false checkout | Implemented and directly tested in process. Pending unpaid order. Same key and fingerprint does not post twice in that process. | The reservation is not durable. Order metadata is not read back. A restart or another WooCommerce MCP process can post again. Inferred from the empty new process, and not a restart test. This is not card checkout. Not verified live. | `tools.py` `_create_order`, `_order_body`. `tests/test_woocommerce.py` `test_create_order_posts_one_unpaid_pending_order_and_hides_customer_data`, `test_create_order_reuses_a_completed_key_and_rejects_conflicts`. |
| Audit gap after a side effect | Implemented. `record` swallows an insert exception and does not change the tool result. Fields exclude arguments and upstream bodies. Directly tested for the field limit, not for a failed insert. | The insert is not in the same transaction as the upstream side effect. A crash can leave no row. Inferred from the call order. A database role that can write the table can change history. Inferred. No application update or delete was found. | `audit.py` `record`, `store.py` `INSERT_AUDIT`. `tests/test_gateway_auth.py` `test_valid_key_lists_and_calls_without_auditing_arguments`. |
| Upstream credential disclosure | Implemented. `seal_secret` uses AES-GCM with a key derived from `GATEWAY_UPSTREAM_KEY` (`secretsbox.py`). That seal is not Kubernetes Secret encoding and not database encryption at rest. Directly tested: tampered ciphertext, a wrong key, and malformed ciphertext raise `CredentialUnreadable`. With both key variables unset, `seal_secret` and `open_secret` raise `CredentialKeyMissing`. A ciphertext the current key cannot open is merged as `enabled=False` and `credential_unreadable=True`. Compose does not pass `GATEWAY_UPSTREAM_KEY`. That last fact is from `compose.yaml`, not from those tests. | The key is in the process environment or a Kubernetes Secret. The chart README says a Secret is base64, not encryption. Database encryption at rest is not in this repository. Neither was verified live. | `secretsbox.py` `seal_secret`, `open_secret`. `tests/test_secretsbox.py` `test_tampering_wrong_key_and_malformed_ciphertext_are_rejected`, `test_missing_key_fails_closed`, `test_unreadable_credential_disables_the_upstream`. `compose.yaml`. `helm/urumi/README.md`. |
| Cluster network or disk exposure | Implemented as chart objects and documentation. NetworkPolicy is rendered when enabled. Probes separate TCP liveness from `/health`. Compose and Helm do not share volumes. | Kindnet does not enforce NetworkPolicy. Calico v3.30.2 did on `kind-urumi-k8s-dev` on 2026-10-18. That cutover is not in the chart. `values-vps.yaml` is not production-ready and was not deployed. PVC deletion can destroy data. Deletion was not exercised. | `helm/urumi/templates/networkpolicy.yaml`, `gateway-deployment.yaml`, `helm/urumi/README.md`. `docs/assignment-tracker.md` Module 9. |

## Uncertainties

- `PolicyTransport` pins the first permitted address for requests that pass through it. The cited test uses a stub. It does not prove that the MCP SDK cannot open a connection outside that client. No bypass of that kind was demonstrated.
- Chat prompt injection is inferred. `chat.py` labels tool text untrusted and limits tool calls. `tests/test_chat.py` does not call a live model. Live Groq tool calls on the dev cluster are recorded in `docs/assignment-tracker.md` Modules 7 and 12. Those calls are not an injection test.
- `LoginLimiter` does not survive another gateway process. That is stated in source and is not a multi-process test.
- No application statement updates or deletes `gateway_audit_log`. Whether a database administrator can is outside the application.
- The storefront documents named at the top of this file still disagree. This note does not say which access path works.
- CI run `38537165909` covers commit `d7ddff1` only. It does not cover this file, `docs/design.md`, or later commits.

## Assignment questions

1. **What a member can do with shared upstream credentials they never see.** A live member key can list and call the tools of every enabled upstream. Stored grants are not required for that path (`dispatch.py`, Module 5 in `docs/assignment-tracker.md`). The gateway attaches the upstream credential on the server when it calls that upstream. The member does not receive the credential in the dashboard, the tool result, or the audit row. A member cannot register upstreams, read the upstream catalog, or grant tools.

2. **Why client API tokens are not forwarded to upstream servers.** `ApiKeyGuard` accepts the caller's `urk_` key and stops. `Registry` then calls the selected upstream with that upstream's own authorization, which is empty for the static Support, Utility, and WooCommerce processes or a sealed dynamic credential opened only in the gateway process. The caller's key is not copied onto the upstream request. Chat uses the same `ToolDispatcher`.

3. **How malicious upstream tool descriptions could manipulate the model, and what mitigations exist.** Tool names and descriptions are model-visible instructions. A hostile upstream can try to make the model call a granted tool or treat tool text as a new instruction. Chat labels returned tool text `Untrusted tool data` and limits the number of tool calls. The MCP result path does not add that label. Mitigations that are implemented are exact grants with no wildcard, omitting disabled and failed upstreams, and excluding colliding public names. They do not stop a granted tool's description from steering the model.

4. **What one team member can and cannot do to another.** A member can issue, list, and revoke only that member's keys and can read only that member's audit rows. A member cannot list users, issue a key for someone else, grant or revoke another person's tools, change roles, disable another user, or manage upstreams. Those actions require an administrator session. An API key is not that session.
