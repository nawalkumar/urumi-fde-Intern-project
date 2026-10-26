"""The chart's WooCommerce policy is gateway-only on the MCP port.

This checks the template text. It does not prove a cluster CNI enforces it.
"""

from pathlib import Path

TEMPLATE = Path("helm/urumi/templates/networkpolicy.yaml").read_text(encoding="utf-8")


def _block(name_suffix: str) -> str:
    marker = f'include "urumi.serviceName" (list . "{name_suffix}")'
    start = TEMPLATE.index(marker)
    end = TEMPLATE.find("---", start)
    return TEMPLATE[start:] if end == -1 else TEMPLATE[start:end]


def test_woocommerce_mcp_accepts_only_the_gateway_on_the_mcp_port() -> None:
    upstream = _block("upstream-net")
    assert "policyTypes:" in upstream
    assert "- Ingress" in upstream
    assert "- Egress" in upstream
    assert 'include "urumi.selectorLabels" (list . "gateway")' in upstream
    assert 'include "urumi.probeFrom" .' in upstream
    assert "port: {{ .Values.service.mcpPort }}" in upstream
    assert ".Values.upstreams.woocommerce.component" in upstream
    assert "podCIDR" not in upstream


def test_database_policies_do_not_admit_kubelet_probe_sources() -> None:
    """MariaDB and PostgreSQL probes are exec, so probe CIDRs stay off those policies.

    Gateway, dashboard, and MCP policies still include the probe helper because
    their kubelet checks are HTTP or TCP from the node. This is template text.
    """
    for name in ("gateway-db-net", "store-db-net"):
        block = _block(name)
        assert 'include "urumi.probeFrom"' not in block
    for name in ("gateway-net", "dashboard-net", "upstream-net", "wordpress-net"):
        assert 'include "urumi.probeFrom"' in _block(name)


