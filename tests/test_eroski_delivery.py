"""Pure and GET-only tests for the Eroski delivery reader."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from open_grocery_mcp.errors import ProviderError
from open_grocery_mcp.providers.eroski_delivery import (
    EroskiDeliveryClient,
    parse_delivery_addresses,
    parse_delivery_slots,
    parse_pickup_stores,
)
from open_grocery_mcp.providers.eroski_http import EroskiHTTPClient


DELIVERY_HTML = """
<section id="homeAddressSelector">
  <input type="radio" id="home-opaque" name="selectedAddressRef" value="addr-opaque" checked>
  <label for="home-opaque">Calle Privada 12, 28050 Madrid · Pablo Pardo · 600 000 000</label>
</section>
<section id="pickupAddressSelector">
  <input type="radio" id="store-1" name="pickupAddressRef" value="store-1">
  <label for="store-1"><b>BIZKAIA - EROSKI/center Deusto</b>
    Blas de Otero 54, 48014 Bilbao <em>892 m</em></label>
  <input type="radio" id="store-2" name="pickupAddressRef" value="store-2">
  <label for="store-2"><b>BIZKAIA - EROSKI/center Indautxu</b>
    Plaza Indautxu, 48011 Bilbao <em>1 km</em></label>
</section>
"""


SELECT_DELIVERY_HTML = """
<select name="selectDeliveryAddress">
  <option value="home-select-opaque" selected>
    Calle Privada 77, 28050 Madrid · Nombre Privado · 600 111 222
  </option>
</select>
"""


SLOTS_HTML = """
<div class="slot-grid">
  <input type="radio" name="selectedAddressRef" value="addr-opaque" checked>
  <h3>24 - 31 de Agosto</h3>
  <a class="delivery-slot available" data-slot-id="slot-1" data-date="2026-08-24">
    09:30h a 10:00h Gratis
  </a>
  <button class="delivery-slot disabled" data-slot-id="slot-2" data-date="2026-08-24" disabled>
    10:00h a 10:30h 4,90€ No disponible
  </button>
</div>
"""


REAL_TABLE_SLOTS_HTML = """
<table>
  <tr><th>24 de agosto</th></tr>
  <tr><td class="delivery-table available" data-ref="real-ref-1" data-price="3,50">
    09:00 - 11:00
  </td></tr>
  <tr><td class="delivery-table unavailable" data-ref="real-ref-2" data-price="0">
    11:00 - 13:00
  </td></tr>
