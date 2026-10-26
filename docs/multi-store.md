# Multi-store provisioning

This note describes the store lifecycle added beside the existing single-store Helm release. The sections below are source and test behavior. No store was created on `kind-urumi-k8s-dev`.

On `kind-urumi-k8s-test` the provisioner and the admission policy are enabled through a gitignored overlay, and stores were created, isolated, and deleted there. That live record, with each criterion's status, is `docs/verification/multi-store-acceptance.md`. `docs/SUBMISSION_TRACKER.md` summarizes it.

The demo store stays in the `default` namespace: WordPress, MariaDB, the WooCommerce MCP server, and their PersistentVolumeClaims. `storeProvisioner.enabled` and `storeProvisioner.admit` are false in `helm/urumi/values.yaml`. `values-dev.yaml` does not set them. Do not `helm upgrade` the `kind-urumi-k8s-dev` release to turn the provisioner on.

## Decision

The live chart is one Helm release for the shared demo. A second release per store would shell out to Helm and could touch that release. Store objects are built in Python and applied by a separate provisioner process, not by the public gateway. Helm renders that process, its ServiceAccount, a narrowed ClusterRole, and a ValidatingAdmissionPolicy only when both `storeProvisioner.enabled` and `storeProvisioner.admit` are true. Helm refuses `enabled` without `admit`, and it refuses `admit` without `enabled`. Both default to false.

Tests use `tests/store_cluster.py`. That double is not imported by the gateway process.

## Lifecycle

States are `provisioning`, `ready`, `failed`, `deleting`, and `deleted`. The API labels them `Provisioning`, `Ready`, `Failed`, `Deleting`, and `Deleted`.

```text
create request
  -> row committed as Provisioning
  -> namespace, quota, limits, policies, secrets, workloads
  -> workloads ready and upstream row saved
  -> Ready
failed or timed out
  -> Failed, and only the owned namespace is deleted
retry from Failed
  -> Provisioning
delete
  -> Deleting, upstream row removed immediately
  -> namespace deleted
  -> Deleted
```

`POST /admin/stores` returns as soon as the row is stored. The public gateway does not apply Kubernetes objects. The provisioner Deployment, when it is rendered, reads `provisioning` and `deleting` rows and continues. Ready rows are a separate one-row drift check so they cannot fill that queue. The drift check rewrites a Ready store's upstream URL back to `http://woo-mcp.st-<id>.svc.cluster.local:8000/mcp`. The admin upstream API rejects ids matching `store_<16 hex>`.

The per-user limit counts every state except `Deleted`, including rows that are still provisioning. The in-memory store holds one lock around the count and insert. PostgreSQL takes a transaction advisory lock for that owner. A repeated idempotency key with the same name returns the original store. The same key with a different name returns 409 and does not create a namespace. A deleted store stays bound to that key; a new store needs a new key.

Limits, from the environment, with these defaults:

| Variable | Default |
| --- | --- |
| `STORE_PROVISIONER_ENABLED` | false |
| `STORE_MAX_PER_USER` | 3 |
| `STORE_PROVISION_TIMEOUT_SECONDS` | 600 |
| `STORE_PROVISION_CONCURRENCY` | 2 |
| `STORE_PROVISION_MAX_ATTEMPTS` | 5 |
| `STORE_INGRESS_BASE_DOMAIN` | `stores.local` |
| `STORE_ALLOW_PLUGIN_EGRESS` | false |

Invalid values fall back to the defaults. A requested image must match a repository and tag pattern. The API does not accept an image or a URL.

## Names

The store id is 16 hexadecimal characters. The namespace is `st-<id>`. The upstream id is `store_<id>`. The ingress host is `<id>.<domain>`. The display name is a label for people. It is not a Kubernetes name, a hostname, or an upstream id.

Public tools keep the existing `{upstream_id}__{tool}` form, for example `store_<id>__list_orders`. A third `woocommerce` segment would not fit the stored permission grammar, which allows one `__`. The static demo store remains `woocommerce__list_orders`. Dashboard chat names the granted `list_orders` tools exactly. A request that covers two stores is instructed to call each granted store tool, and it is not instructed to rename one of them to `woocommerce__list_orders`.

A call or a listing is allowed only when the key has that exact name and the store row is `Ready`. `Provisioning`, `Failed`, `Deleting`, and `Deleted` are hidden and return `Permission denied.` The tool name selects the store. Creating a store does not grant its tools.

## API

