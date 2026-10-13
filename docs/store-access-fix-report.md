# Development store access fix

Implemented on 2026-10-13. The development WordPress Service is reachable in a browser at:

**http://127.0.0.1:18083/wp-login.php**

The proxy is running. Sign in there as `urumi-dev` with the existing local administrator password. This report does not include that password, and browser login was not completed here.

## Root cause

`urumi-wordpress` is a ClusterIP Service. Ingress `urumi-ingress` names class `nginx` and host `store.local`, but the dev cluster has no Ingress controller. WordPress `home` and `siteurl` are `http://store.local`. That name resolves to `127.0.0.1`, and host ports 80 and 443 belong to `urumi-k8s-test-control-plane`. Requests to `http://store.local/` are reset. A raw port-forward also fails in a browser because WordPress redirects and writes absolute `http://store.local` URLs. Details are in `docs/store-access-diagnosis.md`.

## Fix implemented

`scripts/dev-store-access.py` starts one `kubectl port-forward` and a local reverse proxy.

| Role | Binding |
| --- | --- |
| Browser proxy | `0.0.0.0:18083` inside WSL. Windows `wslrelay` publishes it as `127.0.0.1:18083`. |
| Port-forward | `127.0.0.1:18084` to `svc/urumi-wordpress:80` in context `kind-urumi-k8s-dev`, namespace `default`. |

Preferred ports were 18082 and 18083. At startup, Windows localhost connected to a probe on 18082 and the probe never saw that connection, so the script skipped 18082. Windows `netstat` still shows `127.0.0.1:18082` listening under PID 14492 after this proxy is stopped. That process was not terminated. The proxy took 18083, and the port-forward took the next free port, 18084.

The proxy sends `Host: store.local` upstream and does not follow redirects. It rewrites `store.local` in `Location`, `Refresh`, `Content-Location`, and `Link` headers, and in HTML, CSS, JavaScript, JSON, and XML bodies, including percent-encoded and `\/` forms. Request paths, query strings, and bodies are forwarded. `Referer`, `Origin`, and text form bodies that contain the proxy origin are translated back to `http://store.local` before WordPress sees them. `Set-Cookie` values are preserved. A `Domain=store.local` attribute would be removed; the observed login cookies had no `Domain` attribute.

`SIGINT`, `SIGTERM`, and `SIGHUP` stop the proxy and the port-forward process group it created.

No Helm release, Kubernetes object, WordPress option, Compose container, test cluster, or Windows hosts file was changed.

## Files changed

| Path | Change |
| --- | --- |
| `scripts/dev-store-access.py` | New development proxy and port-forward launcher. |
| `docs/store-access-fix-report.md` | This report. |
| `docs/store-access-diagnosis.md` | Phase A audit. Unchanged in this step. |

## Tests and results

`python3 scripts/dev-store-access.py --self-test` printed `self-test ok`.

The behavioral checks below were run against proxy port 18083. After a later restart that only added `SIGHUP` handling, the final process was checked again: Windows `curl.exe` returned HTTP 200 for the login page, WSL `GET /wp-login.php` returned HTTP 200 with form action `http://127.0.0.1:18083/wp-login.php` and no `store.local` in the body, and `GET /wp-admin/` returned HTTP 302 to `http://127.0.0.1:18083/wp-login.php?redirect_to=http%3A%2F%2F127.0.0.1%3A18083%2Fwp-admin%2F&reauth=1`.

