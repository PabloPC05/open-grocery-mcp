"""Promotion normalization and quantity-aware price calculations.

Retailers expose promotions in incompatible shapes. Providers keep only a
small, value-safe normalized mapping in ``Product.metadata['promotions']``;
this module validates those mappings before using them. Unknown or incomplete
rules are descriptive only and never reduce a basket total.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
from typing import Any, Mapping

from open_grocery_mcp.models import Product, as_decimal, money

PROMOTION_TYPES = {
    "direct_discount",
    "percent_discount",
    "bundle_price",
    "second_unit_discount",
    "buy_x_get_y",
    "unknown",
}


def _positive_decimal(value: Any) -> Decimal | None:
    parsed = as_decimal(value)
    return parsed if parsed > 0 else None


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    parsed = as_decimal(value)
    if parsed <= 0 or parsed != parsed.to_integral_value():
        return None
    return int(parsed)


def normalize_promotion(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return one bounded, JSON-safe promotion or ``None`` when unusable."""

    kind = str(raw.get("type") or "unknown").strip().casefold()
    if kind not in PROMOTION_TYPES:
        kind = "unknown"
    result: dict[str, Any] = {"type": kind}
    for key in ("description", "starts_at", "ends_at", "source", "access_scope"):
        value = str(raw.get(key) or "").strip()
        if value:
            result[key] = value[:500]
    for key in ("regular_unit_price", "promotional_unit_price", "bundle_price"):
        value = _positive_decimal(raw.get(key))
        if value is not None:
            result[key] = float(value)
            result[f"{key}_text"] = money(value)
    for key in ("required_quantity", "buy_quantity", "free_quantity"):
        value = _positive_int(raw.get(key))
        if value is not None:
            result[key] = value
    percent = _positive_decimal(raw.get("discount_percent"))
    if percent is None and kind == "direct_discount":
        regular = _positive_decimal(result.get("regular_unit_price"))
        promotional = _positive_decimal(result.get("promotional_unit_price"))
        if regular is not None and promotional is not None and promotional < regular:
            percent = (regular - promotional) * Decimal("100") / regular
    if percent is not None and percent <= 100:
        result["discount_percent"] = float(percent)
    if raw.get("loyalty_required") is not None:
        result["loyalty_required"] = bool(raw.get("loyalty_required"))

    actionable = {
        "direct_discount": "promotional_unit_price" in result,
        "percent_discount": "discount_percent" in result,
        "bundle_price": (
            "bundle_price" in result and "required_quantity" in result
        ),
        "second_unit_discount": "discount_percent" in result,
        "buy_x_get_y": (
            "buy_quantity" in result and "free_quantity" in result
        ),
        "unknown": False,
    }[kind]
    result["actionable"] = actionable
    if len(result) == 2 and not actionable:
        return None
    return result


