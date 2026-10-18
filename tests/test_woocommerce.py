"""WooCommerce tools against scripted HTTP responses. No live store is required."""

import json
import re
import threading

import httpx2
import pytest

from apps.gateway.app.config import UpstreamConfig
from apps.utility_mcp.app.main import create_app as create_utility
from apps.woocommerce_mcp.app.client import WooClient, WooError
from apps.woocommerce_mcp.app.config import WooSettings
from apps.woocommerce_mcp.app.main import create_app as create_woocommerce
from apps.woocommerce_mcp.app.tools import InputError, OrderReview, _OrderReservations, _create_order
from tests.mcp_session import open_mcp, start_gateway

_KEY = "4f3a2b1c-0d5e-4f6a-8b7c-9d0e1f2a3b4c"
_OTHER_KEY = "a1b2c3d4-e5f6-4789-ab12-3456789abcde"


class ScriptedHttp:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    def request(self, method, url, *, params, json, auth, headers, timeout):
        self.calls.append(
            {"method": method, "url": url, "params": params, "json": json, "auth": auth, "headers": headers, "timeout": timeout}
        )
        if self.error is not None:
            raise self.error
        return self.response


class FakeResponse:
    def __init__(self, status_code: int, payload) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _settings(scheme: str = "http") -> WooSettings:
    return WooSettings(
        base_url=f"{scheme}://store.test",
        consumer_key="ck_test",
        consumer_secret="cs_test",
        timeout_seconds=2,
    )


def _client(http: ScriptedHttp, scheme: str = "http") -> WooClient:
    return WooClient(_settings(scheme), sender=http)


async def _names(url: str, api_key: str | None = None) -> list[str]:
    async with open_mcp(url, api_key) as client:
        listed = await client.list_tools()
    return [tool.name for tool in listed.tools]


async def _call(url: str, name: str, arguments: dict, api_key: str | None = None):
    async with open_mcp(url, api_key) as client:
        return await client.call_tool(name, arguments)


def test_missing_and_invalid_configuration_registers_no_tools(monkeypatch) -> None:
    for name in ("WOOCOMMERCE_BASE_URL", "WOOCOMMERCE_CONSUMER_KEY", "WOOCOMMERCE_CONSUMER_SECRET"):
        monkeypatch.delenv(name, raising=False)
    from apps.woocommerce_mcp.app.config import api_state

    assert api_state() == "not_configured"
    monkeypatch.setenv("WOOCOMMERCE_BASE_URL", "http://store.test")
    monkeypatch.setenv("WOOCOMMERCE_CONSUMER_KEY", "ck_test")
    assert api_state() == "not_configured"
    monkeypatch.setenv("WOOCOMMERCE_CONSUMER_SECRET", "cs_test")
    monkeypatch.setenv("WOOCOMMERCE_BASE_URL", "ftp://store.test")
    assert api_state() == "not_configured"
    monkeypatch.setenv("WOOCOMMERCE_BASE_URL", "http://user:password@store.test")
    assert api_state() == "not_configured"


def test_publish_is_visible_and_order_bounds_keep_the_offset() -> None:
    from apps.woocommerce_mcp.app.tools import InputError, _create_product, _list_orders

    http = ScriptedHttp(FakeResponse(200, {"id": 9, "name": "Hat", "regular_price": "4.00", "status": "publish"}))
    _create_product(_client(http), "Hat", "4.00", "publish")
    assert http.calls[0]["json"]["status"] == "publish"
    assert http.calls[0]["json"]["catalog_visibility"] == "visible"
    orders = ScriptedHttp(
        FakeResponse(200, [{"id": 3, "status": "processing", "total": "1.00", "currency": "USD", "date_created": "2026-10-11T01:00:00"}])
    )
    text = _list_orders(_client(orders), 20, "", "2026-10-11T00:00:00+05:30", "2026-10-12T00:00:00+05:30")
    assert orders.calls[0]["params"]["after"] == "2026-10-11T00:00:00+05:30"
    assert orders.calls[0]["params"]["before"] == "2026-10-12T00:00:00+05:30"
    assert "created=2026-10-11T01:00:00" in text
    with pytest.raises(InputError):
        _list_orders(_client(orders), 20, "", "2026-10-11T00:00:00", "")


