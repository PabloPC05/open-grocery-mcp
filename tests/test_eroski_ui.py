from __future__ import annotations

import sys
from decimal import Decimal
from types import ModuleType, SimpleNamespace

from open_grocery_mcp.providers import eroski_ui


class _Tile:
    def __init__(self, text: str, on_click, product_ref: str | None = None) -> None:
        self.first = self
        self._text = text
        self._on_click = on_click
        self._product_ref = product_ref

    def count(self) -> int:
        return 1

    def nth(self, _: int):
        return self

    def is_visible(self) -> bool:
        return True

    def locator(self, _: str):
        return self

    def get_attribute(self, attribute: str):
        if attribute in {"data-product-ref", "data-product-id"}:
            return self._product_ref
        return None

    def inner_text(self) -> str:
        return self._text

    def click(self) -> None:
        self._on_click()


class _AddPage:
    def __init__(self, tile_text: str, product_ref: str | None = None) -> None:
        self.clicked = 0
        self._tile = _Tile(
            tile_text,
            lambda: setattr(self, "clicked", self.clicked + 1),
            product_ref,
        )

    def wait_for_timeout(self, _: int) -> None:
        pass

    def locator(self, _: str):
        return self._tile

    def evaluate(self, _: str):
        return "1,65€" if self.clicked else "1,00€"


class _LinkCollection:
    def __init__(self, links: list[_Tile]) -> None:
        self._links = links

    def count(self) -> int:
        return len(self._links)

    def nth(self, index: int) -> _Tile:
        return self._links[index]


class _MultiAddPage:
    def __init__(self) -> None:
        self.clicked_refs: list[str] = []
        self._links = _LinkCollection(
            [
                _Tile(
                    "Leche 1,00" + chr(0x20AC),
                    lambda: self.clicked_refs.append("wrong"),
                    "wrong",
                ),
                _Tile(
                    "Leche 1,50" + chr(0x20AC),
                    lambda: self.clicked_refs.append("wanted"),
                    "wanted",
                ),
            ]
        )

    def wait_for_timeout(self, _: int) -> None:
        pass

    def locator(self, selector: str):
        if selector == "a.update.toAddProduct":
            return self._links
        return self._links.nth(0)

    def evaluate(self, _: str):
        return "2,50" if self.clicked_refs else "1,00"


class _SelectorCapture:
    def __init__(self) -> None:
        self.selector = None

    def locator(self, selector: str):
        self.selector = selector
        return self


class FakePage:
    def __init__(self) -> None:
        self.timeout = None

    def set_default_timeout(self, value: int) -> None:
        self.timeout = value

    def evaluate(self, expression: str):
        if "location.origin" in expression:
            return "https://supermercado.eroski.es"
        return {"pagina": "safe-test-value"}


class FakeContext:
    def __init__(self) -> None:
        self.page = FakePage()
        self.init_scripts: list[str] = []

    def add_init_script(self, script: str) -> None:
        self.init_scripts.append(script)

    def new_page(self) -> FakePage:
        return self.page


class FakeBrowser:
    def __init__(self) -> None:
        self.context = FakeContext()
        self.closed = False

    def new_context(self, **_: object) -> FakeContext:
        return self.context

    def close(self) -> None:
        self.closed = True


def test_ui_context_returns_the_live_browser_handles(monkeypatch, tmp_path) -> None:
    browser = FakeBrowser()
    manager = SimpleNamespace(
        chromium=SimpleNamespace(launch=lambda **_: browser),
        stop=lambda: None,
    )
    sync_api = ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: SimpleNamespace(start=lambda: manager)
    playwright = ModuleType("playwright")
    playwright.sync_api = sync_api
    monkeypatch.setitem(sys.modules, "playwright", playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)

    ui = eroski_ui.ui_context(str(tmp_path / "storage_state.json"))

    assert ui["page"] is browser.context.page
    assert callable(ui["goto"])
    assert callable(ui["close"])
    assert "dump_session_storage" not in ui
    ui["close"]()
    assert browser.closed is True


def test_direct_add_rejects_restricted_query_without_navigation() -> None:
    navigated = False

    def goto(_):
        nonlocal navigated
        navigated = True
        return True

    result = eroski_ui.add_first_result(
        {"page": object(), "goto": goto}, "tequila"
    )
    assert result == {
        "added": False,
        "write_attempted": False,
        "reason": "age-restricted product blocked",
    }
    assert navigated is False


def test_direct_add_rejects_expensive_tile_before_click() -> None:
    page = _AddPage("Leche 6,00€")
    result = eroski_ui.add_first_result(
        {"page": page, "goto": lambda _: True},
        "leche",
        max_price=Decimal("5.00"),
    )

    assert result["added"] is False
    assert result["reason"] == "ordinary product price exceeds probe cap"
    assert page.clicked == 0


def test_direct_add_requires_an_ordinary_price_before_click() -> None:
    page = _AddPage("Leche precio no disponible")
    result = eroski_ui.add_first_result(
        {"page": page, "goto": lambda _: True}, "leche"
    )

    assert result == {
        "added": False,
        "write_attempted": False,
        "reason": "ordinary product price unavailable",
    }
    assert page.clicked == 0


def test_direct_add_uses_expected_product_ref_instead_of_http_tile_index() -> None:
    page = _MultiAddPage()
    result = eroski_ui.add_first_result(
        {"page": page, "goto": lambda _: True},
        "leche",
        tile_index=0,
        expected_product_ref="wanted",
    )

    assert result["added"] is True
    assert result["write_attempted"] is True
    assert page.clicked_refs == ["wanted"]


def test_product_tile_selector_requires_the_exact_product_item_token() -> None:
    link = _SelectorCapture()

    eroski_ui._product_tile(link)

    assert "product-item" in link.selector
    assert "contains(@class,'product')" not in link.selector


def test_product_ref_is_read_from_live_item_list_descendant() -> None:
    class ItemList:
        def count(self) -> int:
            return 1

        def nth(self, _index: int):
            return self

        def get_attribute(self, attribute: str):
            return "item-list-735423" if attribute == "id" else None

    class Tile:
        def get_attribute(self, _attribute: str):
            return None

        def locator(self, selector: str):
            assert selector in {
                "[data-product-ref]",
                "[data-productref]",
                "[data-product-id]",
                "[id^='item-list-']",
            }
            return ItemList() if selector == "[id^='item-list-']" else _LinkCollection([])

    assert eroski_ui._tile_product_ref(Tile()) == "735423"


def test_direct_add_refusal_before_click_does_not_mark_write_attempt() -> None:
    page = _MultiAddPage()
    result = eroski_ui.add_first_result(
        {"page": page, "goto": lambda _: True},
        "leche",
        expected_product_ref="missing",
    )

    assert result == {
        "added": False,
        "write_attempted": False,
        "reason": "expected product reference not found in rendered tiles",
    }
    assert page.clicked_refs == []


def test_remove_rejects_selector_injection_without_navigation() -> None:
    navigated = False

    def goto(_):
        nonlocal navigated
        navigated = True
        return True

    result = eroski_ui.remove_product(
        {"page": object(), "goto": goto}, 'x"]:has(*)'
    )
    assert result == {"removed": False, "reason": "invalid product id"}
    assert navigated is False