| Test | Result |
| --- | --- |
| `GET /wp-login.php`, including `Accept-Encoding: gzip` | Pass. HTTP 200. `Content-Type: text/html`. Body contains the proxy origin and does not contain `store.local`. Form action is `http://127.0.0.1:18083/wp-login.php`. |
| `GET /` | Pass. HTTP 200. Body contains the proxy origin and does not contain `store.local`. |
| `GET /wp-admin/` without following redirects | Pass. HTTP 302. `Location` is on port 18083 and does not contain `store.local`. |
| `GET` of that `Location` | Pass. HTTP 200 login page. |
| `GET /wp-login.php?action=lostpassword` | Pass. HTTP 200. Query string preserved. Form action includes `action=lostpassword`. |
| `GET /wp-includes/js/clipboard.min.js?ver=2.0.11` | Pass. HTTP 200, `text/javascript`, 9009 bytes, no `store.local`. |
| `HEAD /wp-login.php` | Pass. HTTP 200, empty body. |
| `POST /wp-login.php` with username `dev-access-check` and password `incorrect-local-check` | Pass for routing only. HTTP 200, response contains `id="login_error"`, form action stays on the proxy, and there is no `Location` to `store.local`. This was not an administrator login. |
| Login `Set-Cookie` | Pass. Name `wordpress_test_cookie`, flags `path` and `HttpOnly`, no `Domain`. The unauthenticated `/wp-login.php` response also sets other WordPress cookie names with `Max-Age=0` and no `Domain`. Those are cleared cookies, not a session. Cookie values were not recorded. |
| Windows `curl.exe http://127.0.0.1:18083/wp-login.php` | Pass. `HTTP/1.0 200 OK`. Repeated on the final process with status 200. |
| Windows listeners | Pass for localhost scope. `127.0.0.1:18083` and `127.0.0.1:18084` listen via `wslrelay`. Neither is `0.0.0.0` on Windows. |
| LAN addresses from Windows `curl.exe` | Pass. Connection failed for Wi-Fi `10.140.62.59`, vEthernet `172.27.128.1`, and link-local `169.254.8.242` and `169.254.49.205`. No HTTP response. |
| WordPress pod | Pass. `urumi-wordpress-694c69d4b4-pbhf5` remained `1/1 Running`. |
| PVCs | Pass. `data-urumi-gateway-db-0`, `data-urumi-store-db-0`, and `urumi-wordpress-data` remained `Bound` with the same volume IDs. |
| Kind and Compose containers | Pass. Dev node `1ed1cf799bd9` still publishes only `127.0.0.1:42439->6443`. Test node `b9c0856f352a` still publishes `0.0.0.0:80`, `0.0.0.0:443`, and `127.0.0.1:41405`. The eight `urumi-project` Compose containers stayed `Exited` with the same IDs. |
| Port-forward ownership | Pass. One port-forward is running, PID 97387, parented by this proxy, PID 97268. |
| `SIGTERM` cleanup | Pass. Three earlier proxy processes were stopped with `SIGTERM` during port selection. Each time the child `kubectl port-forward` exited and its WSL port closed. `SIGINT` uses the same handler and was not sent to the final process. |

## Manual verification still required

Open **http://127.0.0.1:18083/wp-login.php** in the Windows browser.

1. The login page should show styles and the form should stay on `127.0.0.1:18083`.
2. Sign in as `urumi-dev`.
3. Confirm `http://127.0.0.1:18083/wp-admin/` loads, including CSS and scripts, and the address bar does not move to `store.local`.
4. In WooCommerce, open Settings, Advanced, REST API, and create a Read/Write key. Copy the consumer key and secret into the local secret store yourself. Do not paste them into chat.

Administrator login and the REST API key screen were not exercised here.

## How to stop and roll back

From a WSL shell:

```bash
kill -TERM 97268
```

PID 97268 is the running `python3 scripts/dev-store-access.py`. That signal also stops port-forward PID 97387. If that Python process is already gone, stop only a matching `kubectl port-forward` for `svc/urumi-wordpress` on `127.0.0.1:18084` if one is still present. In the terminal where the script is attached, Ctrl-C does the same cleanup.

To remove the tool, delete `scripts/dev-store-access.py`. No Helm, Kubernetes, database, or volume rollback is required.

Start it again with:

```bash
python3 scripts/dev-store-access.py
```

The script prints the browser URL. If 18083 is busy, it chooses another free port and the printed URL is the one to open.

## Limitations

- `http://store.local` still reaches the test cluster's port 80. Use the printed `127.0.0.1` URL.
- The WSL socket listens on `0.0.0.0:18083` because a `127.0.0.1`-only socket did not accept Windows connections. Windows exposes the port on `127.0.0.1` only. Processes inside the WSL network namespace can still connect to that socket.
- The proxy drops `Accept-Encoding` and rewrites uncompressed text. A response with a content encoding other than `identity` or `gzip` returns HTTP 502.
- URLs assembled in the browser from split strings are not rewritten.
- Multipart upload bodies are forwarded unchanged, so a proxy URL inside a multipart field is not translated.
- `gateway.local` and `dashboard.local` are not part of this proxy.
- The proxy process must stay running. It is not installed as a service.

## Remaining blockers for the assignment

WooCommerce REST credentials still have to be created in wp-admin after the manual login above. The chart does not create them. Until the consumer key and secret are stored for the development cluster, the WooCommerce MCP server stays unconfigured. That storage step is outside this routing fix.