def test_create_product_defaults_to_draft() -> None:
    from apps.woocommerce_mcp.app.tools import _create_product

    http = ScriptedHttp(FakeResponse(200, {"id": 9, "name": "Draft", "regular_price": "1.00", "status": "draft"}))
    text = _create_product(_client(http), "Draft", "1.00", "draft")
    assert http.calls[0]["json"]["status"] == "draft"
    assert "status=draft" in text


def test_http_auth_timeout_and_malformed_response() -> None:
    http = ScriptedHttp(FakeResponse(200, [{"id": 4, "name": "Mug", "regular_price": "8.00", "status": "publish"}]))
    assert "Mug" in _client(http).get_list("/products", {"per_page": 20})[0]["name"]
    assert "consumer_secret" not in http.calls[0]["params"]
    assert http.calls[0]["auth"] is None
    assert http.calls[0]["headers"]["Authorization"].startswith("OAuth ")
    assert "cs_test" not in http.calls[0]["headers"]["Authorization"]

    secure = ScriptedHttp(FakeResponse(200, []))
    _client(secure, "https").get_list("/products", {})
    assert "consumer_secret" not in secure.calls[0]["params"]
    assert secure.calls[0]["auth"] == ("ck_test", "cs_test")
    assert secure.calls[0]["headers"] == {}

    denied = ScriptedHttp(FakeResponse(401, {"message": "invalid secret cs_test"}))
    with pytest.raises(WooError, match="rejected the API credentials"):
        _client(denied).get_list("/products", {})

    leaked = ScriptedHttp(FakeResponse(400, {"message": "bad cs_test"}))
    with pytest.raises(WooError, match="HTTP 400") as caught:
        _client(leaked).post_object("/products", {"name": "Mug"})
    assert "cs_test" not in str(caught.value)

    broken = ScriptedHttp(FakeResponse(200, json.JSONDecodeError("bad", "doc", 0)))
    with pytest.raises(WooError, match="malformed"):
        _client(broken).get_object("/orders/1")

    slow = ScriptedHttp(error=httpx2.ReadTimeout("timed out"))
    with pytest.raises(WooError, match="timed out"):
        _client(slow).get_list("/products", {})

    down = ScriptedHttp(error=httpx2.ConnectError("refused"))
    with pytest.raises(WooError, match="could not be reached"):
        _client(down).get_list("/products", {})


@pytest.mark.asyncio
async def test_tools_validate_input_before_calling_the_api(launch_app) -> None:
    http = ScriptedHttp(FakeResponse(200, []))
    base = launch_app(create_woocommerce(_client(http)))
    names = await _names(f"{base}/mcp")
    assert names == [
        "list_products",
        "create_product",
        "list_orders",
        "get_order",
        "update_order_status",
        "create_order",
    ]

    invalid_id = await _call(f"{base}/mcp", "get_order", {"order_id": 0})
    invalid_status = await _call(f"{base}/mcp", "update_order_status", {"order_id": 1, "status": "shipped"})
    invalid_page = await _call(f"{base}/mcp", "list_products", {"per_page": 0})
    missing_name = await _call(f"{base}/mcp", "create_product", {"name": "  "})

    assert invalid_id.is_error is True
    assert "positive integer" in invalid_id.content[0].text
    assert "one of:" in invalid_status.content[0].text
    assert "per_page" in invalid_page.content[0].text
    assert "name is required" in missing_name.content[0].text
    assert http.calls == []


@pytest.mark.asyncio
async def test_configured_tools_are_namespaced_and_isolated(launch_app, monkeypatch) -> None:
    for name in ("WOOCOMMERCE_BASE_URL", "WOOCOMMERCE_CONSUMER_KEY", "WOOCOMMERCE_CONSUMER_SECRET"):
        monkeypatch.delenv(name, raising=False)
    http = ScriptedHttp(FakeResponse(200, [{"id": 4, "name": "Mug", "regular_price": "8.00", "status": "publish"}]))
    woo = launch_app(create_woocommerce(_client(http)))
    utility = launch_app(create_utility())
    plain = launch_app(create_woocommerce())
    gateway, _store, issued = await start_gateway(
        launch_app,
        [
            UpstreamConfig("woocommerce", f"{woo}/mcp", 5),
            UpstreamConfig("utility", f"{utility}/mcp", 5),
        ],
        permissions=(
            "woocommerce__list_products",
            "woocommerce__create_product",
            "woocommerce__list_orders",
            "woocommerce__get_order",
            "woocommerce__update_order_status",
            "utility__convert_units",
        ),
    )
    names = await _names(f"{gateway}/mcp", issued.raw_key)
    assert names == [
        "woocommerce__list_products",
        "woocommerce__create_product",
        "woocommerce__list_orders",
        "woocommerce__get_order",
        "woocommerce__update_order_status",
        "woocommerce__create_order",
        "utility__convert_units",
    ]
    listed = await _call(f"{gateway}/mcp", "woocommerce__list_products", {"per_page": 20}, issued.raw_key)
    assert listed.is_error is False
    assert "name=Mug" in listed.content[0].text
    unconfigured = await _names(f"{plain}/mcp")
    assert unconfigured == []
    units = await _call(f"{gateway}/mcp", "utility__convert_units", {"value": 2, "from_unit": "km", "to_unit": "m"}, issued.raw_key)
    assert units.is_error is False
    assert units.content[0].text == "2000"


