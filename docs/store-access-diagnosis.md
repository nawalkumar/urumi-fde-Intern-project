# Development store access diagnosis

Audit date: 2026-10-13. Scope is local browser access to the WordPress/WooCommerce instance in Kubernetes context `kind-urumi-k8s-dev`, namespace `default`. This document records observations and a proposed fix. No routing fix has been applied in this audit. Later notes are elsewhere: `docs/store-access-fix-report.md` records the 2026-10-13 proxy, and `docs/assignment-tracker.md` Module 10 records laptop Ingress on 2026-10-18. This file is not rewritten to match those later results.

The audit started a temporary `kubectl port-forward` to `127.0.0.1:18082` for HTTP checks, then stopped that process. No port-forward is running now. No Helm release, Ingress, Service, Pod, PVC, WordPress option, Docker container, or Windows file was modified.

## Root cause

The development WordPress Service is healthy and answers HTTP inside the cluster, but nothing routes a browser to it on a URL WordPress will keep using.

Three facts combine:

1. `urumi-wordpress` is ClusterIP port 80. The dev Kind node publishes only the API server, `127.0.0.1:42439->6443`. It does not publish port 80 or 443.
2. Helm release `urumi` renders Ingress `urumi-ingress` with class `nginx` and host `store.local`, and WordPress `home` and `siteurl` are both `http://store.local`. The chart does not install an Ingress controller. The dev cluster has no IngressClass and no controller pods, so the Ingress has an empty load-balancer address and does not receive traffic.
3. `store.local` resolves to `127.0.0.1`. Host ports 80 and 443 are published by the other cluster's node, `urumi-k8s-test-control-plane`. Connections to `http://store.local/`, `http://127.0.0.1/`, and `https://127.0.0.1/` are reset. A browser that follows WordPress links or redirects to `http://store.local` reaches that occupied port, not the development WordPress pod.

A plain port-forward is not sufficient. WordPress emits absolute `http://store.local` URLs and redirects. The temporary proxy in `/tmp/urumi-store-proxy.py` follows those redirects with `urllib.request.urlopen`, so it connects to port 80 and receives the same reset.

## Evidence

### Chart intent

| Item | Observation |
| --- | --- |
| `helm/urumi/values.yaml` | `ingress.enabled` defaults to false. `ingress.className` is `nginx`. Comment: the controller is not installed by the chart. |
| `helm/urumi/values-dev.yaml` | `ingress.enabled: true`, class `nginx`, hosts `store.local`, `gateway.local`, `dashboard.local`. `store.publicUrl` is `http://store.local`. Comment: this file does not install a controller. |
| `helm/urumi/templates/ingress.yaml` | Rendered only when Ingress is enabled. Store rule sends `/` to the WordPress Service port. |
| `helm/urumi/templates/wordpress-service.yaml` | Service type is ClusterIP. |
| `helm/urumi/templates/store-configmap.yaml` | `WORDPRESS_URL` comes from `store.publicUrl`. |
| `helm/urumi/scripts/init-store.sh` | First install passes `--url="$WORDPRESS_URL"`. An already-installed site does not get a new URL. |
| `helm/urumi/README.md` and `templates/NOTES.txt` | The chart does not install an Ingress controller or create DNS records. |

Installed user values match `values-dev.yaml` for Ingress and `store.publicUrl`. Secret values were not read.

### Development cluster

Checked with context `kind-urumi-k8s-dev` on 2026-10-13 after the audit port-forward was stopped.

| Resource | Result |
| --- | --- |
| Helm release | `urumi`, namespace `default`, revision 1, chart `urumi-0.3.0`, status `deployed` |
| Pod `urumi-wordpress-694c69d4b4-pbhf5` | `1/1 Running`, node `urumi-k8s-dev-control-plane` |
| Service `urumi-wordpress` | ClusterIP `10.96.36.101`, port 80, selector matches the WordPress pod |
| ConfigMap `urumi-store-config` | `WORDPRESS_URL=http://store.local` |
| WordPress options | `home=http://store.local`, `siteurl=http://store.local`. Administrator login `urumi-dev` exists. Password and email were not printed. |
| Init Job `urumi-wordpress-init` | `Complete` |
| Ingress `urumi-ingress` | Class `nginx`. Hosts `store.local`, `gateway.local`, `dashboard.local`. `status.loadBalancer` is empty. |
| IngressClass | None |
| Ingress controller pods | None in any namespace |
| PVCs | `data-urumi-gateway-db-0`, `data-urumi-store-db-0`, and `urumi-wordpress-data` are `Bound` |
| NetworkPolicies | Present. Chart README states Kind's default kindnet does not enforce them. |
| Dev node container | `1ed1cf799bd9` `urumi-k8s-dev-control-plane`, `127.0.0.1:42439->6443/tcp` |

