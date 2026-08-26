"""Planning, validation and rollback for browser-backed carts."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
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
        unmatched = list(actual_lines)
        for desired in desired_lines:
            match_index = next(
                (
                    index
                    for index, candidate in enumerate(unmatched)
                    if same_line_identity(desired, candidate)
                    and as_decimal(candidate.get("quantity"))
                    == as_decimal(desired.get("quantity"))
                ),
                None,
            )
            if match_index is None:
                return False
            unmatched.pop(match_index)
        return not unmatched

    @staticmethod
    def _validated_quantity(value: Any, *, label: str) -> Decimal:
        if isinstance(value, bool):
            raise InvalidRequest(f"invalid quantity for {label!r}")
        try:
            quantity = Decimal(str(value).replace(",", ".").strip())
        except (InvalidOperation, ValueError, AttributeError):
            raise InvalidRequest(f"invalid quantity for {label!r}") from None
        if not quantity.is_finite() or quantity < 0:
            raise InvalidRequest(f"invalid quantity for {label!r}")
        if quantity > 1000:
            raise InvalidRequest(
                f"quantity for {label!r} exceeds the safety limit of 1000"
            )
        return quantity

    @classmethod
    def _validated_plan_lines(
        cls,
        value: Any,
        *,
        label: str,
        reject_restricted: bool,
    ) -> list[dict[str, Any]]:
        if not isinstance(value, list) or any(
            not isinstance(item, Mapping) for item in value
        ):
            raise InvalidRequest(f"browser cart plan contains malformed {label}")
        if len(value) > 100:
            raise InvalidRequest("browser cart plan exceeds the 100-line safety limit")
        lines: list[dict[str, Any]] = []
        for item in value:
            line = dict(item)
            product_id = str(line.get("product_id") or "").strip()
            name = str(line.get("name") or "").strip()
            url = sanitize_url(line.get("url"))
            if not any((product_id, name, url)):
                raise InvalidRequest(f"browser cart plan contains an unidentified {label}")
            quantity = cls._validated_quantity(
                line.get("quantity"), label=name or product_id or str(url)
            )
            if quantity <= 0 or as_decimal(line.get("unit_price")) <= 0:
                raise InvalidRequest(f"browser cart plan contains an invalid {label}")
            if reject_restricted and is_restricted_product(
                name, line.get("category")
            ):
                raise InvalidRequest(
                    "browser cart plan cannot retain age-restricted products"
                )
            if any(same_line_identity(line, existing) for existing in lines):
                raise InvalidRequest(f"browser cart plan contains duplicate {label}")
            lines.append(line)
        return lines

    @staticmethod
    def _reviewed_prices_match(
        actual: Mapping[str, Any], desired: Sequence[Mapping[str, Any]]
    ) -> bool:
        actual_lines = [
            item for item in actual.get("lines", []) if isinstance(item, Mapping)
        ]
        unmatched = list(actual_lines)
        for desired_line in desired:
            match_index = next(
                (
                    index
                    for index, candidate in enumerate(unmatched)
                    if same_line_identity(desired_line, candidate)
                    and as_decimal(candidate.get("unit_price"))
                    == as_decimal(desired_line.get("unit_price"))
                ),
                None,
            )
            if match_index is None:
                return False
            unmatched.pop(match_index)
        return not unmatched

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

        seen_changes: set[tuple[str, str, str]] = set()
        for change in changes:
            product_id = str(change.get("product_id") or "").strip()
            name = str(change.get("name") or "").strip()
            category = str(change.get("category") or "").strip()
            url = sanitize_url(change.get("url"))
            identity = (
                ("id", product_id, "")
                if product_id
                else ("url", str(url), "")
                if url
                else ("name", name.casefold(), "")
            )
            if identity in seen_changes:
                raise InvalidRequest(
                    f"duplicate browser cart change for {name or product_id!r}"
                )
            seen_changes.add(identity)
            if "quantity" not in change:
                raise InvalidRequest(
                    f"every browser cart change needs quantity for {name or product_id!r}"
                )
            quantity = self._validated_quantity(
                change.get("quantity"), label=name or product_id
            )
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

        if len(desired) > 100:
            raise InvalidRequest("resulting browser cart exceeds the 100-line safety limit")
        restricted = next(
            (
                line
                for line in desired
                if is_restricted_product(line.get("name"), line.get("category"))
            ),
            None,
        )
        if restricted is not None:
            raise InvalidRequest(
                "automated browser cart changes cannot retain age-restricted products"
            )

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
            "previous_total": float(as_decimal(cart.get("total"))),
            "retailer_cart_modified": False,
            "browser_driven": True,
        }

    def commit_cart_update(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        expected = int(plan.get("expected_cart_version") or 0)
        desired = self._validated_plan_lines(
            plan.get("desired_lines"),
            label="desired line",
            reject_restricted=True,
        )
        previous = self._validated_plan_lines(
            plan.get("previous_lines"),
            label="previous line",
            reject_restricted=False,
        )
        cap = as_decimal(plan.get("max_total"))
        expected_total = as_decimal(plan.get("estimated_total"))
        previous_total = as_decimal(plan.get("previous_total"))
        reviewed_total = sum(
            (
                as_decimal(line.get("unit_price"))
                * as_decimal(line.get("quantity"))
                for line in desired
            ),
            Decimal("0"),
        )
        if (
            cap <= 0
            or expected_total < 0
            or previous_total < 0
            or reviewed_total.quantize(Decimal("0.01"))
            != expected_total.quantize(Decimal("0.01"))
        ):
            raise InvalidRequest("browser cart plan contains invalid reviewed totals")
        current = self.cart()
        reviewed_cart_id = str(plan.get("cart_id") or "").strip()
        current_cart_id = str(current.get("cart_id") or "").strip()
        if reviewed_cart_id and current_cart_id and reviewed_cart_id != current_cart_id:
            raise ConcurrentCartChange(
                f"{self.config.label} cart identity changed after review"
            )
        if int(current.get("version") or 0) != expected:
            raise ConcurrentCartChange(
                f"{self.config.label} cart changed after review; prepare the update again"
            )
        driver = self._driver()
        try:
            updated = driver.apply_cart(desired)
            if not self._cart_matches(updated, desired) or not self._reviewed_prices_match(
                updated, desired
            ):
                # A second read handles storefronts whose DOM updates lag the click.
                updated = self.cart()
            if not self._cart_matches(updated, desired) or not self._reviewed_prices_match(
                updated, desired
            ):
                raise ProviderError(
                    f"{self.config.label} cart did not match the reviewed lines and prices"
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
            if total.quantize(Decimal("0.01")) != expected_total.quantize(
                Decimal("0.01")
            ):
                raise ProviderError(
                    f"actual {self.config.label} cart total differs from the reviewed total"
                )
        except Exception as original:
            try:
                observed = self.cart()
            except Exception as read_error:
                raise ProviderError(
                    f"{self.config.label} cart mutation result is ambiguous; inspect the "
                    "retailer cart before any further write"
                ) from read_error
            if self._cart_matches(observed, desired) and self._reviewed_prices_match(
                observed, desired
            ):
                observed_total = as_decimal(observed.get("total"))
                if (
                    (not desired or observed_total > 0)
                    and observed_total <= cap
                    and observed_total.quantize(Decimal("0.01"))
                    == expected_total.quantize(Decimal("0.01"))
                ):
                    return {
                        **observed,
                        "retailer_cart_modified": True,
                        "verified_against_reviewed_plan": True,
                        "write_response_ambiguous_but_state_verified": True,
                        "rollback_available": False,
                    }
            if (
                self._cart_matches(observed, previous)
                and self._reviewed_prices_match(observed, previous)
                and as_decimal(observed.get("total")).quantize(Decimal("0.01"))
                == previous_total.quantize(Decimal("0.01"))
            ):
                # The failed write was proven to have left the reviewed cart
                # untouched. Do not issue a compensating write in this case.
                raise original

            # A third state is ambiguous: applying ``previous`` here could
            # overwrite another actor's legitimate change. Fail closed and leave
            # the retailer cart for inspection instead of attempting rollback.
            raise ProviderError(
                f"{self.config.label} cart update failed ({type(original).__name__}); "
                "the observed cart matches neither the reviewed result nor the "
                "previous cart; inspect the retailer cart before any further write"
            ) from original
        return {
            **updated,
            "retailer_cart_modified": True,
            "verified_against_reviewed_plan": True,
            "rollback_available": False,
        }

