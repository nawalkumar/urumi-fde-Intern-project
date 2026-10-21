# Urumi MCP Gateway

Urumi is a Streamable HTTP gateway that presents Support Notes, Utility, and WooCommerce MCP servers as one authenticated tool surface. Callers use a gateway API key. The gateway checks that key, hides tools the key was not granted, and records the call. A separate dashboard signs in with a username and password to manage people, keys, grants, upstreams, and the audit log.

This repository is the source tree. A Docker Compose project and a Kubernetes/Kind deployment are different environments. Starting one does not update the other. The current source, including registry-refresh compensation, is what the tests in this checkout exercise. It is not automatically what a previously built cluster image is running.

## Features

- Streamable HTTP `POST /mcp` with Bearer API keys. Missing, invalid, and revoked keys share one `401` body.
- Public tool names `{upstream_id}__{tool}`. A colliding public name is omitted from discovery and a call returns an error instead of choosing an upstream.
- Per-key grants. A new key has no permissions. There is no wildcard.
- Upstream timeouts and list failures stay on that upstream. Other upstreams remain callable.
- Disabled upstreams are omitted from discovery. A call returns a tool error and does not contact that server.
- Audit rows for accepted and rejected tool calls, and for successful admin mutations. A persisted upstream change whose registry refresh cannot be undone is audited as `error`, not `success`.
- Dashboard administration: users, roles, API keys, exact grants, upstream registration, collision diagnostics, and audit history.
- Optional dashboard chat that calls tools only through the same dispatcher, using one of the signed-in user's live keys.
- WooCommerce product and order tools when the store REST API is configured. `woocommerce__create_order` creates an unpaid pending order in the WooCommerce MCP process. It is not production checkout.
- Optional multi-store provisioning stays disabled. `storeProvisioner.enabled` and `storeProvisioner.admit` are both false, and the dev values file was not changed. A store row can be saved, but a separate provisioner process applies the Kubernetes objects. The public gateway does not. Helm renders that process only when both flags are true. The admission policy has to be installed and enforced before provisioning is turned on. The provisioner ClusterRole can delete namespaces and is not limited to names `st-<id>`. Creating a store does not grant its tools. See `docs/multi-store.md`.

## Architecture

```mermaid
flowchart LR
  client[MCP client]
  dash[Dashboard]
  gw[Gateway]
  db[(PostgreSQL)]
  support[Support MCP]
  utility[Utility MCP]
  woo[WooCommerce MCP]
  store[WordPress and WooCommerce]

  client -->|"Bearer API key POST /mcp"| gw
  dash -->|"session cookie /admin"| gw
  gw --> db
  gw --> support
  gw --> utility
  gw --> woo
  woo --> store
```

MCP clients never send the dashboard session, and an API key does not authorize `/admin` mutations. The dashboard dev server and the dashboard container proxy `/admin` to the gateway. They do not proxy `/mcp`.

### Request path

1. `POST /mcp` requires `Authorization: Bearer urk_<key_id>.<secret>`. The gateway stores SHA-256 of the whole key. It does not store the raw secret.
2. `tools/list` returns granted public names that are still registered. Disabled upstreams, failed or timed-out lists, and colliding public names are already removed.
3. `tools/call` goes through `ToolDispatcher.execute_tool`. That function checks the sealed key and the exact grant again, then calls one upstream.
4. A slow upstream is bounded by `UPSTREAM_TIMEOUT_SECONDS` (process default 5; Compose and the Helm chart set 20). The caller gets a tool error. Healthy upstreams are unchanged.
5. After a key is accepted, the audit row stores the key id, public tool name, time, and one of `success`, `error`, `timeout`, `upstream_failure`, or `unauthorized`. Arguments and credentials are not fields. Authentication failures use actor `unauthenticated`, an empty tool name, and outcome `unauthorized`.

Admin mutations write a separate success row only after the store accepts the change. The actor is the signed-in user id. The tool name is `admin:<action>`. The target is an id, role, or permission name, not a secret.

If an upstream create, update, or delete commits and registry refresh then fails, the gateway undoes that write and returns HTTP 503 with `persisted: false`. No audit row is written. If the undo itself fails, the change stays, the running registry is aligned with that change when it can be, and the response is HTTP 500 with `persisted: true`. The audit outcome is `error`.

### External MCP client

