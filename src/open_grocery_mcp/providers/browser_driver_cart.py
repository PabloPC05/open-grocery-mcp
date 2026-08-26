"""Cart reads and visible-control mutations for browser retailers."""

from __future__ import annotations

from decimal import Decimal
import re
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import quote, urljoin, urlsplit

from open_grocery_mcp.errors import ProviderError
from open_grocery_mcp.models import as_decimal
from open_grocery_mcp.providers.browser_normalize import (
    canonical_line_key,
    normalize_cart_payload,
    normalize_dom_cart,
    normalized_text,
    same_line_identity,
)
from open_grocery_mcp.providers.browser_scripts import DOM_CART_SCRIPT


class BrowserDriverCartMixin:
    def _captured_cart(self, captured: Iterable[Any]) -> dict[str, Any] | None:
        # Prefer the newest cart-shaped response. Choosing the response with the
        # largest item count can return a stale pre-mutation cart after removals.
        payloads = list(captured)
        for payload in reversed(payloads):
            normalized = normalize_cart_payload(payload, self.config.key)
            if normalized is not None:
                return normalized
        return None

    def _dom_cart(self, page: Any) -> dict[str, Any]:
        payload = page.evaluate(DOM_CART_SCRIPT)
        return normalize_dom_cart(payload if isinstance(payload, Mapping) else {}, self.config.key)

    def read_cart(self) -> dict[str, Any]:
        with self._page() as (page, captured, _):
            self._goto_cart(page)
            page.wait_for_timeout(800)
            cart = self._captured_cart(captured) or self._dom_cart(page)
            if cart["products_count"] == 0:
                try:
                    text = normalized_text(page.locator("body").inner_text())
                except Exception:
                    text = ""
                if not any(re.search(pattern, text, re.I) for pattern in self.config.empty_patterns):
                    # An empty cart is still valid, but flag that no explicit empty marker was seen.
                    cart["warning"] = "no cart lines or explicit empty-cart marker were detected"
            return cart

    @staticmethod
    def _line_fragment(line: Mapping[str, Any]) -> str:
        url = str(line.get("url") or "")
        if url:
            path = urlsplit(url).path.rstrip("/")
            if path:
                return path.split("/")[-1]
        return str(line.get("product_id") or "").strip()

    def _row_for_line(self, page: Any, line: Mapping[str, Any]) -> Any | None:
        fragment = self._line_fragment(line)
        candidates: list[Any] = []
        product_id = str(line.get("product_id") or line.get("id") or "").strip()
        if product_id and re.fullmatch(r"[A-Za-z0-9_-]+", product_id):
            candidates.append(
                page.locator(f'[class*="basket-product-{product_id}"]')
            )
        if fragment:
            candidates.append(page.locator(f'a[href*="{fragment.replace(chr(34), "")}"]'))
        name = str(line.get("name") or "").strip()
        if name:
            candidates.append(page.get_by_text(name, exact=False))
        for candidate in candidates:
            try:
                if not candidate.count():
                    continue
                anchor = candidate.first
                row = anchor.locator(
                    "xpath=ancestor::li[1] | ancestor::article[1] | ancestor::tr[1] | "
                    "ancestor::*[contains(translate(@class,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'cart-item')][1] | "
                    "ancestor::*[contains(translate(@class,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'basket-item')][1]"
                )
                if row.count():
                    return row.first
                return anchor.locator("xpath=..")
            except Exception:
                continue
        return None

    def _remove_line(self, page: Any, line: Mapping[str, Any]) -> bool:
        row = self._row_for_line(page, line)
        if row is None:
            return False
        expression = self._regex(self.config.remove_patterns)
        for selector in (
            "button[aria-label*='eliminar' i]",
            "button[aria-label*='quitar' i]",
            "button[title*='eliminar' i]",
            "button[title*='quitar' i]",
        ):
            try:
                button = row.locator(selector)
                if button.count() and button.first.is_visible():
                    button.first.click()
                    page.wait_for_timeout(350)
                    return True
            except Exception:
                continue
        try:
            button = row.locator("button,a,[role='button']").filter(has_text=expression)
            if button.count() and button.first.is_visible():
                button.first.click()
                page.wait_for_timeout(350)
                return True
        except Exception:
            return False
        return False

    def _set_quantity(self, page: Any, line: Mapping[str, Any], quantity: Decimal) -> None:
        row = self._row_for_line(page, line)
        if row is None:
            raise ProviderError(f"could not find {line.get('name') or line.get('product_id')} in the cart")
        for selector in (
            "input[type='number']",
            "input[name*='quantity' i]",
            "input[name*='cantidad' i]",
            "input[class*='quantity' i]",
        ):
            try:
                field = row.locator(selector)
                if field.count() and field.first.is_visible():
                    field.first.fill(str(quantity.normalize()))
                    field.first.press("Enter")
                    page.wait_for_timeout(450)
                    return
            except Exception:
                continue
        # Fallback for plus/minus widgets.
        try:
            current_payload = row.evaluate(
                r"""node => {
                  const input=node.querySelector('input');
                  if(input) return Number(input.value||1);
                  const q=node.querySelector('[class*=quantity i],[class*=cantidad i]');
                  const m=(q?.textContent||'1').match(/\d+(?:[.,]\d+)?/);
                  return m ? Number(m[0].replace(',','.')) : 1;
                }"""
            )
            current = as_decimal(current_payload, default="1")
        except Exception:
            current = Decimal("1")
        difference = int(quantity - current)
        if quantity != quantity.to_integral_value() or abs(difference) > 100:
            raise ProviderError("this cart exposes only plus/minus buttons and cannot safely set that quantity")
        if difference > 0:
            patterns = (r"aumentar", r"incrementar", r"añadir uno", r"^\s*\+\s*$")
            aria_selector = (
                "button[aria-label*='aumentar' i],"
                "button[aria-label*='incrementar' i],"
                "button[title*='aumentar' i],"
                "button[title*='incrementar' i]"
            )
        else:
            patterns = (r"reducir", r"disminuir", r"quitar uno", r"^\s*[−-]\s*$")
            aria_selector = (
                "button[aria-label*='disminuir' i],"
                "button[aria-label*='reducir' i],"
                "button[title*='disminuir' i],"
                "button[title*='reducir' i]"
            )
        for _ in range(abs(difference)):
            button = row.locator(aria_selector)
            if not button.count():
                expression = self._regex(patterns)
                button = row.locator("button,[role='button']").filter(has_text=expression)
            if not button.count():
                raise ProviderError("could not find the correct quantity control in the cart")
            button.first.click()
            page.wait_for_timeout(250)

    def _retailer_url(self, value: Any) -> str:
        url = urljoin(self.config.base_url, str(value or "").strip())
        expected = (urlsplit(self.config.base_url).hostname or "").casefold()
        actual = (urlsplit(url).hostname or "").casefold()
        if not actual or not (actual == expected or actual.endswith("." + expected)):
            raise ProviderError("refusing to navigate to a product URL outside the retailer domain")
        return url

    def _add_product(self, page: Any, line: Mapping[str, Any]) -> None:
        raw_url = str(line.get("url") or "").strip()
        if not raw_url:
            raise ProviderError(
                f"{self.config.label} browser cart needs the reviewed product URL for {line.get('name') or line.get('product_id')}"
            )
        product_url = self._retailer_url(raw_url)
        product_id = str(line.get("product_id") or "").strip()
        if self.config.key == "eroski" and re.fullmatch(r"\d+", product_id):
            # Eroski's detail-page control does not carry the same signed zone
            # context as a search tile. Searching the public product reference
            # yields one exact Tapestry tile whose ``toAddProduct`` event does.
            search_url = urljoin(
                self.config.base_url,
                f"/es/search/results/?q={quote(product_id)}",
            )
            page.goto(search_url, wait_until="domcontentloaded")
            self._accept_cookies(page)
            target = page.locator(
                f'#item-list-{product_id} a.update.toAddProduct'
            )
            if target.count() and target.first.is_visible():
                target.first.click()
                page.wait_for_timeout(500)
                return
            raise ProviderError(
                f"could not add {line.get('name') or product_id} on Eroski"
            )
        page.goto(product_url, wait_until="domcontentloaded")
        self._accept_cookies(page)
        if self._click_patterns(page, self.config.add_patterns, required=False):
            page.wait_for_timeout(500)
            return
        # Some storefronts use a plus icon as the only add control.
        for selector in (
            "button[aria-label*='añadir' i]",
            "button[aria-label*='agregar' i]",
            "button[title*='añadir' i]",
            "button[data-testid*='add' i]",
        ):
            try:
                button = page.locator(selector)
                if button.count() and button.first.is_visible():
                    button.first.click()
                    page.wait_for_timeout(500)
                    return
            except Exception:
                continue
        raise ProviderError(f"could not add {line.get('name') or line.get('product_id')} on {self.config.label}")

    def apply_cart(self, desired_lines: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        with self._page() as (page, captured, _):
            self._goto_cart(page)
            page.wait_for_timeout(600)
            current = self._captured_cart(captured) or self._dom_cart(page)
            current_lines = [dict(line) for line in current["lines"]]
            desired = [dict(line) for line in desired_lines]

            # Remove only lines absent from the approved result; unrelated merge-mode
            # lines are already included in desired by the planning layer. Product
            # identity may be exposed as ID, URL or name depending on the storefront.
            for line in current_lines:
                if not any(same_line_identity(line, wanted) for wanted in desired):
                    if not self._remove_line(page, line):
                        key = canonical_line_key(line)
                        raise ProviderError(
                            f"could not remove {line.get('name') or key} from {self.config.label}"
                        )

            # Add products not already represented in the current cart. Navigate back
            # to the cart after each product page.
            for line in desired:
                if any(same_line_identity(line, current) for current in current_lines):
                    continue
                self._add_product(page, line)
                self._goto_cart(page)

            # Apply exact reviewed quantities.
            self._goto_cart(page)
            for line in desired:
                self._set_quantity(page, line, as_decimal(line.get("quantity"), default="1"))
            page.wait_for_timeout(800)
            return self._captured_cart(captured) or self._dom_cart(page)