def test_rendered_probe_cidr_does_not_open_database_ports() -> None:
    """A captured host route is not copied onto PostgreSQL or MariaDB ingress."""
    import subprocess
    import tempfile
    from pathlib import Path

    chart = Path("helm/urumi")
    with tempfile.TemporaryDirectory() as directory:
        rendered = Path(directory) / "rendered.yaml"
        completed = subprocess.run(
            [
                "helm",
                "template",
                "pkg",
                str(chart),
                "-f",
                str(chart / "values-disposable-proposed.yaml"),
                "--set",
                "database.password=lint-placeholder",
                "--set",
                "storeProvisioner.probeCidrs=192.0.2.1/32",
                "--set-json",
                'networkPolicy.probeCIDRs=["192.0.2.1/32"]',
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
        rendered.write_text(completed.stdout, encoding="utf-8")
    opened: list[str] = []
    closed: list[str] = []
    for document in completed.stdout.split("---\n"):
        if "kind: NetworkPolicy" not in document:
            continue
        name = ""
        for line in document.splitlines():
            if line.startswith("  name:"):
                name = line.split(":", 1)[1].strip()
                break
        if "192.0.2.1/32" in document:
            opened.append(name)
        else:
            closed.append(name)
    assert "pkg-urumi-gateway-db-net" in closed
    assert "pkg-urumi-store-db-net" in closed
    assert "pkg-urumi-gateway-net" in opened
    assert "pkg-urumi-dashboard-net" in opened
    assert "pkg-urumi-upstream-net" in opened
    assert "pkg-urumi-wordpress-net" in opened


GROQ = ["104.18.38.236/32", "172.64.149.20/32"]


def _render(*extra: str) -> "subprocess.CompletedProcess[str]":
    import subprocess

    return subprocess.run(
        [
            "helm",
            "template",
            "egress",
            "helm/urumi",
            "--set",
            "database.password=lint-placeholder",
            "--set",
            "store.existingSecret=store-placeholder",
            "--set",
            "store.publicUrl=http://store.example",
            "--set",
            "networkPolicy.allowExternalEgress=false",
            *extra,
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def _policies(rendered: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for document in rendered.split("---\n"):
        if "kind: NetworkPolicy" not in document:
            continue
        for line in document.splitlines():
            if line.startswith("  name:"):
                found[line.split(":", 1)[1].strip()] = document
                break
    return found


def _cidrs(document: str) -> list[str]:
    import re

    return re.findall(r'cidr: "?([0-9a-fA-F:./]+)"?', document)


def test_gateway_egress_allowlist_reaches_only_the_gateway_policy() -> None:
    import json

    result = _render(
        "--set-json",
        "networkPolicy.gatewayEgressCIDRs=" + json.dumps(GROQ),
        "--set",
        "storeProvisioner.enabled=true",
        "--set",
        "storeProvisioner.admit=true",
        "--set",
        "storeProvisioner.storageClass=standard",
    )
    assert result.returncode == 0, result.stderr
    policies = _policies(result.stdout)
    gateway = policies["egress-urumi-gateway-net"]
    assert _cidrs(gateway) == GROQ
    rule = gateway.split('cidr: "104.18.38.236/32"', 1)[1].split("---", 1)[0]
    assert "port: 443" in rule.split("- to:", 1)[0]
    assert "except:" not in gateway
    init = policies["egress-urumi-wordpress-init-net"]
    assert _cidrs(init) == []
    assert "port: 443" not in init
    for name, document in policies.items():
        if name != "egress-urumi-gateway-net":
            assert _cidrs(document) == [], name
    assert "0.0.0.0/0" not in result.stdout
    assert result.stdout.count('name: STORE_ALLOW_PLUGIN_EGRESS\n              value: "false"') == 2


def test_gateway_egress_allowlist_is_empty_by_default() -> None:
    result = _render()
    assert result.returncode == 0, result.stderr
    gateway = _policies(result.stdout)["egress-urumi-gateway-net"]
    assert _cidrs(gateway) == []
    assert "port: 443" not in gateway


def test_gateway_egress_allowlist_rejects_non_public_or_wide_entries() -> None:
    import json

    for value in [
        "10.1.2.3/32",
        "172.20.0.1/32",
        "192.168.1.1/32",
        "169.254.1.1/32",
        "127.0.0.1/32",
        "100.64.0.1/32",
        "224.0.0.1/32",
        "0.0.0.0/32",
        "300.1.1.1/32",
        "0.0.0.0/0",
        "104.18.0.0/16",
        "2606:4700::1/128",
    ]:
        result = _render("--set-json", "networkPolicy.gatewayEgressCIDRs=" + json.dumps([value]))
        assert result.returncode != 0, value
        assert "gatewayEgressCIDRs" in result.stderr, value


API_EGRESS = [{"cidr": "10.96.0.1/32", "port": 443}, {"cidr": "172.19.0.2/32", "port": 6443}]
PROVISIONER = [
    "--set",
    "storeProvisioner.enabled=true",
    "--set",
    "storeProvisioner.admit=true",
    "--set",
    "storeProvisioner.storageClass=standard",
]


def _egress_rules(document: str) -> list[str]:
    egress = document.split("\n  egress:\n", 1)[1]
    rules: list[str] = []
    for line in egress.splitlines():
        if line.lstrip().startswith("#"):
            continue
        if line.startswith("    - "):
            rules.append(line + "\n")
        elif line.startswith("      ") and rules:
            rules[-1] += line + "\n"
        elif line.strip():
            break
    return rules


def test_provisioner_api_egress_is_limited_to_listed_hosts() -> None:
    import json
    import re

    result = _render(*PROVISIONER, "--set-json", "storeProvisioner.apiServerEgress=" + json.dumps(API_EGRESS))
    assert result.returncode == 0, result.stderr
    policies = _policies(result.stdout)
    provisioner = policies["egress-urumi-store-provisioner-net"]
    rules = _egress_rules(provisioner)
    assert all(rule.startswith("    - to:") for rule in rules)
    pairs = []
    for rule in rules:
        for cidr in _cidrs(rule):
            pairs.append((cidr, re.findall(r"port: (\d+)", rule)))
    assert pairs == [("10.96.0.1/32", ["443"]), ("172.19.0.2/32", ["6443"])]
    assert sorted(re.findall(r"port: (\d+)", provisioner)) == ["443", "53", "53", "5432", "6443"]
    assert "kubernetes.io/metadata.name: kube-system" in rules[0]
    assert "app.kubernetes.io/component: gateway-db" in rules[1]
    assert "0.0.0.0/0" not in result.stdout
    for name, document in policies.items():
        if name not in {"egress-urumi-store-provisioner-net"}:
            assert _cidrs(document) == [], name


def test_provisioner_api_egress_empty_keeps_the_older_rule() -> None:
    result = _render(*PROVISIONER)
    assert result.returncode == 0, result.stderr
    rules = _egress_rules(_policies(result.stdout)["egress-urumi-store-provisioner-net"])
    open_rules = [rule for rule in rules if rule.startswith("    - ports:")]
    assert len(open_rules) == 1
    assert "port: 443" in open_rules[0] and "port: 6443" in open_rules[0]


def test_provisioner_api_egress_rejects_wide_or_malformed_entries() -> None:
    import json

    for value in [
        [{"cidr": "10.96.0.0/12", "port": 443}],
        [{"cidr": "0.0.0.0/0", "port": 443}],
        [{"cidr": "0.0.0.0/32", "port": 443}],
        [{"cidr": "172.19.0.2/32", "port": 0}],
        [{"cidr": "172.19.0.2/32", "port": 70000}],
        [{"port": 443}],
        ["172.19.0.2/32"],
    ]:
        result = _render(*PROVISIONER, "--set-json", "storeProvisioner.apiServerEgress=" + json.dumps(value))
        assert result.returncode != 0, value
        assert "apiServerEgress" in result.stderr, value


def test_woocommerce_mcp_egress_reaches_wordpress_and_dns_only() -> None:
    woo = _block("woocommerce-mcp-net")
    assert "policyTypes:" in woo
    assert "- Egress" in woo
    assert "- Ingress" not in woo.split("policyTypes:", 1)[1].split("egress:", 1)[0]
    assert 'include "urumi.selectorLabels" (list . "wordpress")' in woo
    assert "port: {{ .Values.store.wordpress.port }}" in woo
    assert "kubernetes.io/metadata.name: kube-system" in woo
