# Urumi Helm chart

This chart packages the gateway, Support Notes MCP, Utility MCP, WooCommerce MCP, the dashboard, one bundled PostgreSQL 16 database, MariaDB 11.4, and WordPress with the WooCommerce initialization Job. Rendering these templates does not update a running cluster. A pod runs the image tag you built and loaded, not the working tree. `values-dev.yaml` uses the tag `k8s-test`. That tag is not proof that a cluster is running the current source.

The chart does not create, grant, or revoke gateway API keys. It does not create WooCommerce REST API keys, products, or orders.

Public tool names stay `woocommerce__...`, `support__...`, and `utility__...`. Those ids come from `WOOCOMMERCE_MCP_URL`, `SUPPORT_MCP_URL`, and `UTILITY_MCP_URL`. The Service DNS name does not change the id.

## What a release contains

With release name `urumi`, the private ClusterIP Services are:

| Service | Port | Purpose |
| --- | --- | --- |
| `urumi-gateway` | 8000 | `GET /health`, `GET /admin/status`, `/mcp` |
| `urumi-support-mcp` | 8000 | Support Notes `/health` and `/mcp` |
| `urumi-utility-mcp` | 8000 | Utility `/health` and `/mcp` |
| `urumi-woocommerce-mcp` | 8000 | WooCommerce MCP `/health` and `/mcp` |
| `urumi-gateway-db` | 5432 | Gateway PostgreSQL |
| `urumi-store-db` | 3306 | Store MariaDB |
| `urumi-wordpress` | 80 | WordPress |
| `urumi-dashboard` | 80 | Dashboard. The container listens on 8080 |

There is no NodePort or LoadBalancer. Database and upstream MCP Services stay ClusterIP and have no Ingress. Public Ingress, when enabled, is only the storefront, the dashboard, and the gateway. The chart does not install an ingress controller.

Gateway upstream URLs are:

```text
http://urumi-support-mcp:8000/mcp
http://urumi-utility-mcp:8000/mcp
http://urumi-woocommerce-mcp:8000/mcp
```

`UPSTREAM_TIMEOUT_SECONDS` is `20`, matching Compose. The process default is `5` only when the variable is absent.

## Images

The chart does not build or pull images. A cluster can start the pods only after these images exist where that cluster can pull them. Local tags are not visible to a remote node.

From the repository root, the image build commands are:

```bash
docker build -f apps/gateway/Dockerfile -t urumi-gateway:local .
docker build -f apps/support_mcp/Dockerfile -t urumi-support-mcp:local .
docker build -f apps/utility_mcp/Dockerfile -t urumi-utility-mcp:local .
docker build -f apps/woocommerce_mcp/Dockerfile -t urumi-woocommerce-mcp:local .
docker build -t urumi-dashboard:local dashboard
```

Each Dockerfile uses the repository root as its build context. Both stages use `python:3.10-slim`. The build stage copies uv from the version-pinned image `ghcr.io/astral-sh/uv:0.12.0`. It installs third-party dependencies with `uv sync --frozen --no-dev --no-install-project`, then, after `README.md` and `apps/` are copied, installs this project with `uv sync --frozen --no-dev --no-editable`. Both steps read `uv.lock`. The runtime stage keeps `/app/.venv` and does not keep uv. `postgres:16-alpine` is the same tag Compose uses. `values.yaml` tags the application images `urumi-gateway:local`, `urumi-support-mcp:local`, `urumi-utility-mcp:local`, `urumi-woocommerce-mcp:local`, and `urumi-dashboard:local`, matching the commands above. The dashboard image is `nginxinc/nginx-unprivileged:1.27-alpine` and listens on 8080 as UID 101. The chart mounts the Nginx site file so `/admin` is proxied to the gateway Service DNS name. Do not build that image as part of a static check. Store images are `mariadb:11.4`, `wordpress:6.8-php8.2-apache`, and `wordpress:cli-php8.2`. `values-dev.yaml` does not override those tags. `values-vps.yaml` sets `replace-me/...` names for the application images. The store images stay on the same public tags unless an operator overrides them.