An external client uses the gateway MCP URL and one user’s API key. It does not use the dashboard login. The dashboard session is an HttpOnly `urumi_session` cookie for `/admin`. That cookie is not sent to `/mcp`, and the dashboard does not proxy `/mcp`. An API key does not authorize `/admin` mutations.

The MCP URL is `<GATEWAY_URL>/mcp`. For the host process in Local setup, `<GATEWAY_URL>` is `http://127.0.0.1:8000`. Replace `<GATEWAY_URL>`, `<KEY_ID>`, and `<API_KEY_SECRET>` locally. Do not commit the real key.

The issued key is one string, `urk_<KEY_ID>.<API_KEY_SECRET>`. `<KEY_ID>` is 16 hexadecimal characters. The secret is the random suffix after the dot. The gateway stores SHA-256 of the whole string and shows the raw key only when it is issued. A new key has no tool grants.

Send that string on MCP requests:

```http
Authorization: Bearer urk_<KEY_ID>.<API_KEY_SECRET>
```

The gateway mounts the MCP Python SDK Streamable HTTP endpoint at `/mcp` with JSON responses and stateless HTTP (`apps/common/http.py`). The tests open it with `mcp.client.streamable_http.streamable_http_client` and the `Authorization` header above (`tests/mcp_session.py`), then call `list_tools` and `call_tool`. That client performs the session setup the SDK requires. This repository does not include a hand-written `initialize` body, and it does not include a configuration file for any other product. Point a Streamable HTTP client at `<GATEWAY_URL>/mcp` and send the Bearer header. Do not treat that as proof that an untested client is compatible. No live external client was exercised for this document.

`list_tools` returns a tool only when both of these are true:

- The key has that exact public name granted. There is no wildcard.
- The registry is still advertising that name. Disabled upstreams, lists that failed or timed out, and colliding public names are already omitted.

A call checks the grant again. An ungranted name returns the tool error `Permission denied.` A colliding name returns `Duplicate public tool name <name>. Conflicting registration excluded.` A disabled upstream returns `Upstream <id> is disabled.` and is not contacted. Other upstreams stay listed and callable. `woocommerce__create_order` is one such tool when WooCommerce is configured and granted. It creates an unpaid pending order in the WooCommerce MCP process. It is not a completed payment or a storefront checkout.

`GET <GATEWAY_URL>/health` does not use an API key. `{"status":"ok"}` means PostgreSQL answered. HTTP 503 with `{"status":"unavailable"}` means it did not.

Missing, malformed, unknown, wrong-secret, and revoked keys all receive the same HTTP 401 body and do not reach an upstream:

```json
{"error": "invalid_token", "error_description": "Authentication required."}
```

The response includes `WWW-Authenticate: Bearer error="invalid_token"`. The body does not say which of those cases failed. If the key store cannot be used, `/mcp` returns HTTP 503 with `{"error":"unavailable","error_description":"Authentication is unavailable."}`. An empty tool list for a live key means nothing granted is currently advertised. Collision diagnostics for an administrator are on `GET /admin/upstreams`, which requires the dashboard session, not the API key.

## Tool discovery

`tools/list` is grant-filtered. It reads the caller's current exact grants, drops names the registry has excluded, and drops a store tool unless that store is Ready and the saved upstream is enabled at the store's canonical MCP URL. A relevance score is not consulted. `tools/list` has no query field in this MCP SDK, so search is separate.

`POST /discovery/search` takes `{"query": "..."}` and the same Bearer API key as `/mcp`. Send it to the gateway. In the host setup that is `http://127.0.0.1:8000/discovery/search`. The dashboard dev server and the dashboard container proxy `/admin` only, so they do not serve this route. It ranks only tools that key may already list. It does not grant permissions. Matching is lexical: exact public name, then tokens in the name, upstream id, and description. There is no model call. The query is limited to 200 characters and the response to 20 tools. An empty query returns that key's authorized tools in name order, capped at 20. A malformed or over-long query returns HTTP 400 and no tools. If listing fails, the response is HTTP 503 and no tools; it does not fall back to the grant list or the full catalog. The response does not echo the query.

`GET /admin/discovery` and `POST /admin/discovery/search` require an administrator session. They show candidate and advertised counts, exclusion categories, collision records, and a lexical search of the advertised catalog. That search does not grant anything. A member session receives 403. An API key cannot read it.

Discovery is computed on each request. There is no shared cache, so one key's result cannot be reused for another key. The process keeps the last event's counts, a correlation id, the query length, and a short digest. It does not keep the query text, API keys, or upstream URLs.

