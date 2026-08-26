from __future__ import annotations

from decimal import Decimal

from open_grocery_mcp import server
from open_grocery_mcp.equivalence import (
    analyze_product_text,
    assess_product_equivalence,
    assess_query_candidate,
    semantic_query_expansions,
)
from open_grocery_mcp.matching import score_product
from open_grocery_mcp.models import Product, StoreInfo
from open_grocery_mcp.offer_evaluation import evaluate_offer_value
from open_grocery_mcp.providers.base import GroceryProvider


def p(product_id: str, name: str, *, category: str | None = None, offer: bool = False) -> Product:
    metadata = (
        {"promotion": {"current_price": 2, "previous_price": 3}}
        if offer
        else {}
    )
    return Product(
        store="alpha",
        id=product_id,
        name=name,
        category=category,
        price=Decimal("2"),
        price_per_unit=Decimal("10"),
        unit="kg",
        metadata=metadata,
    )


def verdict(left: str, right: str) -> dict[str, object]:
    return assess_product_equivalence(p("left", left), p("right", right))


def test_designation_and_galician_aliases_share_a_cheese_identity() -> None:
    result = verdict("Queso DOP Arzúa-Ulloa", "Queixo tierno A. Ulloa")

    assert result["verdict"] in {"equivalent", "compatible"}
    assert result["left_profile"]["facets"]["designation"] == "arzua_ulloa"
    assert result["right_profile"]["facets"]["designation"] == "arzua_ulloa"


def test_designation_query_rejects_generic_cheese() -> None:
    result = assess_query_candidate("queso de Arzúa", p("generic", "Queso tierno de vaca"))

    assert result["verdict"] == "different"
    assert result["conflicts"]["designation"] == ["arzua_ulloa", "not_observed"]


def test_arzuaga_wine_is_not_arzua_cheese() -> None:
    left = p("left", "Queso Arzúa-Ulloa")
    right = p("right", "Vino Arzuaga crianza", category="Vino")

    result = assess_product_equivalence(left, right)

    assert result["verdict"] == "different"
    assert result["conflicts"]["family"] == ["cheese", "wine"]


def test_ham_blocks_are_compatible_but_slices_and_shoulder_are_not() -> None:
    assert verdict(
        "Taco de jamón serrano",
        "Centro de jamón serrano deshuesado",
    )["verdict"] == "equivalent"
    sliced = verdict("Taco de jamón serrano", "Jamón serrano en lonchas")
    shoulder = verdict("Taco de jamón serrano", "Taco de paleta curada")
    assert sliced["conflicts"]["ham_format"] == ["solid_block", "sliced"]
    assert shoulder["conflicts"]["ham_source"] == ["ham", "shoulder"]
    small_pack = verdict(
        "Taco de jamón serrano",
        "Jamón serrano reserva 110 g",
    )
    assert small_pack["conflicts"]["ham_format"] == ["solid_block", "sliced"]


