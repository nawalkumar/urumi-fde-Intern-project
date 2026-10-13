"""WooCommerce MCP tools. Each one calls the REST API; none invent store data."""

import re
import threading
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from apps.woocommerce_mcp.app.client import WooClient, WooError

ORDER_STATUSES = (
    "pending",
    "processing",
    "on-hold",
    "completed",
    "cancelled",
    "refunded",
    "failed",
)
PRODUCT_STATUSES = ("draft", "pending", "private", "publish")


class InputError(Exception):
    """The tool arguments are not valid. The REST API was not called."""


class OrderReview(Exception):
    """An order was created, but its response failed verification."""


_IDEMPOTENCY_KEY = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_LINE_FIELDS = frozenset({"product_id", "quantity"})
_CREATE_ORDER_FIELDS = frozenset({"idempotency_key", "line_items"})
_CREATE_ORDER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["idempotency_key", "line_items"],
    "properties": {
        "idempotency_key": {
            "type": "string",
            "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        },
        "line_items": {
            "type": "array",
            "minItems": 1,
            "maxItems": 20,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["product_id", "quantity"],
                "properties": {
                    "product_id": {"type": "integer", "minimum": 1},
                    "quantity": {"type": "integer", "minimum": 1, "maximum": 99},
                },
            },
        },
    },
}


def register_tools(mcp: MCPServer, client: WooClient) -> None:
    @mcp.tool(name="list_products")
    def list_products(per_page: int = 20, search: str = "") -> str:
        """List products in the configured WooCommerce store."""
        return _run(lambda: _list_products(client, per_page, search))

    @mcp.tool(name="create_product")
    def create_product(name: str, regular_price: str = "", status: str = "draft") -> str:
        """Create a simple product. status publish is visible in the storefront. The default is draft."""
        return _run(lambda: _create_product(client, name, regular_price, status))

    @mcp.tool(name="list_orders")
    def list_orders(per_page: int = 20, status: str = "", after: str = "", before: str = "") -> str:
        """List orders. after and before are optional ISO-8601 bounds with a timezone offset.

        Pass both to cover one day in the store's local timezone. The bounds are
        forwarded to WooCommerce and are not rewritten.
        """
        return _run(lambda: _list_orders(client, per_page, status, after, before))

    @mcp.tool(name="get_order")
    def get_order(order_id: int) -> str:
        """Get one WooCommerce order by its numeric ID."""
        return _run(lambda: _get_order(client, order_id))

    @mcp.tool(name="update_order_status")
    def update_order_status(order_id: int, status: str) -> str:
        """Update the status of one WooCommerce order."""
        return _run(lambda: _update_order_status(client, order_id, status))

    reservations = _OrderReservations()

    @mcp.tool(name="create_order")
    def create_order(idempotency_key: Any = None, line_items: Any = None) -> str:
        """Create one unpaid pending order. This does not take payment or complete checkout."""
        return _run(lambda: _create_order(client, idempotency_key, line_items, reservations))

    _install_create_order_guard(mcp)


def _run(action: Any) -> str:
    try:
        return action()
    except (InputError, WooError, OrderReview) as exc:
        raise ToolError(str(exc)) from exc


def _list_products(client: WooClient, per_page: int, search: str) -> str:
    query: dict[str, Any] = {"per_page": _per_page(per_page)}
    text = _optional_text(search, "search", 100)
    if text:
        query["search"] = text
    return _products(client.get_list("/products", query))


def _create_product(client: WooClient, name: str, regular_price: str, status: str) -> str:
    body: dict[str, Any] = {
        "name": _required_text(name, "name", 200),
        "type": "simple",
        "status": _choice(status, PRODUCT_STATUSES, "status"),
    }
    if body["status"] == "publish":
        body["catalog_visibility"] = "visible"
    price = _price(regular_price)
    if price is not None:
        body["regular_price"] = price
    return _product(client.post_object("/products", body))


def _list_orders(client: WooClient, per_page: int, status: str, after: str = "", before: str = "") -> str:
    query: dict[str, Any] = {"per_page": _per_page(per_page)}
    if status.strip():
        query["status"] = _status(status)
    start = _timestamp_bound(after, "after")
    end = _timestamp_bound(before, "before")
    if start is not None:
        query["after"] = start
    if end is not None:
        query["before"] = end
    return _orders(client.get_list("/orders", query))


