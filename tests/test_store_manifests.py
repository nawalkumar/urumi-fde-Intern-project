"""Manifest assertions for one store. These do not prove a CNI enforces them."""

import base64
import subprocess
from dataclasses import replace
from pathlib import Path

from apps.gateway.app.stores.manifests import SecretMaterial, StoreSpec, secret_resources, workload_resources
from apps.gateway.app.stores.model import namespace_for

ROOT = Path(__file__).resolve().parents[1]


def _spec(store_id: str, owner: str = "abc123def4567890") -> StoreSpec:
    return StoreSpec(
        store_id=store_id,
        owner_id=owner,
        namespace=namespace_for(store_id),
        host=f"{store_id}.stores.local",
        title="North Shop",
        wordpress_image="wordpress:6.8-php8.2-apache",
        mariadb_image="mariadb:11.4",
        cli_image="wordpress:cli-php8.2",
        mcp_image="urumi-woocommerce-mcp:local",
        gateway_namespace="default",
        ingress_namespace="ingress-nginx",
        ingress_class="nginx",
        storage_class="",
        allow_plugin_egress=False,
        probe_cidrs=(),
    )


def _by_kind(resources: list[dict], kind: str) -> list[dict]:
    return [item for item in resources if item["kind"] == kind]


def _namespace_targets(node: object) -> list[str]:
    found: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, dict):
            selector = value.get("namespaceSelector")
            if isinstance(selector, dict):
                labels = selector.get("matchLabels") or {}
                name = labels.get("kubernetes.io/metadata.name")
                if isinstance(name, str):
                    found.append(name)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(node)
    return found


def test_resources_are_named_and_labeled_for_one_store() -> None:
    spec = _spec("0123456789abcdef")
    resources = workload_resources(spec)
    kinds = {item["kind"] for item in resources}
    assert {
        "Namespace",
        "ResourceQuota",
        "LimitRange",
        "PersistentVolumeClaim",
        "StatefulSet",
        "Deployment",
        "Ingress",
        "NetworkPolicy",
    } <= kinds
    for item in resources:
        labels = item["metadata"]["labels"]
        assert labels["urumi.dev/store-id"] == spec.store_id
        assert labels["urumi.dev/owner-id"] == spec.owner_id
        if item["kind"] != "Namespace":
            assert item["metadata"]["namespace"] == spec.namespace
    quota = _by_kind(resources, "ResourceQuota")[0]
    assert quota["spec"]["hard"]["pods"] == "8"
    assert quota["spec"]["hard"]["persistentvolumeclaims"] == "2"
    limits = _by_kind(resources, "LimitRange")[0]
    assert limits["spec"]["limits"][0]["max"]["cpu"] == "1"
    ingress = _by_kind(resources, "Ingress")[0]
    assert ingress["spec"]["rules"][0]["host"] == "0123456789abcdef.stores.local"
    assert ingress["spec"]["rules"][0]["host"] != "store.local"
    claims = _by_kind(resources, "PersistentVolumeClaim")
    stateful = _by_kind(resources, "StatefulSet")[0]
    assert claims[0]["metadata"]["name"] == "wordpress-data"
    assert stateful["spec"]["volumeClaimTemplates"][0]["metadata"]["name"] == "data"
    claim_labels = stateful["spec"]["volumeClaimTemplates"][0]["metadata"]["labels"]
    assert claim_labels["urumi.dev/store-id"] == spec.store_id
    assert claim_labels["urumi.dev/owner-id"] == spec.owner_id
    for key, value in stateful["spec"]["selector"]["matchLabels"].items():
        assert claim_labels[key] == value
    assert stateful["spec"]["persistentVolumeClaimRetentionPolicy"]["whenDeleted"] == "Delete"
    database_container = stateful["spec"]["template"]["spec"]["containers"][0]
    assert database_container["volumeMounts"] == [{"name": "data", "mountPath": "/var/lib/mysql"}]
    mounted = {
        mount["name"]
        for container in stateful["spec"]["template"]["spec"]["containers"]
        for mount in container.get("volumeMounts", [])
    }
    assert {claim["metadata"]["name"] for claim in stateful["spec"]["volumeClaimTemplates"]} <= mounted
    assert database_container["livenessProbe"]["exec"]["command"][0] == "bash"
    assert "tcpSocket" not in database_container["livenessProbe"]
    wordpress = next(item for item in _by_kind(resources, "Deployment") if item["metadata"]["name"] == "wordpress")
    mcp = next(item for item in _by_kind(resources, "Deployment") if item["metadata"]["name"] == "woo-mcp")
    wordpress_probe = wordpress["spec"]["template"]["spec"]["containers"][0]
    assert wordpress_probe["readinessProbe"]["httpGet"]["path"] == "/wp-includes/images/blank.gif"
    assert wordpress_probe["livenessProbe"]["httpGet"]["path"] == "/wp-includes/images/blank.gif"
    assert mcp["spec"]["template"]["spec"]["containers"][0]["livenessProbe"]["httpGet"]["path"] == "/health"
    init = _by_kind(resources, "Job")[0]
    download = next(item for item in init["spec"]["template"]["spec"]["containers"][0]["env"] if item["name"] == "WORDPRESS_PLUGIN_DOWNLOAD")
    assert download["value"] == "false"
    script = next(item for item in resources if item["metadata"]["name"] == "store-init")
    assert "plugin download is disabled" in script["data"]["init-store.sh"]
    assert script["data"]["init-store.sh"].index("wp db check") < script["data"]["init-store.sh"].index("wp core is-installed")
    assert '"consumer_secret" => $secret' in script["data"]["init-store.sh"]
    assert "wc_api_hash($secret)" not in script["data"]["init-store.sh"]
    account = _by_kind(resources, "ServiceAccount")[0]
    assert account["automountServiceAccountToken"] is False