def test_known_food_variants_create_hard_conflicts() -> None:
    cases = (
        ("Arroz largo seco", "Arroz redondo seco", "rice_variety"),
        ("Arroz redondo 1 kg", "Vasito de arroz redondo cocido", "preparation"),
        ("Arroz largo", "Arroz largo vaporizado", "rice_treatment"),
        ("Yogur griego natural", "Yogur desnatado natural", "yogurt_style"),
        ("Yogur griego natural", "Yogur griego de fresa", "yogurt_flavor"),
        ("Chocolate negro 99% cacao", "Chocolate negro 55% cacao", "cacao_band"),
        ("Chocolate negro 70% cacao", "Chocolate negro 55% cacao", "cacao_band"),
        (
            "Chocolate con almendras 85% cacao",
            "Chocolate negro 85% cacao",
            "additions",
        ),
        ("Chocolate Milka Oreo", "Chocolate a la taza", "chocolate_form"),
        ("Ventresca de atún", "Atún claro", "tuna_cut"),
        ("Salmón ahumado", "Filete de salmón fresco", "preparation"),
        ("Detergente gel", "Detergente cápsulas", "detergent_form"),
        ("Leche semidesnatada", "Leche desnatada", "milk_fat"),
        ("Detergente gel para ropa", "Lavavajillas gel", "detergent_use"),
        ("Queso de cabra curado", "Queso de vaca curado", "cheese_milk"),
        ("Queso rallado Grana Padano", "Queso rallado Mozzarella", "cheese_variety"),
        ("Queso fundido en lonchas", "Queso en lonchas", "cheese_processing"),
        ("Queso ligero en lonchas", "Queso en lonchas", "cheese_fat"),
        ("Chocolate sin azúcares añadidos", "Chocolate con leche", "no_added_sugar"),
        ("Lentejas secas", "Lentejas cocidas en tarro", "preparation"),
        ("Pan de molde integral", "Pan de molde blanco", "bread_grain"),
        ("Pan de molde", "Baguette", "bread_form"),
        ("Pan rallado", "Pan de masa madre", "bread_form"),
        ("Huevos de codorniz", "Huevos de gallina", "egg_bird"),
        ("Vino tinto", "Vino blanco", "wine_color"),
        ("Filetes de lomo de cerdo", "Filetes de lomo de ternera", "meat_species"),
        (
            "Filetes de lomo de cerdo adobado",
            "Filetes de lomo de cerdo fresco",
            "meat_preparation",
        ),
        (
            "Pechuga de pavo cocida en lonchas",
            "Filetes de pechuga de pavo",
            "meat_format",
        ),
        ("Carne picada mixta", "Carne picada de vacuno", "meat_species"),
        (
            "Pechugas de pollo enteras congeladas",
            "Pechugas de pollo enteras",
            "preservation",
        ),
        (
            "Costilla de cerdo con hueso",
            "Costilla de cerdo deshuesada",
            "bone",
        ),
        ("Cinta de lomo de cerdo", "Cinta de lomo de cerdo rellena", "meat_preparation"),
        ("Lomo de cerdo Duroc", "Lomo de cerdo ibérico", "meat_breed"),
        ("Lomo ibérico de bellota", "Lomo ibérico de cebo de campo", "meat_grade"),
        ("Filete de merluza", "Filete de bacalao", "fish_species"),
        ("Filete de merluza", "Lomo de merluza", "fish_cut"),
        ("Palitos de bacalao desalado", "Bacalao desalado menú", "fish_cut"),
        ("Tajadas de bacalao desalado", "Lomos de bacalao desalado", "fish_cut"),
        ("Bacalao salado", "Bacalao desalado", "preparation"),
        ("Merluza fresca", "Merluza congelada", "preservation"),
        ("Filete de bacalao con piel", "Filete de bacalao sin piel", "skin"),
        ("Dorada de acuicultura", "Dorada salvaje", "production"),
        ("Pulpo cocido", "Calamar cocido", "seafood_species"),
        ("Gamba pelada", "Gamba con cáscara", "seafood_form"),
        ("Pulpo cocido", "Pulpo crudo", "preparation"),
        ("Gamba pequeña", "Gamba grande", "size_band"),
        ("Rodaja de bonito", "Bonito del norte en aceite de oliva", "preparation"),
        ("Manzana Golden", "Naranja de mesa", "fruit_species"),
        ("Manzana Golden", "Manzana Granny Smith", "produce_variety"),
        ("Manzana fresca", "Manzana congelada", "preservation"),
        ("Manzana entera", "Manzana cortada", "produce_form"),
        ("Tomate cherry", "Patata nueva", "vegetable_species"),
        ("Tomate cherry", "Tomate pera", "produce_variety"),
        ("Lechuga Iceberg unidad", "Lechuga Iceberg bolsa 250 g", "sale_basis"),
        (
            "Patata calibre 60/80mm",
            "Patata calibre 75/80mm",
            "size_band",
        ),
        ("Manzana de Galicia", "Manzana de Portugal", "origin"),
        ("Agua mineral con gas", "Agua mineral sin gas", "carbonation"),
        ("Agua mineral", "Agua de coco", "water_type"),
        ("Refresco de cola", "Refresco de naranja", "beverage_flavor"),
        ("Refresco de cola lima", "Refresco de cola vainilla", "flavor_variant"),
        ("Refresco de cola zero", "Refresco de cola normal", "sugar_profile"),
        (
            "Refresco de cola zero cafeína",
            "Refresco de cola zero azúcar",
            "caffeine_profile",
        ),
        ("Refresco de cola lata", "Refresco de cola botella", "beverage_container"),
        ("Refresco de cola concentrado", "Refresco de cola", "concentration"),
        (
            "Refresco de cola pack 12x33 cl",
            "Refresco de cola pack 24x33 cl",
            "beverage_package",
        ),
        ("Zumo de naranja", "Zumo de manzana", "juice_fruit"),
        ("Zumo de naranja con pulpa", "Zumo de naranja sin pulpa", "pulp"),
        ("Zumo exprimido de naranja", "Néctar de naranja", "juice_style"),
        ("Bebida de avena", "Bebida de almendras", "plant_base"),
        ("Bebida de avena barista", "Bebida de avena normal", "plant_style"),
        ("Cerveza IPA", "Cerveza lager", "beer_style"),
        ("Cerveza 0,0", "Cerveza 5% vol", "alcohol_profile"),
        ("Cerveza lager 5% vol", "Cerveza lager 6% vol", "alcohol_strength"),
        ("Whisky 40% vol", "Ron 40% vol", "spirit_type"),
    )

    for left, right, facet in cases:
        result = verdict(left, right)
        assert result["verdict"] == "different", (left, right, result)
        assert facet in result["conflicts"]


def test_ingredient_mentions_do_not_turn_pet_food_into_salmon() -> None:
    pet = p("pet", "Comida para gato adulto con salmón")

    result = verdict("Filete de salmón", pet.name)
    score, rationale = score_product("salmón", pet)

    assert result["conflicts"]["family"] == ["salmon", "pet_food"]
    assert score == 0
    assert any("semantic conflict" in reason for reason in rationale)


def test_ingredient_mentions_do_not_reclassify_prepared_foods() -> None:
    cases = {
        "Macarrones con atún": "prepared_meal",
        "Empanadillas de atún": "prepared_meal",
        "Ensalada de arroz": "prepared_meal",
        "Barritas de cereales con chocolate": "cereal_product",
        "Galletas con chocolate": "cookie",
        "Sirope de chocolate": "sauce",
    }

    for name, expected in cases.items():
        assert analyze_product_text(name).family == expected


def test_head_noun_beats_secondary_ingredient_mentions() -> None:
    cases = {
        "Chocolate con leche": "chocolate",
        "Atún claro en aceite de oliva": "tuna",
        "Café con leche en cápsulas": "coffee",
        "Pan de leche": "bread",
        "Queso elaborado con leche de vaca": "cheese",
        "Pasta fresca rellena de queso": "pasta",
        "Arroz con leche": "prepared_meal",
        "Filetes de atún en aceite de oliva": "tuna",
        "Palitos de pan con queso": "bread",
        "6 Panes de leche": "bread",
        "Huevos sorpresa de chocolate": "chocolate",
        "Helado sabor huevo de chocolate": "frozen_dessert",
        "Harina de maíz PAN": "flour",
    }

    for name, expected in cases.items():
        assert analyze_product_text(name).family == expected


def test_common_family_names_are_recognized() -> None:
    cases = {
        "Bífidus natural": "yogurt",
        "Actimel de fresa": "yogurt",
        "Espirales con vegetales": "pasta",
        "Tiburón nº 4": "pasta",
        "Spaguetti 1 kg": "pasta",
        "Farfalle con vegetales": "pasta",
        "Baguette 220 g": "bread",
        "Hogaza de masa madre": "bread",
    }

    for name, expected in cases.items():
        assert analyze_product_text(name).family == expected


def test_chocolate_percentage_ignores_zero_percent_sugar_claims() -> None:
    sugar_free = analyze_product_text("Chocolate con leche 0% azúcares añadidos")
    cacao = analyze_product_text("Chocolate con leche intenso 40%")

    assert "cacao_band" not in sugar_free.facets
    assert cacao.facets["cacao_band"] == "low_cacao"