class SequenceHttp:
    def __init__(self, responses: list) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def request(self, method, url, *, params, json, auth, headers, timeout):
        self.calls.append(
            {"method": method, "url": url, "params": params, "json": json, "auth": auth, "headers": headers, "timeout": timeout}
        )
        if not self.responses:
            raise AssertionError(f"unexpected {method}")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _lines(*pairs: tuple[int, int]) -> list[dict]:
    return [{"product_id": product_id, "quantity": quantity} for product_id, quantity in pairs]


def _product(
    product_id: int = 123,
    price: object = "8.00",
    status: str = "publish",
    product_type: str = "simple",
    stock_status: str = "instock",
    manage_stock: object = False,
    stock_quantity: object | None = None,
) -> dict:
    body = {
        "id": product_id,
        "type": product_type,
        "status": status,
        "price": price,
        "stock_status": stock_status,
        "manage_stock": manage_stock,
    }
    if stock_quantity is not None:
        body["stock_quantity"] = stock_quantity
    return body


def _created_order(
    order_id: int = 41,
    product_id: int = 123,
    quantity: int = 2,
    price: object = "8.00",
    total: object = "16.00",
    **overrides: object,
) -> dict:
    body = {
        "id": order_id,
        "status": "pending",
        "total": total,
        "currency": "USD",
        "date_paid": None,
        "set_paid": False,
        "billing": {"email": "buyer@example.com", "address": "secret street"},
        "shipping": {"address": "secret dock"},
        "line_items": [{"product_id": product_id, "quantity": quantity, "price": price, "name": "Mug"}],
    }
    body.update(overrides)
    return body


def _posts(http: SequenceHttp) -> list[dict]:
    return [call for call in http.calls if call["method"] == "POST"]


def test_create_order_rejects_invalid_input_before_http() -> None:
    http = SequenceHttp([])
    reservations = _OrderReservations()
    client = _client(http)
    samples = [
        ("not-a-uuid", _lines((123, 1)), "UUID v4"),
        ("4F3A2B1C-0D5E-4F6A-8B7C-9D0E1F2A3B4C", _lines((123, 1)), "UUID v4"),
        (_KEY, [], "between 1 and 20"),
        (_KEY, _lines((123, 1)) * 21, "between 1 and 20"),
        (_KEY, [{"product_id": True, "quantity": 1}], "positive integer"),
        (_KEY, [{"product_id": 0, "quantity": 1}], "positive integer"),
        (_KEY, [{"product_id": 123, "quantity": True}], "quantity"),
        (_KEY, [{"product_id": 123, "quantity": 0}], "quantity"),
        (_KEY, [{"product_id": 123, "quantity": 100}], "quantity"),
        (_KEY, [{"product_id": 123, "quantity": "2"}], "quantity"),
        (_KEY, [{"product_id": 123, "quantity": 1, "price": "1.00"}], "only product_id and quantity"),
        (_KEY, [_lines((123, 1))[0], _lines((123, 2))[0]], "duplicate"),
        (_KEY, [{"quantity": 1}], "only product_id and quantity"),
    ]
    for key, items, message in samples:
        with pytest.raises(InputError, match=message):
            _create_order(client, key, items, reservations)
    assert http.calls == []