def test_network_policy_does_not_select_the_other_store() -> None:
    left = _spec("0123456789abcdef")
    right = _spec("fedcba9876543210")
    left_policies = [item for item in workload_resources(left) if item["kind"] == "NetworkPolicy"]
    right_policies = [item for item in workload_resources(right) if item["kind"] == "NetworkPolicy"]
    deny = next(item for item in left_policies if item["metadata"]["name"] == "default-deny")
    assert deny["spec"]["podSelector"] == {}
    assert deny["spec"]["policyTypes"] == ["Ingress", "Egress"]
    assert "ingress" not in deny["spec"]
    assert "egress" not in deny["spec"]
    for policy in left_policies:
        assert right.namespace not in _namespace_targets(policy)
        assert left.namespace not in _namespace_targets(policy)
        blob = str(policy)
        assert right.namespace not in blob
    wordpress = next(item for item in left_policies if item["metadata"]["name"] == "wordpress")
    database_peer = wordpress["spec"]["egress"][0]["to"][0]
    assert "namespaceSelector" not in database_peer
    assert database_peer["podSelector"]["matchLabels"]["app.kubernetes.io/component"] == "store-db"
    mcp = next(item for item in right_policies if item["metadata"]["name"] == "woo-mcp")
    wordpress_peer = mcp["spec"]["egress"][0]["to"][0]
    assert "namespaceSelector" not in wordpress_peer
    gateway_peer = mcp["spec"]["ingress"][0]["from"][0]
    assert gateway_peer["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"] == "default"
    assert gateway_peer["podSelector"]["matchLabels"]["urumi.dev/gateway"] == "allowed"
    database = next(item for item in left_policies if item["metadata"]["name"] == "store-db")
    assert database["spec"]["policyTypes"] == ["Ingress", "Egress"]
    assert "egress" not in database["spec"]
    probed = replace(left, probe_cidrs=("172.19.0.2/32",))
    probed_policies = [item for item in workload_resources(probed) if item["kind"] == "NetworkPolicy"]
    probed_db = next(item for item in probed_policies if item["metadata"]["name"] == "store-db")
    probed_web = next(item for item in probed_policies if item["metadata"]["name"] == "wordpress")
    assert "172.19.0.2/32" not in str(probed_db)
    assert "172.19.0.2/32" in str(probed_web)


def test_broad_probe_networks_are_rejected(monkeypatch) -> None:
    monkeypatch.setenv(
        "STORE_PROBE_CIDRS",
        "0.0.0.0/0,10.0.0.0/8,172.16.0.0/12,172.19.0.2/32,::/0,2001:db8::/32,2001:db8::1/128",
    )
    from apps.gateway.app.config import Settings

    assert Settings.from_env().stores.probe_cidrs == ("172.19.0.2/32", "2001:db8::1/128")


def test_secrets_keep_values_out_of_labels() -> None:
    spec = _spec("0123456789abcdef")
    material = SecretMaterial(
        mariadb_root="root-secret-sentinel-value",
        mariadb_password="db-secret-sentinel-value",
        admin_password="admin-secret-sentinel-value",
        consumer_key="ck_sentinelkeyvalue00000000000000000000",
        consumer_secret="cs_sentinelsecretvalue000000000000000000",
    )
    secrets = secret_resources(spec, material)
    assert [item["metadata"]["name"] for item in secrets] == ["store-db", "woocommerce-credentials"]
    encoded = secrets[0]["data"]["MARIADB_PASSWORD"]
    assert base64.b64decode(encoded).decode() == material.mariadb_password
    assert material.mariadb_password not in str(secrets[0]["metadata"])
    assert material.consumer_secret not in str(secrets[1]["metadata"])