def test_prepared_meal_profiles_capture_type_ingredient_format_and_storage() -> None:
    pizza = analyze_product_text("Pizza jamón y queso congelada")
    croquettes = analyze_product_text(
        "Croquetas de jamón ibérico ultracongeladas"
    )
    salad = analyze_product_text("Froiz ensalada 4 estaciones")
    soup = analyze_product_text("Sopa de letras deshidratada en sobre")
    inferred_dry_soup = analyze_product_text("Sopa Knorr Minestrone 59 g")
    leafy_salad = analyze_product_text("Ensalada Gourmet Eroski bolsa 175 g")

    assert pizza.family == "prepared_meal"
    assert pizza.facets == {
        "meal_type": "pizza",
        "preservation": "frozen",
        "meal_format": "complete_dish",
        "meal_preparation": "heat_or_cook",
        "meal_variant": "ham_cheese",
        "main_ingredient": "ham_cheese",
    }
    assert croquettes.facets["meal_type"] == "croquette"
    assert croquettes.facets["main_ingredient"] == "iberian_ham"
    assert croquettes.facets["preservation"] == "frozen"
    assert salad.facets["meal_type"] == "salad"
    assert salad.facets["meal_format"] == "leafy_mix"
    assert salad.facets["meal_preparation"] == "ready_to_eat"
    assert soup.facets["meal_type"] == "soup"
    assert soup.facets["meal_format"] == "dry_mix"
    assert soup.facets["meal_preparation"] == "needs_reconstitution"
    assert inferred_dry_soup.facets["meal_format"] == "dry_mix"
    assert inferred_dry_soup.facets["preservation"] == "dried"
    assert leafy_salad.facets["meal_format"] == "leafy_mix"


def test_ready_lasagna_is_not_dry_lasagna_pasta() -> None:
    ready_names = (
        "Lasaña de atún refrigerada",
        "Lasaña vegetal",
        "Lasaña boloñesa congelada",
        "Lasaña de espinacas y queso",
    )
    dry_names = (
        "Placas para lasaña",
        "Hojas de lasaña",
        "Pasta Gallo lasaña al huevo",
        "Lasaña fácil 18 placas caja",
    )

    for name in ready_names:
        assert analyze_product_text(name).family == "prepared_meal"
        assert analyze_product_text(name).facets["meal_type"] == "lasagna"
    for name in dry_names:
        assert analyze_product_text(name).family == "pasta"


def test_prepared_meal_components_and_non_food_mentions_are_excluded() -> None:
    cases = {
        "Masa para empanada": "bakery_component",
        "Base para pizza": "bakery_component",
        "Obleas para empanadillas": "bakery_component",
        "Crema facial hidratante": "skincare",
        "Espinacas a la crema ultracongeladas": "vegetable",
        "Espirales con vegetales": "pasta",
    }

    for name, expected in cases.items():
        assert analyze_product_text(name).family == expected


def test_prepared_meal_equivalence_rejects_incompatible_subtypes() -> None:
    cases = (
        ("Croquetas de jamón", "Croquetas de bacalao", "main_ingredient"),
        ("Pizza de jamón congelada", "Pizza de jamón refrigerada", "preservation"),
        ("Paella de marisco", "Preparado para paella de marisco", "meal_type"),
        ("Sopa de pollo", "Crema de pollo", "meal_type"),
        ("Ensalada César", "Ensalada de pasta", "meal_variant"),
    )

    for left, right, conflict in cases:
        result = verdict(left, right)
        assert result["verdict"] == "different"
        assert conflict in result["conflicts"]


def test_explicit_prepared_meal_queries_require_matching_evidence() -> None:
    cases = (
        ("croquetas de jamón", "Croquetas caseras", "main_ingredient"),
        ("pizza 4 quesos", "Pizza de jamón y queso", "meal_variant"),
        ("paella de marisco", "Caldo para paella", "meal_type"),
        ("ensalada César", "Ensalada de pasta", "meal_variant"),
    )

    for query, name, conflict in cases:
        result = assess_query_candidate(query, p("candidate", name))
        assert result["verdict"] == "different"
        assert conflict in result["conflicts"]


def test_meat_profiles_capture_species_cut_treatment_and_format() -> None:
    loin = analyze_product_text("Lomo de cerdo trozo")
    breast = analyze_product_text(
        "Filetes de pechuga de pollo marinadas empanadas sin gluten"
    )
    mince = analyze_product_text("Picada mixta de vacuno y cerdo")
    deli = analyze_product_text("Pechuga de pavo cocida en finas lonchas")

    assert loin.family == "meat"
    assert loin.facets["meat_species"] == "pork"
    assert loin.facets["meat_cut"] == "loin"
    assert loin.facets["meat_format"] == "chunks"
    assert breast.facets["meat_species"] == "chicken"
    assert breast.facets["meat_cut"] == "breast"
    assert breast.facets["meat_preparation"] == "breaded+marinated"
    assert breast.facets["meat_format"] == "fillets"
    assert mince.facets["meat_species"] == "mixed"
    assert mince.facets["meat_format"] == "ground"
    assert deli.facets["meat_species"] == "turkey"
    assert deli.facets["meat_preparation"] == "cooked"
    assert deli.facets["meat_format"] == "deli_slices"


def test_meat_ingredient_false_positives_keep_their_real_family() -> None:
    cases = {
        "Lejía CONEJO garrafa 2 l": "bleach",
        "Comida conejos enanos 1 kg": "pet_food",
        "Especias para cordero": "seasoning",
        "Caldo de pollo": "prepared_meal",
        "Hamburguesa vegetal": "plant_based",
        "Lomo de bacalao desalado": "fish",
        "Cortezas de cerdo fritas": "snack",
        "Manteca de cerdo ibérico": "cooking_fat",
    }

    for name, expected in cases.items():
        assert analyze_product_text(name).family == expected