def test_create_order_checks_catalog_before_posting() -> None:
    missing = SequenceHttp([FakeResponse(404, {"message": "missing"})])
    with pytest.raises(InputError, match="not found"):
        _create_order(_client(missing), _KEY, _lines((123, 1)), _OrderReservations())
    assert _posts(missing) == []

    cases = [
        (_product(status="draft"), "Only published simple products can be ordered."),
        (_product(product_type="variable"), "Only published simple products can be ordered."),
        (_product(product_id=99), "A product_id did not match the WooCommerce product."),
        (_product(price=""), "A product does not have a valid catalog price."),
        (_product(price="1.234"), "A product does not have a valid catalog price."),
        (_product(price=True), "A product does not have a valid catalog price."),
        (_product(stock_status="outofstock"), "A product is not in stock."),
        (_product(stock_status="unknown"), "A product is not in stock."),
        (_product(manage_stock=True, stock_quantity=1), "A product does not have enough stock."),
        (_product(manage_stock="yes"), "A product stock setting could not be verified."),
    ]
    for product, message in cases:
        http = SequenceHttp([FakeResponse(200, product)])
        with pytest.raises(InputError, match=re.escape(message)):
            _create_order(_client(http), _KEY, _lines((123, 2)), _OrderReservations())
        assert _posts(http) == []

    enough = SequenceHttp(
        [
            FakeResponse(200, _product(manage_stock=False, stock_quantity=0)),
            FakeResponse(201, _created_order()),
        ]
    )
    text = _create_order(_client(enough), _KEY, _lines((123, 2)), _OrderReservations())
    assert text.startswith("id=41 ")
    assert len(_posts(enough)) == 1


def test_create_order_posts_one_unpaid_pending_order_and_hides_customer_data() -> None:
    http = SequenceHttp(
        [
            FakeResponse(200, _product()),
            FakeResponse(201, _created_order()),
        ]
    )
    text = _create_order(_client(http), _KEY, _lines((123, 2)), _OrderReservations())
    posted = _posts(http)[0]["json"]
    assert posted == {
        "status": "pending",
        "set_paid": False,
        "line_items": [{"product_id": 123, "quantity": 2}],
        "meta_data": [{"key": "urumi_idempotency_key", "value": _KEY}],
    }
    assert "buyer@example.com" not in text
    assert "secret" not in text
    assert "cs_test" not in text
    assert text == "id=41 status=pending total=16.00 currency=USD paid=false items=123x2"
    assert http.calls[0]["url"].endswith("/products/123")
    assert http.calls[1]["url"].endswith("/orders")


def test_create_order_reuses_a_completed_key_and_rejects_conflicts() -> None:
    http = SequenceHttp(
        [
            FakeResponse(200, _product()),
            FakeResponse(201, _created_order()),
            FakeResponse(200, _product()),
            FakeResponse(201, _created_order(order_id=42)),
        ]
    )
    reservations = _OrderReservations()
    client = _client(http)
    first = _create_order(client, _KEY, _lines((123, 2)), reservations)
    second = _create_order(client, _KEY, _lines((123, 2)), reservations)
    other = _create_order(client, _OTHER_KEY, _lines((123, 2)), reservations)
    assert first == second
    assert "id=42" in other
    assert len(_posts(http)) == 2
    with pytest.raises(InputError, match="different order"):
        _create_order(client, _KEY, _lines((123, 1)), reservations)
    assert len(_posts(http)) == 2


def test_concurrent_create_order_posts_once() -> None:
    started = threading.Event()
    hold = threading.Event()

    class BlockingHttp:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def request(self, method, url, *, params, json, auth, headers, timeout):
            self.calls.append({"method": method, "url": url, "json": json})
            if method == "GET":
                started.set()
                if not hold.wait(2):
                    raise TimeoutError("catalog read stayed blocked")
                return FakeResponse(200, _product())
            return FakeResponse(201, _created_order())

    http = BlockingHttp()
    reservations = _OrderReservations()
    outcome: dict[str, object] = {}

    def first() -> None:
        try:
            outcome["value"] = _create_order(_client(http), _KEY, _lines((123, 2)), reservations)
        except Exception as exc:
            outcome["value"] = exc

    worker = threading.Thread(target=first)
    worker.start()
    assert started.wait(2)
    with pytest.raises(InputError, match="in progress"):
        _create_order(_client(http), _KEY, _lines((123, 2)), reservations)
    assert [call["method"] for call in http.calls] == ["GET"]
    hold.set()
    worker.join(3)
    assert outcome["value"] == "id=41 status=pending total=16.00 currency=USD paid=false items=123x2"
    assert [call["method"] for call in http.calls] == ["GET", "POST"]


