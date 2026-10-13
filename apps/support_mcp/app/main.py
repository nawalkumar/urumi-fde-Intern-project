"""Deterministic support-note search. The gateway exposes this as support__search_notes."""

from mcp.server.mcpserver import MCPServer

from apps.common.http import build_http_app

NOTES = (
    {"title": "Shipping", "body": "Standard shipping takes 3 business days."},
    {"title": "Returns", "body": "Returns are accepted within 30 days."},
    {"title": "Support hours", "body": "Support hours are 09:00 to 17:00 UTC on weekdays."},
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