def _provider_promotions(product: Product) -> list[dict[str, Any]]:
    """Translate provider evidence into the common promotion contract.

    Providers intentionally retain fields close to each retailer's observed
    response.  This boundary is the only place where those shapes acquire
    pricing semantics, and only explicit numeric fields are actionable.
    """

    metadata = product.metadata or {}
    raw = metadata.get("promotion")
    promotions: list[dict[str, Any]] = []
    if isinstance(raw, Mapping) and raw.get("available") is not False:
        current = _positive_decimal(raw.get("current_price")) or product.price
        previous = _positive_decimal(raw.get("previous_price"))
        offer = _positive_decimal(raw.get("offer_price"))
        source = str(raw.get("source") or "retailer promotion field")
        raw_kind = str(raw.get("type") or "").casefold()
        description = str(raw.get("label") or "").strip()
        is_coupon = "coupon" in raw_kind or "cupon" in raw_kind
        loyalty = (
            "loyalty" in raw_kind
            or "fidel" in raw_kind
            or source == "fidelity_offer_price"
        )
        # A coupon label does not prove that this account can redeem it.
        if is_coupon:
            promotions.append(
                {
                    "type": "unknown",
                    "description": description or "personal coupon observed",
                    "source": source,
                    "access_scope": "personal_coupon",
                    "loyalty_required": True,
                }
            )
        elif offer is not None and offer < current:
            promotions.append(
                {
                    "type": "direct_discount",
                    "regular_unit_price": current,
                    "promotional_unit_price": offer,
                    "description": description,
                    "source": source,
                    "loyalty_required": loyalty,
                }
            )
        elif not is_coupon and previous is not None and previous > current:
            promotions.append(
                {
                    "type": "direct_discount",
                    "regular_unit_price": previous,
                    "promotional_unit_price": current,
                    "description": description,
                    "source": source,
                    "loyalty_required": loyalty,
                }
            )

        mechanic = raw.get("quantity_mechanic")
        if isinstance(mechanic, Mapping) and not is_coupon:
            required = _positive_int(mechanic.get("buy_quantity"))
            paid = _positive_int(mechanic.get("pay_quantity"))
            discount = _positive_decimal(mechanic.get("discount_percent"))
            if required == 2 and discount is not None and discount <= 100:
                promotions.append(
                    {
                        "type": "second_unit_discount",
                        "required_quantity": 2,
                        "regular_unit_price": current,
                        "discount_percent": discount,
                        "description": description,
                        "source": source,
                        "loyalty_required": loyalty,
                    }
                )
            elif required and paid and paid < required and current > 0:
                promotions.append(
                    {
                        "type": "bundle_price",
                        "required_quantity": required,
                        "bundle_price": current * paid,
                        "regular_unit_price": current,
                        "description": description,
                        "source": source,
                        "loyalty_required": loyalty,
                    }
                )
            elif description:
                promotions.append(
                    {
                        "type": "unknown",
                        "description": description,
                        "source": source,
                        "loyalty_required": loyalty,
                    }
                )
        elif description and not promotions:
            promotions.append(
                {
                    "type": "unknown",
                    "description": description,
                    "source": source,
                    "loyalty_required": loyalty,
                }
            )

    # Froiz authenticated/public catalogue metadata is deliberately flat.
    order = (
        _positive_decimal(metadata.get("order_price"))
        or _positive_decimal(metadata.get("catalogue_current_price"))
        or product.price
    )
    base = (
        _positive_decimal(metadata.get("base_price"))
        or _positive_decimal(metadata.get("catalogue_previous_price"))
    )
    if metadata.get("promotion_type") == "direct_discount" and base and base > order:
        promotions.append(
            {
                "type": "direct_discount",
                "regular_unit_price": base,
                "promotional_unit_price": order,
                "description": str(metadata.get("offer") or ""),
                "source": str(metadata.get("price_source") or "froiz price fields"),
            }
        )
    required = _positive_int(metadata.get("promotion_quantity"))
    unit_price = _positive_decimal(metadata.get("promotion_unit_price"))
    if required and unit_price is not None:
        promotions.append(
            {
                "type": "direct_discount",
                "required_quantity": required,
                "regular_unit_price": base or order,
                "promotional_unit_price": unit_price,
                "source": str(metadata.get("price_source") or "froiz quantity fields"),
            }
        )
    return promotions


def product_promotions(product: Product) -> list[dict[str, Any]]:
    raw = product.metadata.get("promotions", []) if product.metadata else []
    if isinstance(raw, Mapping):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raw = []
    result: list[dict[str, Any]] = []
    for item in [*raw, *_provider_promotions(product)]:
        if not isinstance(item, Mapping):
            continue
        normalized = normalize_promotion(item)
        if normalized is not None:
            result.append(normalized)
    return result


def _candidate_total(
    promotion: Mapping[str, Any],
    *,
    quantity: Decimal,
    regular_price: Decimal,
) -> tuple[Decimal | None, str | None]:
    required = Decimal(str(promotion.get("required_quantity") or 1))
    if quantity < required:
        return None, "required quantity not reached"
    kind = str(promotion.get("type") or "unknown")
    promotion_regular = (
        _positive_decimal(promotion.get("regular_unit_price")) or regular_price
    )
    promotional = _positive_decimal(promotion.get("promotional_unit_price"))
    percent = _positive_decimal(promotion.get("discount_percent"))

    if kind == "direct_discount" and promotional is not None:
        return promotional * quantity, None
    if kind == "percent_discount" and percent is not None and percent <= 100:
        return promotion_regular * quantity * (
            Decimal("1") - percent / Decimal("100")
        ), None
    if quantity != quantity.to_integral_value():
        return None, "quantity promotion requires whole units"
    whole = int(quantity)
    if kind == "bundle_price":
        bundle_quantity = _positive_int(promotion.get("required_quantity"))
        bundle_price = _positive_decimal(promotion.get("bundle_price"))
        if bundle_quantity and bundle_price is not None:
            bundles, remainder = divmod(whole, bundle_quantity)
            return bundle_price * bundles + promotion_regular * remainder, None
    if kind == "second_unit_discount" and percent is not None and percent <= 100:
        pairs, remainder = divmod(whole, 2)
        pair_price = promotion_regular * (
            Decimal("2") - percent / Decimal("100")
        )
        return pair_price * pairs + promotion_regular * remainder, None
    if kind == "buy_x_get_y":
        buy = _positive_int(promotion.get("buy_quantity"))
        free = _positive_int(promotion.get("free_quantity"))
        if buy and free:
            cycle = buy + free
            cycles, remainder = divmod(whole, cycle)
            paid = cycles * buy + min(remainder, buy)
            return promotion_regular * paid, None
    return None, "promotion rule is descriptive only"