The runtime user is `urumi`, UID and GID 10001. Local images can be built from these Dockerfiles. Installing or upgrading the chart is a separate, reviewed step. Do not treat a previous local cluster as updated with later source changes until its image tags are rebuilt and a reviewed upgrade is applied.

## Secrets

Committed values contain no passwords. Choose one path:

1. Create a Secret yourself and set `database.existingSecret`.
2. Set `database.password` in a gitignored file such as `helm/urumi/values-local.yaml`. The chart then creates the Secret. Do not commit that file. `helm template` prints `stringData` when the chart creates the Secret.

Do not set both `database.existingSecret` and `database.password`.

The Secret keys are:

| Key | Consumer | Value |
| --- | --- | --- |
| `POSTGRES_PASSWORD` | PostgreSQL image | Raw password |
| `GATEWAY_DATABASE_URL` | Gateway process | psycopg connection string |

The gateway reads only `GATEWAY_DATABASE_URL` and passes it to psycopg. When this chart creates the Secret, the value is a libpq keyword string, not a URI. psycopg accepts that form, including passwords that contain `@`, `:`, `/`, spaces, quotes, and backslashes. A single-line password is required.

Example shape, with a fake password:

```text
host='urumi-gateway-db' port=5432 dbname='gateway' user='gateway' password='p@ss:w/ord'
```

An operator-supplied Secret may instead use a percent-encoded URI. Encode the password before placing it in the URI. `url` query encoding is not enough for a space, because a space must be `%20`.

```text
postgresql://gateway:p%40ss%3Aw%2Ford@urumi-gateway-db:5432/gateway
```

`POSTGRES_USER` and `POSTGRES_DB` default to `gateway`. The connection string must use the same user, database, host, and port as the Service.

Create the development Secret yourself. This command is an example; it was not run here, and the password is a placeholder:

```bash
kubectl create secret generic urumi-dev-gateway-credentials \
  --from-literal=POSTGRES_PASSWORD='replace-with-a-real-password' \
  --from-literal=GATEWAY_DATABASE_URL="host='urumi-gateway-db' port=5432 dbname='gateway' user='gateway' password='replace-with-a-real-password'"
```

After rotating an existing Secret, change `database.rolloutToken` so the gateway and database pods roll. The token is not the password.

Optional WooCommerce keys are not required for this milestone. The MCP server starts with `woocommerce_api: not_configured` until all of these exist:

- `WOOCOMMERCE_BASE_URL`
- `WOOCOMMERCE_CONSUMER_KEY`
- `WOOCOMMERCE_CONSUMER_SECRET`

`CHAT_API_KEY`, `GATEWAY_UPSTREAM_KEY`, and the WooCommerce consumer keys are optional Secret references. If the Secret or key is absent, that environment variable is unset and only that feature stays disabled: chat returns 503, upstream credential storage fails closed, and WooCommerce tools stay unregistered. The gateway and WooCommerce MCP processes still start. Database passwords and WordPress administrator credentials are required. A missing database or store Secret keeps those pods from starting. When the store is enabled and `woocommerce.baseUrl` is empty, `WOOCOMMERCE_BASE_URL` is the in-cluster WordPress Service. `WOOCOMMERCE_SITE_URL` defaults to `store.publicUrl`. Override either value when the public address and the in-cluster address differ from those defaults. Product and order tools stay unregistered until the consumer key and secret are present. Create those keys in wp-admin after the init Job finishes. Do not put them in a committed values file.

## Store

MariaDB and WordPress use new PersistentVolumeClaims. Do not reuse or mount the Compose volumes `urumi-project_wordpress_db` and `urumi-project_wordpress_html`.

Create the store Secret before install. This command is an example. It was not run here, and every value is a placeholder:

```bash
kubectl create secret generic urumi-dev-store-credentials \
  --from-literal=MARIADB_ROOT_PASSWORD='replace-with-a-real-root-password' \
  --from-literal=MARIADB_PASSWORD='replace-with-a-real-database-password' \
  --from-literal=WORDPRESS_DB_PASSWORD='replace-with-a-real-database-password' \
  --from-literal=WORDPRESS_ADMIN_USER='replace-with-an-admin-user' \
  --from-literal=WORDPRESS_ADMIN_PASSWORD='replace-with-a-real-admin-password' \
  --from-literal=WORDPRESS_ADMIN_EMAIL='replace-with-an-email'
```

