"""Safe planning and verified commits for the captured Gadis HTTP cart."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from open_grocery_mcp.errors import (
    AuthenticationRequired,
    BudgetExceeded,
    ConcurrentCartChange,
    InvalidRequest,
    ProviderError,
)
from open_grocery_mcp.models import as_decimal, money
from open_grocery_mcp.providers.browser_normalize import is_restricted_product


class GadisCartMixin:
    """Use HTTP for verified whole-unit carts and retain browser fallback."""

    @staticmethod
    def _line_signature(
        lines: Sequence[Mapping[str, Any]],
    ) -> tuple[tuple[str, str], ...]:
        normalized: list[tuple[str, str]] = []
        for line in lines:
            product_id = str(line.get("product_id", "")).strip()
            quantity = as_decimal(line.get("quantity"))
            if product_id and quantity > 0:
                normalized.append((product_id, str(quantity.normalize())))
        return tuple(sorted(normalized))

    @classmethod
    def _cart_matches(
        cls,
        cart: Mapping[str, Any],
        desired: Sequence[Mapping[str, Any]],
    ) -> bool:
        lines = cart.get("lines", [])
        actual = (
            [line for line in lines if isinstance(line, Mapping)]
            if isinstance(lines, list)
            else []
        )
        return cls._line_signature(actual) == cls._line_signature(desired)

    @staticmethod
    def _price_signature(
        lines: Sequence[Mapping[str, Any]],
    ) -> tuple[tuple[str, str], ...]:
        return tuple(
            sorted(
                (
                    str(line.get("product_id") or "").strip(),
                    str(as_decimal(line.get("unit_price")).normalize()),
                )
                for line in lines
                if str(line.get("product_id") or "").strip()
                and as_decimal(line.get("quantity")) > 0
            )
        )

    @staticmethod
    def _whole_quantity(value: Any) -> int:
        quantity = as_decimal(value)
        if quantity <= 0:
            return 0
        integral = quantity.to_integral_value()
        if quantity != integral:
            raise InvalidRequest(
                "the captured Gadis HTTP cart supports whole-unit quantities only; "
                "the browser fallback is required for fractional quantities"
            )
        if integral > 1000:
            raise InvalidRequest("Gadis cart quantity exceeds the safety limit of 1000")
        return int(integral)

    @classmethod
    def _http_compatible(
        cls,
        changes: Sequence[Mapping[str, Any]],
    ) -> bool:
        for change in changes:
            product_id = str(change.get("product_id", "")).strip()
            if not product_id:
                raise InvalidRequest("every Gadis cart change needs a product_id")
            if "quantity" not in change:
                raise InvalidRequest(
                    f"every Gadis cart change needs quantity for {product_id!r}"
                )
            quantity = cls._validated_quantity(
                change.get("quantity"), product_id=product_id
            )
            if quantity > 0 and quantity != quantity.to_integral_value():
                return False
        return True

    @staticmethod
    def _public_line(line: Mapping[str, Any]) -> dict[str, Any]:
        quantity = as_decimal(line.get("quantity"))
        unit_price = as_decimal(line.get("unit_price"))
        line_total = as_decimal(line.get("line_total"))
        if line_total <= 0:
            line_total = unit_price * quantity
        return {
            "product_id": str(line.get("product_id", "")),
            "name": str(line.get("name", "")),
            "quantity": float(quantity),
            "unit_price": float(unit_price),
            "unit_price_text": money(unit_price),
            "line_total": float(line_total),
            "line_total_text": money(line_total),
        }

    @staticmethod
    def _write_lines(cart: Mapping[str, Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        products = cart.get("products", [])
        for raw in products if isinstance(products, list) else []:
            if not isinstance(raw, Mapping):
                continue
            product_id = str(raw.get("product_id", "")).strip()
            quantity = as_decimal(raw.get("amount"))
            if not product_id or quantity <= 0:
                continue
            line_total = as_decimal(raw.get("line_price"))
            result.append(
                {
                    "product_id": product_id,
                    "name": str(raw.get("product_name", "")).strip(),
                    "quantity": float(quantity),
                    "unit_price": float(line_total / quantity),
                    "line_total": float(line_total),
                    "preparation_mode_id": raw.get("preparation_mode_id"),
                    "product_note": raw.get("product_note"),
                    "substitution_type": raw.get("substitution_type"),
                }
            )
        return result

    def _normalize_http_cart(self, raw_cart: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(self._http.normalize_cart(raw_cart))
        normalized["store_id"] = str(raw_cart.get("store_id", "")).strip() or None
        normalized["lines"] = [
            self._public_line(line) for line in self._write_lines(raw_cart)
        ]
        return normalized

    def _http_cart(self) -> dict[str, Any]:
        return self._normalize_http_cart(self._http.read_cart())

    def _browser_preview(
        self,
        changes: Sequence[Mapping[str, Any]],
        *,
        mode: str,
        expected_version: int | None,
        max_total: Decimal,
        reason: str,
    ) -> dict[str, Any]:
        plan = self._browser.preview_cart_update(
            changes,
            mode=mode,
            expected_version=expected_version,
            max_total=max_total,
        )
        return {
            **plan,
            "plan_backend": "browser",
            "browser_driven": True,
            "http_fallback_reason": reason,
        }

    def cart(self) -> dict[str, Any]:
        try:
            return {
                **self._http_cart(),
                "cart_backend": "gadis_http",
                "browser_driven": False,
            }
        except (AuthenticationRequired, ProviderError) as exc:
            fallback = self._browser.cart()
            return {
                **fallback,
                "cart_backend": "browser",
                "browser_driven": True,
                "http_fallback_reason": type(exc).__name__,
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
            raise InvalidRequest("Gadis cart updates are limited to 100 product lines")
        if not self._http_compatible(changes):
            return self._browser_preview(
                changes,
                mode=mode,
                expected_version=expected_version,
                max_total=max_total,
                reason="fractional quantity or missing product id",
            )

        try:
            raw_cart = self._http.read_cart()
        except (AuthenticationRequired, ProviderError) as exc:
            return self._browser_preview(
                changes,
                mode=mode,
                expected_version=expected_version,
                max_total=max_total,
                reason=type(exc).__name__,
            )

        normalized = self._normalize_http_cart(raw_cart)
        version = int(normalized.get("version") or 0)
        if expected_version is not None and version != expected_version:
            raise ConcurrentCartChange(
                f"Gadis cart version is {version}, not reviewed version {expected_version}"
            )
        previous_lines = self._write_lines(raw_cart)
        if any(
            as_decimal(line.get("quantity"))
            != as_decimal(line.get("quantity")).to_integral_value()
            for line in previous_lines
        ):
            return self._browser_preview(
                changes,
                mode=mode,
                expected_version=expected_version,
                max_total=max_total,
                reason="existing cart contains a fractional quantity",
            )

        current = {
            str(line["product_id"]): dict(line)
            for line in previous_lines
            if line.get("product_id")
        }
        desired: dict[str, dict[str, Any]] = (
            {}
            if mode == "replace"
            else {key: dict(value) for key, value in current.items()}
        )
        seen_changes: set[str] = set()

        for change in changes:
            product_id = str(change.get("product_id", "")).strip()
            name = str(change.get("name", "")).strip()
            category = str(change.get("category", "")).strip()
            if product_id in seen_changes:
                raise InvalidRequest(
                    f"duplicate Gadis cart change for product {product_id!r}"
                )
            seen_changes.add(product_id)
            if "quantity" not in change:
                raise InvalidRequest(
                    f"every Gadis cart change needs quantity for {product_id!r}"
                )
            quantity = self._validated_quantity(
                change.get("quantity"), product_id=product_id
            )
            if is_restricted_product(name, category):
                raise InvalidRequest(
                    f"automated purchase of age-restricted product {name!r} is not supported"
                )
            if quantity <= 0:
                desired.pop(product_id, None)
                continue
            amount = self._whole_quantity(quantity)
            existing = desired.get(product_id) or current.get(product_id)
            unit_price = as_decimal(change.get("unit_price") or change.get("price"))
            if unit_price <= 0 and existing is not None:
                unit_price = as_decimal(existing.get("unit_price"))
            if unit_price <= 0:
                raise ProviderError(
                    f"no reviewed unit price is available for {name or product_id}"
                )
            desired[product_id] = {
                "product_id": product_id,
                "name": name or str(existing.get("name", "") if existing else ""),
                "quantity": float(amount),
                "unit_price": float(unit_price),
                "preparation_mode_id": (
                    existing.get("preparation_mode_id") if existing else None
                ),
                "product_note": existing.get("product_note") if existing else None,
                "substitution_type": (
                    existing.get("substitution_type") if existing else None
                ),
            }

        if len(desired) > 100:
            raise InvalidRequest(
                "resulting Gadis cart exceeds the 100-line safety limit"
            )
        retained_restricted = next(
            (
                line
                for line in desired.values()
                if is_restricted_product(line.get("name"))
            ),
            None,
        )
        if retained_restricted is not None:
            raise InvalidRequest(
                "automated Gadis cart changes cannot retain age-restricted products"
            )

        total = sum(
            (
                as_decimal(line.get("unit_price"))
                * as_decimal(line.get("quantity"))
                for line in desired.values()
            ),
            Decimal("0"),
        )
        non_product_costs = as_decimal(normalized.get("non_product_costs"))
        total += non_product_costs
        if total > max_total:
            raise BudgetExceeded(
                f"proposed Gadis cart total {money(total)} EUR exceeds cap "
                f"{money(max_total)} EUR"
            )

        cart_id = str(normalized.get("cart_id") or "").strip()
        store_id = str(normalized.get("store_id") or "").strip()
        if not cart_id or not store_id:
            raise ProviderError("Gadis cart did not expose a usable cart/store id")
        desired_lines = list(desired.values())
        return {
            "store": "gadis",
            "cart_id": cart_id,
            "store_id": store_id,
            "expected_cart_version": version,
            "mode": mode,
            "max_total": float(max_total),
            "max_total_text": money(max_total),
            "estimated_total": float(total),
            "estimated_total_text": money(total),
            "non_product_costs": float(non_product_costs),
            "non_product_costs_text": money(non_product_costs),
            "currency": "EUR",
            "lines": [self._public_line(line) for line in desired_lines],
            "desired_lines": desired_lines,
            "previous_lines": previous_lines,
            "previous_total": float(as_decimal(normalized.get("total"))),
            "previous_non_product_costs": float(non_product_costs),
            "retailer_cart_modified": False,
            "plan_backend": "gadis_http",
            "browser_driven": False,
        }

    def _apply_http_lines(
        self,
        desired_lines: Sequence[Mapping[str, Any]],
        *,
        expected_version: int | None = None,
    ) -> None:
        raw_cart = self._http.read_cart()
        cart = self._normalize_http_cart(raw_cart)
        if (
            expected_version is not None
            and int(cart.get("version") or 0) != expected_version
        ):
            raise ConcurrentCartChange(
                "Gadis cart changed immediately before the HTTP mutation"
            )
        cart_id = str(cart.get("cart_id") or "").strip()
        store_id = str(cart.get("store_id") or "").strip()
        if not cart_id or not store_id:
            raise ProviderError("Gadis cart did not expose a usable cart/store id")
        current = {
            str(line["product_id"]): dict(line)
            for line in self._write_lines(raw_cart)
            if line.get("product_id")
        }
        desired = {
            str(line.get("product_id", "")): dict(line)
            for line in desired_lines
            if str(line.get("product_id", "")).strip()
            and as_decimal(line.get("quantity")) > 0
        }

        def reread_after_write(
            expected: Mapping[str, Mapping[str, Any]],
        ) -> dict[str, dict[str, Any]]:
            observed_raw = self._http.read_cart()
            observed_cart = self._normalize_http_cart(observed_raw)
            if (
                str(observed_cart.get("cart_id") or "") != cart_id
                or str(observed_cart.get("store_id") or "") != store_id
            ):
                raise ConcurrentCartChange(
                    "Gadis cart identity changed during the HTTP mutation"
                )
            observed_lines = self._write_lines(observed_raw)
            if self._line_signature(observed_lines) != self._line_signature(
                list(expected.values())
            ):
                raise ConcurrentCartChange(
                    "Gadis cart changed concurrently between HTTP mutations"
                )
            return {
                str(line["product_id"]): dict(line)
                for line in observed_lines
                if line.get("product_id")
            }

        # Remove first so a replacement cannot temporarily exceed the reviewed cap.
        for product_id in sorted(set(current) - set(desired)):
            line = current[product_id]
            self._http.update_product(
                cart_id,
                store_id,
                product_id,
                0,
                preparation_mode_id=line.get("preparation_mode_id"),
                product_note=line.get("product_note"),
                substitution_type=line.get("substitution_type"),
            )
            current.pop(product_id, None)
            current = reread_after_write(current)

        for product_id in sorted(desired):
            line = desired[product_id]
            amount = self._whole_quantity(line.get("quantity"))
            previous = current.get(product_id)
            if (
                previous is not None
                and self._whole_quantity(previous.get("quantity")) == amount
            ):
                continue
            self._http.update_product(
                cart_id,
                store_id,
                product_id,
                amount,
                preparation_mode_id=line.get("preparation_mode_id"),
                product_note=line.get("product_note"),
                substitution_type=line.get("substitution_type"),
            )
            current[product_id] = dict(line)
            current = reread_after_write(current)

    def _verified_http_result(
        self,
        desired_lines: Sequence[Mapping[str, Any]],
        max_total: Decimal,
        expected_total: Decimal,
        expected_non_product_costs: Decimal,
    ) -> dict[str, Any]:
        updated = self._http_cart()
        if not self._cart_matches(updated, desired_lines):
            raise ProviderError("Gadis cart did not match the reviewed product quantities")
        total = as_decimal(updated.get("total"))
        actual_non_product_costs = as_decimal(updated.get("non_product_costs"))
        if desired_lines and total <= 0:
            raise BudgetExceeded("could not verify a positive Gadis cart total after writing")
        if total > max_total:
            raise BudgetExceeded(
                f"actual Gadis cart total {money(total)} EUR exceeds cap "
                f"{money(max_total)} EUR"
            )
        if total.quantize(Decimal("0.01")) != expected_total.quantize(
            Decimal("0.01")
        ):
            raise ProviderError("Gadis actual cart total differs from the reviewed total")
        if actual_non_product_costs.quantize(Decimal("0.01")) != expected_non_product_costs.quantize(
            Decimal("0.01")
        ):
            raise ProviderError("Gadis non-product costs changed from the reviewed costs")
        actual_lines = self._cart_lines(updated)
        if self._price_signature(actual_lines) != self._price_signature(desired_lines):
            raise ProviderError("Gadis cart prices did not match the reviewed prices")
        return updated

    def _restore_http_cart(
        self,
        previous_lines: Sequence[Mapping[str, Any]],
        *,
        previous_total: Decimal,
        previous_non_product_costs: Decimal,
        expected_version: int,
    ) -> None:
        self._apply_http_lines(previous_lines, expected_version=expected_version)
        restored = self._http_cart()
        if not self._cart_matches(restored, previous_lines):
            raise ProviderError("Gadis automatic rollback did not restore the previous cart")
        if self._price_signature(self._cart_lines(restored)) != self._price_signature(
            previous_lines
        ) or as_decimal(restored.get("total")).quantize(
            Decimal("0.01")
        ) != previous_total.quantize(Decimal("0.01")):
            raise ProviderError(
                "Gadis automatic rollback did not restore reviewed prices and total"
            )
        if as_decimal(restored.get("non_product_costs")).quantize(
            Decimal("0.01")
        ) != previous_non_product_costs.quantize(Decimal("0.01")):
            raise ProviderError(
                "Gadis automatic rollback did not restore non-product costs"
            )

    def commit_cart_update(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        if plan.get("plan_backend") != "gadis_http":
            return self._browser.commit_cart_update(plan)

        expected_version = int(plan.get("expected_cart_version") or 0)
        max_total = as_decimal(plan.get("max_total"))
        expected_total = as_decimal(plan.get("estimated_total"))
        expected_non_product_costs = as_decimal(plan.get("non_product_costs"))
        previous_total = as_decimal(plan.get("previous_total"))
        previous_non_product_costs = as_decimal(
            plan.get("previous_non_product_costs")
        )
        desired_value = plan.get("desired_lines")
        previous_value = plan.get("previous_lines")
        if not isinstance(desired_value, list) or not isinstance(previous_value, list):
            raise InvalidRequest("Gadis cart plan contains invalid line collections")
        if any(not isinstance(item, Mapping) for item in desired_value) or any(
            not isinstance(item, Mapping) for item in previous_value
        ):
            raise InvalidRequest("Gadis cart plan contains malformed lines")
        desired_lines = [dict(item) for item in desired_value]
        previous_lines = [dict(item) for item in previous_value]
        if (
            max_total <= 0
            or expected_total < 0
            or previous_total < 0
            or expected_non_product_costs < 0
            or previous_non_product_costs < 0
        ):
            raise InvalidRequest("Gadis cart plan contains invalid totals")
        for label, lines in (("desired", desired_lines), ("previous", previous_lines)):
            seen: set[str] = set()
            for line in lines:
                product_id = str(line.get("product_id") or "").strip()
                if (
                    not product_id
                    or self._whole_quantity(line.get("quantity")) <= 0
                    or as_decimal(line.get("unit_price")) <= 0
                    or product_id in seen
                ):
                    raise InvalidRequest(
                        f"Gadis cart plan contains an invalid {label} line"
                    )
                seen.add(product_id)
        if len(desired_lines) > 100 or len(previous_lines) > 100:
            raise InvalidRequest("Gadis cart plan exceeds the 100-line safety limit")
        calculated_total = sum(
            (
                as_decimal(line.get("unit_price"))
                * as_decimal(line.get("quantity"))
                for line in desired_lines
            ),
            Decimal("0"),
        )
        calculated_total += expected_non_product_costs
        if calculated_total.quantize(Decimal("0.01")) != expected_total.quantize(
            Decimal("0.01")
        ):
            raise InvalidRequest("Gadis cart plan total no longer matches its lines")
        current = self._http_cart()
        current_version = int(current.get("version") or 0)
        if current_version != expected_version:
            raise ConcurrentCartChange(
                f"Gadis cart changed from version {expected_version} to "
                f"{current_version}; review again"
            )
        if as_decimal(current.get("non_product_costs")).quantize(
            Decimal("0.01")
        ) != expected_non_product_costs.quantize(Decimal("0.01")):
            raise ConcurrentCartChange(
                "Gadis non-product costs changed since the cart was reviewed"
            )

        try:
            self._apply_http_lines(
                desired_lines,
                expected_version=expected_version,
            )
            updated = self._verified_http_result(
                desired_lines,
                max_total,
                expected_total,
                expected_non_product_costs,
            )
        except Exception as original:
            # Never retry the ambiguous mutation. A safe read may prove that it
            # actually completed; otherwise restore the reviewed previous cart.
            try:
                observed = self._http_cart()
            except Exception as read_error:
                raise ProviderError(
                    "Gadis cart write result is ambiguous and the cart could not "
                    "be reread; inspect it before any further write"
                ) from read_error
            if self._cart_matches(observed, desired_lines):
                total = as_decimal(observed.get("total"))
                if (
                    (not desired_lines or total > 0)
                    and total <= max_total
                    and total.quantize(Decimal("0.01"))
                    == expected_total.quantize(Decimal("0.01"))
                    and as_decimal(observed.get("non_product_costs")).quantize(
                        Decimal("0.01")
                    )
                    == expected_non_product_costs.quantize(Decimal("0.01"))
                    and self._price_signature(self._cart_lines(observed))
                    == self._price_signature(desired_lines)
                ):
                    return {
                        **observed,
                        "retailer_cart_modified": True,
                        "verified_against_reviewed_plan": True,
                        "write_response_ambiguous_but_state_verified": True,
                        "order_placed": False,
                        "cart_backend": "gadis_http",
                    }
            if (
                self._cart_matches(observed, previous_lines)
                and self._price_signature(self._cart_lines(observed))
                == self._price_signature(previous_lines)
                and as_decimal(observed.get("total")).quantize(Decimal("0.01"))
                == previous_total.quantize(Decimal("0.01"))
                and as_decimal(observed.get("non_product_costs")).quantize(
                    Decimal("0.01")
                )
                == previous_non_product_costs.quantize(Decimal("0.01"))
            ):
                raise ProviderError(
                    f"Gadis cart update failed ({type(original).__name__}); "
                    "the previous cart remained unchanged"
                ) from original
            # Do not overwrite a cart whose line set is neither the reviewed
            # target nor the reviewed starting point.  It may be a concurrent
            # user's update, and an automatic restore would destroy it.
            if not self._cart_matches(observed, desired_lines) and not self._cart_matches(
                observed, previous_lines
            ):
                raise ConcurrentCartChange(
                    "Gadis cart changed to an unreviewed state after the failed "
                    "write; inspect it before any further write"
                ) from original
            try:
                rollback_guard = self._http_cart()
                if int(rollback_guard.get("version") or 0) != int(
                    observed.get("version") or 0
                ):
                    raise ConcurrentCartChange(
                        "Gadis cart changed again before rollback"
                    )
                self._restore_http_cart(
                    previous_lines,
                    previous_total=previous_total,
                    previous_non_product_costs=previous_non_product_costs,
                    expected_version=int(rollback_guard.get("version") or 0),
                )
            except Exception as rollback_error:
                raise ProviderError(
                    "Gadis cart update failed and rollback could not be verified; "
                    "inspect the retailer cart before any further write"
                ) from rollback_error
            if isinstance(original, BudgetExceeded):
                raise BudgetExceeded(
                    f"{original}; previous Gadis cart restored"
                ) from original
            raise ProviderError(
                f"Gadis cart update failed ({type(original).__name__}); "
                "previous cart restored"
            ) from original

        return {
            **updated,
            "retailer_cart_modified": True,
            "verified_against_reviewed_plan": True,
            "order_placed": False,
            "cart_backend": "gadis_http",
        }
    @staticmethod
    def _validated_quantity(value: Any, *, product_id: str) -> Decimal:
        if isinstance(value, bool):
            raise InvalidRequest(f"invalid quantity for Gadis product {product_id!r}")
        try:
            quantity = Decimal(str(value).replace(",", ".").strip())
        except (InvalidOperation, ValueError, AttributeError):
            raise InvalidRequest(
                f"invalid quantity for Gadis product {product_id!r}"
            ) from None
        if not quantity.is_finite() or quantity < 0:
            raise InvalidRequest(f"invalid quantity for Gadis product {product_id!r}")
        if quantity > 1000:
            raise InvalidRequest(
                f"quantity for Gadis product {product_id!r} exceeds the safety limit"
            )
        return quantity
