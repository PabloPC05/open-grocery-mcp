"""Quality, substitution and observability services for semantic matching."""

from __future__ import annotations

import re
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from open_grocery_mcp.equivalence import (
    SemanticProfile,
    analyze_product_text,
    analyze_query_text,
    assess_query_candidate,
    compare_profiles,
    semantic_profile_cache_info,
)
from open_grocery_mcp.alias_data import semantic_alias_data
from open_grocery_mcp.data_files import data_path
from open_grocery_mcp.matching import normalize_text, tokens
from open_grocery_mcp.models import Product

ONTOLOGY_VERSION = "2026.08.25-p2"
ALIAS_DATA_VERSION = str(semantic_alias_data().get("version") or "unknown")

_PRIVATE_LABELS = {
    "mercadona": {
        "hacendado": "Hacendado",
        "deliplus": "Deliplus",
        "compy": "Compy",
        "bosque verde": "Bosque Verde",
    },
    "eroski": {"eroski": "Eroski", "basic": "Eroski Basic", "seleqtia": "Eroski Seleqtia"},
    "froiz": {"froiz": "Froiz", "alteza": "Alteza", "ifa": "IFA"},
    "gadis": {"gadis": "Gadis", "ifa": "IFA", "eliges": "IFA Eliges", "sabe": "IFA Sabe"},
}

with data_path("quality_budgets.json").open(encoding="utf-8") as _budget_handle:
    _budget_payload = json.load(_budget_handle)
QUALITY_BUDGET_VERSION = str(_budget_payload.pop("version", "unknown"))
_QUALITY_BUDGETS: dict[str, dict[str, float | int]] = _budget_payload

_MASS = re.compile(r"(?<!\d)(\d+(?:[.,]\d+)?)\s*(kg|g)\b", re.I)
_VOLUME = re.compile(r"(?<!\d)(\d+(?:[.,]\d+)?)\s*(l|ml|cl)\b", re.I)
_COUNT = re.compile(r"(?<!\d)(\d+)\s*(?:uds?|unidades?|rollos?|dosis)\b", re.I)
_MULTIPACK = re.compile(
    r"(?<!\d)(\d+)\s*x\s*(\d+(?:[.,]\d+)?)\s*(kg|g|l|ml|cl)\b",
    re.I,
)
_DRAINED = re.compile(
    r"(?:peso\s+escurrido|pne\.?)\s*[:.]?\s*(\d+(?:[.,]\d+)?)\s*(kg|g)",
    re.I,
)
_RANGE = re.compile(
    r"(?<!\d)(\d+(?:[.,]\d+)?)\s*(?:-|a)\s*(\d+(?:[.,]\d+)?)\s*(kg|g|l|ml|cl)\b",
    re.I,
)


@dataclass(frozen=True, slots=True)
class PackagingProfile:
    dimension: str | None
    amount: float | None
    unit: str | None
    pack_count: int | None = None
    drained_amount: float | None = None
    variable_weight: bool = False
    minimum_amount: float | None = None
    maximum_amount: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "dimension": self.dimension,
                "amount": self.amount,
                "unit": self.unit,
                "pack_count": self.pack_count,
                "drained_amount": self.drained_amount,
                "variable_weight": self.variable_weight,
                "minimum_amount": self.minimum_amount,
                "maximum_amount": self.maximum_amount,
            }.items()
            if value is not None and value is not False
        }


def _number(value: str) -> float:
    return float(value.replace(",", "."))