Session cookie only. A user sees and changes only that user's rows. Another user's id returns 404 and an `admin:store_denied` audit row. The body does not include Secret data or a Kubernetes object.

| Method | Path | Result |
| --- | --- | --- |
| GET | `/admin/stores?limit=50` | Up to 50 stores that are not Deleted |
| POST | `/admin/stores` | 202 new, 200 same idempotency key, 409 limit or key reuse with a different name |
| GET | `/admin/stores/{id}` | Status |
| POST | `/admin/stores/{id}/delete` | 202 while deleting, 200 if already Deleted |
| GET | `/admin/stores/{id}/deletion` | Same status payload, including `deletion_complete` |
| POST | `/admin/stores/{id}/retry` | 202 from Failed, 409 otherwise |

`Idempotency-Key` may be the header or `idempotency_key`. If both are sent they must match.

## Registration

When the workloads are ready, the gateway saves one dynamic upstream:

`http://woo-mcp.st-<id>.svc.cluster.local:8000/mcp`

The row has no gateway credential. The WooCommerce consumer key and the database password stay in Secrets in the store namespace. The MCP pod reads them from the environment. The URL is checked against that exact shape before it is saved. `UPSTREAM_ALLOW_PRIVATE` must stay true, which is the existing default, because the address is a cluster DNS name.

If registration fails, the row stays `Provisioning` and the next pass retries. At the attempt or time limit the owned namespace and the upstream row are removed and the state is `Failed`.

Deletion removes the upstream row before it deletes the namespace. A later reconcile treats a missing namespace with a leftover upstream as a failure and removes the upstream. A Ready store whose upstream row disappeared is registered again if the namespace is still owned.

## Kubernetes objects

Each store namespace gets a default-deny NetworkPolicy and separate policies for WordPress, MariaDB, the MCP server, and the init Job.

- WordPress egress to MariaDB uses a pod selector and no namespace selector, so it stays in that namespace.
- The MCP server egress to WordPress uses the same rule.
- MariaDB has no egress rules.
- The MCP server accepts ingress only from the gateway namespace on pods labeled `urumi.dev/gateway=allowed`, plus optional probe CIDRs.
- WordPress accepts the MCP server, the init Job, the ingress-controller namespace, and optional probe CIDRs.
- MariaDB accepts only WordPress and the init Job. Its probes run inside the container, so probe CIDRs are not database clients.
- DNS egress is to `kube-system`.
- TCP 443 to public addresses, excluding private and link-local ranges, is added for the init Job only when `STORE_ALLOW_PLUGIN_EGRESS` is true. The init script downloads WooCommerce only when that flag sets `WORDPRESS_PLUGIN_DOWNLOAD=true`. The default image path is a WordPress image that already contains the plugin (`deploy/wordpress/Dockerfile`). The download stays off.

The client refuses `.` and `..` path segments, including one layer of percent-encoding, and it refuses `default`, `kube-system`, `kube-public`, `kube-node-lease`, `ingress-nginx`, and `calico-system`. A namespace that already exists with a different store id is not updated or deleted.

Secret get and update are limited to the names `store-db` and `woocommerce-credentials`. Deployment, StatefulSet, claim, and the other store object names are likewise limited on get and update. Create has no `resourceNames` restriction in Kubernetes RBAC. Namespace delete also cannot be limited to `st-<id>` by a ClusterRole. `templates/store-provisioner-admission.yaml` is the separate boundary: it matches only the provisioner service account, fails closed, and denies a write unless the namespace is `st-<16 hex>` and the store-id label matches. On `kind-urumi-k8s-test` the policy is installed with `failurePolicy: Fail` and `validationActions: [Deny]`. One server dry-run ConfigMap create in `default`, sent as the provisioner ServiceAccount, was denied and not stored (`docs/verification/evidence/multi-store/provisioner-enabled-20261025T053720Z.json`). That is one kind and one verb. Namespace delete, Secret create, and volume delete outside the scope were not tried. The policy status reports CEL type-check warnings on the persistent-volume branch, because other matched kinds have no `spec.claimRef`; warnings do not stop enforcement. The public gateway ServiceAccount is unchanged and does not mount the provisioner token.

Official MariaDB and WordPress images start as root. Those containers set `allowPrivilegeEscalation: false` and do not set `runAsNonRoot`. The MCP container runs as UID 10001 and drops all capabilities. The init Job runs as UID 33. The namespace is not labeled `restricted` because those database and WordPress images would not satisfy it.

