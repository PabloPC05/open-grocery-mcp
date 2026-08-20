"""Store-agnostic data models.

The public MCP layer returns plain JSON-compatible dictionaries. Internally we
keep prices as :class:`decimal.Decimal` so basket arithmetic does not accumulate
binary floating-point errors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping

CENT = Decimal("0.01")


def as_decimal(value: Any, *, default: str = "0") -> Decimal:
    """Convert retailer JSON values to a finite Decimal.

    Retailer APIs are inconsistent: a price may be a JSON number, a quoted
    number, ``null`` or an empty string. Invalid inputs become ``default``.
    """

    if value is None or isinstance(value, bool):
        return Decimal(default)
    try:
        result = Decimal(str(value).replace(",", ".").strip())
    except (InvalidOperation, ValueError, AttributeError):
        return Decimal(default)
    return result if result.is_finite() else Decimal(default)


def money(value: Decimal) -> str:
    """Return a stable two-decimal money string."""

    return format(value.quantize(CENT, rounding=ROUND_HALF_UP), "f")


@dataclass(frozen=True, slots=True)
class Product:
    """A product normalized across retailer backends."""

    store: str
    id: str
    name: str
    price: Decimal
    currency: str = "EUR"
    price_per_unit: Decimal | None = None
    unit: str | None = None
    brand: str | None = None
    category: str | None = None
    available: bool = True
    url: str | None = None
    ean: str | None = None
    origin: str | None = None
    ingredients: str | None = None
    nutrients: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "store": self.store,
            "id": self.id,
            "name": self.name,
            "price": float(self.price),
            "price_text": money(self.price),
            "currency": self.currency,
            "available": self.available,
        }
        optional = {
            "price_per_unit": (
                float(self.price_per_unit) if self.price_per_unit is not None else None
            ),
            "unit": self.unit,
            "brand": self.brand,
            "category": self.category,
            "url": self.url,
            "ean": self.ean,
            "origin": self.origin,
            "ingredients": self.ingredients,
            "nutrients": self.nutrients,
        }
        out.update({key: value for key, value in optional.items() if value not in (None, "")})
        if self.price_per_unit is not None:
            out["price_per_unit_text"] = money(self.price_per_unit)
        if self.metadata:
            out["metadata"] = dict(self.metadata)
        return out


@dataclass(frozen=True, slots=True)
class BasketItem:
    """One requested item before matching it to a retailer product."""

    query: str
    quantity: Decimal = Decimal("1")
    required: bool = True
    max_unit_price: Decimal | None = None

    @classmethod
    def from_value(cls, value: str | Mapping[str, Any]) -> "BasketItem":
        if isinstance(value, str):
            query = value.strip()
            if not query:
                raise ValueError("basket item query cannot be empty")
            return cls(query=query)
        if not isinstance(value, Mapping):
            raise TypeError("each basket item must be a string or object")
        query = str(value.get("query", "")).strip()
        if not query:
            raise ValueError("each basket item needs a non-empty 'query'")
        quantity = as_decimal(value.get("quantity", 1), default="1")
        if quantity <= 0:
            raise ValueError(f"quantity for {query!r} must be greater than zero")
        maximum = value.get("max_unit_price")
        max_price = None if maximum in (None, "") else as_decimal(maximum)
        if max_price is not None and max_price <= 0:
            raise ValueError(f"max_unit_price for {query!r} must be greater than zero")
        return cls(
            query=query,
            quantity=quantity,
            required=bool(value.get("required", True)),
            max_unit_price=max_price,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "query": self.query,
            "quantity": float(self.quantity),
            "required": self.required,
        }
        if self.max_unit_price is not None:
            out["max_unit_price"] = float(self.max_unit_price)
        return out


@dataclass(frozen=True, slots=True)
class Match:
    """A selected product and the confidence of the textual match."""

    product: Product
    score: float
    rationale: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "product": self.product.to_dict(),
            "match_score": round(self.score, 4),
            "match_rationale": list(self.rationale),
        }


@dataclass(frozen=True, slots=True)
class StoreInfo:
    """Public metadata for one provider."""

    key: str
    label: str
    country: str
    languages: tuple[str, ...]
    capabilities: tuple[str, ...]
    requires_postal_code: bool = False
    price_scope: str = "retailer-default assortment"
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "key": self.key,
            "label": self.label,
            "country": self.country,
            "languages": list(self.languages),
            "capabilities": list(self.capabilities),
            "requires_postal_code": self.requires_postal_code,
            "price_scope": self.price_scope,
        }
        if self.notes:
            out["notes"] = self.notes
        return out