def parse_packaging(text: str) -> PackagingProfile:
    normalized = normalize_text(text)
    multi = _MULTIPACK.search(normalized)
    drained = _DRAINED.search(normalized)
    variable = bool(
        re.search(r"\b(?:al peso|peso variable|aproximadamente|aprox)\b", normalized)
    )
    ranged = _RANGE.search(normalized)
    if ranged:
        unit = ranged.group(3).casefold()
        return PackagingProfile(
            "mass" if unit in {"kg", "g"} else "volume",
            None,
            unit,
            drained_amount=_number(drained.group(1)) if drained else None,
            variable_weight=True,
            minimum_amount=_number(ranged.group(1)),
            maximum_amount=_number(ranged.group(2)),
        )
    if multi:
        count = int(multi.group(1))
        amount = _number(multi.group(2))
        unit = multi.group(3).casefold()
        dimension = "mass" if unit in {"kg", "g"} else "volume"
        return PackagingProfile(
            dimension,
            amount * count,
            unit,
            pack_count=count,
            drained_amount=_number(drained.group(1)) if drained else None,
            variable_weight=variable,
        )
    mass = _MASS.search(normalized)
    if mass:
        return PackagingProfile(
            "mass",
            _number(mass.group(1)),
            mass.group(2).casefold(),
            drained_amount=_number(drained.group(1)) if drained else None,
            variable_weight=variable,
        )
    volume = _VOLUME.search(normalized)
    if volume:
        return PackagingProfile(
            "volume",
            _number(volume.group(1)),
            volume.group(2).casefold(),
            variable_weight=variable,
        )
    count = _COUNT.search(normalized)
    if count:
        return PackagingProfile("count", float(count.group(1)), "item")
    return PackagingProfile(None, None, None, variable_weight=variable)


def _base_amount(profile: PackagingProfile) -> tuple[float | None, str | None]:
    amount = profile.amount
    if amount is None and profile.minimum_amount is not None and profile.maximum_amount is not None:
        amount = (profile.minimum_amount + profile.maximum_amount) / 2
    if amount is None:
        return None, None
    factors = {"kg": (1.0, "kg"), "g": (0.001, "kg"), "l": (1.0, "L"), "cl": (0.01, "L"), "ml": (0.001, "L"), "item": (1.0, "item")}
    factor, basis = factors.get(profile.unit or "", (1.0, profile.unit))
    return amount * factor, basis


def packaging_compatibility(left: Product | str, right: Product | str) -> dict[str, Any]:
    left_profile = parse_packaging(left.name if isinstance(left, Product) else left)
    right_profile = parse_packaging(right.name if isinstance(right, Product) else right)
    left_amount, left_basis = _base_amount(left_profile)
    right_amount, right_basis = _base_amount(right_profile)
    compatible = bool(left_basis and left_basis == right_basis)
    ratio = None
    if compatible and left_amount and right_amount:
        ratio = max(left_amount, right_amount) / min(left_amount, right_amount)
    return {
        "compatible": compatible,
        "basis": left_basis if compatible else None,
        "size_ratio": round(ratio, 4) if ratio is not None else None,
        "left": left_profile.to_dict(),
        "right": right_profile.to_dict(),
    }


def normalize_brand(product: Product) -> dict[str, Any]:
    observed = normalize_text(" ".join(filter(None, (product.brand, product.name))))
    aliases = _PRIVATE_LABELS.get(product.store, {})
    observed_tokens = set(tokens(observed))
    for alias, canonical in aliases.items():
        if set(tokens(alias)) <= observed_tokens:
            return {
                "canonical": canonical,
                "private_label": True,
                "observed": product.brand,
                "manufacturer": product.metadata.get("manufacturer"),
                "subbrand": product.metadata.get("subbrand"),
            }
    return {
        "canonical": product.brand.strip() if product.brand else None,
        "private_label": False,
        "observed": product.brand,
        "manufacturer": product.metadata.get("manufacturer"),
        "subbrand": product.metadata.get("subbrand"),
    }


def structured_profile(product: Product) -> SemanticProfile:
    legal_name = str(product.metadata.get("legal_name") or "").strip()
    preparation = str(product.metadata.get("preparation") or "").strip()
    taxonomy = str(product.metadata.get("taxonomy") or "").strip()
    profile = analyze_product_text(
        " ".join(filter(None, (product.name, legal_name, preparation))),
        " ".join(filter(None, (product.category, taxonomy))),
    )
    facets = dict(profile.facets)
    if product.origin and "origin" not in facets:
        facets["origin"] = normalize_text(product.origin).replace(" ", "_")
    attributes = product.metadata.get("semantic_attributes")
    if isinstance(attributes, Mapping):
        for key, value in attributes.items():
            if isinstance(key, str) and isinstance(value, (str, int, float, bool)):
                facets.setdefault(key, str(value))
    concepts = tuple([profile.family] if profile.family else []) + tuple(
        f"{key}:{value}" for key, value in sorted(facets.items())
    )
    return SemanticProfile(profile.family, facets, concepts)


