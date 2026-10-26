"""support__search_notes matches order, COD, checkout, and refund queries."""

import pytest

from apps.support_mcp.app.main import NOTES, create_app
from tests.mcp_session import open_mcp


async def _search(url: str, query: str) -> str:
    async with open_mcp(f"{url}/mcp") as client:
        result = await client.call_tool("search_notes", {"query": query})
    assert result.is_error is False
    return result.content[0].text


def test_original_notes_unchanged() -> None:
    assert NOTES[:3] == (
        {"title": "Shipping", "body": "Standard shipping takes 3 business days."},
        {"title": "Returns", "body": "Returns are accepted within 30 days."},
        {"title": "Support hours", "body": "Support hours are 09:00 to 17:00 UTC on weekdays."},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "titles"),
    [
        ("order", ["Order status:", "Order tracking:", "Cash on Delivery (COD):", "Refunds:"]),
        ("order status", ["Order status:"]),
        ("tracking", ["Order tracking:"]),
        ("COD", ["Cash on Delivery (COD):"]),
        ("cash on delivery", ["Cash on Delivery (COD):"]),
        ("checkout", ["Cash on Delivery (COD):", "Checkout and payment:"]),
        ("payment method", ["Checkout and payment:"]),
        ("refund", ["Order status:", "Refunds:"]),
        ("returns", ["Returns:", "Refunds:"]),
    ],
)
async def test_queries_return_relevant_notes(launch_app, query: str, titles: list[str]) -> None:
    url = launch_app(create_app())
    lines = (await _search(url, query)).splitlines()
    assert [line.split(": ", 1)[0] + ":" for line in lines] == titles


@pytest.mark.asyncio
async def test_existing_queries_keep_their_results(launch_app) -> None:
    url = launch_app(create_app())
    assert await _search(url, "shipping") == "Shipping: Standard shipping takes 3 business days."
    assert await _search(url, "support") == "Support hours: Support hours are 09:00 to 17:00 UTC on weekdays."
    assert await _search(url, "argument-sentinel-not-stored") == "No notes matched."