## Collision handling

Upstream ids must be unique and must not contain `__`. Two registrations that would publish the same public name are both excluded. Discovery omits that name. A call returns `Duplicate public tool name <name>. Conflicting registration excluded.` The gateway does not pick either copy. `GET /admin/discovery` adds the upstream ids, the category `duplicate_public_name`, and the resolution `excluded`. The same exclusion is on `GET /admin/upstreams`. An empty list renders “No diagnostics reported.”

## Administration

| | MCP API key | Admin session |
| --- | --- | --- |
| Credential | `urk_<key_id>.<secret>` Bearer token | Username and password |
| Storage | Key hash in PostgreSQL | HttpOnly `urumi_session` cookie |
| Allows | `POST /mcp` for granted tools, and `POST /discovery/search` on the gateway (port 8000 in the host setup). Search ranks only tools that key may already call. It does not grant permissions. | `/admin` routes for that user |
| Does not allow | Admin mutations. The dashboard origin proxies `/admin` and does not serve `/discovery/search`. | Tool execution by itself |

An administrator can create members, change roles, disable users, issue and revoke keys, grant and revoke exact permissions, and register, edit, disable, or remove dynamic upstreams. A member can issue, list, and revoke only that member's keys and can read only that member's audit rows. A member cannot issue a key for someone else, grant tools, or manage upstreams. Disabling a user revokes that user's sessions.

Create the first administrator with the password written to a new file outside the repository. The command does not print the password.

```bash
python -m apps.gateway.provision create-admin USERNAME --output /absolute/path/outside-the-repo.password
```

`GET /admin/status` is separate. Unset, empty, or `open` for `GATEWAY_STATUS_PERMISSION` lets any live key read status. `required` allows only a live key with the exact grant `gateway__read_status`. That name is not an MCP tool. Any other value stops startup.

## Technology