def quality_budget(family: str | None) -> dict[str, float | int]:
    return dict(_QUALITY_BUDGETS.get(family or "", _QUALITY_BUDGETS["default"]))


def evidence_passes_budget(result: Mapping[str, Any], family: str | None) -> bool:
    budget = quality_budget(family)
    return (
        result.get("verdict") != "different"
        and float(result.get("score", 0)) >= float(budget["minimum_score"])
        and len(result.get("uncertain_facets", ())) <= int(budget["maximum_uncertain"])
    )


def product_identity(left: Product, right: Product) -> dict[str, Any]:
    if left.store == right.store and left.id == right.id:
        return {"same": True, "basis": "store_product_id"}
    left_ean = "".join(character for character in str(left.ean or "") if character.isdigit())
    right_ean = "".join(character for character in str(right.ean or "") if character.isdigit())
    if left_ean and right_ean and left_ean.lstrip("0") == right_ean.lstrip("0"):
        return {"same": True, "basis": "ean"}
    return {"same": False, "basis": None}


def relationship(left: Product, right: Product) -> dict[str, Any]:
    identity = product_identity(left, right)
    if identity["same"]:
        return {"relationship": "same_sku", "identity": identity}
    left_profile = structured_profile(left)
    right_profile = structured_profile(right)
    equivalence = {
        **compare_profiles(left_profile, right_profile),
        "left_profile": left_profile.to_dict(),
        "right_profile": right_profile.to_dict(),
    }
    family = equivalence["left_profile"]["family"] or equivalence["right_profile"]["family"]
    if equivalence["verdict"] == "different":
        kind = "different"
    elif equivalence["verdict"] == "equivalent" and evidence_passes_budget(
        equivalence, family
    ):
        kind = "direct_equivalent"
    else:
        kind = "possible_substitute"
    return {
        "relationship": kind,
        "identity": identity,
        "equivalence": equivalence,
        "quality_budget": quality_budget(family),
        "automatic_use_allowed": kind == "direct_equivalent",
        "packaging": packaging_compatibility(left, right),
    }


def _constraint_terms(value: Any) -> set[str]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, Mapping)):
        return set()
    return {normalize_text(str(item)) for item in value if str(item).strip()}


def check_constraints(
    product: Product,
    constraints: Mapping[str, Any] | None,
) -> dict[str, Any]:
    constraints = constraints or {}
    haystack = normalize_text(
        " ".join(
            filter(
                None,
                (product.name, product.category, product.ingredients, product.brand),
            )
        )
    )
    observed = set(tokens(haystack))
    violations: list[str] = []
    for term in _constraint_terms(constraints.get("exclude_terms")):
        if set(tokens(term)) <= observed:
            violations.append(f"excluded term observed: {term}")
    for term in _constraint_terms(constraints.get("require_terms")):
        if not set(tokens(term)) <= observed:
            violations.append(f"required term not observed: {term}")
    allergens = _constraint_terms(constraints.get("allergens"))
    if allergens and not product.ingredients:
        violations.append("allergen safety cannot be verified without ingredients")
    for allergen in allergens:
        if set(tokens(allergen)) <= observed:
            violations.append(f"allergen observed: {allergen}")
    brand = normalize_brand(product)
    allowed_brands = _constraint_terms(constraints.get("allowed_brands"))
    excluded_brands = _constraint_terms(constraints.get("excluded_brands"))
    observed_brand = normalize_text(str(brand.get("canonical") or ""))
    if allowed_brands and observed_brand not in allowed_brands:
        violations.append("brand is outside the explicit allowed list")
    if observed_brand and observed_brand in excluded_brands:
        violations.append(f"excluded brand observed: {observed_brand}")
    diet = normalize_text(str(constraints.get("diet", "")))
    family = structured_profile(product).family
    if diet == "vegan" and family in {
        "meat", "ham", "fish", "seafood", "salmon", "tuna", "milk", "cheese", "yogurt", "eggs"
    }:
        violations.append(f"family {family} is not vegan")
    elif diet == "vegetarian" and family in {
        "meat", "ham", "fish", "seafood", "salmon", "tuna"
    }:
        violations.append(f"family {family} is not vegetarian")
    elif diet in {"vegan", "vegetarian"} and not product.ingredients:
        declared = normalize_text(str(product.metadata.get("diet") or ""))
        if declared != diet and not (diet == "vegetarian" and declared == "vegan"):
            violations.append(f"{diet} status cannot be verified from retailer data")
    return {
        "accepted": not violations,
        "violations": violations,
        "constraints_applied": dict(constraints),
        "source": "explicit_user_constraints_only",
    }