### Hostname and ports

| Check | Result |
| --- | --- |
| WSL `/etc/hosts` | No `store.local`, `gateway.local`, or `dashboard.local` entry |
| `getent hosts store.local` | `127.0.0.1`. WSL `resolv.conf` nameserver is `10.255.255.254` |
| `gateway.local`, `dashboard.local` | Do not resolve in WSL |
| Windows `C:\Windows\System32\drivers\etc\hosts` | Line 23 is `127.0.0.1 store.local`. That file was not edited. `gateway.local` and `dashboard.local` are absent. |
| `~/.wslconfig` under `/mnt/c/Users` | Not found |
| Host ports 80 and 443 | Published by `b9c0856f352a` `urumi-k8s-test-control-plane` as `0.0.0.0:80->80/tcp` and `0.0.0.0:443->443/tcp` |
| `curl http://store.local/` and `curl http://127.0.0.1/` | `curl: (56) Recv failure: Connection reset by peer` |
| `curl https://127.0.0.1/` | `Connection reset by peer` during TLS |
| Test cluster context `kind-urumi-k8s-test` | Read-only list shows no Ingress and no WordPress Service. Several Calico and CoreDNS pods are not Ready. This cluster was not modified. |
| Ports 8080, 18080–18086, 18090, 18443 | Free on `127.0.0.1` at audit time. Rechecked 18082, 18083, and 18085; still free. |
| Existing port-forwards | None at audit start, and none after the temporary audit forward was stopped |
| Compose project `urumi-project` | Eight containers are `Exited`. They were not started or stopped. WordPress Compose container `76b61d180dfd` is exited and its published mapping is `0.0.0.0:8080->80/tcp`. |
| Python | 3.10.12 |

### Direct WordPress HTTP behavior

These requests went through a temporary port-forward, `127.0.0.1:18082` to Service `urumi-wordpress` port 80. Redirects were not followed. Cookie values were not recorded.

| Request | Status | Relevant result |
| --- | --- | --- |
| `GET /` with `Host: 127.0.0.1:18082` | 301 | `Location: http://127.0.0.1/`, `X-Redirect-By: WordPress` |
| `GET /` with `Host: store.local` | 200 | HTML contains absolute `http://store.local/...` asset and feed links. Body length 73898. |
| `GET /wp-login.php` with either Host | 200 | Form action is `http://store.local/wp-login.php`. `Set-Cookie` name `wordpress_test_cookie`, attributes `path` and `HttpOnly`. No `Domain` attribute. |
| `GET /wp-admin/` with `Host: 127.0.0.1:18082` | 302 | `Location: http://store.local/wp-login.php?redirect_to=http%3A%2F%2F127.0.0.1%3A18082%2Fwp-admin%2F&reauth=1` |
| `GET /wp-admin/` with `Host: store.local` | 302 | `Location: http://store.local/wp-login.php?redirect_to=http%3A%2F%2Fstore.local%2Fwp-admin%2F&reauth=1` |
| `GET /wp-login.php` with `Accept-Encoding: gzip` and `Host: store.local` | 200 | `Content-Encoding: gzip` |

`urllib.request.urlopen` against `GET /` on the port-forward raised `ConnectionResetError: [Errno 104] Connection reset by peer`. The same call for `GET /wp-admin/` with `Host: store.local` raised the same error. With redirect handling disabled, `GET /` returned HTTP 301 and `Location: http://127.0.0.1/`.

### Why `/tmp/urumi-store-proxy.py` fails

The script listens on `127.0.0.1:18085` and forwards to `http://127.0.0.1:18083` with `Host: store.local`. It uses `urllib.request.urlopen`, which follows redirects. Observed failure modes:

- Nothing is listening on 18083 or 18085 now. The earlier port-forward was described on 18082, which is not the port this script calls.
- When a backend does answer, `GET /wp-admin/` with `Host: store.local` is a 302 to `http://store.local/wp-login.php?...`. Following that URL connects to `127.0.0.1:80` and the test-cluster mapping resets the connection.
- `GET /` through a port-forward without rewriting `Host` is a 301 to `http://127.0.0.1/` with no port. Following that URL hits the same reset.
- The login form and admin assets use absolute `http://store.local` URLs. Passing the HTML through unchanged sends the browser to port 80.
- The script implements only `GET`, `POST`, and `HEAD`.
- It does not rewrite `Location`, HTML, CSS, JavaScript, or JSON, and it does not decompress gzip before inspection.
- A WSL process bound only to `127.0.0.1:18082` showed up as listening in Windows `netstat`, but Windows `curl.exe` to that port timed out with 0 bytes. A short-lived WSL listener on `0.0.0.0:18084` answered Windows `curl.exe`. While a later listener was bound to `0.0.0.0:18086`, Windows `netstat` showed `127.0.0.1:18086` listening under `wslrelay`, not `0.0.0.0`. Those listeners were closed. Windows `curl.exe` to the Docker-published API `https://127.0.0.1:42439/version` returned HTTP 200.