def test_create_order_timeout_is_uncertain_and_is_not_retried() -> None:
    http = SequenceHttp(
        [
            FakeResponse(200, _product()),
            httpx2.ReadTimeout("timed out"),
        ]
    )
    reservations = _OrderReservations()
    client = _client(http)
    with pytest.raises(WooError, match="timed out"):
        _create_order(client, _KEY, _lines((123, 2)), reservations)
    with pytest.raises(InputError, match="Do not retry"):
        _create_order(client, _KEY, _lines((123, 2)), reservations)
    assert len(_posts(http)) == 1

    lost = SequenceHttp([FakeResponse(200, _product()), httpx2.ConnectError("refused")])
    lost_reservations = _OrderReservations()
    lost_client = _client(lost)
    with pytest.raises(WooError, match="could not be reached"):
        _create_order(lost_client, _KEY, _lines((123, 2)), lost_reservations)
    with pytest.raises(InputError, match="Do not retry"):
        _create_order(lost_client, _KEY, _lines((123, 2)), lost_reservations)
    assert len(_posts(lost)) == 1

    malformed = SequenceHttp([FakeResponse(200, _product()), FakeResponse(200, ["not", "an", "order"])])
    held = _OrderReservations()
    with pytest.raises(WooError, match="malformed"):
        _create_order(_client(malformed), _KEY, _lines((123, 2)), held)
    with pytest.raises(InputError, match="Do not retry"):
        _create_order(_client(malformed), _KEY, _lines((123, 2)), held)
    assert len(_posts(malformed)) == 1

    failed = SequenceHttp([FakeResponse(200, _product()), FakeResponse(500, {"message": "unavailable"})])
    blocked = _OrderReservations()
    with pytest.raises(WooError, match="HTTP 500"):
        _create_order(_client(failed), _KEY, _lines((123, 2)), blocked)
    with pytest.raises(InputError, match="Do not retry"):
        _create_order(_client(failed), _KEY, _lines((123, 2)), blocked)
    assert len(_posts(failed)) == 1


def test_ambiguous_post_failures_are_not_retried() -> None:
    samples = [
        FakeResponse(400, {"message": "bad cs_test"}),
        FakeResponse(302, {"message": "redirected"}),
        FakeResponse(404, {"message": "missing order route"}),
        FakeResponse(409, {"message": "conflict"}),
    ]
    for response in samples:
        http = SequenceHttp([FakeResponse(200, _product()), response])
        reservations = _OrderReservations()
        client = _client(http)
        with pytest.raises(WooError) as caught:
            _create_order(client, _KEY, _lines((123, 2)), reservations)
        assert "cs_test" not in str(caught.value)
        with pytest.raises(InputError, match="Do not retry"):
            _create_order(client, _KEY, _lines((123, 2)), reservations)
        assert len(_posts(http)) == 1
        assert not any(call["method"] in {"PUT", "DELETE"} for call in http.calls)


@pytest.mark.parametrize("status_code", [401, 403])
def test_credential_rejection_is_not_retried(status_code: int) -> None:
    http = SequenceHttp(
        [
            FakeResponse(200, _product()),
            FakeResponse(status_code, {"message": "invalid secret cs_test"}),
        ]
    )
    reservations = _OrderReservations()
    client = _client(http)
    with pytest.raises(WooError, match="rejected the API credentials") as caught:
        _create_order(client, _KEY, _lines((123, 2)), reservations)
    assert "cs_test" not in str(caught.value)
    with pytest.raises(InputError, match="Do not retry"):
        _create_order(client, _KEY, _lines((123, 2)), reservations)
    assert len(_posts(http)) == 1
    assert not any(call["method"] in {"PUT", "DELETE"} for call in http.calls)


def test_create_order_reports_verification_failure_without_a_follow_up_write() -> None:
    http = SequenceHttp(
        [
            FakeResponse(200, _product()),
            FakeResponse(201, _created_order(status="processing", billing={"email": "buyer@example.com"})),
        ]
    )
    reservations = _OrderReservations()
    client = _client(http)
    with pytest.raises(OrderReview, match="Order 41 was created but requires manual review"):
        _create_order(client, _KEY, _lines((123, 2)), reservations)
    with pytest.raises(OrderReview, match="Order 41") as review:
        _create_order(client, _KEY, _lines((123, 2)), reservations)
    assert "buyer@example.com" not in str(review.value)
    assert len(_posts(http)) == 1
    assert [call["method"] for call in http.calls] == ["GET", "POST"]

    attached = SequenceHttp(
        [
            FakeResponse(200, _product()),
            FakeResponse(201, _created_order(customer_id=7)),
        ]
    )
    with pytest.raises(OrderReview, match="Order 41"):
        _create_order(_client(attached), _KEY, _lines((123, 2)), _OrderReservations())
    assert [call["method"] for call in attached.calls] == ["GET", "POST"]

    mismatch = SequenceHttp(
        [
            FakeResponse(200, _product()),
            FakeResponse(201, _created_order(price="1.00")),
        ]
    )
    with pytest.raises(OrderReview, match="Order 41"):
        _create_order(_client(mismatch), _KEY, _lines((123, 2)), _OrderReservations())
    assert len(_posts(mismatch)) == 1
    assert not any(call["method"] in {"PUT", "DELETE"} for call in mismatch.calls)