def assess_substitution(
    query: str,
    candidate: Product,
    *,
    intent: str | None = None,
    constraints: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    semantic = assess_query_candidate(query, candidate)
    candidate_profile = structured_profile(candidate)
    if candidate_profile != analyze_product_text(candidate.name, candidate.category):
        query_profile = analyze_query_text(query)
        structured = compare_profiles(query_profile, candidate_profile)
        semantic = {
            **structured,
            "query_profile": query_profile.to_dict(),
            "product_profile": candidate_profile.to_dict(),
        }
    constraint_result = check_constraints(candidate, constraints)
    intent_key = normalize_text(intent or "")
    intent_conflict: str | None = None
    intent_evidence: list[str] = []
    facets = semantic["product_profile"]["facets"]
    observed_words = set(tokens(" ".join((candidate.name, candidate.category or ""))))
    if intent_key in {"freir", "fritura"} and facets.get("oil_use") not in {
        None, "frying"
    }:
        intent_conflict = "candidate is not marked for frying"
    intent_rules = {
        "plancha": ({"filete", "filetes", "lomo", "lomitos", "chuleta"}, {"picada", "guisar", "estofar"}),
        "guiso": ({"dados", "trozos", "guisar", "estofar"}, {"empanado", "empanada"}),
        "bocadillo": ({"lonchas", "loncheado", "filetes", "taco"}, {"picado", "picada"}),
        "pizza": ({"rallado", "rallada", "tiras", "lonchas"}, {"pieza", "entero", "entera"}),
    }
    rule = intent_rules.get(intent_key.removeprefix("para "))
    intent_review = False
    if rule:
        preferred, conflicting = rule
        if observed_words & conflicting:
            intent_conflict = f"candidate format conflicts with intent {intent_key}"
        elif observed_words & preferred:
            intent_evidence.append(f"candidate format supports intent {intent_key}")
        else:
            intent_review = True
            intent_evidence.append(f"candidate format does not prove suitability for {intent_key}")
    if semantic["verdict"] == "different" or not constraint_result["accepted"] or intent_conflict:
        verdict = "rejected"
    elif semantic["verdict"] == "equivalent" and not semantic["uncertain_facets"] and not intent_review:
        verdict = "direct_substitute"
    else:
        verdict = "review_substitute"
    return {
        "verdict": verdict,
        "direction": "query_to_candidate",
        "query": query,
        "intent": intent,
        "semantic": semantic,
        "constraints": constraint_result,
        "intent_conflict": intent_conflict,
        "intent_evidence": intent_evidence,
        "packaging": parse_packaging(candidate.name).to_dict(),
        "brand": normalize_brand(candidate),
    }


def ontology_info() -> dict[str, Any]:
    return {
        "ontology_version": ONTOLOGY_VERSION,
        "alias_data_version": ALIAS_DATA_VERSION,
        "quality_budgets": {key: dict(value) for key, value in _QUALITY_BUDGETS.items()},
        "quality_budget_version": QUALITY_BUDGET_VERSION,
        "relationship_levels": [
            "same_sku",
            "direct_equivalent",
            "possible_substitute",
            "different",
        ],
        "constraint_policy": "explicit_only_never_inferred_from_purchase_history",
        "profile_cache": semantic_profile_cache_info(),
    }


__all__ = [
    "ALIAS_DATA_VERSION",
    "ONTOLOGY_VERSION",
    "QUALITY_BUDGET_VERSION",
    "assess_substitution",
    "check_constraints",
    "evidence_passes_budget",
    "normalize_brand",
    "ontology_info",
    "packaging_compatibility",
    "parse_packaging",
    "product_identity",
    "quality_budget",
    "relationship",
    "structured_profile",
]
