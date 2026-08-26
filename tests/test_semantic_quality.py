from __future__ import annotations

from decimal import Decimal

from open_grocery_mcp.models import Product
from open_grocery_mcp.quality_audit import audit_corpus
from open_grocery_mcp.semantic_quality import (
    assess_substitution,
    check_constraints,
    normalize_brand,
    ontology_info,
    packaging_compatibility,
    parse_packaging,
    relationship,
    structured_profile,
)


def p(product_id: str, name: str, **kwargs: object) -> Product:
    return Product(
        store=str(kwargs.pop("store", "mercadona")),
        id=product_id,
        name=name,
        price=Decimal("1"),
        **kwargs,
    )


def test_packaging_handles_multipack_drained_and_variable_weight() -> None:
    multi = parse_packaging("Atún pack 6x70 g peso escurrido 56 g")
    variable = parse_packaging("Empanada 570 g aproximadamente")

    assert multi.dimension == "mass"
    assert multi.amount == 420
    assert multi.pack_count == 6
    assert multi.drained_amount == 56
    assert variable.variable_weight is True
    ranged = parse_packaging("Pieza de 500-700 g peso variable")
    compatible = packaging_compatibility("Pack 2x250 g", "Botella 1 L")
    assert ranged.minimum_amount == 500
    assert ranged.maximum_amount == 700
    assert compatible["compatible"] is False


def test_brand_normalization_keeps_identity_separate_from_equivalence() -> None:
    own = p("own", "Leche entera Hacendado", brand="Hacendado")
    same_ean = p("other", "Leche entera otra marca", store="eroski", ean="123")
    same_ean_2 = p("third", "Leche entera", store="froiz", ean="123")

    assert normalize_brand(own)["private_label"] is True
    assert relationship(same_ean, same_ean_2)["relationship"] == "same_sku"
    assert relationship(own, same_ean)["relationship"] != "same_sku"


def test_structured_metadata_adds_evidence_without_overwriting_name() -> None:
    product = p(
        "meta",
        "Queso tierno",
        origin="Galicia",
        metadata={"semantic_attributes": {"cheese_milk": "cow"}},
    )

    profile = structured_profile(product)

    assert profile.facets["origin"] == "galicia"
    assert profile.facets["cheese_milk"] == "cow"
    other = p("meta-2", "Queso tierno", metadata={"semantic_attributes": {"cheese_milk": "goat"}})
    assert relationship(product, other)["relationship"] == "different"


def test_substitution_is_directional_and_honours_explicit_constraints() -> None:
    generic = p("generic", "Harina de trigo")
    strong = p("strong", "Harina de trigo de fuerza")
    allergen = p("allergen", "Galletas de avena", ingredients="avena, leche, trigo")

    broad = assess_substitution("harina de trigo", strong)
    narrow = assess_substitution("harina de trigo de fuerza", generic)
    constrained = check_constraints(allergen, {"allergens": ["leche"]})

    assert broad["verdict"] in {"direct_substitute", "review_substitute"}
    assert narrow["verdict"] == "rejected"
    assert constrained["accepted"] is False
    assert constrained["source"] == "explicit_user_constraints_only"


def test_intent_and_unverifiable_safety_constraints_are_visible() -> None:
    fillets = p("fillets", "Lomo de cerdo filetes")
    unknown_ingredients = p("unknown", "Galletas de avena")

    intent = assess_substitution("lomo de cerdo", fillets, intent="para plancha")
    safety = check_constraints(unknown_ingredients, {"allergens": ["leche"]})

    assert intent["intent_evidence"]
    assert safety["accepted"] is False
    assert "cannot be verified" in safety["violations"][0]


def test_ontology_and_anonymized_corpus_are_observable_and_green() -> None:
    status = ontology_info()
    audit = audit_corpus()

    assert status["ontology_version"]
    assert status["quality_budgets"]["baby_food"]["maximum_uncertain"] == 0
    assert audit["total_cases"] >= 20
    assert audit["failed"] == 0
    assert audit["pass_rate"] == 1
    assert audit["stores_represented"] == ["eroski", "froiz", "gadis", "mercadona"]
    assert audit["confusion_matrix"]["false_positive"] == 0