def test_explicit_meat_query_rejects_missing_or_conflicting_facets() -> None:
    cases = (
        ("lomo de cerdo", "Lomo de bacalao desalado", "family"),
        ("cordero", "Burger meat de Angus", "meat_species"),
        ("lomo de cerdo", "Costilla de cerdo", "meat_cut"),
        ("carne picada", "Carne de ternera para guisar", "meat_format"),
    )

    for query, name, conflict in cases:
        result = assess_query_candidate(query, p("candidate", name))
        assert result["verdict"] == "different"
        assert conflict in result["conflicts"]


def test_fish_profiles_capture_species_cut_processing_and_production() -> None:
    hake = analyze_product_text(
        "Filetes de merluza sin piel Hacendado ultracongelados"
    )
    cod = analyze_product_text("Lomos de bacalao desalado")
    sardine = analyze_product_text("Sardinas en aceite de oliva")
    bream = analyze_product_text("Dorada de acuicultura pieza 500 g")
    sticks = analyze_product_text("Palitos de bacalao desalado")
    pieces = analyze_product_text("Bacalao desalado menú")

    assert hake.family == "fish"
    assert hake.facets["fish_species"] == "hake"
    assert hake.facets["fish_cut"] == "fillet"
    assert hake.facets["skin"] == "skinless"
    assert hake.facets["preservation"] == "frozen"
    assert cod.facets["fish_species"] == "cod"
    assert cod.facets["fish_cut"] == "loin"
    assert cod.facets["preparation"] == "desalted"
    assert sardine.facets["preparation"] == "canned"
    assert sardine.facets["preserving_medium"] == "olive_oil"
    assert bream.facets["fish_species"] == "sea_bream"
    assert bream.facets["fish_cut"] == "whole"
    assert bream.facets["production"] == "farmed"
    assert sticks.facets["fish_cut"] == "sticks"
    assert pieces.facets["fish_cut"] == "pieces"


def test_seafood_profiles_capture_species_form_processing_and_size() -> None:
    octopus = analyze_product_text("Pulpo cocido troceado")
    shrimp = analyze_product_text("Gamba pelada cruda mediana ultracongelada")
    mussel = analyze_product_text("Mejillones en escabeche")

    assert octopus.family == "seafood"
    assert octopus.facets["seafood_species"] == "octopus"
    assert octopus.facets["seafood_form"] == "chopped"
    assert octopus.facets["preparation"] == "cooked"
    assert shrimp.facets["seafood_species"] == "shrimp"
    assert shrimp.facets["seafood_form"] == "peeled"
    assert shrimp.facets["preservation"] == "frozen"
    assert shrimp.facets["size_band"] == "medium"
    assert mussel.facets["seafood_species"] == "mussel"
    assert mussel.facets["seafood_form"] == "meat_only"
    assert mussel.facets["preparation"] == "canned"
    assert mussel.facets["preserving_medium"] == "pickled"


def test_aquatic_ingredient_false_positives_keep_their_real_family() -> None:
    cases = {
        "Galletas María dorada": "cookie",
        "Salteado de gambas y espárragos": "prepared_meal",
        "Alimento gato con salmón y gambas": "pet_food",
        "Salsa de mejillones": "sauce",
    }

    for name, expected in cases.items():
        assert analyze_product_text(name).family == expected


def test_explicit_fish_and_seafood_queries_reject_conflicts() -> None:
    cases = (
        ("dorada", "Galletas María dorada", "family"),
        ("gamba pelada", "Gamba entera", "seafood_form"),
        ("bacalao desalado", "Bacalao salado", "preparation"),
        ("pulpo cocido", "Pulpo crudo", "preparation"),
    )

    for query, name, conflict in cases:
        result = assess_query_candidate(query, p("candidate", name))
        assert result["verdict"] == "different"
        assert conflict in result["conflicts"]


def test_produce_profiles_capture_species_variety_state_form_and_sale_basis() -> None:
    apple = analyze_product_text("Manzana Golden, bolsa 1,5 kg")
    orange = analyze_product_text(
        "Naranja de zumo de España, al peso, compra mínima 1 kg"
    )
    potato = analyze_product_text(
        "Patata granel freír-cocer calibre 60/80mm kilo"
    )
    onion = analyze_product_text("Cebolla troceada ultracongelada")
    lettuce = analyze_product_text("Lechuga Iceberg unidad")
    prepared_potato = analyze_product_text("Patata Golden Long McCain 1 kg")

    assert apple.family == "fruit"
    assert apple.facets["fruit_species"] == "apple"
    assert apple.facets["produce_variety"] == "golden"
    assert apple.facets["preservation"] == "fresh"
    assert apple.facets["produce_form"] == "whole"
    assert apple.facets["sale_basis"] == "package"
    assert orange.facets["fruit_species"] == "orange"
    assert orange.facets["produce_use"] == "juice"
    assert orange.facets["origin"] == "spain"
    assert orange.facets["sale_basis"] == "weight"
    assert potato.family == "vegetable"
    assert potato.facets["vegetable_species"] == "potato"
    assert potato.facets["produce_use"] == "multi_purpose"
    assert potato.facets["size_band"] == "60-80mm"
    assert onion.facets["preservation"] == "frozen"
    assert onion.facets["produce_form"] == "cut"
    assert lettuce.facets["produce_variety"] == "iceberg"
    assert lettuce.facets["sale_basis"] == "item"
    assert prepared_potato.facets["preparation"] == "pre_fried"
    assert prepared_potato.facets["preservation"] == "frozen"
    assert prepared_potato.facets["produce_form"] == "processed"