`WORDPRESS_DB_PASSWORD` must be the same value as `MARIADB_PASSWORD`. The chart's own Secret template sets them that way when `store.existingSecret` is empty. Do not commit that values file. `helm template` prints `stringData` when the chart creates the Secret.

`values-dev.yaml` expects the Secret name `urumi-dev-store-credentials`. `values-vps.yaml` uses `replace-me` until an operator supplies the real name.

The WordPress pod waits for MariaDB to accept TCP connections, then the official image creates `wp-config.php`. A post-install and post-upgrade Job runs `scripts/init-store.sh` as uid 33. The script is the same install sequence as `deploy/wordpress/init-store.sh`. It installs WordPress only when the site is not already installed, so a repeat run does not create another admin user. `wp core update` runs only in that first-install branch, because the image tag can lag the current release. An upgrade does not update WordPress core. The Job still activates WooCommerce and runs the WooCommerce page setup again. Those steps do not create products or orders. The first install needs outbound HTTPS to WordPress.org. An upgrade does not. The Job does not print secrets. It mounts the WordPress volume and must land on the same node because that claim is `ReadWriteOnce`. Its wait for `wp-config.php` is longer than the WordPress container's database wait, because the two waits overlap. `helm install` and `helm upgrade` need `--timeout 20m`; the Helm default of five minutes can expire during first database startup. Changing `store.rolloutToken` restarts the pods. It does not change a MariaDB user or root password that was written when the data directory was first initialized. `store.demoPayment` defaults to `cod`. The init Job then enables WooCommerce's built-in Cash on delivery method. Set it to an empty string to leave payment methods unchanged. The Job does not create products, orders, or customers. Cash on delivery is a demonstration option, not a card processor. Storefront checkout still depends on WooCommerce pages, a reachable public URL, and a later manual check.

MariaDB probes call `healthcheck.sh --connect --innodb_initialized` and do not put a password on the command line. WordPress readiness is HTTP `/`. WordPress liveness is TCP, so a slow PHP request does not restart the pod. The MariaDB and WordPress image entrypoints start as root, so those containers do not set `runAsNonRoot`. The init container does, because `wordpress:cli-php8.2` runs as `www-data`.

`store.publicUrl` is the address WordPress stores at install time and the default WooCommerce signature URL. `WOOCOMMERCE_BASE_URL` stays the in-cluster WordPress Service, `http://<release>-wordpress`, unless `woocommerce.baseUrl` is set. For the local values file the public URL is `http://store.local`. `values-dev.yaml` enables Ingress for `store.local`, `gateway.local`, and `dashboard.local` without TLS. `values-vps.yaml` uses `https://store.replace-me.invalid` and placeholder hosts `store.replace-me.invalid`, `gateway.replace-me.invalid`, and `dashboard.replace-me.invalid`. Its TLS block names a Secret `replace-me-tls`. That name is a placeholder. Create the TLS Secret in the cluster, or point `ingress.tls` at a cert-manager Secret, before any install. The chart does not contain a certificate or a private key.

The dashboard browser origin is the dashboard Ingress host. Nginx on that pod proxies `/admin` to the gateway Service. Login cookies are set for the dashboard host. `gateway.cookieSecure` is false in development and true in the VPS file. The VPS file therefore requires `ingress.tls`. Point DNS for the three hosts at the ingress controller. This chart does not install the controller or create DNS records.

An ingress controller, a cluster, and image loading are external prerequisites. This chart does not install them. Local Kind, k3d, or Minikube must load the WordPress, MariaDB, and PostgreSQL images into the node. A host Docker tag is not visible to the node until then.

MariaDB data is a StatefulSet claim mounted at `/var/lib/mysql`. WordPress content is a claim mounted at `/var/www/html`. Both use `helm.sh/resource-policy: keep` when retain is true. The MariaDB StatefulSet also sets `persistentVolumeClaimRetentionPolicy` to `Retain`. Deleting a PVC by hand can still delete the volume when the StorageClass reclaim policy is `Delete`. Helm does not resize an existing claim. Do not point `storageClass` at the Compose Docker volumes.

