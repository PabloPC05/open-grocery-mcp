"""Planning, validation and rollback for browser-backed carts."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping, Sequence

from open_grocery_mcp.errors import (
    BudgetExceeded,
    ConcurrentCartChange,
    InvalidRequest,
    ProviderError,
)
from open_grocery_mcp.models import as_decimal, money
from open_grocery_mcp.providers.browser_normalize import (
    is_restricted_product,
    sanitize_url,
    same_line_identity,
)


class BrowserAccountCartMixin:
    @staticmethod
    def _match_line(
        line: Mapping[str, Any],
        candidates: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any] | None:
        return next(
            (candidate for candidate in candidates if same_line_identity(line, candidate)),
            None,
        )

    @classmethod
    def _cart_matches(
        cls,
        actual: Mapping[str, Any],
        desired_lines: Sequence[Mapping[str, Any]],
    ) -> bool:
        actual_lines = [item for item in actual.get("lines", []) if isinstance(item, Mapping)]
        if len(actual_lines) != len(desired_lines):
            return False
        for desired in desired_lines:
            actual_line = cls._match_line(desired, actual_lines)
            if actual_line is None:
                return False
            if as_decimal(actual_line.get("quantity")) != as_decimal(desired.get("quantity")):
                return False
        return True

    @staticmethod
    def _public_line(line: Mapping[str, Any]) -> dict[str, Any]:
        price = as_decimal(line.get("unit_price"))
        quantity = as_decimal(line.get("quantity"), default="1")
        return {
            "product_id": str(line.get("product_id") or ""),
            "name": str(line.get("name") or ""),
            "quantity": float(quantity),
            "unit_price": float(price),
            "unit_price_text": money(price),
            "line_total": float(price * quantity),
            "line_total_text": money(price * quantity),
            "url": sanitize_url(line.get("url")),
        }

    def preview_cart_update(
        self,
        changes: Sequence[Mapping[str, Any]],
        *,
        mode: str,
        expected_version: int | None,
        max_total: Decimal,
    ) -> dict[str, Any]:
        if mode not in {"merge", "replace"}:
            raise InvalidRequest("cart update mode must be 'merge' or 'replace'")
        if max_total <= 0:
            raise InvalidRequest("max_total must be greater than zero")
        if len(changes) > 100:
            raise InvalidRequest("browser cart updates are limited to 100 product lines")
        cart = self.cart()
        version = int(cart.get("version") or 0)
        if expected_version is not None and version != expected_version:
            raise ConcurrentCartChange(
                f"{self.config.label} cart version is {version}, not reviewed version {expected_version}"
            )
        previous_lines = [dict(line) for line in cart.get("lines", []) if isinstance(line, Mapping)]
        desired: list[dict[str, Any]] = [] if mode == "replace" else [dict(line) for line in previous_lines]

        for change in changes:
            product_id = str(change.get("product_id") or "").strip()
            name = str(change.get("name") or "").strip()
            category = str(change.get("category") or "").strip()
            url = sanitize_url(change.get("url"))
            quantity = as_decimal(change.get("quantity"))
            price = as_decimal(change.get("unit_price") or change.get("price"))
            if not any((product_id, name, url)):
                raise InvalidRequest("every browser cart change needs product_id, name or product URL")
            if is_restricted_product(name, category):
                raise InvalidRequest(
                    f"automated purchase of age-restricted product {name!r} is not supported"
                )
            probe = {"product_id": product_id, "name": name, "url": url}
            existing = self._match_line(probe, desired)
            if quantity <= 0:
                if existing is not None:
                    desired.remove(existing)  # type: ignore[arg-type]
                continue
            if quantity > 1000:
                raise InvalidRequest(
                    f"quantity for {name or product_id!r} exceeds the safety limit of 1000"
                )
            if price <= 0 and existing is not None:
                price = as_decimal(existing.get("unit_price"))
            if price <= 0:
                raise ProviderError(f"no reviewed unit price is available for {name or product_id}")
            line = {
                "product_id": product_id,
                "name": name or str(existing.get("name") if existing else ""),
                "quantity": float(quantity),
                "unit_price": float(price),
                "url": url or (sanitize_url(existing.get("url")) if existing else None),
            }
            if existing is not None:
                index = desired.index(existing)  # type: ignore[arg-type]
                desired[index] = line
            else:
                desired.append(line)

        total = sum(
            (as_decimal(line.get("unit_price")) * as_decimal(line.get("quantity")) for line in desired),
            Decimal("0"),
        )
        if total > max_total:
            raise BudgetExceeded(
                f"proposed {self.config.label} cart total {money(total)} EUR exceeds cap {money(max_total)} EUR"
            )
        return {
            "store": self.config.key,
            "cart_id": str(cart.get("cart_id") or ""),
            "expected_cart_version": version,
            "mode": mode,
            "max_total": float(max_total),
            "max_total_text": money(max_total),
            "estimated_total": float(total),
            "estimated_total_text": money(total),
            "currency": "EUR",
            "lines": [self._public_line(line) for line in desired],
            "desired_lines": desired,
            "previous_lines": previous_lines,
            "retailer_cart_modified": False,
            "browser_driven": True,
        }

    def commit_cart_update(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        expected = int(plan.get("expected_cart_version") or 0)
        desired = [dict(item) for item in plan.get("desired_lines", []) if isinstance(item, Mapping)]
        previous = [dict(item) for item in plan.get("previous_lines", []) if isinstance(item, Mapping)]
        cap = as_decimal(plan.get("max_total"))
        current = self.cart()
        if int(current.get("version") or 0) != expected:
            raise ConcurrentCartChange(
                f"{self.config.label} cart changed after review; prepare the update again"
            )
        driver = self._driver()
        try:
            updated = driver.apply_cart(desired)
            if not self._cart_matches(updated, desired):
                # A second read handles storefronts whose DOM updates lag the click.
                updated = self.cart()
            if not self._cart_matches(updated, desired):
                raise ProviderError(
                    f"{self.config.label} cart did not match the reviewed product quantities"
                )
            total = as_decimal(updated.get("total"))
            if desired and total <= 0:
                raise BudgetExceeded(
                    f"could not verify a positive {self.config.label} cart total after writing; rolled back"
                )
            if total > cap:
                raise BudgetExceeded(
                    f"actual {self.config.label} cart total {money(total)} EUR exceeds cap {money(cap)} EUR"
                )
        except Exception:
            try:
                driver.apply_cart(previous)
            except Exception:
                pass
            raise
        return {
            **updated,
            "retailer_cart_modified": True,
            "verified_against_reviewed_plan": True,
            "rollback_available": False,
        }