def test_mixed_and_processed_produce_remain_distinguishable() -> None:
    mixed = analyze_product_text("Dúo frutas fresa y plátano congeladas")
    fried = analyze_product_text("Tomate frito, frasco 550 g")
    preserved = analyze_product_text("Tomate triturado, lata 400 g")
    fresh = analyze_product_text("Tomate rosa al peso")

    assert mixed.family == "fruit"
    assert mixed.facets["fruit_species"] == "mixed"
    assert mixed.facets["preservation"] == "frozen"
    assert mixed.facets["produce_form"] == "mixed"
    assert fried.family == "sauce"
    assert preserved.family == "vegetable"
    assert preserved.facets["preservation"] == "canned"
    assert preserved.facets["produce_form"] == "crushed"
    assert verdict("Tomate frito, frasco 550 g", "Tomate rosa al peso")[
        "verdict"
    ] == "different"
    assert fresh.facets["preservation"] == "fresh"


def test_produce_ingredient_false_positives_keep_their_real_family() -> None:
    cases = {
        "Zumo de manzana": "juice",
        "Smoothie de fresa y plátano": "juice",
        "Yogur sabor fresa": "yogurt",
        "Mermelada de naranja": "fruit_spread",
        "Bizcocho de manzana": "dessert",
        "Tortilla de patata y cebolla": "prepared_meal",
        "Patatas fritas clásicas": "snack",
        "Patatas LAY'S GOURMET": "snack",
        "Patatas sabor jamón RUFFLES": "snack",
        "Patatas bravas con mayonesa y salsa brava": "prepared_meal",
        "Crema de calabacín": "prepared_meal",
        "Algas deshidratadas lechuga de mar": "seaweed",
        "Ambientador aroma fresa": "household_freshener",
    }

    for name, expected in cases.items():
        assert analyze_product_text(name).family == expected


def test_explicit_produce_queries_reject_conflicts_or_missing_evidence() -> None:
    cases = (
        ("manzana Golden", "Manzana Granny Smith", "produce_variety"),
        ("tomate cherry", "Tomate pera", "produce_variety"),
        ("cebolla congelada", "Cebolla dulce fresca", "preservation"),
        ("lechuga unidad", "Lechuga Iceberg bolsa 250 g", "sale_basis"),
        ("manzana de Galicia", "Manzana Golden", "origin"),
        ("patata calibre 60/80mm", "Patata calibre 75/80mm", "size_band"),
    )

    for query, name, conflict in cases:
        result = assess_query_candidate(query, p("candidate", name))
        assert result["verdict"] == "different"
        assert conflict in result["conflicts"]


def test_non_alcoholic_beverage_profiles_capture_kind_flavor_and_format() -> None:
    water = analyze_product_text("Agua mineral con gas botella 1,5 l")
    cola = analyze_product_text(
        "Refresco de cola zero azúcar zero cafeína pack 12x33 cl"
    )
    juice = analyze_product_text(
        "Zumo exprimido de naranja sin pulpa botella 1 l"
    )
    plant = analyze_product_text(
        "Bebida de avena barista sin azúcar con calcio brik 1 l"
    )

    assert water.family == "water"
    assert water.facets["water_type"] == "mineral"
    assert water.facets["carbonation"] == "sparkling"
    assert water.facets["beverage_container"] == "bottle"
    assert cola.family == "soft_drink"
    assert cola.facets["beverage_flavor"] == "cola"
    assert cola.facets["sugar_profile"] == "sugar_free"
    assert cola.facets["caffeine_profile"] == "caffeine_free"
    assert cola.facets["beverage_package"] == "multipack_12"
    assert juice.family == "juice"
    assert juice.facets["juice_fruit"] == "orange"
    assert juice.facets["juice_style"] == "juice"
    assert juice.facets["preparation"] == "squeezed"
    assert juice.facets["pulp"] == "without_pulp"
    assert juice.facets["concentration"] == "not_from_concentrate"
    assert plant.family == "plant_drink"
    assert plant.facets["plant_base"] == "oat"
    assert plant.facets["plant_style"] == "barista"
    assert plant.facets["no_added_sugar"] == "yes"
    assert plant.facets["fortification"] == "calcium"


def test_regulated_beverage_profiles_are_modeled_without_live_automation() -> None:
    beer = analyze_product_text("Cerveza IPA 0,0 lata 33 cl")
    spirit = analyze_product_text("Whisky escocés 40% vol botella 70 cl")
    cider = analyze_product_text("Sidra natural 5% vol botella 70 cl")
    wine = analyze_product_text("Vino tinto 13% vol botella 75 cl")

    assert beer.family == "beer"
    assert beer.facets["beer_style"] == "ipa"
    assert beer.facets["alcohol_profile"] == "alcohol_free"
    assert spirit.family == "spirit"
    assert spirit.facets["spirit_type"] == "whisky"
    assert spirit.facets["alcohol_strength"] == "40"
    assert cider.family == "cider"
    assert cider.facets["cider_style"] == "natural"
    assert wine.family == "wine"
    assert wine.facets["wine_color"] == "red"
    assert wine.facets["alcohol_strength"] == "13"


def test_beverage_ingredient_and_non_food_false_positives_keep_their_family() -> None:
    cases = {
        "Agua destilada para plancha": "household_cleaner",
        "Agua oxigenada desinfectante": "personal_care",
        "Agua de colonia perfumada": "personal_care",
        "Cola blanca adhesiva": "household_supplies",
        "Gominolas sabor cola": "confectionery",
        "Bombones rellenos de licor": "confectionery",
        "Cacao soluble COLA CAO Original": "chocolate",
        "Pollo a la cerveza": "meat",
        "Salsa de whisky": "sauce",
        "Champú de cerveza": "shampoo",
        "Arroz con leche": "prepared_meal",
        "Leche con arroz": "milk",
    }

    for name, expected in cases.items():
        assert analyze_product_text(name).family == expected


def test_explicit_beverage_queries_reject_conflicts_or_missing_evidence() -> None:
    cases = (
        ("agua con gas", "Agua mineral sin gas", "carbonation"),
        ("refresco cola zero", "Refresco cola normal", "sugar_profile"),
        ("zumo naranja con pulpa", "Zumo naranja sin pulpa", "pulp"),
        ("bebida avena barista", "Bebida de avena normal", "plant_style"),
        ("bebida de almendra sin azúcar", "Bebida de almendra", "no_added_sugar"),
        (
            "refresco cola pack 12x33 cl",
            "Refresco cola pack 24x33 cl",
            "beverage_package",
        ),
    )

    for query, name, conflict in cases:
        result = assess_query_candidate(query, p("candidate", name))
        assert result["verdict"] == "different"
        assert conflict in result["conflicts"]


