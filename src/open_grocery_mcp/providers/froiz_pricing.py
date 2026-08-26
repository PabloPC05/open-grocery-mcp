"""Safe, evidence-based Froiz price and promotion normalization.

The authenticated Nuxt catalogue exposes ``order_price`` and
``base_price``.  Empathy's public catalogue exposes the current value under
``__prices.current.value``.  Promotion metadata is kept flat and scalar so a
retailer payload cannot leak nested private data through ``Product.metadata``.
"""

from __future__ import annotations

from decimal import Decimal
from math import isfinite
from typing import Any, Mapping

from open_grocery_mcp.models import as_decimal

_MAX_TEXT = 160
_QUANTITY_KINDS = {
    "quantity",
    "multi_buy",
    "multibuy",
    "volume",
}


def _price(value: Any) -> Decimal:
    if isinstance(value, Mapping):
        for key in ("value", "amount", "price", "unit_price"):
            if value.get(key) not in (None, ""):
                return as_decimal(value.get(key))
        return Decimal("0")
    return as_decimal(value)


def _scalar(value: Any) -> str | int | float | bool | None:
    """Return only bounded JSON scalars; discard nested retailer payloads."""

    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if isfinite(value) else None
    if isinstance(value, str):
        text = value.strip()
        return text[:_MAX_TEXT] if text else None
    return None


def _put_price(metadata: dict[str, Any], key: str, value: Any) -> Decimal:
    amount = _price(value)
    if amount > 0:
        metadata[key] = float(amount)
    return amount


def _quantity_offer(metadata: dict[str, Any], offer: Mapping[str, Any]) -> None:
    kind = str(
        offer.get("type") or offer.get("kind") or offer.get("promotion_type") or ""
    ).strip().casefold().replace("-", "_").replace(" ", "_")
    quantity = offer.get("quantity")
    if quantity in (None, ""):
        quantity = offer.get("min_quantity")
    if quantity in (None, ""):
        quantity = offer.get("from_quantity")
    offer_price = offer.get("unit_price")
    if offer_price in (None, ""):
        offer_price = offer.get("price")
    quantity_decimal = _price(quantity)
    price_decimal = _price(offer_price)
    # A quantity promotion is reported only when the payload explicitly says
    # it is one and supplies both quantity and price.  No 2x1 or percentage
    # semantics are inferred from an opaque offer label.
    if kind not in _QUANTITY_KINDS or quantity_decimal <= 0 or price_decimal <= 0:
        return
    metadata["quantity_promotion_type"] = "quantity"
    if "promotion_type" not in metadata:
        metadata["promotion_type"] = "quantity"
    metadata["promotion_quantity"] = float(quantity_decimal)
    metadata["promotion_unit_price"] = float(price_decimal)


def normalize_pricing(
    raw: Mapping[str, Any],
    *,
    price_source: str,
    public_current: Any = None,
) -> dict[str, Any]:
    """Normalize direct discounts and explicit quantity offers only."""

    metadata: dict[str, Any] = {"price_source": price_source}
    order_price = _put_price(metadata, "order_price", raw.get("order_price"))
    base_price = _put_price(metadata, "base_price", raw.get("base_price"))
    if order_price <= 0 and public_current not in (None, ""):
        _put_price(metadata, "catalogue_current_price", public_current)

    offer = raw.get("offer")
    scalar_offer = _scalar(offer)
    if scalar_offer is not None:
        metadata["offer"] = scalar_offer
    elif isinstance(offer, Mapping):
        offer_type = _scalar(
            offer.get("type") or offer.get("kind") or offer.get("promotion_type")
        )
        if offer_type is not None:
            metadata["offer_type"] = offer_type
        _quantity_offer(metadata, offer)

    if order_price > 0 and base_price > order_price:
        discount = base_price - order_price
        metadata["promotion_type"] = "direct_discount"
        metadata["discount_amount"] = float(discount)
        metadata["discount_percent"] = float((discount / base_price) * 100)
    return metadata


def public_pricing_metadata(raw: Mapping[str, Any]) -> dict[str, Any]:
    prices = raw.get("__prices")
    current = prices.get("current") if isinstance(prices, Mapping) else None
    value = current.get("value") if isinstance(current, Mapping) else None
    metadata = normalize_pricing(
        raw,
        price_source="empathy.__prices.current.value",
        public_current=value,
    )
    previous = prices.get("previous") if isinstance(prices, Mapping) else None
    previous_value = (
        previous.get("value") if isinstance(previous, Mapping) else None
    )
    current_price = _price(value)
    previous_price = _price(previous_value)
    if current_price > 0 and previous_price > current_price:
        metadata.update(
            {
                "catalogue_previous_price": float(previous_price),
                "promotion_type": "direct_discount",
                "discount_amount": float(previous_price - current_price),
                "discount_percent": float(
                    ((previous_price - current_price) / previous_price) * 100
                ),
            }
        )
    return metadata


__all__ = ["normalize_pricing", "public_pricing_metadata"]