@pytest.mark.parametrize(
    "order",
    [
        _created_order(set_paid=True),
        _created_order(set_paid=1),
        _created_order(set_paid=1.0),
        _created_order(transaction_id="txn-1"),
        _created_order(date_paid="2026-01-01T00:00:00"),
        _created_order(quantity=1),
    ],
)
def test_create_order_flags_unpaid_check_failures_without_another_write(order: dict) -> None:
    http = SequenceHttp([FakeResponse(200, _product()), FakeResponse(201, order)])
    reservations = _OrderReservations()
    client = _client(http)
    with pytest.raises(OrderReview, match="Order 41 was created but requires manual review"):
        _create_order(client, _KEY, _lines((123, 2)), reservations)
    with pytest.raises(OrderReview, match="Order 41"):
        _create_order(client, _KEY, _lines((123, 2)), reservations)
    assert [call["method"] for call in http.calls] == ["GET", "POST"]


@pytest.mark.asyncio
async def test_create_order_schema_permission_and_safe_result(launch_app) -> None:
    http = SequenceHttp(
        [
            FakeResponse(200, _product()),
            FakeResponse(201, _created_order()),
        ]
    )
    base = launch_app(create_woocommerce(_client(http)))
    async with open_mcp(f"{base}/mcp") as client:
        listed = await client.list_tools()
    schema = next(tool.input_schema for tool in listed.tools if tool.name == "create_order")
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["idempotency_key", "line_items"]
    assert schema["properties"]["line_items"]["items"]["additionalProperties"] is False

    extra = await _call(
        f"{base}/mcp",
        "create_order",
        {"idempotency_key": _KEY, "line_items": _lines((123, 2)), "billing": {"email": "buyer@example.com"}, "price": "1.00"},
    )
    assert extra.is_error is True
    assert "does not accept those fields" in extra.content[0].text
    assert "buyer@example.com" not in extra.content[0].text
    assert http.calls == []

    created = await _call(f"{base}/mcp", "create_order", {"idempotency_key": _KEY, "line_items": _lines((123, 2))})
    assert created.is_error is False
    assert created.content[0].text == "id=41 status=pending total=16.00 currency=USD paid=false items=123x2"
    assert "buyer@example.com" not in created.content[0].text

    utility = launch_app(create_utility())
    gateway, store, granted = await start_gateway(
        launch_app,
        [
            UpstreamConfig("woocommerce", f"{base}/mcp", 5),
            UpstreamConfig("utility", f"{utility}/mcp", 5),
        ],
        permissions=("woocommerce__list_products",),
    )
    hidden = await _names(f"{gateway}/mcp", granted.raw_key)
    assert "woocommerce__create_order" in hidden
    assert await store.permits(granted.key_id, "woocommerce__create_order") is False
    assert await store.permits(granted.key_id, "woocommerce__list_products") is True
    http.responses.extend([FakeResponse(200, _product()), FakeResponse(201, _created_order(order_id=42))])
    allowed = await _call(
        f"{gateway}/mcp",
        "woocommerce__create_order",
        {"idempotency_key": _OTHER_KEY, "line_items": _lines((123, 2))},
        granted.raw_key,
    )
    assert allowed.is_error is False
    assert "id=42" in allowed.content[0].text
    assert "buyer@example.com" not in allowed.content[0].text
    blob = "\n".join(repr(row) for row in store.audit_log)
    assert _OTHER_KEY not in blob
    assert "buyer@example.com" not in blob
    assert granted.raw_key not in blob
    assert any(
        row.actor == granted.key_id and row.tool_name == "woocommerce__create_order" and row.outcome == "success"
        for row in store.audit_log
    )