Helm startup does not run `apps.gateway.provision`. Issue and revoke keys with the existing provision command after PostgreSQL is reachable. Do not put that command in a startup container; it prints the raw key once.

## Probes

Gateway liveness is a TCP check on port 8000. Gateway readiness is `GET /health`. That endpoint returns 503 when PostgreSQL is down, so it is not used for liveness.

Support, Utility, and WooCommerce use `GET /health` for both probes. WooCommerce still returns HTTP 200 when its API is `not_configured`. Readiness does not wait for store credentials.

PostgreSQL uses `pg_isready -U gateway -d gateway`, with a startup probe of up to about five minutes. The check does not put the password on the command line.

## Persistence

PostgreSQL stores key hashes, exact tool grants, and audit rows in a `ReadWriteOnce` volume mounted at `/var/lib/postgresql/data`. The default size is `1Gi`. `values-vps.yaml` leaves the size at `1Gi` and sets the storage class to `replace-me`; both must be replaced before a real install. An empty storage class uses the cluster default.

When `database.persistence.retain` is true (the default):

- `persistentVolumeClaimRetentionPolicy.whenDeleted` and `whenScaled` are `Retain`, so deleting or scaling the StatefulSet leaves the PVC object in place. This needs Kubernetes 1.27 or newer.
- The claim template carries `helm.sh/resource-policy: keep`.

Those settings do not cover every deletion path:

- `kubectl delete pvc` still deletes the claim.
- A dynamically provisioned PV often has reclaim policy `Delete`. Deleting the PVC then deletes the volume and the data.
- Helm does not resize an existing claim when `persistence.size` changes. Expansion depends on the storage class, and shrinking is not supported.
- This chart does not create a StorageClass or a backup.

The gateway process creates its tables on startup with `CREATE TABLE IF NOT EXISTS`. There is no migration job in this chart.

## Security defaults

Pods set `automountServiceAccountToken: false`. The gateway and MCP Deployments set `pythonPodSecurityContext` to run as UID and GID 10001 with `runAsNonRoot`, and `pythonContainerSecurityContext` drops all capabilities and sets `allowPrivilegeEscalation: false`. The root filesystem is not read-only, and there is no custom seccomp profile. `podSecurityContext` and `containerSecurityContext` stay empty because those values also apply to PostgreSQL. The PostgreSQL image still starts as root so it can prepare the data directory. Kubernetes has not been used to validate the runtime user, filesystem permissions, or probes.

Resource requests and limits are unbenchmarked placeholders. The VPS file is not a production configuration. Its hostnames, image names, storage class, and Secret names are `replace-me` placeholders.

Pods use the chart ServiceAccount and set `automountServiceAccountToken: false`. The chart does not create a Role or RoleBinding, so the workloads have no Kubernetes API permissions.

NetworkPolicies are rendered when `networkPolicy.enabled` is true. They allow the dashboard and the ingress-controller namespace to reach the gateway, the gateway to reach the three MCP servers and PostgreSQL, WooCommerce MCP and the ingress-controller namespace to reach WordPress, WordPress and the init Job to reach MariaDB, and DNS to `kube-system`. The gateway and the init Job may also use TCP 443 outside private and link-local ranges when `networkPolicy.allowExternalEgress` is true. Kind's default kindnet does not enforce NetworkPolicy. Calico or Cilium does. Set `networkPolicy.ingressNamespace` to the namespace that actually runs the ingress controller before relying on the policies. `values-dev.yaml` keeps `ingress-nginx`. `values-vps.yaml` sets `replace-me`, so a policy-enforcing install denies ingress until that name is the controller's namespace. k3s Traefik runs in `kube-system`. The public HTTPS rule is IPv4 only and excludes private and link-local ranges. IPv6 egress to the internet is not allowed while a policy is enforced. DNS is allowed to pods in `kube-system`, for either address family, because that rule selects pods rather than an address block.