- Python 3.10 or newer. Application images use `python:3.10-slim`.
- [uv](https://docs.astral.sh/uv/) 0.12.0 in the Dockerfiles, with `uv.lock`.
- FastAPI, Uvicorn, `mcp==2.3.0`, psycopg, cryptography.
- PostgreSQL 16 for keys, grants, sessions, upstream rows, and audit.
- React 19 and Vite 6 for the dashboard. Vitest for UI tests.
- Optional Compose stack: MariaDB 11.4, WordPress 6.8, WooCommerce.
- Optional Helm chart for Kubernetes 1.27 or newer.

## Repository layout

```text
apps/gateway/          Gateway, admin API, registry, audit, provision
apps/support_mcp/      Support Notes MCP server
apps/utility_mcp/      Utility MCP server
apps/woocommerce_mcp/  WooCommerce MCP server
dashboard/             Vite administration and status UI
tests/                 Gateway pytest suite
helm/urumi/            Kubernetes chart
deploy/wordpress/      Compose store init script
compose.yaml           Local Compose project
```

## Local setup

Prerequisites: Python 3.10+, uv, and Node.js 22 for the dashboard. Docker is required for the PostgreSQL-backed tests and for Compose. Helm is required only to lint or render the chart.

```bash
uv sync --frozen --extra dev
```

Copy the example environment and fill it in locally. `.env` is gitignored.

```bash
cp .env.example .env
```

Run the three upstreams and the gateway on the host. The database URL below is a placeholder. Do not commit a real password. Compose does not publish PostgreSQL, so this host command needs a database you started yourself.

```bash
uv run uvicorn apps.support_mcp.app.main:app --host 127.0.0.1 --port 8001
uv run uvicorn apps.utility_mcp.app.main:app --host 127.0.0.1 --port 8002
uv run uvicorn apps.woocommerce_mcp.app.main:app --host 127.0.0.1 --port 8003
```

```bash
WOOCOMMERCE_MCP_URL=http://127.0.0.1:8003/mcp \
SUPPORT_MCP_URL=http://127.0.0.1:8001/mcp \
UTILITY_MCP_URL=http://127.0.0.1:8002/mcp \
UPSTREAM_TIMEOUT_SECONDS=5 \
GATEWAY_DATABASE_URL=postgresql://gateway:set-the-password-here@127.0.0.1:5432/gateway \
uv run uvicorn apps.gateway.app.main:app --host 127.0.0.1 --port 8000
```

From `dashboard/`:

```bash
npm ci
npm run dev
```

Open http://127.0.0.1:5173. `GATEWAY_ORIGIN` defaults to `http://127.0.0.1:8000` and must be an absolute http or https origin without credentials.

| Endpoint | Where |
| --- | --- |
| Gateway health | `GET http://127.0.0.1:8000/health` |
| MCP | `POST http://127.0.0.1:8000/mcp` |
| Admin API | `http://127.0.0.1:8000/admin/...` |
| Dashboard dev UI | `http://127.0.0.1:5173` (proxies `/admin` only) |

`/health` returns `{"status":"ok"}` when PostgreSQL answers, and `503` with `{"status":"unavailable"}` when it does not. It does not require an API key.

## Tests

Gateway tests, including the disposable PostgreSQL tests:

```bash
uv run pytest
```

`pyproject.toml` sets `testpaths = ["tests"]` and `asyncio_mode = "auto"`. Most tests use an in-memory store and scripted HTTP. `tests/test_postgres_permissions.py` and `tests/test_reset_admin_password_postgres.py` start a new `postgres:16-alpine` container on `127.0.0.1` only, with a random name and no named volume. They refuse a mount named `gateway_db`. They do not use Compose or Kubernetes. Docker must be available. Unset `GATEWAY_DATABASE_URL` if your shell inherited one from a live environment; the fixtures also reject the Compose database name.

Dashboard tests and production build:

```bash
cd dashboard
npm ci
npm test
npm run build
```

`npm test` mocks HTTP. It does not contact a gateway.

The isolated end-to-end harness used during development is not part of this repository. It is a local process that must create its own database, bind only to `127.0.0.1`, and avoid Compose, Kind, and existing volumes. Do not point it at a live cluster. It is not run by the GitHub Actions workflow.

## Docker Compose

Compose is a local store plus gateway stack. It is not the Kind deployment. The project name comes from the directory. Volumes are `wordpress_db`, `wordpress_html`, and `gateway_db`. Do not delete those volumes if you need the existing store or gateway data.

Published ports from `compose.yaml`:

| Service | Host port |
| --- | --- |
| WordPress | 8080 |
| Gateway | 8000 |
| Support MCP | 8001 |
| Utility MCP | 8002 |
| WooCommerce MCP | 8003 |

PostgreSQL and MariaDB are not published. The gateway image tag in Compose is `urumi-gateway:local`.

Build, then start only when you intend to create or reuse those volumes:

```bash
docker build -f apps/gateway/Dockerfile -t urumi-gateway:local .
docker build -f apps/support_mcp/Dockerfile -t urumi-support-mcp:local .
docker build -f apps/utility_mcp/Dockerfile -t urumi-utility-mcp:local .
docker build -f apps/woocommerce_mcp/Dockerfile -t urumi-woocommerce-mcp:local .
docker compose up --build
```

Set `GATEWAY_DB_PASSWORD` before the first start. Issue a key only after `gateway-db` is healthy:

```bash
docker compose run --rm gateway-key issue
docker compose run --rm gateway-key grant KEY_ID utility__convert_units
```

The raw key is printed once. Store it outside the repository.

Compose does not pass `GATEWAY_UPSTREAM_KEY` or `CHAT_API_KEY` into the gateway service. Dynamic upstream credentials and chat stay unavailable until those variables are added to the gateway environment. Static Support, Utility, and WooCommerce URLs are environment upstreams and stay trusted by default, which is what allows the Compose hostnames.

WooCommerce product and order tools register only when `WOOCOMMERCE_BASE_URL`, `WOOCOMMERCE_CONSUMER_KEY`, and `WOOCOMMERCE_CONSUMER_SECRET` are set. Create the REST key in wp-admin, put it in `.env`, and recreate `woocommerce-mcp` and `gateway`. Until then, WooCommerce health reports `woocommerce_api: not_configured`.

## Kubernetes

`helm/urumi/README.md` is the chart guide. `values-dev.yaml` is the local overlay for `kind-urumi-k8s-dev`. Its image tags are gateway `chat-20261018`, dashboard `module5-20261018`, and Support, Utility, and WooCommerce MCP `k8s-test`. `docs/assignment-tracker.md` records, on 2026-10-19, gateway image `urumi-gateway:partial-20261019` and dashboard image `urumi-dashboard:partial-20261019`. The recorded rollback images are `urumi-gateway:grants-msg-20261019` and `urumi-dashboard:module5-20261018`. Those are recorded historical states, not a verified current cluster inspection, and they are not this checkout. Earlier notes in `helm/urumi/README.md`, `docs/threat-model.md`, and `docs/baseline-verification.md` still name `urumi-gateway:grants-msg-20261019` as the image running earlier that day. The tracker is the later record. Do not `helm upgrade` with the current `values-dev.yaml`: that file's gateway tag and `networkPolicy.probeCIDRs` disagree with the tags and policies recorded in the tracker. The commands are in the chart guide. HTTP hosts are `store.local`, `gateway.local`, and `dashboard.local`. The tracker also records, on 2026-10-19, dev node InternalIP `172.19.0.2`, test node InternalIP `172.19.0.3`, and NetworkPolicy objects that allowed `172.19.0.2/32`. `networkPolicy.probeCIDRs` in `values-dev.yaml` is still `172.19.0.3/32`. Neither CIDR is a verified kubelet probe source, because that source IP has not been captured. The values file was not applied. This paragraph was not checked against the cluster. `docs/assignment-tracker.md` Modules 9 and 10 keep the addresses recorded for those 2026-10-18 checks. `values-vps.yaml` is a worksheet of `replace-me` placeholders. It has not been deployed. It is not a production configuration, and it does not provide TLS or a registry.

The chart does not install an Ingress controller. On the dev Kind cluster, `ingress-nginx` is installed separately and the node is labeled `ingress-ready=true`. The controller listens on port 80 inside the dev node. Docker does not publish the dev node's port 80. Host ports 80 and 443 are published by `urumi-k8s-test-control-plane`.

`scripts/dev-port80-forward.py` runs in container `urumi-dev-port80`, which shares the test node's network namespace and forwards TCP port 80 to the dev node at `172.19.0.2`. The Windows hosts file maps all three names to `127.0.0.1`:

```
127.0.0.1    store.local
127.0.0.1    dashboard.local
127.0.0.1    gateway.local
```

A browser on this laptop then opens `http://store.local/`, `http://dashboard.local/`, and `http://gateway.local/health`. On 2026-10-19 those three routes returned HTTP 200 through the dev node and through the forwarder. WordPress `home` and `siteurl` stay `http://store.local`. Recheck the dev node address with `kubectl get nodes -o wide` before replacing the forwarder. The running forwarder already targets `172.19.0.2`. If that address changes, replace the container:

```bash
docker rm -f urumi-dev-port80
docker run -d --name urumi-dev-port80 \
  --restart unless-stopped \
  --network container:urumi-k8s-test-control-plane \
  --user root \
  --entrypoint /app/.venv/bin/python \
  -v "$PWD/scripts/dev-port80-forward.py:/forward.py:ro" \
  urumi-gateway:chat-20261018 \
  /forward.py <dev-node-ip> 80
```

`docker rm -f urumi-dev-port80` stops the forwarder and leaves both clusters' port publishes unchanged. Direct checks against the dev node still use `curl --resolve <host>:80:<dev-node-ip>`.

The chart does not build images. A node runs only an image that has been built and loaded or pulled. Building `:local` for Compose does not update a cluster tag.

Render and lint without installing:

```bash
helm lint helm/urumi
helm template urumi helm/urumi -f helm/urumi/values-dev.yaml
helm template urumi helm/urumi -f helm/urumi/values-vps.yaml
```

Do not run `helm install` or `helm upgrade` until image tags, Secrets, storage, DNS, and the ingress controller have been reviewed. PersistentVolumeClaims for PostgreSQL, MariaDB, and WordPress are retained by default. Deleting a claim, or a StorageClass with reclaim policy `Delete`, can still destroy data.

There is no separate production deployment in this repository beyond that VPS worksheet.

Additional stores are not part of that Helm release. Both provisioner flags stay false. A separate provisioner would apply store objects, and only after the admission policy is installed. That has not been done. `docs/multi-store.md` describes the lifecycle. No store was created on `kind-urumi-k8s-dev`. `scripts/disposable-store-acceptance.sh` has not been executed.

## Security

- Never commit `.env`, recovery passwords, API keys, database URLs with real passwords, or Helm values that contain Secret data.
- Generate `GATEWAY_UPSTREAM_KEY` with `openssl rand -base64 32` when you need to store upstream credentials. It must be at least 32 characters. Keep it in the process environment or a Kubernetes Secret.
- `CHAT_API_KEY` is optional. Without it, `POST /admin/chat` returns 503.
- Dynamic upstream URLs reject loopback, link-local, and metadata addresses. Private addresses are allowed unless `UPSTREAM_ALLOW_PRIVATE` is false. Environment upstreams are trusted unless `UPSTREAM_TRUST_ENVIRONMENT` is false.
- A Kubernetes Secret is base64, not encryption. Restrict Secret read access and enable encryption at rest before treating those values as protected.
- Login failures are limited per process. Session revocation is stored in PostgreSQL, so it applies to every gateway process that shares that database.
- `woocommerce__create_order` idempotency lives in the WooCommerce MCP process memory. It does not survive a restart and is not safe across multiple processes.

## Limitations

- Discovery search matches tokens in the public name, upstream id, and description. It does not match synonyms, and it was exercised with in-process fakes rather than the dev cluster.
- Multi-store provisioning stays off. `storeProvisioner.enabled` and `storeProvisioner.admit` are false. Helm renders the provisioner only when both are true. The admission policy is not installed on a cluster. It has to be installed and enforced before provisioning is enabled. RBAC does not limit namespace deletion to `st-<id>`. Store manifest and unit tests check rendered objects and in-process behavior. They do not show runtime cross-store isolation, and they do not prove that NetworkPolicies are enforced. The disposable-cluster acceptance script has not been run. See `docs/multi-store.md`.
- The audit insert is not in the same database transaction as the mutation. A crash after the commit, or a failed audit insert, can leave a committed change without the expected audit row. `Auditor.record` logs the exception type and does not change the HTTP result.
- Registry refresh compensation undoes the upstream write when it can. It is not a single transaction wrapped around the in-process refresh.
- Dashboard chat tests mock the model provider. On `kind-urumi-k8s-dev`, Secret `urumi-dev-chat` was used for live Groq chat on 2026-10-18. That key is not in the repository. Without it, `POST /admin/chat` returns 503. The recorded calls created products 17 and 19 and listed live orders. See `docs/assignment-tracker.md`.
- Resource requests in the chart are placeholders, not a measured capacity plan.
- Unit tests do not prove that a running cluster is serving this checkout. The dev-cluster checks recorded in `docs/assignment-tracker.md` say Calico v3.30.2 enforced NetworkPolicy on `kind-urumi-k8s-dev` (the gateway reached WooCommerce MCP; Support and Utility timed out). That note is not a new cluster inspection, and it is not what the store manifest tests prove. Those tests render objects. A node rebuild can put Kindnet back, and Kindnet does not enforce NetworkPolicy.
- `values-vps.yaml` is unverified as a deployment. `helm lint` and `helm template` of that file are not a VPS install. No registry, DNS name, TLS certificate, or `helm upgrade` is recorded. A VPS deployment has not been done.

## Troubleshooting

- Gateway `/health` returns 503: PostgreSQL is down or `GATEWAY_DATABASE_URL` is wrong. Readiness should fail. Do not use `/health` as a liveness probe that restarts the process for a database outage. The chart uses TCP for gateway liveness and `/health` for readiness.
- Compose gateway cannot resolve `gateway-db`: the gateway is running on the host. Use a published database port or run the gateway in Compose.
- WooCommerce tools are missing: the consumer key and secret are empty, or the MCP server was not recreated after `.env` changed.
- Dashboard admin calls fail in dev: the Vite server must be able to reach `GATEWAY_ORIGIN`. It proxies `/admin` only. `/mcp` and `/discovery/search` are not proxied. Client discovery search goes to the gateway, port 8000 in the host setup.
- Helm install exceeds five minutes on first database startup: the chart guide uses `--timeout 20m`.
- `values-vps.yaml` still contains `replace-me`: do not install it.

## Status

Implemented in this source tree: gateway authentication, grants, namespaced tools, collision exclusion, upstream enablement, admin sessions, audit of successful admin mutations, upstream diagnostics in the dashboard, and compensation when registry refresh fails after an upstream write.

Recorded on the local Kind cluster, and not a production deploy: Ingress on `store.local`, `dashboard.local`, and `gateway.local`, Calico NetworkPolicy enforcement, and live chat tool calls. Not claimed: that this checkout is what the cluster is running now, that `values-vps.yaml` is production-ready, that TLS or a VPS exists, or that a live card checkout exists.
