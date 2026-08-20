from pathlib import Path
import shutil

import pytest

from open_grocery_mcp.providers.browser_config import BrowserStoreConfig
from open_grocery_mcp.providers.browser_driver import PlaywrightBrowserDriver


CONFIG = BrowserStoreConfig(
    key="demo",
    label="Demo",
    base_url="https://demo.test",
    cart_paths=("/cart",),
)


def make_driver(tmp_path: Path) -> PlaywrightBrowserDriver:
    return PlaywrightBrowserDriver(
        CONFIG,
        state_path=tmp_path / "state.json",
        checkout_store=tmp_path / "checkouts.json",
    )


def test_captured_cart_prefers_newest_response_after_removal(tmp_path):
    driver = make_driver(tmp_path)
    before = {
        "cart": {
            "lines": [
                {"product_id": "1", "name": "Leche", "quantity": 1, "unit_price": 1},
                {"product_id": "2", "name": "Pan", "quantity": 1, "unit_price": 2},
            ],
            "total": 3,
        }
    }
    after = {
        "cart": {
            "lines": [
                {"product_id": "1", "name": "Leche", "quantity": 1, "unit_price": 1}
            ],
            "total": 1,
        }
    }
    cart = driver._captured_cart([before, after])
    assert cart is not None
    assert cart["products_count"] == 1
    assert cart["total"] == 1.0


def test_dom_cart_script_with_real_chromium(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    executable = shutil.which("chromium") or shutil.which("google-chrome")
    if executable is None:
        pytest.skip("no Chromium executable installed")

    driver = make_driver(tmp_path)
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True, executable_path=executable)
        try:
            page = browser.new_page()
            page.set_content(
                """
                <main>
                  <article class="cart-item" data-product-id="milk-1">
                    <a href="https://demo.test/product/milk-1">Leche entera 1 L</a>
                    <span>1,25 €</span>
                    <input type="number" name="quantity" value="2" />
                    <button aria-label="Eliminar producto">x</button>
                  </article>
                  <div class="cart-total">Total 2,50 €</div>
                </main>
                """
            )
            cart = driver._dom_cart(page)
        finally:
            browser.close()

    assert cart["products_count"] == 1
    assert cart["lines"][0]["product_id"] == "milk-1"
    assert cart["lines"][0]["quantity"] == 2.0
    assert cart["total_text"] == "2.50"