def test_query_expansion_is_concept_driven_and_bounded() -> None:
    cheese = semantic_query_expansions("queso de Arzúa")
    ham = semantic_query_expansions("taco de jamón serrano")

    assert "ulloa" in cheese
    assert "a ulloa" not in cheese
    assert "jamon serrano centro" in ham
    assert "jamon serrano deshuesado" in ham
    assert semantic_query_expansions("lomo de cerdo") == []
    assert semantic_query_expansions("merluza") == []
    assert semantic_query_expansions("gamba") == []
    assert len(cheese) <= 12
    assert analyze_product_text("ulloa").family == "cheese"
    assert "dentifrico" in semantic_query_expansions("pasta dental")
    assert "gel de bano" in semantic_query_expansions("gel de ducha")
    assert "tarrito bebe" in semantic_query_expansions("potito")
    assert "ajo granulado" in semantic_query_expansions("especias")


class OfferProvider(GroceryProvider):
    info = StoreInfo(
        key="alpha",
        label="Alpha",
        country="ES",
        languages=("es",),
        capabilities=("search",),
    )

    def search(self, query: str, **_: object) -> list[Product]:
        del query
        return [
            p("offer", "Ventresca de atún en aceite de oliva 100 g", offer=True),
            p("cheap", "Atún claro en aceite de oliva 100 g"),
        ]


def test_offer_filter_does_not_compare_different_cuts() -> None:
    result = evaluate_offer_value(OfferProvider(), query="atun")

    assert result["counts"]["unverified"] == 1
    assert result["counts"]["not_worthwhile"] == 0


class MeatOfferProvider(GroceryProvider):
    info = StoreInfo(
        key="alpha",
        label="Alpha",
        country="ES",
        languages=("es",),
        capabilities=("search",),
    )

    def search(self, query: str, **_: object) -> list[Product]:
        del query
        return [
            p("offer", "Filetes de lomo de cerdo adobado 500 g", offer=True),
            p("cheap", "Filetes de lomo de cerdo fresco 500 g"),
        ]


def test_offer_filter_does_not_compare_different_meat_treatments() -> None:
    result = evaluate_offer_value(MeatOfferProvider(), query="lomo de cerdo")

    assert result["counts"]["unverified"] == 1
    assert result["counts"]["not_worthwhile"] == 0


class FishOfferProvider(GroceryProvider):
    info = StoreInfo(
        key="alpha",
        label="Alpha",
        country="ES",
        languages=("es",),
        capabilities=("search",),
    )

    def search(self, query: str, **_: object) -> list[Product]:
        del query
        return [
            p("offer", "Lomos de bacalao desalado 400 g", offer=True),
            p("cheap", "Lomos de bacalao salado 400 g"),
        ]


def test_offer_filter_does_not_compare_salted_and_desalted_fish() -> None:
    result = evaluate_offer_value(FishOfferProvider(), query="bacalao")

    assert result["counts"]["unverified"] == 1
    assert result["counts"]["not_worthwhile"] == 0


class SeafoodOfferProvider(GroceryProvider):
    info = StoreInfo(
        key="alpha",
        label="Alpha",
        country="ES",
        languages=("es",),
        capabilities=("search",),
    )

    def search(self, query: str, **_: object) -> list[Product]:
        del query
        return [
            p("offer", "Gamba pelada mediana 300 g", offer=True),
            p("cheap", "Gamba entera mediana 300 g"),
        ]


def test_offer_filter_does_not_compare_peeled_and_shell_on_seafood() -> None:
    result = evaluate_offer_value(SeafoodOfferProvider(), query="gamba")

    assert result["counts"]["unverified"] == 1
    assert result["counts"]["not_worthwhile"] == 0


class ProduceOfferProvider(GroceryProvider):
    info = StoreInfo(
        key="alpha",
        label="Alpha",
        country="ES",
        languages=("es",),
        capabilities=("search",),
    )

    def search(self, query: str, **_: object) -> list[Product]:
        del query
        return [
            p("offer", "Manzana Golden bolsa 1 kg", offer=True),
            p("cheap", "Manzana Granny Smith bolsa 1 kg"),
        ]


def test_offer_filter_does_not_compare_different_produce_varieties() -> None:
    result = evaluate_offer_value(ProduceOfferProvider(), query="manzana")

    assert result["counts"]["unverified"] == 1
    assert result["counts"]["not_worthwhile"] == 0


class ProducePreservationOfferProvider(GroceryProvider):
    info = StoreInfo(
        key="alpha",
        label="Alpha",
        country="ES",
        languages=("es",),
        capabilities=("search",),
    )

    def search(self, query: str, **_: object) -> list[Product]:
        del query
        return [
            p("offer", "Cebolla troceada ultracongelada 500 g", offer=True),
            p("cheap", "Cebolla dulce fresca 500 g"),
        ]


def test_offer_filter_does_not_compare_frozen_cut_and_fresh_whole_produce() -> None:
    result = evaluate_offer_value(
        ProducePreservationOfferProvider(), query="cebolla"
    )

    assert result["counts"]["unverified"] == 1
    assert result["counts"]["not_worthwhile"] == 0


class ProduceIngredientOfferProvider(GroceryProvider):
    info = StoreInfo(
        key="alpha",
        label="Alpha",
        country="ES",
        languages=("es",),
        capabilities=("search",),
    )

    def search(self, query: str, **_: object) -> list[Product]:
        del query
        return [
            p("juice-offer", "Zumo de manzana 1 l", offer=True),
            p("juice-cheap", "Zumo natural de manzana 1 l"),
            p("apple", "Manzana Golden 1 kg"),
        ]