When the CNI does not enforce NetworkPolicy, these objects do not restrict traffic. Any pod that can route to a ClusterIP can reach the gateway, the MCP servers, and both databases. Traffic from outside the cluster still has to pass through the ingress controller, because the Services are not NodePorts or LoadBalancers. The chart renders the policies. It does not install Calico or Cilium. On `kind-urumi-k8s-dev`, the policy objects are present and `docs/assignment-tracker.md` Module 9 records that Calico v3.30.2 enforced them on 2026-10-18. A read-only listing the same evening showed those objects allowing `172.19.0.2/32`, while `values-dev.yaml` sets `172.19.0.3/32` and the dev node InternalIP was `172.19.0.3`. `172.19.0.2` was the other Kind node. The kubelet probe source was not captured. `helm upgrade` was not run, and this chart has not been installed on a VPS.

A Kubernetes Secret is base64-encoded, not encrypted. Anyone who can read Secrets in the namespace can decode them. Restrict that access with RBAC, and enable encryption at rest for the cluster's Secret store before treating these values as protected. Do not put `CHAT_API_KEY`, `GATEWAY_UPSTREAM_KEY`, database passwords, or administrator credentials in a ConfigMap or in Git.

Create the development key Secrets yourself. These commands were not run here. The values are placeholders:

```bash
kubectl create secret generic urumi-dev-gateway-keys \
  --from-literal=GATEWAY_UPSTREAM_KEY='replace-with-at-least-32-characters'
kubectl create secret generic urumi-dev-chat \
  --from-literal=CHAT_API_KEY='replace-with-the-provider-key'
kubectl create secret generic urumi-dev-woocommerce-credentials \
  --from-literal=WOOCOMMERCE_CONSUMER_KEY='replace-with-the-consumer-key' \
  --from-literal=WOOCOMMERCE_CONSUMER_SECRET='replace-with-the-consumer-secret'
```

Generate `GATEWAY_UPSTREAM_KEY` with `openssl rand -base64 32`. On a VPS, create Secrets with the same keys under the names you set in a private values file. `values-vps.yaml` uses `replace-me` until you do. Do not commit the generated values.

## Rollback

`helm uninstall` removes workloads but the PostgreSQL, MariaDB, and WordPress claims use `helm.sh/resource-policy: keep` and the StatefulSets use `persistentVolumeClaimRetentionPolicy: Retain`. That does not save you from `kubectl delete pvc` or from a StorageClass whose reclaim policy is `Delete`. Do not add a hook that deletes those claims. The init Job's hook policy deletes only the finished Job before the next one starts. `helm upgrade` runs that Job again. It does not reinstall WordPress or update WordPress core when the site is already installed. It enables Cash on delivery again when `store.demoPayment` is `cod`. Changing a database password in a Secret does not rewrite a password already stored in an initialized data directory. Take a volume backup before an upgrade or rollback. This chart has not been upgraded in a cluster.

## Values files

- `values.yaml` fails closed until a gateway Secret name or password is supplied, and until the store Secret or store passwords and `store.publicUrl` are supplied.
- `values-dev.yaml` uses bundled PostgreSQL and MariaDB, `IfNotPresent`, ClusterIP Services, HTTP Ingress hosts `store.local`, `gateway.local`, and `dashboard.local`, and Secret names `urumi-dev-gateway-credentials`, `urumi-dev-store-credentials`, `urumi-dev-gateway-keys`, `urumi-dev-chat`, and `urumi-dev-woocommerce-credentials`. Cookies are not marked Secure.
- `values-vps.yaml` is an operator worksheet. It turns on secure cookies, placeholder TLS, and placeholder Secret names. Replace those names before any install. Do not treat a successful `helm template` as a deployment.

```bash
helm lint helm/urumi
helm template urumi helm/urumi -f helm/urumi/values-dev.yaml
helm template urumi helm/urumi -f helm/urumi/values-vps.yaml
```

Do not run `helm install`, `helm upgrade`, or `helm uninstall` until image locations, Secrets, storage, DNS, and the ingress controller have been reviewed. The Vite development server is only for local UI work. The chart's dashboard path is the dashboard image plus the Nginx proxy. `values-vps.yaml` remains a placeholder worksheet, not a production release. A future install, after that review, looks like:

```bash
helm upgrade --install urumi helm/urumi -f helm/urumi/values-dev.yaml --timeout 20m
```

Use a private values file instead of `values-vps.yaml` until every `replace-me` value has been replaced. This command is not part of the repository's test workflow and was not run to validate this checkout.