def _validity_status(promotion: Mapping[str, Any]) -> tuple[bool, str | None]:
    now = datetime.now(timezone.utc)
    parsed: dict[str, datetime] = {}
    for key in ("starts_at", "ends_at"):
        raw = str(promotion.get(key) or "").strip()
        if not raw:
            continue
        try:
            value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return False, "promotion validity could not be verified"
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        if key == "ends_at" and len(raw) == 10:
            value = value.replace(
                hour=23,
                minute=59,
                second=59,
                microsecond=999999,
            )
        parsed[key] = value.astimezone(timezone.utc)
    if parsed.get("starts_at", now) > now:
        return False, "promotion has not started"
    if parsed.get("ends_at", now) < now:
        return False, "promotion has expired"
    return True, None


def price_product_quantity(
    product: Product,
    quantity: Decimal,
    *,
    include_loyalty: bool = False,
) -> dict[str, Any]:
    """Calculate the cheapest explicitly supported promotion for a quantity.

    Promotions are alternatives, never stacked. The ordinary displayed price
    remains a candidate, so malformed retailer metadata cannot inflate a cart.
    """

    if not quantity.is_finite() or quantity <= 0:
        raise ValueError("quantity must be a positive finite Decimal")
    promotions = product_promotions(product)
    regular_price = product.price
    for promotion in promotions:
        value = _positive_decimal(promotion.get("regular_unit_price"))
        if value is not None:
            regular_price = max(regular_price, value)
    displayed_total = product.price * quantity
    regular_total = regular_price * quantity
    best_total = displayed_total
    applied: dict[str, Any] | None = None
    warnings: list[str] = []
    for promotion in promotions:
        if promotion.get("access_scope") == "personal_coupon":
            warnings.append("personal coupon shown as a separate non-actionable scenario")
            continue
        active, validity_warning = _validity_status(promotion)
        if not active:
            if validity_warning:
                warnings.append(validity_warning)
            continue
        if promotion.get("loyalty_required") and not include_loyalty:
            warnings.append("loyalty promotion excluded")
            continue
        candidate, reason = _candidate_total(
            promotion,
            quantity=quantity,
            regular_price=regular_price,
        )
        if candidate is None:
            if reason:
                warnings.append(reason)
            continue
        if candidate < best_total or (
            candidate == best_total
            and applied is None
            and promotion.get("actionable")
        ):
            best_total = candidate
            applied = dict(promotion)
    savings = max(Decimal("0"), regular_total - best_total)
    effective_unit = best_total / quantity
    return {
        "quantity": float(quantity),
        "displayed_unit_price": float(product.price),
        "displayed_unit_price_text": money(product.price),
        "regular_unit_price": float(regular_price),
        "regular_unit_price_text": money(regular_price),
        "effective_unit_price": float(effective_unit),
        "effective_unit_price_text": money(effective_unit),
        "regular_total": float(regular_total),
        "regular_total_text": money(regular_total),
        "effective_total": float(best_total),
        "effective_total_text": money(best_total),
        "savings": float(savings),
        "savings_text": money(savings),
        "applied_promotion": applied,
        "promotions": promotions,
        "warnings": sorted(set(warnings)),
    }


def whole_promotion_cycles(quantity: Decimal, cycle: int) -> int:
    """Small public helper used by property tests and callers."""

    if cycle <= 0 or quantity <= 0:
        return 0
    return int((quantity / Decimal(cycle)).to_integral_value(rounding=ROUND_FLOOR))