def test_provisioner_rbac_is_not_cluster_admin() -> None:
    text = (ROOT / "helm/urumi/templates/store-provisioner-rbac.yaml").read_text(encoding="utf-8")
    assert "cluster-admin" not in text
    assert 'resources: ["*"]' not in text
    assert "pods/exec" not in text
    assert "secrets" in text
    assert 'verbs: ["get", "create", "delete"]' in text
    disabled = _helm("storeProvisioner.enabled=false")
    assert disabled.returncode == 0, disabled.stderr
    assert "store-provisioner" not in disabled.stdout
    assert "ValidatingAdmissionPolicy" not in disabled.stdout
    blocked = _helm("storeProvisioner.enabled=true", "storeProvisioner.admit=false")
    assert blocked.returncode != 0
    assert "non-deployable" in blocked.stderr
    assert "lint-placeholder" not in blocked.stderr
    orphan = _helm("storeProvisioner.enabled=false", "storeProvisioner.admit=true")
    assert orphan.returncode != 0
    assert "admit cannot be true" in orphan.stderr
    enabled = _helm("storeProvisioner.enabled=true", "storeProvisioner.admit=true", "storeProvisioner.storageClass=local-path")
    assert enabled.returncode == 0, enabled.stderr
    rendered = enabled.stdout
    assert "kind: ClusterRole" in rendered
    assert "kind: ValidatingAdmissionPolicy" in rendered
    assert "failurePolicy: Fail" in rendered
    assert "validationActions: [Deny]" in rendered
    assert "st-[0-9a-f]{16}" in rendered
    assert "urumi.dev/store-id" in rendered
    assert "urumi.dev/gateway: allowed" in rendered
    assert 'urumi.dev/store: "true"' in rendered
    role = _document(rendered, "ClusterRole")
    verb_lines = [line.strip() for line in role.splitlines() if line.strip().startswith("verbs:")]
    assert verb_lines.count('verbs: ["get", "create", "delete"]') == 1
    assert verb_lines.count('verbs: ["get", "list", "delete"]') == 1
    assert verb_lines.count('verbs: ["delete"]') == 1
    allowed_deletes = {
        'verbs: ["get", "create", "delete"]',
        'verbs: ["get", "list", "delete"]',
        'verbs: ["delete"]',
    }
    assert all("patch" not in line for line in verb_lines)
    assert all("delete" not in line or line in allowed_deletes for line in verb_lines)
    assert 'resourceNames: ["store-db", "woocommerce-credentials"]' in role
    assert 'resources: ["*"]' not in role
    gateway = _document(rendered, "Deployment", "urumi-gateway")
    assert "store-provisioner" not in gateway
    assert "automountServiceAccountToken: true" not in gateway
    assert "STORE_CLUSTER_RECONCILER" not in gateway
    provisioner_net = _document(rendered, "NetworkPolicy", "urumi-store-provisioner-net")
    assert "port: 443" in provisioner_net
    assert "port: 6443" in provisioner_net
    provisioner = _document(rendered, "Deployment", "urumi-store-provisioner")
    assert "serviceAccountName: urumi-store-provisioner" in provisioner
    assert "automountServiceAccountToken: true" in provisioner
    assert "apps.gateway.app.stores.provisioner" in provisioner
    assert "CHAT_API" not in provisioner
    assert "STORE_STORAGE_CLASS" in provisioner
    assert 'value: "local-path"' in provisioner
    assert "claimRef.namespace" in rendered
    assert "persistentvolumes" in role


def _document(text: str, kind: str, name: str | None = None) -> str:
    matches = []
    for document in text.split("\n---\n"):
        if not any(line == f"kind: {kind}" for line in document.splitlines()):
            continue
        if name is not None and f"\n  name: {name}\n" not in document:
            continue
        matches.append(document)
    assert len(matches) == 1
    return matches[0]


def _helm(*sets: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [
            "helm",
            "template",
            "urumi",
            "helm/urumi",
            "--set",
            "database.password=lint-placeholder",
            "--set",
            "store.enabled=false",
            *[arg for pair in sets for arg in ("--set", pair)],
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result