def _get_order(client: WooClient, order_id: int) -> str:
    return _order(client.get_object(f"/orders/{_order_id(order_id)}"))


def _update_order_status(client: WooClient, order_id: int, status: str) -> str:
    body = {"status": _status(status)}
    return _order(client.put_object(f"/orders/{_order_id(order_id)}", body))


def _per_page(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise InputError("per_page must be an integer from 1 to 100.")
    return value


def _order_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InputError("order_id must be a positive integer.")
    return value


def _status(value: str) -> str:
    return _choice(value, ORDER_STATUSES, "status")


def _choice(value: str, allowed: tuple[str, ...], field: str) -> str:
    if not isinstance(value, str):
        raise InputError(f"{field} must be text.")
    choice = value.strip().casefold()
    if choice not in allowed:
        raise InputError(f"{field} must be one of: {', '.join(allowed)}.")
    return choice


def _required_text(value: str, field: str, limit: int) -> str:
    text = _optional_text(value, field, limit)
    if not text:
        raise InputError(f"{field} is required.")
    return text


def _optional_text(value: str, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise InputError(f"{field} must be text.")
    text = value.strip()
    if len(text) > limit:
        raise InputError(f"{field} must be at most {limit} characters.")
    return text


def _price(value: str) -> str | None:
    if not isinstance(value, str):
        raise InputError("regular_price must be a non-negative amount with up to two decimal places.")
    text = value.strip()
    if not text:
        return None
    whole, dot, fraction = text.partition(".")
    if not whole.isdigit() or (dot and (not fraction.isdigit() or len(fraction) > 2)):
        raise InputError("regular_price must be a non-negative amount with up to two decimal places.")
    return text


def _products(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No products found."
    return "\n".join(_product(row) for row in rows)


def _product(row: dict[str, Any]) -> str:
    price = row.get("regular_price") or row.get("price") or ""
    return f"id={row.get('id')} name={row.get('name')} price={price} status={row.get('status')}"


def _orders(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No orders found."
    return "\n".join(_order(row) for row in rows)


def _order(row: dict[str, Any]) -> str:
    text = f"id={row.get('id')} status={row.get('status')} total={row.get('total')} currency={row.get('currency')}"
    created = row.get("date_created")
    if isinstance(created, str) and created.strip():
        text = f"{text} created={created.strip()[:40]}"
    return text


def _timestamp_bound(value: str, field: str) -> str | None:
    """Accept one ISO-8601 timestamp that states its offset. Empty means unused."""
    from datetime import datetime

    if not isinstance(value, str):
        raise InputError(f"{field} must be an ISO-8601 timestamp with a timezone offset.")
    text = value.strip()
    if not text:
        return None
    if len(text) > 40:
        raise InputError(f"{field} must be an ISO-8601 timestamp with a timezone offset.")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InputError(f"{field} must be an ISO-8601 timestamp with a timezone offset.") from exc
    if parsed.tzinfo is None:
        raise InputError(f"{field} must be an ISO-8601 timestamp with a timezone offset.")
    return text


@dataclass
class _Reservation:
    fingerprint: str
    state: str
    summary: str | None


class _OrderReservations:
    """In-process create_order reservations. The lock covers check-and-set only."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: dict[str, _Reservation] = {}

    def claim(self, key: str, fingerprint: str) -> str | None:
        """Reserve a new key, return a completed summary, or reject a reuse."""
        with self._lock:
            row = self._rows.get(key)
            if row is None:
                self._rows[key] = _Reservation(fingerprint, "inflight", None)
                return None
            if row.fingerprint != fingerprint:
                raise InputError("idempotency_key was already used for a different order.")
            if row.state == "completed" and row.summary is not None:
                return row.summary
            if row.state == "inflight":
                raise InputError("idempotency_key is already in progress.")
            if row.summary:
                raise OrderReview(row.summary)
            raise InputError(
                "A previous create_order attempt with this idempotency_key did not confirm "
                "whether an order was created. Do not retry this key."
            )

    def release(self, key: str) -> None:
        with self._lock:
            row = self._rows.get(key)
            if row is not None and row.state == "inflight":
                del self._rows[key]

    def mark(self, key: str, fingerprint: str, state: str, summary: str | None) -> None:
        with self._lock:
            row = self._rows.get(key)
            if row is None or row.state != "inflight" or row.fingerprint != fingerprint:
                return
            row.state = state
            row.summary = summary


def _install_create_order_guard(mcp: MCPServer) -> None:
    """Reject unexpected top-level fields before argument coercion or HTTP."""
    tool = mcp._tool_manager.get_tool("create_order")
    if tool is None:
        raise RuntimeError("create_order was not registered.")
    original_run = tool.run

    async def run(arguments: dict[str, Any] | None, context: Any, convert_result: bool = False) -> Any:
        payload = {} if arguments is None else arguments
        if not isinstance(payload, dict) or set(payload) - _CREATE_ORDER_FIELDS:
            raise ToolError("create_order does not accept those fields.")
        return await original_run(payload, context, convert_result=convert_result)

    object.__setattr__(tool, "run", run)
    tool.parameters = _CREATE_ORDER_SCHEMA


def _create_order(
    client: WooClient,
    idempotency_key: object,
    line_items: object,
    reservations: _OrderReservations,
) -> str:
    items = _validate_line_items(line_items)
    key = _idempotency_key(idempotency_key)
    fingerprint = _fingerprint(items)
    cached = reservations.claim(key, fingerprint)
    if cached is not None:
        return cached
    try:
        catalog = _load_catalog(client, items)
    except Exception:
        reservations.release(key)
        raise
    try:
        created = client.post_object("/orders", _order_body(key, items))
    except WooError as exc:
        if _post_outcome_unknown(exc):
            reservations.mark(key, fingerprint, "uncertain", None)
        else:
            reservations.release(key)
        raise
    except Exception:
        reservations.mark(key, fingerprint, "uncertain", None)
        raise
    try:
        summary = _verified_summary(created, items, catalog)
    except OrderReview as exc:
        reservations.mark(key, fingerprint, "uncertain", str(exc))
        raise
    reservations.mark(key, fingerprint, "completed", summary)
    return summary


def _validate_line_items(value: object) -> list[tuple[int, int]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 20:
        raise InputError("line_items must contain between 1 and 20 items.")
    items: list[tuple[int, int]] = []
    seen: set[int] = set()
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != _LINE_FIELDS:
            raise InputError("Each line item must contain only product_id and quantity.")
        product_id = _positive_int(entry.get("product_id"), "product_id")
        quantity = entry.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 99:
            raise InputError("quantity must be an integer from 1 to 99.")
        if product_id in seen:
            raise InputError("line_items contains a duplicate product_id.")
        seen.add(product_id)
        items.append((product_id, quantity))
    return items


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InputError(f"{field} must be a positive integer.")
    return value


def _idempotency_key(value: object) -> str:
    if not isinstance(value, str) or _IDEMPOTENCY_KEY.fullmatch(value) is None:
        raise InputError("idempotency_key must be a UUID v4.")
    return value


def _fingerprint(items: list[tuple[int, int]]) -> str:
    return ",".join(f"{product_id}:{quantity}" for product_id, quantity in sorted(items))


def _load_catalog(client: WooClient, items: list[tuple[int, int]]) -> dict[int, str]:
    catalog: dict[int, str] = {}
    for product_id, quantity in items:
        try:
            product = client.get_object(f"/products/{product_id}")
        except WooError as exc:
            if str(exc) == "WooCommerce could not find that resource.":
                raise InputError("A product_id was not found.") from exc
            raise
        catalog[product_id] = _checked_product(product, product_id, quantity)
    return catalog


def _checked_product(product: object, product_id: int, quantity: int) -> str:
    if not isinstance(product, dict) or product.get("id") != product_id:
        raise InputError("A product_id did not match the WooCommerce product.")
    if product.get("type") != "simple" or product.get("status") != "publish":
        raise InputError("Only published simple products can be ordered.")
    price = _catalog_price(product.get("price"))
    stock_status = product.get("stock_status")
    if not isinstance(stock_status, str) or stock_status.casefold() not in {"instock", "onbackorder"}:
        raise InputError("A product is not in stock.")
    manage_stock = product.get("manage_stock")
    if manage_stock is True:
        available = product.get("stock_quantity")
        if isinstance(available, bool) or not isinstance(available, int) or available < quantity:
            raise InputError("A product does not have enough stock.")
    elif manage_stock not in {False, None}:
        raise InputError("A product stock setting could not be verified.")
    return price


def _catalog_price(value: object) -> str:
    if isinstance(value, bool):
        raise InputError("A product does not have a valid catalog price.")
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str):
        raise InputError("A product does not have a valid catalog price.")
    text = value.strip()
    whole, dot, fraction = text.partition(".")
    if not whole.isdigit() or (dot and (not fraction.isdigit() or len(fraction) > 2)):
        raise InputError("A product does not have a valid catalog price.")
    normalized = _normalize_money(text)
    if normalized is None:
        raise InputError("A product does not have a valid catalog price.")
    return normalized


def _order_body(key: str, items: list[tuple[int, int]]) -> dict[str, Any]:
    return {
        "status": "pending",
        "set_paid": False,
        "line_items": [{"product_id": product_id, "quantity": quantity} for product_id, quantity in items],
        "meta_data": [{"key": "urumi_idempotency_key", "value": key}],
    }


def _post_outcome_unknown(exc: WooError) -> bool:
    """Return whether this failed order POST may already have been stored.

    The request has already been sent. HTTP 401 and 403 become the credential
    rejection error only after that response arrives, so they do not prove
    that WooCommerce stored nothing. Redirects, other 4xx and 5xx responses,
    timeouts, lost connections, and malformed success bodies are uncertain
    for the same reason. The idempotency key stays reserved.
    """
    del exc
    return True


def _verified_summary(order: object, items: list[tuple[int, int]], catalog: dict[int, str]) -> str:
    if not isinstance(order, dict):
        raise OrderReview("The created order could not be verified and requires manual review.")
    order_id = order.get("id")
    if isinstance(order_id, bool) or not isinstance(order_id, int) or order_id < 1:
        raise OrderReview("The created order could not be verified and requires manual review.")
    review = f"Order {order_id} was created but requires manual review."
    currency = order.get("currency")
    total = _normalize_money(order.get("total")) if isinstance(order.get("total"), str) else None
    if (
        order.get("status") != "pending"
        or order.get("date_paid") not in {None, ""}
        or _set_paid_is_true(order.get("set_paid"))
        or _transaction_id_present(order.get("transaction_id"))
        or order.get("payment_method") not in {None, ""}
        or order.get("payment_method_title") not in {None, ""}
        or order.get("customer_id") not in {None, 0}
        or not isinstance(currency, str)
        or len(currency) != 3
        or not currency.isalpha()
        or not currency.isupper()
        or total is None
    ):
        raise OrderReview(review)
    lines = order.get("line_items")
    if not isinstance(lines, list) or len(lines) != len(items):
        raise OrderReview(review)
    expected = {product_id: (quantity, catalog[product_id]) for product_id, quantity in items}
    seen: set[int] = set()
    for line in lines:
        if not isinstance(line, dict):
            raise OrderReview(review)
        product_id = line.get("product_id")
        if not isinstance(product_id, int) or isinstance(product_id, bool) or product_id not in expected or product_id in seen:
            raise OrderReview(review)
        quantity, price = expected[product_id]
        if line.get("quantity") != quantity or not _money_equal(price, line.get("price")):
            raise OrderReview(review)
        seen.add(product_id)
    rendered = ",".join(f"{product_id}x{quantity}" for product_id, quantity in items)
    return f"id={order_id} status=pending total={total} currency={currency} paid=false items={rendered}"


def _set_paid_is_true(value: object) -> bool:
    """True for boolean True and for numeric 1. False is not numeric 1."""
    if isinstance(value, bool):
        return value is True
    if isinstance(value, (int, float)):
        return value == 1
    return False


def _transaction_id_present(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _money_equal(expected: str, actual: object) -> bool:
    if isinstance(actual, bool):
        return False
    if isinstance(actual, int):
        actual = str(actual)
    elif isinstance(actual, float):
        actual = format(actual, "f")
    if not isinstance(actual, str):
        return False
    normalized = _normalize_money(actual.strip())
    return normalized is not None and normalized == expected


def _normalize_money(value: str) -> str | None:
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    if amount < 0 or amount != amount.quantize(Decimal("0.01")):
        return None
    return f"{amount.quantize(Decimal('0.01'))}"
