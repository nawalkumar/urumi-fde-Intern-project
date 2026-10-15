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


def test_woocommerce_mcp_egress_reaches_wordpress_and_dns_only() -> None:
    woo = _block("woocommerce-mcp-net")
    assert "policyTypes:" in woo
    assert "- Egress" in woo
    assert "- Ingress" not in woo.split("policyTypes:", 1)[1].split("egress:", 1)[0]
    assert 'include "urumi.selectorLabels" (list . "wordpress")' in woo
    assert "port: {{ .Values.store.wordpress.port }}" in woo
    assert "kubernetes.io/metadata.name: kube-system" in woo