Provisioned claims do not set `helm.sh/resource-policy: keep`. The database StatefulSet asks Kubernetes to delete its claims when the StatefulSet is deleted. Deleting the namespace deletes the claims. A StorageClass reclaim policy of Delete then deletes the volume. A Retain policy leaves the volume Released with `claimRef.namespace` still set to `st-<id>`. After the namespace is gone, the provisioner lists volumes and deletes only those Released, Failed, or Available volumes. A Bound volume for that namespace keeps the store in Deleting until a later pass. Volumes for any other namespace are not deleted. The demo claims in `default` are not selected.

An existing init Job is not updated with PUT. `spec.template` is immutable, and the controller adds a selector after create. A succeeded Job is left in place. A failed Job, or a still-running Job whose template no longer matches, is deleted and created again. That replacement is limited to the name `wordpress-init`.

## What Ready means

`Ready` means the cluster client reported the WordPress Deployment, MariaDB StatefulSet, and WooCommerce MCP Deployment at one ready replica, the init Job succeeded, and the upstream row was saved. It does not mean a shopper checked out, and it does not mean a tool call succeeded. Manifest tests do not prove that Calico or any other CNI is enforcing the policies.

## Disposable end-to-end path

`scripts/disposable-store-acceptance.sh` prints the steps and refuses to run them unless `--execute` is set and `URUMI_DISPOSABLE_KUBE_CONTEXT` names the current context, contains `disposable`, and is not `kind-urumi-k8s-dev` or `kind-urumi-k8s-test`. `--unit` was run. `--execute` was not. The owner later authorized `kind-urumi-k8s-test` for a live pass, which used reviewed manual commands instead of this script; see the ledger section "Test-cluster live pass".

Prerequisites before any execute:

1. A new Kind cluster whose context name contains `disposable`. Do not use the existing dev or test clusters.
2. Calico, or another CNI that enforces NetworkPolicy. Kindnet does not.
3. An ingress controller in the namespace set by `STORE_INGRESS_NAMESPACE` (default `ingress-nginx`).
4. Images present on the node: `urumi-wordpress-woo` built from `deploy/wordpress/Dockerfile` (WooCommerce is already in the image), `mariadb:11.4`, `wordpress:cli-php8.2`, and `urumi-woocommerce-mcp` built from this checkout. The gateway and dashboard images also have to be built from this checkout. The running `partial-20261019` images do not contain this code.
5. `STORE_PROVISIONER_ENABLED=true` and `STORE_ALLOW_PLUGIN_EGRESS=false` when the WordPress image already contains WooCommerce. `helm/urumi/values-disposable.yaml` is the values file for that cluster. Do not use it on the live release.
6. Helm install of this chart on that cluster only, with `storeProvisioner.enabled=true`, so the gateway ServiceAccount and the `urumi.dev/gateway=allowed` label exist. Do not point that install at the live release.
7. Hosts or DNS for `<store id>.stores.local`. Do not reuse `store.local`.
8. An admin session, then an exact grant of `store_<id>__list_orders` for each store on the chat key. Creation does not grant tools.

Cleanup on that cluster is `POST /admin/stores/{id}/delete`, repeated once. The namespace, claims, Secrets, and upstream row for that id are removed. The other store is left in place. Do not delete namespaces by hand unless the name is `st-<id>` and the label `urumi.dev/store-id` matches.

## Known gaps

Runtime acceptance is recorded in `docs/verification/multi-store-acceptance.md`. A source change is not a cluster pass.

- Standard RBAC cannot express "only namespaces named st-<id>". The admission policy is the write boundary. It also denies persistent-volume changes except deletion of a volume whose claim namespace is `st-<16 hex>`. On the test cluster it denied one out-of-scope dry-run write. Do not enable the provisioner on the `kind-urumi-k8s-dev` release.
- Secret create is cluster-scoped at RBAC and is expected to be denied by that policy outside `st-<id>`. That denial was not tried live. GET of an unrelated Secret is denied by `resourceNames`, which does not depend on admission.
- The node `/32` used for kubelet probes can reach every store's WordPress and MCP ports. The store MCP does not authenticate callers.
- The chart default for provisioner egress allows TCP 443 and 6443 to any destination unless `storeProvisioner.apiServerEgress` is set.
- Not demonstrated live: cross-owner denial, concurrent provisioning, the provisioning timeout, the Retain-volume sweep, repeated delete, restart during delete, and the chat answer text for a cross-store comparison.
- `Ready` means the workloads and the upstream registration succeeded. It still does not by itself mean a shopper checked out.