def test_offer_filter_rejects_secondary_ingredient_results_before_evaluation() -> None:
    result = evaluate_offer_value(ProduceIngredientOfferProvider(), query="manzana")

    assert result["products_observed"] == 3
    assert result["products_examined"] == 1
    assert result["products_rejected_by_query"] == 2
    assert result["offers_examined"] == 0


class BeverageOfferProvider(GroceryProvider):
    info = StoreInfo(
        key="alpha",
        label="Alpha",
        country="ES",
        languages=("es",),
        capabilities=("search",),
    )

    def search(self, query: str, **_: object) -> list[Product]:
        del query
        return [
            p("offer", "Refresco de cola zero lata 33 cl", offer=True),
            p("cheap", "Refresco de cola normal lata 33 cl"),
        ]


def test_offer_filter_does_not_compare_zero_and_regular_soft_drinks() -> None:
    result = evaluate_offer_value(BeverageOfferProvider(), query="refresco cola")

    assert result["counts"]["unverified"] == 1
    assert result["counts"]["not_worthwhile"] == 0


class JuiceOfferProvider(GroceryProvider):
    info = StoreInfo(
        key="alpha",
        label="Alpha",
        country="ES",
        languages=("es",),
        capabilities=("search",),
    )

    def search(self, query: str, **_: object) -> list[Product]:
        del query
        return [
            p("offer", "Zumo de naranja brik 1 l", offer=True),
            p("cheap", "Zumo de manzana brik 1 l"),
        ]


def test_offer_filter_does_not_compare_different_juice_fruits() -> None:
    result = evaluate_offer_value(JuiceOfferProvider(), query="zumo")

    assert result["counts"]["unverified"] == 1
    assert result["counts"]["not_worthwhile"] == 0


def test_server_explains_equivalence() -> None:
    result = server.explain_product_equivalence(
        "Taco de jamón serrano",
        "Jamón serrano en lonchas",
    )

    assert result["verdict"] == "different"
    assert "ham_format" in result["conflicts"]


def test_pantry_profiles_capture_product_kind_form_and_use() -> None:
    flour = analyze_product_text("Harina de trigo de fuerza")
    sweetener = analyze_product_text("Edulcorante líquido sacarina")
    salt = analyze_product_text("Sal marina fina yodada")
    sauce = analyze_product_text("Salsa de soja con menos sal")
    cereal = analyze_product_text("Cereales de maíz rellenos de chocolate")
    cookie = analyze_product_text("Galleta rellena de chocolate")
    snack = analyze_product_text("Snack de maíz sabor barbacoa")
    spice = analyze_product_text("Ajo granulado")

    assert flour.facets["flour_source"] == "wheat"
    assert flour.facets["flour_use"] == "strong_bread"
    assert sweetener.facets["sweetener_kind"] == "saccharin"
    assert sweetener.facets["sweetener_form"] == "liquid"
    assert salt.facets["salt_source"] == "sea"
    assert salt.facets["salt_grain"] == "fine"
    assert salt.facets["iodized"] == "yes"
    assert sauce.facets["sauce_type"] == "soy"
    assert sauce.facets["sauce_style"] == "reduced_salt"
    assert cereal.facets["cereal_base"] == "corn"
    assert cereal.facets["cereal_form"] == "filled"
    assert cookie.facets["cookie_filling"] == "chocolate"
    assert snack.facets["snack_base"] == "corn"
    assert snack.facets["snack_flavor"] == "barbecue"
    assert spice.facets["seasoning_type"] == "garlic"


def test_pantry_equivalence_rejects_materially_different_variants() -> None:
    cases = (
        ("Harina de trigo", "Harina de garbanzo", "flour_source"),
        ("Azúcar blanco", "Azúcar glas", "sweetener_kind"),
        ("Sal fina yodada", "Sal gruesa yodada", "salt_grain"),
        ("Salsa de soja", "Salsa barbacoa", "sauce_type"),
        ("Cereales copos de maíz", "Cereales rellenos de leche", "cereal_form"),
        ("Galleta María", "Galleta rellena de chocolate", "cookie_filling"),
        ("Snack de maíz barbacoa", "Snack de maíz queso", "snack_flavor"),
        ("Ajo granulado", "Pimienta molida", "seasoning_type"),
        ("Caldo de pollo líquido", "Pastillas de caldo de pollo", "broth_form"),
    )

    for left, right, conflict in cases:
        result = verdict(left, right)
        assert result["verdict"] == "different", (left, right, result)
        assert conflict in result["conflicts"]


def test_household_profiles_separate_use_form_and_capacity() -> None:
    laundry = analyze_product_text("Detergente ropa líquido concentrado")
    hand = analyze_product_text("Lavavajillas a mano concentrado")
    machine = analyze_product_text("Lavavajillas máquina pastillas")
    softener = analyze_product_text("Suavizante azul concentrado")
    bleach = analyze_product_text("Lejía lavadora")
    cleaner = analyze_product_text("Limpiador baños en gel")
    bags = analyze_product_text("Bolsas basura 30 L autocierre perfumadas")
    paper = analyze_product_text("Papel cocina 2 capas")

    assert laundry.facets["detergent_use"] == "laundry"
    assert laundry.facets["detergent_form"] == "liquid"
    assert hand.facets["detergent_use"] == "hand_dishwashing"
    assert machine.facets["detergent_use"] == "dishwasher_machine"
    assert machine.facets["detergent_form"] == "capsules"
    assert softener.family == "fabric_softener"
    assert softener.facets["scent"] == "blue"
    assert bleach.family == "bleach"
    assert bleach.facets["bleach_use"] == "laundry"
    assert cleaner.facets["cleaner_use"] == "bathroom"
    assert bags.facets["capacity_l"] == "30"
    assert bags.facets["bag_closure"] == "drawstring"
    assert paper.facets["paper_ply"] == "2"