</table>
"""


def test_saved_addresses_are_redacted_but_postal_and_id_remain() -> None:
    addresses = parse_delivery_addresses(DELIVERY_HTML)

    assert addresses == [
        {
            "id": "addr-opaque",
            "label": "28050 · Dirección guardada",
            "postal_code": "28050",
            "street_redacted": True,
            "default": True,
        }
    ]
    serialized = json.dumps(addresses, ensure_ascii=False)
    assert "Calle Privada" not in serialized
    assert "Pablo" not in serialized
    assert "600 000" not in serialized


def test_saved_address_select_preserves_only_opaque_id_and_postal() -> None:
    addresses = parse_delivery_addresses(SELECT_DELIVERY_HTML)

    assert addresses == [
        {
            "id": "home-select-opaque",
            "label": "28050 · Dirección guardada",
            "postal_code": "28050",
            "street_redacted": True,
            "default": True,
        }
    ]
    serialized = json.dumps(addresses, ensure_ascii=False)
    assert "Calle Privada" not in serialized
    assert "Nombre Privado" not in serialized
    assert "600 111" not in serialized


def test_saved_address_select_skips_malformed_and_duplicate_options() -> None:
    source = """
    <select name="selectDeliveryAddress">
      <option value="">Placeholder 28050</option>
      <option>Missing value 28051</option>
      <option value="duplicate" selected>First 28052</option>
      <option value="duplicate">Second 28053</option>
      <option value="no-postal" selected>Missing postal</option>
    </select>
    """

    assert parse_delivery_addresses(source) == [
        {
            "id": "duplicate",
            "label": "28052 · Dirección guardada",
            "postal_code": "28052",
            "street_redacted": True,
            "default": True,
        }
    ]


def test_pickup_stores_keep_public_name_and_distance() -> None:
    stores = parse_pickup_stores(DELIVERY_HTML)

    assert [item["id"] for item in stores] == ["store-1", "store-2"]
    assert stores[0]["postal_code"] == "48014"
    assert "Deusto" in stores[0]["label"]
    assert stores[0]["distance"] == "892 m"
    assert stores[1]["distance"] == "1 km"


def test_slots_normalize_time_price_and_disabled_state() -> None:
    slots = parse_delivery_slots(SLOTS_HTML)

    assert slots[0] == {
        "id": "slot-1",
        "date": "2026-08-24",
        "start": "09:30",
        "end": "10:00",
        "available": True,
        "open": True,
        "price": 0.0,
        "price_text": "0.00",
        "label": "2026-08-24 09:30–10:00",
    }
    assert slots[1]["available"] is False
    assert slots[1]["price"] == 4.9
    assert slots[1]["price_text"] == "4.90"


def test_real_delivery_table_cells_use_jquery_data_refs_and_prices() -> None:
    slots = parse_delivery_slots(REAL_TABLE_SLOTS_HTML)

    assert slots[0]["id"] == "real-ref-1"
    assert slots[0]["price"] == 3.5
    assert slots[0]["price_text"] == "3.50"
    assert slots[1]["id"] == "real-ref-2"
    assert slots[1]["available"] is False


class FakeHTTP:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.persist_calls = 0

    def _get_html(self, path: str, **params: str) -> str:
        self.calls.append((path, params))
        return self.pages[path]

    def _persist_authenticated_cookies(self) -> bool:
        self.persist_calls += 1
        return True


def _rotating_delivery_transport(
    requests: list[httpx.Request], *, rotate: bool = False
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/" and request.url.params.get("zipCode"):
            headers = (
                {"Set-Cookie": "JSESSIONID=delivery-rotated; Path=/; HttpOnly"}
                if rotate
                else {}
            )
            return httpx.Response(200, text="<html>authenticated</html>", headers=headers)
        if request.url.path == "/es/bookingdelivery/shopdelivery/":
            return httpx.Response(200, text=DELIVERY_HTML)
        if request.url.path == "/es/bookingdeliverysummary/":
            return httpx.Response(200, text=SLOTS_HTML)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _http_with_delivery_state(
    tmp_path: Path,
    requests: list[httpx.Request],
    *,
    rotate: bool = False,
) -> EroskiHTTPClient:
    state_path = tmp_path / "storage_state.json"
    state_path.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "JSESSIONID",
                        "value": "delivery-original",
                        "domain": "supermercado.eroski.es",
                        "path": "/",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return EroskiHTTPClient(
        state_path=state_path,
        client=httpx.Client(
            transport=_rotating_delivery_transport(requests, rotate=rotate)
        ),
    )


def test_client_delivery_reads_are_get_only() -> None:
    fake = FakeHTTP(
        {
            "/es/bookingdelivery/shopdelivery/": DELIVERY_HTML,
            "/es/bookingdeliverysummary/": SLOTS_HTML,
        }
    )
    client = EroskiDeliveryClient(http=fake)

    assert client.delivery_addresses()[0]["id"] == "addr-opaque"
    assert client.pickup_stores()[0]["id"] == "store-1"
    assert client.delivery_slots("addr-opaque")[0]["id"] == "slot-1"
    assert [path for path, _ in fake.calls] == [
        "/es/bookingdelivery/shopdelivery/",
        "/es/bookingdelivery/shopdelivery/",
        "/es/bookingdeliverysummary/",
    ]
    assert fake.persist_calls == 3


def test_cookie_persistence_requires_private_address_evidence() -> None:
    pickup_only = DELIVERY_HTML.split('<section id="homeAddressSelector">', 1)[1]
    pickup_only = pickup_only.split("</section>", 1)[1]
    public = FakeHTTP({"/es/bookingdelivery/shopdelivery/": pickup_only})
    EroskiDeliveryClient(http=public).pickup_stores()
    assert public.persist_calls == 0

    empty = FakeHTTP({"/es/bookingdelivery/shopdelivery/": ""})
    EroskiDeliveryClient(http=empty).read_delivery_page()
    assert empty.persist_calls == 0


def test_getter_protocol_without_cookie_writer_remains_supported() -> None:
    class BareGetter:
        def _get_html(self, path: str, **params: str) -> str:
            assert path == "/es/bookingdelivery/shopdelivery/"
            return DELIVERY_HTML

    page = EroskiDeliveryClient(http=BareGetter()).read_delivery_page()
    assert page.addresses[0]["id"] == "addr-opaque"


def test_slots_persist_only_after_valid_selected_context_and_nonempty_slots() -> None:
    valid = FakeHTTP({"/es/bookingdeliverysummary/": SLOTS_HTML})
    EroskiDeliveryClient(http=valid).delivery_slots("addr-opaque")
    assert valid.persist_calls == 1

    mismatch = FakeHTTP({"/es/bookingdeliverysummary/": SLOTS_HTML})
    with pytest.raises(ProviderError, match="different selected address"):
        EroskiDeliveryClient(http=mismatch).delivery_slots("other")
    assert mismatch.persist_calls == 0

    no_slots = FakeHTTP(
        {
            "/es/bookingdeliverysummary/": SLOTS_HTML.split(
                '<a class="delivery-slot', 1
            )[0]
            + "</div>",
        }
    )
    with pytest.raises(ProviderError, match="no readable current slots"):
        EroskiDeliveryClient(http=no_slots).delivery_slots("addr-opaque")
    assert no_slots.persist_calls == 0


def test_delivery_private_read_persists_rotated_cookie_for_new_process(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    first_http = _http_with_delivery_state(tmp_path, requests, rotate=True)
    state_path = first_http.state_path
    EroskiDeliveryClient(http=first_http).delivery_addresses()
    first_http.close()

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert [
        row["value"]
        for row in state["cookies"]
        if row.get("name") == "JSESSIONID"
        and row.get("domain") == "supermercado.eroski.es"
    ] == ["delivery-rotated"]

    second_requests: list[httpx.Request] = []
    second_http = EroskiHTTPClient(
        state_path=state_path,
        client=httpx.Client(
            transport=_rotating_delivery_transport(second_requests)
        ),
    )
    EroskiDeliveryClient(http=second_http).delivery_addresses()
    bootstrap = next(
        request for request in second_requests if request.url.path == "/"
    )
    assert "delivery-rotated" in bootstrap.headers.get("cookie", "")
    second_http.close()


def test_delivery_cookie_persistence_failure_keeps_original_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []
    client = _http_with_delivery_state(tmp_path, requests, rotate=True)
    state_path = client.state_path
    original = state_path.read_text(encoding="utf-8")
    monkeypatch.setattr(
        "open_grocery_mcp.providers.eroski_http.os.replace",
        lambda *_args: (_ for _ in ()).throw(OSError("simulated replace failure")),
    )

    page = EroskiDeliveryClient(http=client).read_delivery_page()

    assert page.addresses
    assert state_path.read_text(encoding="utf-8") == original
    assert not list(state_path.parent.glob("*.tmp"))
    assert not list(state_path.parent.glob(".*.tmp"))
    client.close()


def test_client_requires_an_address_id_without_mutating_context() -> None:
    fake = FakeHTTP({"/es/bookingdeliverysummary/": SLOTS_HTML})
    client = EroskiDeliveryClient(http=fake)

    with pytest.raises(ProviderError, match="non-empty address"):
        client.delivery_slots("")
    assert fake.calls == []


def test_current_summary_without_slots_fails_closed() -> None:
    fake = FakeHTTP({"/es/bookingdeliverysummary/": "<html><body>Entrega</body></html>"})
    client = EroskiDeliveryClient(http=fake)

    with pytest.raises(ProviderError, match="no readable current slots"):
        client.current_delivery_slots()


def test_delivery_slots_rejects_unobservable_or_mismatched_context() -> None:
    no_context = EroskiDeliveryClient(http=FakeHTTP({"/es/bookingdeliverysummary/": SLOTS_HTML.replace(
        '<input type="radio" name="selectedAddressRef" value="addr-opaque" checked>', ""
    )}))
    with pytest.raises(ProviderError, match="does not expose a selected address"):
        no_context.delivery_slots("addr-opaque")

    mismatch = EroskiDeliveryClient(http=FakeHTTP({"/es/bookingdeliverysummary/": SLOTS_HTML}))
    with pytest.raises(ProviderError, match="different selected address"):
        mismatch.delivery_slots("another-address")


def test_delivery_slots_accepts_selected_address_select_context() -> None:
    summary = SELECT_DELIVERY_HTML + SLOTS_HTML.replace(
        '<input type="radio" name="selectedAddressRef" value="addr-opaque" checked>',
        "",
    )
    fake = FakeHTTP({"/es/bookingdeliverysummary/": summary})
    client = EroskiDeliveryClient(http=fake)

    assert client.delivery_slots("home-select-opaque")[0]["id"] == "slot-1"
