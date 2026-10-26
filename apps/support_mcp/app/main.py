"""Deterministic support-note search. The gateway exposes this as support__search_notes."""

from mcp.server.mcpserver import MCPServer

from apps.common.http import build_http_app

NOTES = (
    {"title": "Shipping", "body": "Standard shipping takes 3 business days."},
    {"title": "Returns", "body": "Returns are accepted within 30 days."},
    {"title": "Support hours", "body": "Support hours are 09:00 to 17:00 UTC on weekdays."},
    {
        "title": "Order status",
        "body": "Look up an order by its order number. WooCommerce order statuses include Pending payment, "
        "Processing, On hold, Completed, Cancelled, Refunded, and Failed.",
    },
    {
        "title": "Order tracking",
        "body": "These notes do not include carrier tracking numbers. To track an order, check its current status "
        "using the order number.",
    },
    {
        "title": "Cash on Delivery (COD)",
        "body": "When Cash on Delivery is offered at checkout, the order is placed without paying online and "
        "payment is made on delivery.",
    },
    {
        "title": "Checkout and payment",
        "body": "Checkout needs at least one available payment method. If checkout reports that no payment methods "
        "are available, check which payment methods are enabled in the WooCommerce payment settings.",
    },
    {
        "title": "Refunds",
        "body": "These notes do not state a refund policy. A refunded order shows the status Refunded. For returns, "
        "see the Returns note.",
    },
)

def create_app():
    mcp = MCPServer("support-notes")

    @mcp.tool(name="search_notes")
    def search_notes(query: str) -> str:
        """Find support notes whose title or body contains the query."""
        needle = query.casefold()
        matches = [note for note in NOTES if needle in note["title"].casefold() or needle in note["body"].casefold()]
        if not matches:
            return "No notes matched."
        return "\n".join(f"{note['title']}: {note['body']}" for note in matches)

    return build_http_app(mcp, lambda: {"status": "ok"})


app = create_app()
