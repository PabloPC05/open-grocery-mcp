"""Deterministic product matching for cross-store basket comparisons.

This is intentionally transparent rather than LLM-based. Every selection has a
score and rationale so an agent can tell the user when two products are only an
approximation.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from open_grocery_mcp.models import Match, Product

_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_QUANTITY_RE = re.compile(
    r"(?P<value>\d+(?:[.,]\d+)?)\s*(?P<unit>kg|g|l|ml|cl|ud(?:s)?|u|unidades?|unidad)\b",
    re.IGNORECASE,
)
_STOPWORDS = {
    "de",
    "del",
    "la",
    "el",
    "los",
    "las",
    "un",
    "una",
    "y",
    "con",
    "sin",
    "pack",
    "paquete",
    "envase",
}


@dataclass(frozen=True, slots=True)
class ParsedQuantity:
    dimension: str
    value: Decimal


def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def tokens(value: str) -> tuple[str, ...]:
    return tuple(
        token
        for token in _TOKEN_RE.findall(normalize_text(value))
        if token not in _STOPWORDS
    )


def parse_quantity(value: str) -> ParsedQuantity | None:
    match = _QUANTITY_RE.search(normalize_text(value))
    if not match:
        return None
    amount = Decimal(match.group("value").replace(",", "."))
    unit = match.group("unit").lower()
    if unit == "kg":
        return ParsedQuantity("mass", amount * 1000)
    if unit == "g":
        return ParsedQuantity("mass", amount)
    if unit == "l":
        return ParsedQuantity("volume", amount * 1000)
    if unit == "cl":
        return ParsedQuantity("volume", amount * 10)
    if unit == "ml":
        return ParsedQuantity("volume", amount)
    return ParsedQuantity("count", amount)


def _quantity_similarity(requested: ParsedQuantity, candidate: ParsedQuantity) -> float:
    if requested.dimension != candidate.dimension:
        return -0.18
    high = max(requested.value, candidate.value)
    low = min(requested.value, candidate.value)
    if high <= 0:
        return 0.0
    ratio = float(low / high)
    # Exact formats receive +0.15; a 2x size difference is neutral; larger
    # mismatches become a small penalty.
    return 0.3 * ratio - 0.15


def score_product(query: str, product: Product, *, position: int = 0) -> tuple[float, tuple[str, ...]]:
    query_tokens = set(tokens(query))
    product_tokens = set(tokens(product.name))
    if not query_tokens or not product_tokens:
        return 0.0, ("no comparable text tokens",)

    intersection = query_tokens & product_tokens
    coverage = len(intersection) / len(query_tokens)
    precision = len(intersection) / len(product_tokens)
    score = 0.72 * coverage + 0.18 * precision
    rationale: list[str] = [f"token coverage {coverage:.0%}"]

    normalized_query = normalize_text(query).strip()
    normalized_name = normalize_text(product.name).strip()
    if normalized_query and normalized_query in normalized_name:
        score += 0.10
        rationale.append("query appears verbatim in product name")

    requested_quantity = parse_quantity(query)
    candidate_quantity = parse_quantity(product.name)
    if requested_quantity and candidate_quantity:
        quantity_score = _quantity_similarity(requested_quantity, candidate_quantity)
        score += quantity_score
        if quantity_score >= 0.10:
            rationale.append("format/quantity closely matches")
        elif quantity_score >= 0:
            rationale.append("format/quantity partially matches")
        else:
            rationale.append("format/quantity differs")
    elif requested_quantity and not candidate_quantity:
        score -= 0.04
        rationale.append("requested quantity was not found in product name")

    if not product.available:
        score -= 0.40
        rationale.append("product marked unavailable")

    # Preserve a small amount of the retailer's own relevance ordering.
    score -= min(position, 20) * 0.002
    return max(0.0, min(1.0, score)), tuple(rationale)


def select_best(
    query: str,
    products: Iterable[Product],
    *,
    minimum_score: float = 0.28,
    max_unit_price: Decimal | None = None,
) -> Match | None:
    candidates: list[tuple[float, Decimal, int, Product, tuple[str, ...]]] = []
    for position, product in enumerate(products):
        if not product.available:
            continue
        if max_unit_price is not None and product.price > max_unit_price:
            continue
        score, rationale = score_product(query, product, position=position)
        candidates.append((score, product.price, position, product, rationale))
    if not candidates:
        return None

    # Textual equivalence wins. Price only breaks near ties, preventing a very
    # cheap but unrelated result from being chosen.
    candidates.sort(key=lambda item: (-round(item[0], 3), item[1], item[2]))
    score, _, _, product, rationale = candidates[0]
    if not math.isfinite(score) or score < minimum_score:
        return None
    return Match(product=product, score=score, rationale=rationale)
