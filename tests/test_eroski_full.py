from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from open_grocery_mcp.providers import eroski_ui
from open_grocery_mcp.providers.eroski_full import EroskiFullProvider


def test_full_provider_forwards_expected_product_ref_to_ui(monkeypatch) -> None:
    provider = object.__new__(EroskiFullProvider)
    provider._account = SimpleNamespace(state_path="state.json")
    provider._http = SimpleNamespace(state_path="")
    calls: dict[str, object] = {}

    monkeypatch.setattr(
        eroski_ui,
        "ui_context",
        lambda _: {"close": lambda: None},
    )

    def fake_add(ui, query, **kwargs):
        calls.update(kwargs)
        return {"added": False, "write_attempted": False}

    monkeypatch.setattr(eroski_ui, "add_first_result", fake_add)

    result = provider.add_item_via_browser(
        "leche",
        tile_index=7,
        max_price=Decimal("5.00"),
        expected_product_ref="wanted",
    )

    assert result["write_attempted"] is False
    assert calls == {
        "tile_index": 7,
        "max_price": Decimal("5.00"),
        "expected_product_ref": "wanted",
    }