## Risks

- Changing `home` or `siteurl` would write the existing WordPress database. `WOOCOMMERCE_SITE_URL` defaults to `store.publicUrl` (`http://store.local`). The in-cluster REST client signs against that public URL. Leaving the stored URLs unchanged keeps that contract.
- Installing an Ingress controller, or mapping host ports 80 or 443 on the dev cluster, conflicts with the test cluster's published ports and needs separate approval.
- Opening `http://store.local` in a browser still reaches the test cluster's port 80 until some later, separately approved hostname and port plan exists.
- A URL-rewriting proxy can miss a URL that WordPress assembles from split strings at runtime. Login HTML, redirects, and localized script URLs observed in this audit are whole `http://store.local` strings.
- Binding the browser listener to `0.0.0.0` inside WSL is what made Windows localhost succeed. Windows then published it on `127.0.0.1` only. Inside the WSL network namespace the socket is open on all interfaces, including the Docker bridge. It is not a Docker `0.0.0.0` publish.
- The proxy process is local and disappears when stopped. It does not survive a reboot by itself.

## Proposed smallest safe fix

Add one development-only Python 3.10 standard-library process. It does not change Helm, Kubernetes objects, WordPress options, PVCs, Compose, or the test cluster.

Browser URL: `http://127.0.0.1:18082/wp-login.php`

Process layout:

1. `kubectl --context kind-urumi-k8s-dev -n default port-forward --address 127.0.0.1 svc/urumi-wordpress 18083:80`
2. A rewriting proxy on `0.0.0.0:18082` that connects only to `127.0.0.1:18083`.

Proxy behavior:

- Forward the request method, path, query string, body, and headers. Send `Host: store.local` upstream so WordPress serves the canonical host.
- Do not follow redirects. Rewrite `Location` and `Refresh` targets that use `http://store.local` or `http://127.0.0.1` without a port to `http://127.0.0.1:18082`.
- Decompress gzip text responses, rewrite `http://store.local` and protocol-relative `//store.local` in HTML, CSS, JavaScript, and JSON, then send a corrected `Content-Length`.
- Keep `Set-Cookie` values unchanged. If a `Domain=store.local` attribute appears, drop that attribute so the cookie stays on `127.0.0.1`.
- Support `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD`, and `OPTIONS`.
- Do not log request bodies, cookies, or credentials.
- On Ctrl-C, stop the proxy and the port-forward it started.

This keeps canonical WordPress redirects inside the proxy: upstream `Host: store.local` avoids the port-stripping 301, and rewritten links and `Location` headers stay on port 18082, which is reachable from WSL and from Windows localhost.

## Files and resources that would change after approval

| Path or resource | Change |
| --- | --- |
| `scripts/dev-store-access.py` | New file. Local launcher and proxy. |
| `docs/store-access-diagnosis.md` | This audit record. Already added. |
| Kubernetes, Helm, WordPress data, Compose, test cluster, Windows hosts | No change |

## Commands that would run after approval

Create the script, then start it in the foreground from the repository root:

```bash
python3 scripts/dev-store-access.py
```

That process starts the port-forward above and serves `http://127.0.0.1:18082/`. No `kubectl apply`, `helm upgrade`, or Docker Compose command is part of the fix.

## Rollback

- Press Ctrl-C in the terminal running `scripts/dev-store-access.py`. That stops the proxy and its port-forward.
- Delete `scripts/dev-store-access.py` if the script should not remain in the tree.
- No cluster, database, or volume rollback is required, because those objects are not changed.

## Verification planned after approval

Record actual pass or fail for each item. Do not treat this list as results.

- `GET http://127.0.0.1:18082/wp-login.php` returns HTTP 200.
- Response `Location` headers and the login form action use `http://127.0.0.1:18082`, not `http://store.local`.
- A redirect target returned by the proxy is reachable on 18082.
- Login `POST` handling preserves the body and returns `Set-Cookie` without a `Domain=store.local` attribute. Successful admin login is a manual browser test unless an already-exported credential is supplied outside the chat log. Cookie values will not be printed.
- WordPress pod stays `Running` and Ready. PVCs stay `Bound`.
- Compose container IDs and both Kind node port mappings stay as recorded above.