def test_household_equivalence_and_appliance_false_positive_are_safe() -> None:
    cases = (
        ("Detergente ropa líquido", "Lavavajillas a mano líquido", "detergent_use"),
        ("Lavavajillas a mano", "Lavavajillas máquina pastillas", "detergent_use"),
        ("Lejía lavadora", "Lejía multiusos", "bleach_use"),
        ("Limpiador baño", "Limpiador suelos", "cleaner_use"),
        ("Bolsas basura 30 L", "Bolsas basura 100 L", "capacity_l"),
    )

    for left, right, conflict in cases:
        result = verdict(left, right)
        assert result["verdict"] == "different"
        assert conflict in result["conflicts"]
    assert analyze_product_text("Lavavajillas 12 place settings BALAY").family == "household_appliance"
    assert analyze_product_text("Sal para lavavajillas").family == "dishwasher_additive"


def test_personal_care_profiles_are_distinct_and_explainable() -> None:
    shampoo = analyze_product_text("Champú anticaspa cabello seco")
    gel = analyze_product_text("Gel de ducha piel sensible")
    soap = analyze_product_text("Jabón de manos líquido")
    deodorant = analyze_product_text("Desodorante roll-on antitranspirante")
    toothpaste = analyze_product_text("Dentífrico blanqueador")
    brush = analyze_product_text("Cepillo dental infantil suave")

    assert shampoo.family == "shampoo"
    assert shampoo.facets["anti_dandruff"] == "yes"
    assert shampoo.facets["hair_need"] == "dry"
    assert gel.family == "shower_gel"
    assert gel.facets["skin_need"] == "sensitive"
    assert soap.family == "soap"
    assert soap.facets["soap_use"] == "hands"
    assert soap.facets["soap_form"] == "liquid"
    assert deodorant.facets["deodorant_form"] == "roll_on"
    assert deodorant.facets["antiperspirant"] == "yes"
    assert toothpaste.family == "toothpaste"
    assert toothpaste.facets["oral_need"] == "whitening"
    assert brush.family == "toothbrush"
    assert brush.facets["brush_hardness"] == "soft"
    assert brush.facets["target_age"] == "child"


def test_personal_care_equivalence_rejects_wrong_product_or_variant() -> None:
    family_cases = (
        ("Champú hidratante", "Gel de ducha hidratante"),
        ("Jabón de manos", "Champú familiar"),
        ("Dentífrico blanqueador", "Cepillo dental medio"),
    )
    for left, right in family_cases:
        assert verdict(left, right)["conflicts"]["family"]

    cases = (
        ("Desodorante roll-on", "Desodorante spray", "deodorant_form"),
        ("Dentífrico blanqueador", "Dentífrico encías", "oral_need"),
        ("Cepillo dental suave", "Cepillo dental duro", "brush_hardness"),
    )
    for left, right, conflict in cases:
        assert conflict in verdict(left, right)["conflicts"]


def test_baby_and_pet_profiles_do_not_collapse_species_age_or_format() -> None:
    jar = analyze_product_text("Potito de pollo con arroz +6 meses")
    pouch = analyze_product_text("Bolsita puré de frutas +8 meses")
    dog = analyze_product_text("Comida perro adulto paté de pollo")
    cat = analyze_product_text("Pienso gato esterilizado de salmón")

    assert jar.family == "baby_food"
    assert jar.facets["baby_food_type"] == "jar"
    assert jar.facets["age_band"] == "6m_plus"
    assert jar.facets["main_ingredient"] == "chicken"
    assert pouch.facets["baby_food_type"] == "pouch"
    assert pouch.facets["main_ingredient"] == "fruit"
    assert dog.family == "pet_food"
    assert dog.facets["pet_species"] == "dog"
    assert dog.facets["pet_food_form"] == "wet"
    assert cat.facets["pet_species"] == "cat"
    assert cat.facets["pet_food_form"] == "dry"
    assert cat.facets["pet_need"] == "sterilized"


def test_baby_and_pet_equivalence_rejects_unsafe_substitutions() -> None:
    cases = (
        ("Potito pollo +6 meses", "Potito pollo +12 meses", "age_band"),
        ("Potito pollo +6 meses", "Bolsita fruta +6 meses", "baby_food_type"),
        ("Comida perro adulto pollo", "Comida gato adulto pollo", "pet_species"),
        ("Pienso gato salmón", "Paté gato salmón", "pet_food_form"),
        ("Pienso gato esterilizado", "Pienso gato adulto", "pet_need"),
    )

    for left, right, conflict in cases:
        result = verdict(left, right)
        assert result["verdict"] == "different"
        assert conflict in result["conflicts"]


def test_existing_family_extensions_capture_previously_missing_variants() -> None:
    pasta = analyze_product_text("Espaguetis de lenteja")
    tofu = analyze_product_text("Tofu ahumado extra firme")
    bread = analyze_product_text("Pan integral de centeno con masa madre")
    eggs = analyze_product_text("Huevos camperos talla L")
    yogurt = analyze_product_text("Actimel bebible de fresa")
    coffee = analyze_product_text("Café en cápsulas intensidad 10")
    ham = analyze_product_text("Jamón ibérico de bellota en lonchas")
    salmon = analyze_product_text("Lomos de salmón congelados")

    assert pasta.facets["pasta_shape"] == "spaghetti"
    assert pasta.facets["pasta_base"] == "lentil"
    assert tofu.facets["tofu_firmness"] == "extra_firm"
    assert bread.facets["bread_source"] == "rye"
    assert bread.facets["bread_style"] == "sourdough"
    assert eggs.facets["egg_size"] == "l"
    assert yogurt.facets["yogurt_format"] == "drinkable"
    assert coffee.facets["coffee_intensity"] == "10"
    assert ham.facets["ham_grade"] == "acorn"
    assert salmon.facets["preservation"] == "frozen"


def test_semantic_queries_remove_common_cross_category_noise() -> None:
    cases = (
        ("azúcar", "Magdalenas con azúcar"),
        ("sal", "Mantequilla con sal"),
        ("especias", "Harina especial repostería"),
        ("pasta dental", "Pasta espirales 500 g"),
        ("potito", "Anilla de potón kilo"),
        ("snacks", "Snacks para gato con pollo"),
    )

    for query, name in cases:
        result = assess_query_candidate(query, p("noise", name))
        assert result["verdict"] == "different", (query, name, result)
