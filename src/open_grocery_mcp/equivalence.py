"""Explainable, conservative semantic equivalence for grocery products.

The retailer catalogues do not share a taxonomy.  This module extracts a small
set of high-value families and facets from Spanish/Galician product names and
categories.  Rules are deliberately explicit: an unknown concept stays unknown
and an observed conflict is never repaired by a high lexical score.
"""

from __future__ import annotations

import re
from functools import lru_cache
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from open_grocery_mcp.alias_data import aliases_for
from open_grocery_mcp.models import Product

_WORD_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_PERCENT_RE = re.compile(r"(?<!\d)(\d{1,3})\s*%")
_MASS_RE = re.compile(r"(?<!\d)(\d+(?:[.,]\d+)?)\s*(kg|g)\b", re.IGNORECASE)
_CALIBRE_RANGE_RE = re.compile(
    r"\bcalibre\s+(?:[a-z]\s*\()?\s*(\d{1,3})\s*[/\-]\s*(\d{1,3})\s*(?:mm)?",
    re.IGNORECASE,
)
_DIMENSION_RANGE_RE = re.compile(
    r"(?<!\d)(\d{1,3})\s*[/\-]\s*(\d{1,3})\s*mm\b",
    re.IGNORECASE,
)
_ABV_RE = re.compile(
    r"(?<!\d)(\d{1,2}(?:[.,]\d+)?)\s*%\s*(?:vol\.?|alc\.?)",
    re.IGNORECASE,
)
_MULTIPACK_RE = re.compile(r"\b(?:pack\s*)?(\d{1,2})\s*x\s*\d", re.IGNORECASE)


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _words(value: str) -> set[str]:
    return set(_WORD_RE.findall(_normalize(value)))


def _ordered_words(value: str) -> tuple[str, ...]:
    return tuple(_WORD_RE.findall(_normalize(value)))


def _alias_words(value: str) -> frozenset[str]:
    return frozenset(_words(value))


def _has(words: set[str], alias: str) -> bool:
    wanted = _alias_words(alias)
    return bool(wanted) and wanted <= words


def _alias_position(ordered: tuple[str, ...], alias: str) -> int | None:
    wanted = _ordered_words(alias)
    if not wanted or len(wanted) > len(ordered):
        return None
    for position in range(len(ordered) - len(wanted) + 1):
        if ordered[position : position + len(wanted)] == wanted:
            return position
    return None


def _first(words: set[str], choices: Mapping[str, Iterable[str]]) -> str | None:
    for value, aliases in choices.items():
        if any(_has(words, alias) for alias in aliases):
            return value
    return None


_FAMILY_ALIASES: dict[str, tuple[str, ...]] = {
    "toilet_paper": ("papel higienico",),
    "detergent": ("detergente", "lavavajillas"),
    "yogurt": (
        "yogur",
        "yoghourt",
        "skyr",
        "kefir",
        "bifidus",
        "activia",
        "actimel",
        "l casei",
    ),
    "milk": ("leche",),
    "cheese": ("queso", "queixo"),
    "rice": ("arroz",),
    "pasta": (
        "pasta",
        "macarron",
        "macarrones",
        "espagueti",
        "espaguetis",
        "spaghetti",
        "spaguetti",
        "spaghettini",
        "fusilli",
        "helice",
        "helices",
        "espiral",
        "espirales",
        "fideo",
        "fideos",
        "fideua",
        "tallarines",
        "tortellini",
        "ravioli",
        "rigatoni",
        "penne",
        "linguine",
        "tiburon",
        "pajaritas",
        "farfalle",
        "plumas",
        "lasana",
    ),
    "oil": ("aceite",),
    "coffee": ("cafe",),
    "chocolate": ("chocolate",),
    "tuna": ("atun", "bonito del norte"),
    "salmon": ("salmon",),
    "fish": (
        "merluza",
        "pescadilla",
        "bacalao",
        "sardina",
        "sardinas",
        "sardinilla",
        "parrocha",
        "dorada",
        "lubina",
        "caballa",
        "trucha",
        "rape",
        "lenguado",
        "rodaballo",
        "boqueron",
        "anchoa",
        "jurel",
        "pez espada",
        "salmonete",
    ),
    "seafood": (
        "pulpo",
        "calamar",
        "calamares",
        "chipiron",
        "chipirones",
        "pota",
        "poton",
        "sepia",
        "choco",
        "gamba",
        "gambas",
        "gambon",
        "langostino",
        "langostinos",
        "cigala",
        "cigalas",
        "mejillon",
        "mejillones",
        "almeja",
        "almejas",
        "vieira",
        "vieiras",
        "berberecho",
        "berberechos",
        "navaja",
        "navajas",
    ),
    "ham": ("jamon", "paleta curada", "paleta serrana"),
    "meat": (
        "carne",
        "carne picada",
        "picada",
        "burger meat",
        "cerdo",
        "porcino",
        "vacuno",
        "ternera",
        "vaca",
        "buey",
        "pollo",
        "pavo",
        "cordero",
        "lechal",
        "ternasco",
        "conejo",
        "lomo",
        "solomillo",
        "costilla",
        "costillas",
        "chuleta",
        "chuletas",
        "pechuga",
        "pechugas",
    ),
    "tofu": ("tofu",),
    "banana": ("platano", "banana"),
    "fruit": ("fruta", "frutas", "fruto", "frutos"),
    "vegetable": ("verdura", "verduras", "hortaliza", "hortalizas"),
    "legume": ("lenteja", "lentejas", "garbanzo", "alubia", "judia"),
    "bread": (
        "pan",
        "panes",
        "panecillo",
        "panecillos",
        "baguette",
        "hogaza",
        "barra",
    ),
    "eggs": ("huevo", "huevos"),
    "soft_drink": (
        "refresco",
        "refrescos",
        "cola",
        "coca cola",
        "pepsi",
        "fanta",
        "sprite",
        "seven up",
        "7up",
        "kas",
        "tonica",
        "gaseosa",
    ),
    "wine": ("vino",),
}

_DESIGNATIONS: dict[str, tuple[str, ...]] = {
    "arzua_ulloa": ("arzua ulloa", "a ulloa", "arzua", "ulloa"),
    "tetilla": ("tetilla",),
    "san_simon_da_costa": ("san simon da costa", "san simon"),
    "cebreiro": ("cebreiro",),
    "manchego": ("manchego",),
    "idiazabal": ("idiazabal",),
    "cabrales": ("cabrales",),
    "mahon": ("mahon",),
    "roncal": ("roncal",),
    "torta_del_casar": ("torta del casar",),
}

_DESIGNATION_SEARCH_ALIASES: dict[str, tuple[str, ...]] = {
    "arzua_ulloa": ("arzua", "ulloa", "arzua ulloa"),
    **{
        key: aliases
        for key, aliases in _DESIGNATIONS.items()
        if key != "arzua_ulloa"
    },
}

_FACET_ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "milk_fat": {
        "semi": ("semidesnatada", "semidesnatado"),
        "skimmed": ("desnatada", "desnatado"),
        "whole": ("entera", "entero"),
    },
    "oil_source": {
        "olive": ("oliva",),
        "sunflower": ("girasol",),
        "coconut": ("coco",),
    },
    "coffee_form": {
        "capsules": ("capsula", "capsulas", "pods"),
        "soluble": ("soluble", "instantaneo"),
        "ground": ("molido",),
        "beans": ("grano",),
    },
    "detergent_form": {
        "capsules": ("capsula", "capsulas", "caps", "pods", "pastilla", "pastillas"),
        "powder": ("polvo",),
        "gel": ("gel",),
        "liquid": ("liquido",),
    },
    "cheese_cure": {
        "fresh": ("fresco",),
        "tender": ("tierno",),
        "semi_cured": ("semicurado",),
        "cured": ("curado",),
        "aged": ("anejo", "viejo"),
    },
    "cheese_form": {
        "grated": ("rallado",),
        "sliced": ("lonchas", "loncheado"),
        "spread": ("untar", "crema"),
        "whipped": ("batido",),
        "log": ("rulo",),
        "solid": ("barra", "cuna", "pieza"),
    },
    "rice_variety": {
        "basmati": ("basmati",),
        "bomba": ("bomba",),
        "long": ("largo",),
        "round": ("redondo",),
        "thai": ("thai", "jazmin"),
    },
    "yogurt_style": {
        "greek": ("griego",),
        "skyr": ("skyr",),
        "kefir": ("kefir",),
    },
    "tuna_species": {
        "bonito": ("bonito del norte", "bonito"),
        "light_tuna": ("atun claro",),
        "tuna": ("atun",),
    },
    "tuna_cut": {
        "ventresca": ("ventresca", "ijada"),
        "loin": ("lomo", "lomos"),
        "fillet": ("filete", "filetes"),
        "steak": ("rodaja", "rodajas"),
        "crumbs": ("migas",),
    },
    "ham_style": {
        "iberian": ("iberico", "iberica"),
        "serrano": ("serrano", "serrana"),
        "generic_cured": ("curado", "curada"),
    },
    "ham_format": {
        "diced": ("taquito", "taquitos", "dados"),
        "sliced": ("loncha", "lonchas", "loncheado", "al corte"),
        "solid_block": ("taco", "centro", "deshuesado", "bloque"),
        "whole": ("pata", "entero", "pieza"),
    },
    "pasta_base": {
        "lentil": ("lenteja", "lentejas"),
        "chickpea": ("garbanzo", "garbanzos"),
        "pea": ("guisante", "guisantes"),
        "wheat": ("trigo",),
    },
    "tofu_style": {
        "smoked": ("ahumado",),
        "marinated": ("marinado",),
        "plain": ("natural",),
    },
}

_MEAT_SPECIES_ALIASES: dict[str, tuple[str, ...]] = {
    "pork": ("cerdo", "porcino", "cochinillo", "duroc", "iberico", "iberica"),
    "beef": ("vacuno", "ternera", "vaca", "buey", "angus"),
    "chicken": ("pollo",),
    "turkey": ("pavo",),
    "lamb": ("cordero", "lechal", "ternasco"),
    "rabbit": ("conejo",),
}

_MEAT_CUT_ALIASES: dict[str, tuple[str, ...]] = {
    "tenderloin": ("solomillo",),
    "loin": ("lomo", "cinta de lomo"),
    "ribs": ("costilla", "costillas", "costilleja"),
    "breast": ("pechuga", "pechugas"),
    "thigh": ("muslo", "muslos", "contramuslo", "contramuslos"),
    "wings": ("alita", "alitas"),
    "shoulder": ("paletilla", "paletillas"),
    "liver": ("higado",),
    "flank": ("falda",),
    "hip": ("cadera", "babilla"),
    "round": ("redondo",),
}

_FISH_SPECIES_ALIASES: dict[str, tuple[str, ...]] = {
    "hake": ("merluza", "pescadilla"),
    "cod": ("bacalao",),
    "sardine": ("sardina", "sardinas", "sardinilla", "sardinillas", "parrocha"),
    "sea_bream": ("dorada",),
    "sea_bass": ("lubina",),
    "mackerel": ("caballa",),
    "trout": ("trucha",),
    "monkfish": ("rape",),
    "sole": ("lenguado",),
    "turbot": ("rodaballo",),
    "anchovy": ("boqueron", "boquerones", "anchoa", "anchoas"),
    "horse_mackerel": ("jurel", "jureles"),
    "swordfish": ("pez espada", "espada"),
    "shark": ("marrajo", "tintorera", "cazon"),
    "red_mullet": ("salmonete", "salmonetes"),
    "blue_whiting": ("lirio", "bacaladilla"),
    "redfish": ("gallineta",),
}

_SEAFOOD_SPECIES_ALIASES: dict[str, tuple[str, ...]] = {
    "octopus": ("pulpo",),
    "squid": ("calamar", "calamares", "chipiron", "chipirones"),
    "pota": ("pota", "poton"),
    "cuttlefish": ("sepia", "choco"),
    "shrimp": ("gamba", "gambas"),
    "king_prawn": ("gambon", "gambones"),
    "prawn": ("langostino", "langostinos"),
    "crayfish": ("cigala", "cigalas"),
    "mussel": ("mejillon", "mejillones"),
    "clam": ("almeja", "almejas", "almejon", "almejones"),
    "scallop": (
        "vieira",
        "vieiras",
        "zamburina",
        "zamburinas",
        "volandeira",
        "volandeiras",
    ),
    "cockle": ("berberecho", "berberechos"),
    "razor_clam": ("navaja", "navajas"),
}

_FRUIT_SPECIES_ALIASES: dict[str, tuple[str, ...]] = {
    "apple": ("manzana", "manzanas"),
    "orange": ("naranja", "naranjas"),
    "strawberry": ("fresa", "fresas", "freson", "fresones"),
    "pear": ("pera", "peras"),
    "kiwi": ("kiwi", "kiwis"),
    "grape": ("uva", "uvas"),
    "lemon": ("limon", "limones"),
    "mandarin": ("mandarina", "mandarinas", "clementina", "clementinas"),
    "peach": ("melocoton", "melocotones"),
    "nectarine": ("nectarina", "nectarinas"),
    "apricot": ("albaricoque", "albaricoques"),
    "plum": ("ciruela", "ciruelas"),
    "cherry": ("cereza", "cerezas", "picota", "picotas"),
    "pineapple": ("pina", "pinas"),
    "melon": ("melon", "melones"),
    "watermelon": ("sandia", "sandias"),
    "mango": ("mango", "mangos"),
    "avocado": ("aguacate", "aguacates"),
    "raspberry": ("frambuesa", "frambuesas"),
    "blueberry": ("arandano", "arandanos"),
    "blackberry": ("mora", "moras"),
    "pomegranate": ("granada", "granadas"),
    "fig": ("higo", "higos"),
    "persimmon": ("caqui", "kaki", "persimon"),
}

_VEGETABLE_SPECIES_ALIASES: dict[str, tuple[str, ...]] = {
    "tomato": ("tomate", "tomates"),
    "potato": ("patata", "patatas"),
    "sweet_potato": ("boniato", "boniatos", "batata", "batatas"),
    "onion": ("cebolla", "cebollas"),
    "lettuce": ("lechuga", "lechugas", "cogollo", "cogollos"),
    "pepper": ("pimiento", "pimientos"),
    "zucchini": ("calabacin", "calabacines"),
    "eggplant": ("berenjena", "berenjenas"),
    "pumpkin": ("calabaza", "calabazas"),
    "carrot": ("zanahoria", "zanahorias"),
    "cucumber": ("pepino", "pepinos"),
    "broccoli": ("brocoli",),
    "cauliflower": ("coliflor", "coliflores"),
    "cabbage": ("repollo", "repollos", "col", "coles"),
    "garlic": ("ajo", "ajos"),
    "leek": ("puerro", "puerros"),
    "spinach": ("espinaca", "espinacas"),
    "chard": ("acelga", "acelgas"),
    "asparagus": ("esparrago", "esparragos"),
    "artichoke": ("alcachofa", "alcachofas"),
    "mushroom": ("champinon", "champinones", "seta", "setas"),
    "green_bean": ("judia verde", "judias verdes"),
    "pea": ("guisante", "guisantes"),
    "corn": ("mazorca", "mazorcas"),
}

_BEVERAGE_FRUIT_ALIASES: dict[str, tuple[str, ...]] = {
    "orange": ("naranja", "naranjas"),
    "lemon": ("limon", "limones"),
    "lime": ("lima", "limas"),
    "apple": ("manzana", "manzanas"),
    "pineapple": ("pina", "pinas"),
    "peach": ("melocoton", "melocotones"),
    "grape": ("uva", "uvas"),
    "strawberry": ("fresa", "fresas"),
    "mango": ("mango", "mangos"),
    "pear": ("pera", "peras"),
    "tomato": ("tomate", "tomates"),
    "carrot": ("zanahoria", "zanahorias"),
    "mandarin": ("mandarina", "mandarinas"),
    "pomegranate": ("granada", "granadas"),
    "coconut": ("coco",),
}

_PLANT_DRINK_BASE_ALIASES: dict[str, tuple[str, ...]] = {
    "oat": ("avena",),
    "soy": ("soja",),
    "almond": ("almendra", "almendras"),
    "rice": ("arroz",),
    "coconut": ("coco",),
    "hazelnut": ("avellana", "avellanas"),
    "pea": ("guisante", "guisantes"),
}

_SPIRIT_TYPE_ALIASES: dict[str, tuple[str, ...]] = {
    "gin": ("ginebra",),
    "rum": ("ron",),
    "whisky": ("whisky", "whiskey", "bourbon"),
    "vodka": ("vodka",),
    "tequila": ("tequila", "mezcal"),
    "brandy": ("brandy", "conac"),
    "pomace": ("orujo", "aguardiente"),
    "liqueur": ("licor", "pacharan"),
}

_FRUIT_HEAD_WORDS = frozenset(
    alias
    for aliases in _FRUIT_SPECIES_ALIASES.values()
    for alias in aliases
    if " " not in alias
)
_VEGETABLE_HEAD_WORDS = frozenset(
    alias
    for aliases in _VEGETABLE_SPECIES_ALIASES.values()
    for alias in aliases
    if " " not in alias
)

_FISH_HEAD_WORDS = frozenset(
    alias
    for aliases in _FISH_SPECIES_ALIASES.values()
    for alias in aliases
    if " " not in alias
)
_SEAFOOD_HEAD_WORDS = frozenset(
    alias
    for aliases in _SEAFOOD_SPECIES_ALIASES.values()
    for alias in aliases
    if " " not in alias
)

_CRITICAL_FACETS: dict[str, frozenset[str]] = {
    "milk": frozenset({"milk_fat", "lactose_free"}),
    "oil": frozenset({"oil_source", "oil_grade", "oil_use"}),
    "coffee": frozenset(
        {"coffee_form", "coffee_roast", "coffee_intensity", "decaffeinated", "origin"}
    ),
    "detergent": frozenset(
        {"detergent_use", "detergent_form", "laundry_specialty", "concentration"}
    ),
    "cheese": frozenset(
        {
            "designation",
            "cheese_variety",
            "cheese_milk",
            "cheese_cure",
            "cheese_form",
            "cheese_processing",
            "cheese_fat",
            "lactose_free",
            "no_added_sugar",
        }
    ),
    "rice": frozenset({"rice_variety", "rice_treatment", "rice_use", "preparation"}),
    "yogurt": frozenset(
        {
            "yogurt_style",
            "yogurt_flavor",
            "yogurt_format",
            "milk_fat",
            "lactose_free",
            "no_added_sugar",
        }
    ),
    "chocolate": frozenset(
        {
            "chocolate_type",
            "cacao_band",
            "chocolate_form",
            "additions",
            "no_added_sugar",
        }
    ),
    "tuna": frozenset(
        {"tuna_species", "tuna_cut", "preparation", "preservation", "preserving_medium"}
    ),
    "salmon": frozenset(
        {"preparation", "preservation", "preserving_medium", "fish_cut", "production"}
    ),
    "fish": frozenset(
        {
            "fish_species",
            "fish_cut",
            "preparation",
            "preservation",
            "preserving_medium",
            "skin",
            "bones",
            "production",
        }
    ),
    "seafood": frozenset(
        {
            "seafood_species",
            "seafood_form",
            "preparation",
            "preservation",
            "preserving_medium",
            "size_band",
        }
    ),
    "ham": frozenset({"ham_source", "ham_style", "ham_format", "ham_grade"}),
    "meat": frozenset(
        {
            "meat_species",
            "meat_breed",
            "meat_grade",
            "meat_cut",
            "meat_preparation",
            "meat_format",
            "preservation",
            "bone",
        }
    ),
    "tofu": frozenset({"tofu_style", "tofu_firmness"}),
    "pasta": frozenset(
        {"pasta_base", "pasta_style", "pasta_shape", "preparation", "gluten_free"}
    ),
    "toilet_paper": frozenset({"paper_form"}),
    "banana": frozenset({"banana_type"}),
    "fruit": frozenset(
        {
            "fruit_species",
            "produce_variety",
            "produce_form",
            "preparation",
            "preservation",
            "produce_use",
            "produce_production",
            "sale_basis",
            "size_band",
            "origin",
        }
    ),
    "vegetable": frozenset(
        {
            "vegetable_species",
            "produce_variety",
            "produce_form",
            "preparation",
            "preservation",
            "produce_use",
            "produce_production",
            "sale_basis",
            "size_band",
            "origin",
        }
    ),
    "prepared_meal": frozenset(
        {
            "meal_type",
            "main_ingredient",
            "meal_variant",
            "meal_format",
            "meal_preparation",
            "preservation",
            "broth_form",
        }
    ),
    "legume": frozenset({"legume_type", "preparation"}),
    "bread": frozenset(
        {"bread_form", "bread_grain", "bread_source", "bread_style", "gluten_free"}
    ),
    "eggs": frozenset({"egg_bird", "egg_size", "egg_production", "egg_format"}),
    "flour": frozenset({"flour_source", "flour_treatment", "flour_use", "gluten_free"}),
    "sweetener": frozenset({"sweetener_kind", "sweetener_form"}),
    "salt": frozenset({"salt_source", "salt_grain", "iodized", "salt_flavor"}),
    "sauce": frozenset({"sauce_type", "sauce_style", "sugar_profile"}),
    "preserve": frozenset({"preserve_content", "preserving_medium"}),
    "cereal_product": frozenset(
        {"cereal_base", "cereal_form", "cereal_flavor", "sugar_profile"}
    ),
    "cookie": frozenset(
        {"cookie_style", "cookie_flavor", "cookie_filling", "gluten_free", "sugar_profile"}
    ),
    "snack": frozenset({"snack_base", "snack_form", "snack_flavor"}),
    "seasoning": frozenset({"seasoning_type", "seasoning_form"}),
    "baking_ingredient": frozenset({"baking_kind", "baking_use"}),
    "fabric_softener": frozenset({"scent", "concentration", "hypoallergenic"}),
    "bleach": frozenset({"bleach_use", "detergent_added", "scented"}),
    "household_cleaner": frozenset(
        {"cleaner_use", "cleaner_form", "bleach_added"}
    ),
    "dishwasher_additive": frozenset({"dishwasher_additive_type"}),
    "trash_bag": frozenset({"capacity_l", "bag_closure", "scented"}),
    "kitchen_paper": frozenset({"paper_form", "paper_ply"}),
    "shampoo": frozenset({"hair_need", "anti_dandruff", "target_age"}),
    "shower_gel": frozenset({"skin_need", "target_age"}),
    "soap": frozenset({"soap_use", "soap_form", "skin_need"}),
    "deodorant": frozenset({"deodorant_form", "antiperspirant", "target_user"}),
    "toothpaste": frozenset({"oral_need", "target_age"}),
    "toothbrush": frozenset({"brush_hardness", "brush_kind", "target_age"}),
    "mouthwash": frozenset({"oral_need", "alcohol_free", "target_age"}),
    "feminine_hygiene": frozenset({"hygiene_type", "absorbency", "with_wings"}),
    "skincare": frozenset({"skincare_use", "skin_need", "target_age"}),
    "baby_hygiene": frozenset({"baby_hygiene_type", "baby_size"}),
    "baby_food": frozenset({"baby_food_type", "age_band", "main_ingredient"}),
    "pet_food": frozenset(
        {"pet_species", "pet_food_form", "pet_life_stage", "pet_need", "main_ingredient"}
    ),
    "water": frozenset(
        {
            "water_type",
            "carbonation",
            "beverage_flavor",
            "beverage_container",
            "beverage_package",
        }
    ),
    "soft_drink": frozenset(
        {
            "beverage_flavor",
            "flavor_variant",
            "sugar_profile",
            "caffeine_profile",
            "carbonation",
            "concentration",
            "beverage_container",
            "beverage_package",
        }
    ),
    "juice": frozenset(
        {
            "juice_fruit",
            "juice_style",
            "preparation",
            "pulp",
            "concentration",
            "no_added_sugar",
            "beverage_container",
            "beverage_package",
        }
    ),
    "plant_drink": frozenset(
        {
            "plant_base",
            "plant_style",
            "beverage_flavor",
            "no_added_sugar",
            "fortification",
            "beverage_container",
            "beverage_package",
        }
    ),
    "beverage": frozenset(
        {
            "beverage_kind",
            "beverage_flavor",
            "sugar_profile",
            "caffeine_profile",
            "carbonation",
            "concentration",
            "beverage_container",
            "beverage_package",
        }
    ),
    "beer": frozenset(
        {
            "beer_style",
            "alcohol_profile",
            "alcohol_strength",
            "beverage_container",
            "beverage_package",
        }
    ),
    "spirit": frozenset(
        {"spirit_type", "alcohol_profile", "alcohol_strength", "beverage_container"}
    ),
    "cider": frozenset(
        {"cider_style", "alcohol_profile", "alcohol_strength", "beverage_container"}
    ),
    "wine": frozenset(
        {
            "wine_color",
            "wine_style",
            "alcohol_free",
            "alcohol_profile",
            "alcohol_strength",
            "beverage_container",
        }
    ),
}

_ASYMMETRIC_STRICT = frozenset(
    {
        "designation",
        "cheese_processing",
        "no_added_sugar",
        "lactose_free",
        "gluten_free",
        "decaffeinated",
        "origin",
        "pet_need",
    }
)
_QUERY_REQUIRED_IF_OBSERVED = frozenset(
    {
        "designation",
        "lactose_free",
        "gluten_free",
        "decaffeinated",
        "origin",
        "no_added_sugar",
        "meat_species",
        "meat_breed",
        "meat_grade",
        "meat_cut",
        "meat_format",
        "meat_preparation",
        "preservation",
        "bone",
        "fish_species",
        "fish_cut",
        "seafood_species",
        "seafood_form",
        "preparation",
        "preserving_medium",
        "skin",
        "bones",
        "production",
        "size_band",
        "tuna_species",
        "tuna_cut",
        "fruit_species",
        "vegetable_species",
        "produce_variety",
        "produce_form",
        "produce_use",
        "produce_production",
        "sale_basis",
        "meal_type",
        "main_ingredient",
        "meal_variant",
        "meal_format",
        "meal_preparation",
        "broth_form",
        "flour_source",
        "flour_treatment",
        "flour_use",
        "sweetener_kind",
        "sweetener_form",
        "salt_source",
        "salt_grain",
        "iodized",
        "salt_flavor",
        "sauce_type",
        "sauce_style",
        "preserve_content",
        "cereal_base",
        "cereal_form",
        "cereal_flavor",
        "cookie_style",
        "cookie_flavor",
        "cookie_filling",
        "snack_base",
        "snack_form",
        "snack_flavor",
        "seasoning_type",
        "seasoning_form",
        "baking_kind",
        "baking_use",
        "detergent_use",
        "detergent_form",
        "laundry_specialty",
        "concentration",
        "scent",
        "hypoallergenic",
        "bleach_use",
        "detergent_added",
        "scented",
        "cleaner_use",
        "cleaner_form",
        "bleach_added",
        "dishwasher_additive_type",
        "capacity_l",
        "bag_closure",
        "paper_form",
        "paper_ply",
        "hair_need",
        "anti_dandruff",
        "target_age",
        "skin_need",
        "soap_use",
        "soap_form",
        "deodorant_form",
        "antiperspirant",
        "target_user",
        "oral_need",
        "brush_hardness",
        "brush_kind",
        "hygiene_type",
        "absorbency",
        "with_wings",
        "skincare_use",
        "baby_hygiene_type",
        "baby_size",
        "baby_food_type",
        "age_band",
        "pet_species",
        "pet_food_form",
        "pet_life_stage",
        "pet_need",
        "pasta_shape",
        "tofu_firmness",
        "bread_style",
        "bread_source",
        "egg_format",
        "oil_use",
        "coffee_intensity",
        "yogurt_format",
        "ham_grade",
        "water_type",
        "carbonation",
        "beverage_flavor",
        "flavor_variant",
        "beverage_container",
        "beverage_package",
        "sugar_profile",
        "caffeine_profile",
        "juice_fruit",
        "juice_style",
        "pulp",
        "concentration",
        "plant_base",
        "plant_style",
        "fortification",
        "beverage_kind",
        "beer_style",
        "spirit_type",
        "cider_style",
        "alcohol_profile",
        "alcohol_strength",
    }
)


@dataclass(frozen=True, slots=True)
class SemanticProfile:
    family: str | None
    facets: Mapping[str, str]
    concepts: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "facets": dict(self.facets),
            "concepts": list(self.concepts),
        }


def _family(words: set[str], text: str) -> str | None:
    ordered = _ordered_words(text)
    first = ordered[0] if ordered else ""
    head = next((word for word in ordered if not word.isdigit()), "")
    leading = set(ordered[:3])
    animal = bool(words & {"perro", "perros", "gato", "gatos", "mascota"})
    if animal and not _has(words, "perro caliente"):
        return "pet_food"
    if head in {"alimento", "comida", "snack", "barrita", "barritas"} and words & {
        "conejo",
        "conejos",
        "hamster",
        "hamsters",
    }:
        return "pet_food"
    if (
        words & {"bebe", "infantil", "mes", "meses"}
        and words
        & {
            "alimento",
            "bolsita",
            "cereal",
            "cereales",
            "papilla",
            "potito",
            "pure",
            "tarrito",
            "tarro",
        }
    ) or head in {"potito", "potitos", "tarrito", "tarritos", "papilla", "papillas"}:
        return "baby_food"
    if head in {"panal", "panales", "toallita", "toallitas"} and words & {
        "bebe",
        "infantil",
        "nino",
        "nina",
    }:
        return "baby_hygiene"
    if head == "sal" and words & {"lavavajillas", "maquina"}:
        return "dishwasher_additive"
    if head in {"lejia", "lejias"}:
        return "bleach"
    if head in {"limpiador", "limpiadora", "limpiadores"}:
        return "household_cleaner"
    if head in {"suavizante", "suavizantes"}:
        return "fabric_softener"
    if head in {"bolsa", "bolsas"} and words & {"basura", "residuos"}:
        return "trash_bag"
    if head == "papel" and words & {"cocina", "hogar", "multiusos", "absorbente"}:
        return "kitchen_paper"
    if head in {"ambientador", "ambientadores"}:
        return "household_freshener"
    if head in {"lavavajillas", "lavaplatos"} and words & {
        "beko",
        "balay",
        "settings",
        "place",
    }:
        return "household_appliance"
    if head in {"lavavajillas", "lavaplatos", "vajillas"}:
        return "detergent"
    if head == "gel" and words & {"detergente", "lavavajillas"}:
        return "detergent"
    if head in {"champu", "champues"}:
        return "shampoo"
    if head == "gel" and words & {"ducha", "bano", "piel"}:
        return "shower_gel"
    if head in {"jabon", "jabones"}:
        if words & {"lavadora", "ropa", "detergente"}:
            return "detergent"
        return "soap"
    if head in {"desodorante", "desodorantes"}:
        return "deodorant"
    if head in {"dentifrico", "dentifricos"} or (
        head == "pasta" and words & {"dental", "dientes"}
    ):
        return "toothpaste"
    if head in {"cepillo", "cepillos"} and words & {"dental", "dientes", "ortodoncia"}:
        return "toothbrush"
    if head in {"colutorio", "colutorios", "enjuague", "enjuagues"} and words & {
        "bucal",
        "dental",
    }:
        return "mouthwash"
    if head in {"compresa", "compresas", "tampon", "tampones", "protegeslip"}:
        return "feminine_hygiene"
    if head in {"locion", "lociones"}:
        return "skincare"
    if head == "gel":
        return "personal_care"
    if head in {
        "bombon",
        "bombones",
        "caramelo",
        "caramelos",
        "chicle",
        "chicles",
        "golosina",
        "golosinas",
        "gominola",
        "gominolas",
    }:
        return "confectionery"
    if head in {"cacao", "cacaos"}:
        return "chocolate"
    if head in {"azucar", "azucares", "edulcorante", "edulcorantes", "sacarina"}:
        return "sweetener"
    if head in {"eritritol", "panela", "stevia", "sucralosa"}:
        return "sweetener"
    if head in {"sal", "sales"}:
        return "salt"
    if head in {"levadura", "levaduras", "gasificante", "gasificantes"}:
        return "baking_ingredient"
    if head in {
        "especia",
        "especias",
        "sazonador",
        "sazonadores",
        "canela",
        "comino",
        "curcuma",
        "oregano",
        "pimenton",
        "pimienta",
        "romero",
        "tomillo",
    }:
        return "seasoning"
    if head in {"polvo", "granulado"} and (
        _first(words, _FRUIT_SPECIES_ALIASES)
        or _first(words, _VEGETABLE_SPECIES_ALIASES)
    ):
        return "seasoning"
    if head == "agua":
        if words & {"destilada", "desmineralizada", "plancha"}:
            return "household_cleaner"
        if words & {"micelar", "oxigenada", "colonia", "perfumada"}:
            return "personal_care"
        if words & {"tonica"}:
            return "soft_drink"
        return "water"
    plant_base = _first(words, _PLANT_DRINK_BASE_ALIASES)
    plant_milk = any(
        _alias_position(ordered, f"leche de {alias}") is not None
        or _alias_position(ordered, f"leche {alias}") is not None
        for alias in (
            "avena",
            "soja",
            "almendra",
            "almendras",
            "arroz",
            "coco",
            "avellana",
            "avellanas",
            "guisante",
        )
    )
    if plant_base and (head in {"bebida", "bebidas"} or plant_milk):
        return "plant_drink"
    if head in {"horchata", "horchatas"}:
        return "plant_drink"
    if head in {"zumo", "zumos", "nectar", "smoothie"}:
        return "juice"
    if head in {
        "cola",
        "gaseosa",
        "gaseosas",
        "refresco",
        "refrescos",
        "soda",
        "tonica",
        "tonicas",
    }:
        if head == "cola" and words & {"blanca", "pegamento", "adhesivo"}:
            return "household_supplies"
        return "soft_drink"
    if head in {"cerveza", "cervezas"}:
        return "beer"
    if head in {"sidra", "cider"}:
        return "cider"
    if head in {
        "ginebra",
        "ron",
        "whisky",
        "whiskey",
        "vodka",
        "tequila",
        "brandy",
        "conac",
        "aguardiente",
        "orujo",
        "licor",
    }:
        return "spirit"
    if head in {"vino", "vinos", "cava", "champagne", "champan"}:
        return "wine"
    if head in {
        "batido",
        "batidos",
        "bebida",
        "bebidas",
    }:
        return "beverage"
    if head in {"mermelada", "mermeladas", "compota", "compotas"}:
        return "fruit_spread"
    if head in {"guacamole", "mayonesa", "mahonesa", "ketchup", "mostaza", "pesto"}:
        return "sauce"
    if head in {"vinagre", "vinagres"}:
        return "vinegar"
    if head in {"conserva", "conservas"}:
        return "preserve"
    if head in {
        "bizcocho",
        "bizcochos",
        "tarta",
        "tartas",
        "pastel",
        "pasteles",
        "gelatina",
        "gelatinas",
    }:
        return "dessert"
    if head in {"crema", "cremas"} and words & {
        "facial",
        "corporal",
        "manos",
        "pies",
        "hidratante",
        "antiarrugas",
        "depilatoria",
        "solar",
    }:
        return "skincare"
    if head in {"masa", "masas", "base", "bases", "oblea", "obleas"} and words & {
        "pizza",
        "pizzas",
        "empanada",
        "empanadas",
        "empanadilla",
        "empanadillas",
    }:
        return "bakery_component"
    if head in {
        "caldo",
        "caldos",
        "sopa",
        "sopas",
        "crema",
        "cremas",
        "salteado",
        "salteados",
        "paella",
        "fritura",
        "pure",
        "pures",
        "tortilla",
        "tortillas",
    }:
        return "prepared_meal"
    if "caldo" in words and head in {"cubito", "cubitos", "pastilla", "pastillas"}:
        return "prepared_meal"
    if head in {"aperitivo", "aperitivos"}:
        return "snack"
    if head in {"snack", "snacks", "nacho", "nachos", "gusanito", "gusanitos"}:
        return "snack"
    if head in {"patata", "patatas"} and words & {
        "frita",
        "fritas",
        "chips",
        "light",
        "lay",
        "lays",
        "ruffles",
    }:
        return "snack"
    if head in {"patata", "patatas"} and "bravas" in words:
        return "prepared_meal"
    if head in {"alga", "algas"}:
        return "seaweed"
    if head in {"corteza", "cortezas"} and "cerdo" in words:
        return "snack"
    if head in {"manteca", "grasa"}:
        return "cooking_fat"
    fruit_species = _first(words, _FRUIT_SPECIES_ALIASES)
    vegetable_species = _first(words, _VEGETABLE_SPECIES_ALIASES)
    if vegetable_species and words & {"polvo", "granulado", "granulada"}:
        return "seasoning"
    if vegetable_species == "tomato" and words & {"frito", "frita"}:
        return "sauce"
    if (fruit_species or vegetable_species) and words & {
        "relleno",
        "rellena",
        "rellenos",
        "rellenas",
        "gratinado",
        "gratinada",
    }:
        return "prepared_meal"
    if fruit_species and head in _FRUIT_HEAD_WORDS:
        return "fruit"
    if vegetable_species and (
        head in _VEGETABLE_HEAD_WORDS
        or head in {"cogollo", "cogollos", "corazon", "corazones"}
        or (
            head in {"judia", "judias"}
            and words & {"verde", "verdes"}
        )
    ):
        return "vegetable"
    fish_species = _first(words, _FISH_SPECIES_ALIASES)
    seafood_species = _first(words, _SEAFOOD_SPECIES_ALIASES)
    aquatic_heads = {
        "lomo",
        "lomos",
        "filete",
        "filetes",
        "rodaja",
        "rodajas",
        "porcion",
        "porciones",
        "medallon",
        "medallones",
        "cola",
        "colas",
        "migas",
        "ventresca",
        "ventrescas",
        "ijada",
        "ijadas",
        "medio",
        "pata",
        "patas",
        "anilla",
        "anillas",
        "carne",
    }
    if words & {"atun", "bonito"} and (
        head in aquatic_heads or head in {"atun", "bonito"}
    ):
        return "tuna"
    if "salmon" in words and (head in aquatic_heads or head == "salmon"):
        return "salmon"
    if fish_species and (head in aquatic_heads or head in _FISH_HEAD_WORDS):
        return "fish"
    if seafood_species and (head in aquatic_heads or head in _SEAFOOD_HEAD_WORDS):
        return "seafood"
    meat_species = _first(words, _MEAT_SPECIES_ALIASES)
    meat_heads = {
        "carne",
        "picada",
        "picado",
        "burger",
        "hamburguesa",
        "filete",
        "filetes",
        "cinta",
        "lomo",
        "solomillo",
        "costilla",
        "costillas",
        "costilleja",
        "tira",
        "tiras",
        "chuleta",
        "chuletas",
        "chuletillas",
        "escalopin",
        "escalopines",
        "pechuga",
        "pechugas",
        "muslo",
        "muslos",
        "paletilla",
        "paletillas",
        "medio",
        "conejo",
        "cordero",
        "fiambre",
    }
    ground_meat = (
        _has(words, "carne picada")
        or "picada" in words
        or "picado" in words
        or _has(words, "burger meat")
    )
    if "vegetal" in words and (ground_meat or head in {"burger", "hamburguesa"}):
        return "plant_based"
    if (
        meat_species
        and (head in meat_heads or ground_meat or head == "preparado")
    ) or (ground_meat and head in {"carne", "picada", "picado", "burger", "preparado"}) or (
        head == "lomo" and "embuchado" in words
    ):
        return "meat"
    if head == "jamon" and words & {"pavo", "pollo"}:
        return "meat"
    prepared_aliases = {
        "pizza",
        "pizzas",
        "croqueta",
        "croquetas",
        "poke",
        "empanadilla",
        "hamburguesa",
        "elaborado",
        "tabla",
        "empanada",
        "empanadas",
        "empanadillas",
        "ensalada",
        "ensaladas",
        "sandwich",
    }
    prepared_position = min(
        (
            position
            for alias in prepared_aliases
            if (position := _alias_position(ordered, alias)) is not None
        ),
        default=None,
    )
    dry_lasagna = "lasana" in words and bool(
        words & {"placa", "placas", "hoja", "hojas"}
        or head == "pasta"
        or (
            words & {"huevo", "caja", "facil", "directa"}
            and not words
            & {
                "atun",
                "bolonesa",
                "carne",
                "pollo",
                "vegetal",
                "verduras",
                "espinacas",
                "bechamel",
                "congelada",
                "refrigerada",
            }
        )
    )
    pasta_dish = bool(
        re.search(
            r"\b(?:macarrones|espaguetis|spaghetti|tallarines|pasta)\s+"
            r"(?:a la|con|en)\s+(?:bolonesa|carbonara|atun|pollo|carne|salsa)",
            _normalize(text),
        )
    )
    rice_dish = bool(
        re.search(
            r"\barroz\s+(?:tres delicias|a la cubana|negro|con\s+"
            r"(?:pollo|carne|verduras|marisco|mariscos|setas))\b",
            _normalize(text),
        )
    )
    if (
        (
            prepared_position is not None
            and prepared_position <= 2
            and head not in {"sabor", "aroma"}
            and head
            not in {
                "queso",
                "queixo",
                "pasta",
                "arroz",
                "pan",
                "leche",
                "yogur",
                "cafe",
                "aceite",
                "chocolate",
            }
        )
        or first == "preparado"
        or {"listo", "comer"} <= words
        or ("lasana" in words and not dry_lasagna)
        or pasta_dish
        or rice_dish
    ):
        return "prepared_meal"
    if first in {"barrita", "barritas", "cereal", "cereales"}:
        return "cereal_product"
    if first in {"galleta", "galletas", "cookie", "cookies"} or (
        first == "mini" and leading & {"galleta", "galletas"}
    ):
        return "cookie"
    if first in {"sirope", "salsa"}:
        return "sauce"
    if head == "barra" and "chocolate" in words:
        return "snack"
    if head in {"helado", "helados"}:
        return "frozen_dessert"
    if head in {"harina", "harinas"}:
        return "flour"
    if head in {"huevo", "huevos"} and "chocolate" in words:
        return "chocolate"
    if first in {"tortita", "tortitas"}:
        return "snack"
    if leading & {"pate", "mousse"}:
        return "spread"

    # Prefer the head noun over ingredient mentions.  A global alias order
    # cannot correctly classify both "chocolate con leche" and "leche con
    # cacao", or "atún en aceite" and "aceite de atún".
    head_families = (
        ("chocolate", {"chocolate", "chocolates"}),
        ("tuna", {"atun", "bonito", "ventresca"}),
        ("salmon", {"salmon"}),
        ("coffee", {"cafe"}),
        ("cheese", {"queso", "queixo"}),
        (
            "yogurt",
            {"yogur", "yoghourt", "skyr", "kefir", "bifidus", "activia", "actimel"},
        ),
        ("rice", {"arroz"}),
        ("pasta", set(_FAMILY_ALIASES["pasta"])),
        ("bread", set(_FAMILY_ALIASES["bread"])),
        ("milk", {"leche"}),
        ("oil", {"aceite"}),
    )
    if first == "arroz" and _has(words, "con leche"):
        return "prepared_meal"
    for family, heads in head_families:
        if head in heads:
            return family
    if len(ordered) >= 2 and ordered[:2] == ("l", "casei"):
        return "yogurt"
    positional_matches: list[tuple[int, int, str]] = []
    for family_order, (family, aliases) in enumerate(_FAMILY_ALIASES.items()):
        positions = [
            position
            for alias in aliases
            if (position := _alias_position(ordered, alias)) is not None
        ]
        if positions:
            positional_matches.append((min(positions), family_order, family))
    if positional_matches:
        return min(positional_matches)[2]
    return None


def _dietary_facets(text: str, facets: dict[str, str]) -> None:
    normalized = _normalize(text)
    for phrase, key in (
        ("sin lactosa", "lactose_free"),
        ("sin gluten", "gluten_free"),
        ("descafeinado", "decaffeinated"),
    ):
        if phrase in normalized:
            facets[key] = "yes"
    if (
        "sin azucar" in normalized
        or "sin azucares anadidos" in normalized
        or re.search(r"\b0\s*%\s*azucar", normalized)
    ):
        facets["no_added_sugar"] = "yes"


def _preserving_medium(words: set[str]) -> str | None:
    if _has(words, "aceite oliva") or _has(words, "aceite de oliva"):
        return "olive_oil"
    if _has(words, "aceite girasol") or _has(words, "aceite de girasol"):
        return "sunflower_oil"
    if "escabeche" in words:
        return "pickled"
    if _has(words, "al natural"):
        return "natural"
    if "tomate" in words:
        return "tomato"
    if "tinta" in words:
        return "ink"
    if words & {"salsa", "americana", "vieira", "picantona"}:
        return "sauce"
    return None


def _observed_alias_values(
    words: set[str], choices: Mapping[str, Iterable[str]]
) -> list[str]:
    return [
        value
        for value, aliases in choices.items()
        if any(_has(words, alias) for alias in aliases)
    ]


def _produce_variety(species: str | None, words: set[str]) -> str | None:
    choices: dict[str, dict[str, tuple[str, ...]]] = {
        "apple": {
            "golden": ("golden",),
            "pink_lady": ("pink lady",),
            "granny_smith": ("granny smith",),
            "royal_gala": ("royal gala",),
            "reineta": ("reineta",),
            "fuji": ("fuji",),
            "kanzi": ("kanzi",),
            "akane": ("akane",),
            "morgana": ("morgana",),
            "red_sweet": ("roja dulce",),
            "red_tart": ("roja acidulce",),
            "red": ("roja", "rojo"),
        },
        "orange": {
            "navelina": ("navelina",),
            "navel": ("navel",),
            "lane_late": ("lane late",),
            "salustiana": ("salustiana",),
        },
        "pear": {
            "conference": ("conference", "conferencia"),
            "ercolina": ("ercolina",),
            "blanquilla": ("blanquilla",),
            "limonera": ("limonera",),
        },
        "grape": {
            "red_seedless": ("roja sin semillas",),
            "white_seedless": ("blanca sin semillas",),
            "red": ("roja",),
            "white": ("blanca",),
        },
        "melon": {
            "piel_de_sapo": ("piel de sapo",),
            "galia": ("galia",),
            "cantaloupe": ("cantalupo", "cantaloupe"),
        },
        "watermelon": {"seedless": ("sin semillas",)},
        "kiwi": {"gold": ("gold", "amarillo"), "green": ("verde",)},
        "tomato": {
            "cherry": ("cherry",),
            "pear": ("pera",),
            "on_the_vine": ("en rama", "rama"),
            "pink": ("rosa",),
            "kumato": ("kumato",),
            "raf": ("raf",),
            "canary": ("canario", "canaria"),
        },
        "potato": {
            "kennebec": ("kennebec",),
            "agria": ("agria",),
            "monalisa": ("monalisa",),
            "red": ("roja", "rojas"),
            "new": ("nueva", "nuevas"),
        },
        "onion": {
            "shallot": ("chalota", "chalote"),
            "spring": ("tierna", "cebolleta"),
            "red": ("roja", "rojas"),
            "white": ("blanca", "blancas"),
            "sweet": ("dulce", "dulces"),
            "flat": ("chata",),
        },
        "lettuce": {
            "iceberg": ("iceberg",),
            "romaine": ("romana",),
            "batavia": ("batavia",),
            "oak_leaf": ("hoja de roble", "hoja roble"),
            "trocadero": ("trocadero",),
            "butterhead": ("mantecosa",),
            "curly": ("rizada", "riza"),
            "smooth": ("lisa",),
        },
        "pepper": {
            "padron": ("padron",),
            "piquillo": ("piquillo",),
            "italian": ("italiano", "italiana"),
            "red": ("rojo", "rojos"),
            "green": ("verde", "verdes"),
            "yellow": ("amarillo", "amarillos"),
        },
        "zucchini": {"green": ("verde",), "white": ("blanco",)},
        "pumpkin": {"butternut": ("violin", "cacahuete", "butternut")},
    }
    return _first(words, choices.get(species or "", {}))


def _produce_origin(text: str) -> str | None:
    normalized = _normalize(text)
    origins = {
        "canary_islands": ("canarias",),
        "galicia": ("galicia", "gallega", "gallego"),
        "basque_country": ("pais vasco", "euskadi", "euskal baserri"),
        "spain": ("espana", "espanola", "espanol"),
        "portugal": ("portugal", "portuguesa", "portugues"),
        "france": ("francia", "francesa", "frances"),
        "italy": ("italia",),
        "morocco": ("marruecos", "marroqui"),
        "costa_rica": ("costa rica",),
        "colombia": ("colombia",),
        "peru": ("peru",),
        "chile": ("chile",),
        "argentina": ("argentina",),
        "south_africa": ("sudafrica", "africa del sur"),
        "new_zealand": ("nueva zelanda",),
        "local": ("local",),
    }
    for origin, aliases in origins.items():
        if any(re.search(rf"\b{re.escape(alias)}\b", normalized) for alias in aliases):
            return origin
    return None


def _produce_calibre(text: str, words: set[str]) -> str | None:
    normalized = _normalize(text)
    match = _DIMENSION_RANGE_RE.search(normalized)
    if match:
        return f"{match.group(1)}-{match.group(2)}mm"
    match = _CALIBRE_RANGE_RE.search(normalized)
    if match:
        return f"{match.group(1)}-{match.group(2)}mm"
    if "calibre" in words:
        if "g" in words:
            return "large"
        if "m" in words:
            return "medium"
        if "p" in words:
            return "small"
    return _first(
        words,
        {
            "small": ("pequeno", "pequena", "pequenos", "pequenas"),
            "medium": ("mediano", "mediana", "medianos", "medianas"),
            "large": ("grande", "grandes"),
        },
    )


def _beverage_container(words: set[str]) -> str | None:
    return _first(
        words,
        {
            "glass_bottle": ("vidrio",),
            "carton": ("brik", "brick", "tetra brik", "tetrapak"),
            "can": ("lata", "latas"),
            "large_bottle": ("garrafa", "garrafas"),
            "small_bottle": ("botellin", "botellines", "botellon", "botellones"),
            "bottle": ("botella", "botellas", "pet"),
            "pouch": ("bolsita", "pouch"),
        },
    )


def _beverage_package(text: str, words: set[str]) -> str | None:
    match = _MULTIPACK_RE.search(_normalize(text))
    if match:
        return f"multipack_{int(match.group(1))}"
    if words & {"pack", "multipack"}:
        return "multipack"
    return None


def _beverage_flavor(words: set[str]) -> str | None:
    if words & {"cola"}:
        return "cola"
    if words & {"naranja"}:
        return "orange"
    if words & {"limon"} and words & {"lima"}:
        return "lemon_lime"
    if words & {"limon"}:
        return "lemon"
    if words & {"tonica"}:
        return "tonic"
    if words & {"jengibre", "ginger"}:
        return "ginger"
    if words & {"cafe"}:
        return "coffee"
    if words & {"chocolate", "cacao"}:
        return "chocolate"
    if words & {"vainilla"}:
        return "vanilla"
    if words & {"matcha"}:
        return "matcha"
    return None


def _alcohol_strength(text: str) -> str | None:
    match = _ABV_RE.search(_normalize(text))
    if not match:
        return None
    return match.group(1).replace(",", ".")


def _alcohol_profile(
    text: str,
    words: set[str],
    *,
    for_query: bool,
) -> str | None:
    normalized = _normalize(text)
    if _has(words, "sin alcohol") or re.search(r"\b0\s*[,\.]\s*0\b", normalized):
        return "alcohol_free"
    strength = _alcohol_strength(text)
    if strength is not None and float(strength) < 1:
        return "low_alcohol"
    return None if for_query else "alcoholic"


def _prepared_meal_facets(
    words: set[str],
    text: str,
    *,
    for_query: bool,
    primary_words: set[str] | None,
) -> dict[str, str]:
    """Extract distinctions that determine whether two prepared foods substitute."""

    meal_words = primary_words or words
    ordered = _ordered_words(text)
    type_aliases: dict[str, tuple[str, ...]] = {
        "broth": ("caldo", "caldos"),
        "pizza": ("pizza", "pizzas"),
        "croquette": ("croqueta", "croquetas"),
        "empanada": ("empanada", "empanadas", "empanadilla", "empanadillas"),
        "lasagna": ("lasana",),
        "salad": ("ensalada", "ensaladas"),
        "soup": ("sopa", "sopas"),
        "cream_soup": ("crema", "cremas"),
        "paella": ("paella",),
        "rice_dish": ("arroz",),
        "pasta_dish": (
            "macarrones",
            "espaguetis",
            "spaghetti",
            "tallarines",
            "pasta",
        ),
        "omelette": ("tortilla", "tortillas"),
        "stir_fry": ("salteado", "salteados"),
        "puree": ("pure", "pures"),
        "poke": ("poke",),
        "sandwich": ("sandwich",),
        "fried_assortment": ("fritura",),
    }
    positioned_types: list[tuple[int, int, str]] = []
    for type_order, (meal_type, aliases) in enumerate(type_aliases.items()):
        positions = [
            position
            for alias in aliases
            if (position := _alias_position(ordered, alias)) is not None
        ]
        if positions:
            positioned_types.append((min(positions), type_order, meal_type))
    meal_type = min(positioned_types)[2] if positioned_types else "prepared_other"
    if meal_type == "rice_dish" and _has(meal_words, "con leche"):
        meal_type = "rice_dessert"

    facets: dict[str, str] = {"meal_type": meal_type}

    if words & {
        "congelado",
        "congelada",
        "congelados",
        "congeladas",
        "ultracongelado",
        "ultracongelada",
        "ultracongelados",
        "ultracongeladas",
    }:
        facets["preservation"] = "frozen"
    elif words & {"refrigerado", "refrigerada", "refrigerados", "refrigeradas"}:
        facets["preservation"] = "refrigerated"
    elif meal_type == "pizza" and meal_words & {"fresca", "fresco"}:
        facets["preservation"] = "refrigerated"
    elif words & {
        "deshidratado",
        "deshidratada",
        "deshidratados",
        "deshidratadas",
        "liofilizado",
        "liofilizada",
    }:
        facets["preservation"] = "dried"

    normalized = _normalize(text)
    mass_match = _MASS_RE.search(normalized)
    small_dry_soup = bool(
        meal_type in {"soup", "cream_soup"}
        and mass_match
        and mass_match.group(2).casefold() == "g"
        and float(mass_match.group(1).replace(",", ".")) <= 120
        and not meal_words & {"botella", "brik", "frasco", "tarrina"}
    )
    if small_dry_soup:
        facets["preservation"] = "dried"
    broth_form = None
    if meal_type == "broth":
        broth_form = _first(
            meal_words,
            {
                "cube": ("pastilla", "pastillas", "cubito", "cubitos"),
                "powder": ("polvo", "granulado", "granulado"),
                "concentrate": ("concentrado", "concentrada"),
                "liquid": ("liquido", "brik", "brick", "botella"),
            },
        )
        if not broth_form and not for_query:
            broth_form = "liquid_or_unspecified"
        if broth_form:
            facets["broth_form"] = broth_form
    if meal_type == "broth":
        meal_format = "broth"
    elif meal_type == "paella" and (
        _has(meal_words, "preparado para paella")
        or re.search(r"\bpreparado\b.*\bpaella\b", normalized)
    ):
        meal_format = "ingredient_kit"
    elif small_dry_soup or meal_words & {
        "sobre",
        "sobres",
        "polvo",
        "deshidratado",
        "deshidratada",
    }:
        meal_format = "dry_mix"
    elif meal_type == "salad" and (
        meal_words & {"brote", "brotes", "hoja", "hojas", "mezcla"}
        or _has(meal_words, "cuatro estaciones")
        or _has(meal_words, "4 estaciones")
        or (
            meal_words & {"bolsa", "gourmet", "seleccion", "mixta"}
            and not meal_words
            & {
                "atun",
                "pollo",
                "pasta",
                "arroz",
                "cesar",
                "caesar",
                "calvo",
            }
        )
    ):
        meal_format = "leafy_mix"
    else:
        meal_format = "complete_dish"
    if meal_format == "ingredient_kit" and meal_type == "paella":
        meal_type = "paella_kit"
        facets["meal_type"] = meal_type
    format_is_explicit = (
        meal_format != "complete_dish"
        and not (meal_type == "broth" and for_query and broth_form is None)
    ) or bool(meal_words & {"plato", "listo", "lista", "calentar", "microondas"})
    if not for_query or format_is_explicit:
        facets["meal_format"] = meal_format

    if meal_format == "dry_mix":
        meal_preparation = "needs_reconstitution"
    elif meal_format == "ingredient_kit":
        meal_preparation = "needs_cooking"
    elif meal_format == "broth":
        meal_preparation = (
            "ready_to_use"
            if broth_form in {None, "liquid", "liquid_or_unspecified"}
            else "needs_dilution"
        )
    elif meal_type in {"salad", "poke", "sandwich"}:
        meal_preparation = "ready_to_eat"
    elif meal_type in {"soup", "cream_soup", "puree"}:
        meal_preparation = "ready_to_heat"
    elif meal_type != "prepared_other" or not for_query:
        meal_preparation = "heat_or_cook"
    else:
        meal_preparation = None
    if meal_preparation and (not for_query or format_is_explicit):
        facets["meal_preparation"] = meal_preparation

    four_cheese = bool(
        re.search(r"\b(?:4|cuatro)\s+quesos?\b", normalized)
    )
    if meal_type == "pizza":
        variant = _first(
            meal_words,
            {
                "barbecue": ("barbacoa", "bbq"),
                "pepperoni": ("pepperoni",),
                "prosciutto": ("prosciutto",),
                "margherita": ("margarita", "margherita"),
            },
        )
        if four_cheese:
            variant = "four_cheese"
        elif "jamon" in meal_words and "queso" in meal_words:
            variant = "ham_cheese"
        if variant:
            facets["meal_variant"] = variant
    elif meal_type == "lasagna":
        variant = _first(
            meal_words,
            {
                "bolognese": ("bolonesa", "bolognese"),
                "spinach_cheese": ("espinacas queso",),
                "vegetable": ("vegetal", "verduras"),
            },
        )
        if variant:
            facets["meal_variant"] = variant
    elif meal_type == "salad":
        variant = _first(
            meal_words,
            {
                "caesar": ("cesar", "caesar"),
                "pasta": ("pasta",),
                "rice": ("arroz",),
                "leafy_mix": ("brotes", "mezcla", "cuatro estaciones", "4 estaciones"),
            },
        )
        if variant:
            facets["meal_variant"] = variant
    elif meal_type == "soup":
        variant = _first(
            meal_words,
            {
                "minestrone": ("minestrone",),
                "cocido": ("cocido",),
                "letters": ("letras", "letritas"),
                "noodles": ("fideos", "fideo"),
            },
        )
        if variant:
            facets["meal_variant"] = variant
    elif meal_type == "paella":
        if meal_words & {"mixta", "mixto"}:
            facets["meal_variant"] = "mixed"
        elif meal_words & {"marisco", "mariscos", "marinera"}:
            facets["meal_variant"] = "seafood"
        elif "pollo" in meal_words and meal_words & {"verdura", "verduras", "vegetal"}:
            facets["meal_variant"] = "chicken_vegetable"

    if meal_type == "pizza" and four_cheese:
        main_ingredient = "cheese"
    elif meal_type == "pizza" and "jamon" in meal_words and "queso" in meal_words:
        main_ingredient = "ham_cheese"
    elif meal_type == "lasagna" and meal_words & {"espinaca", "espinacas"} and "queso" in meal_words:
        main_ingredient = "spinach_cheese"
    elif meal_type == "paella" and meal_words & {"mixta", "mixto"}:
        main_ingredient = "mixed"
    else:
        main_ingredient = _first(
            meal_words,
            {
                "iberian_ham": ("jamon iberico",),
                "ham": ("jamon",),
                "chicken": ("pollo",),
                "cod": ("bacalao",),
                "hake": ("merluza",),
                "squid": ("calamar", "calamares", "tinta de calamar"),
                "tuna": ("atun", "bonito"),
                "seafood": ("marisco", "mariscos", "marinera"),
                "beef": ("vacuno", "ternera"),
                "pork": ("cerdo",),
                "meat": ("carne", "bolonesa"),
                "stew": ("cocido",),
                "cheese": ("queso", "camembert", "mozzarella"),
                "legumes": ("legumbre", "legumbres", "lenteja", "lentejas"),
                "apple": ("manzana",),
                "vegetable": (
                    "vegetal",
                    "vegetales",
                    "verdura",
                    "verduras",
                    "espinaca",
                    "espinacas",
                    "calabacin",
                    "vegano",
                    "vegana",
                ),
            },
        )
    if main_ingredient:
        facets["main_ingredient"] = main_ingredient
    return facets


def _remaining_family_facets(
    family: str | None,
    words: set[str],
    text: str,
    *,
    for_query: bool,
    primary_words: set[str] | None,
) -> dict[str, str]:
    """Facets for pantry, household, personal-care, baby and pet products."""

    observed = primary_words or words
    normalized = _normalize(text)
    facets: dict[str, str] = {}

    if family == "flour":
        source = _first(
            observed,
            {
                "wheat": ("trigo",),
                "corn": ("maiz",),
                "rice": ("arroz",),
                "chickpea": ("garbanzo",),
                "oat": ("avena",),
                "rye": ("centeno",),
                "spelt": ("espelta",),
                "almond": ("almendra",),
            },
        )
        if source:
            facets["flour_source"] = source
        if "integral" in observed:
            facets["flour_treatment"] = "wholegrain"
        elif not for_query:
            facets["flour_treatment"] = "refined_or_unspecified"
        use = _first(
            observed,
            {
                "strong_bread": ("fuerza",),
                "pastry": ("reposteria", "bizcocho"),
                "frying": ("fritos", "fritura", "tempura"),
                "pizza": ("pizza", "pizzeria"),
                "bread": ("panificable", "pan"),
            },
        )
        if use:
            facets["flour_use"] = use

    if family == "sweetener":
        kind = _first(
            observed,
            {
                "erythritol": ("eritritol",),
                "stevia": ("stevia",),
                "saccharin": ("sacarina",),
                "sucralose": ("sucralosa",),
                "panela": ("panela",),
                "icing_sugar": ("azucar glas", "azucar glace"),
                "vanilla_sugar": ("azucar vainillado",),
                "brown_sugar": ("azucar moreno", "azucar de cana"),
                "white_sugar": ("azucar blanco",),
            },
        )
        if kind:
            facets["sweetener_kind"] = kind
        form = _first(
            observed,
            {
                "liquid": ("liquido", "liquida"),
                "tablets": ("comprimidos", "pastillas"),
                "sachets": ("sobres",),
                "cubes": ("terrones",),
                "powder": ("polvo", "granulado", "glas", "glace"),
            },
        )
        if form:
            facets["sweetener_form"] = form
        elif not for_query:
            facets["sweetener_form"] = "granulated_or_unspecified"

    if family == "salt":
        source = _first(
            observed,
            {
                "himalayan": ("himalaya", "rosa"),
                "smoked": ("ahumada", "ahumado"),
                "sea": ("marina", "mar"),
                "spring": ("anana",),
            },
        )
        if source:
            facets["salt_source"] = source
        grain = _first(
            observed,
            {
                "flakes": ("escamas", "maldon"),
                "coarse": ("gruesa", "grueso"),
                "fine": ("fina", "fino", "mesa"),
            },
        )
        if grain:
            facets["salt_grain"] = grain
        if observed & {"yodada", "yodado", "yodo"}:
            facets["iodized"] = "yes"
        elif not for_query:
            facets["iodized"] = "not_observed"
        flavor = _first(
            observed,
            {
                "garlic": ("ajo",),
                "herbs": ("hierbas",),
                "truffle": ("trufa",),
            },
        )
        if flavor:
            facets["salt_flavor"] = flavor

    if family == "sauce":
        sauce_type = _first(
            observed,
            {
                "mayonnaise": ("mayonesa", "mahonesa", "salsa fina", "ligeresa"),
                "ketchup": ("ketchup",),
                "mustard": ("mostaza",),
                "soy": ("soja",),
                "barbecue": ("barbacoa", "bbq"),
                "tomato": ("tomate",),
                "pesto": ("pesto",),
                "bechamel": ("bechamel",),
                "aioli": ("allioli", "alioli"),
                "guacamole": ("guacamole",),
                "bolognese": ("bolonesa",),
                "teriyaki": ("teriyaki",),
                "yogurt": ("yogur",),
                "kebab": ("kebab",),
                "hot": ("picante", "brava", "tabasco"),
            },
        )
        if sauce_type:
            facets["sauce_type"] = sauce_type
        style = _first(
            observed,
            {
                "light": ("light", "ligera", "ligero"),
                "reduced_salt": ("menos sal", "reducida en sal"),
                "spicy": ("picante", "spicy"),
                "mild": ("suave",),
            },
        )
        if style:
            facets["sauce_style"] = style
        if _has(observed, "sin azucar") or observed & {"zero", "cero"}:
            facets["sugar_profile"] = "sugar_free"

    if family == "preserve":
        content = _first(
            observed,
            {
                "vegetable": ("verdura", "verduras", "hortalizas"),
                "fish": ("pescado", "pescados"),
                "seafood": ("marisco", "mariscos"),
                "meat": ("carne",),
                "fruit": ("fruta", "frutas"),
            },
        )
        if content:
            facets["preserve_content"] = content
        medium = _preserving_medium(observed)
        if medium:
            facets["preserving_medium"] = medium

    if family == "cereal_product":
        base = _first(
            observed,
            {
                "oat": ("avena",),
                "corn": ("maiz", "corn"),
                "wheat": ("trigo",),
                "rice": ("arroz", "krispies"),
                "mixed": ("multicereales",),
            },
        )
        if base:
            facets["cereal_base"] = base
        form = _first(
            observed,
            {
                "filled": ("relleno", "rellenos"),
                "flakes": ("copos", "flakes"),
                "muesli": ("muesli",),
                "granola": ("granola", "crunchy"),
                "biscuits": ("weetabix",),
            },
        )
        if form:
            facets["cereal_form"] = form
        flavor = _first(
            observed,
            {
                "chocolate_hazelnut": ("chocolate avellana",),
                "chocolate": ("chocolate", "choco"),
                "honey": ("miel",),
                "milk": ("leche",),
                "fruit": ("frutas", "frutos rojos"),
            },
        )
        if flavor:
            facets["cereal_flavor"] = flavor
        if _has(observed, "sin azucar") or _has(observed, "0 azucares"):
            facets["sugar_profile"] = "sugar_free"

    if family == "cookie":
        style = _first(
            observed,
            {
                "digestive": ("digestive",),
                "maria": ("maria",),
                "cookie": ("cookie", "cookies", "chips ahoy"),
                "wafer": ("barquillo", "wafer"),
                "shortbread": ("mantequilla",),
                "oat": ("avena",),
            },
        )
        if style:
            facets["cookie_style"] = style
        flavor = _first(
            observed,
            {
                "chocolate": ("chocolate", "cacao"),
                "cinnamon": ("canela", "napolitanas"),
                "lemon": ("limon",),
                "coconut": ("coco",),
            },
        )
        if flavor:
            facets["cookie_flavor"] = flavor
        if observed & {"rellena", "rellenas", "relleno", "rellenos"}:
            facets["cookie_filling"] = flavor or "filled_unspecified"
        elif not for_query:
            facets["cookie_filling"] = "unfilled"
        if _has(observed, "sin azucar") or _has(observed, "sin azucares"):
            facets["sugar_profile"] = "sugar_free"

    if family == "snack":
        base = _first(
            observed,
            {
                "potato": ("patata", "patatas", "pringles"),
                "corn": ("maiz", "nachos", "cheetos"),
                "pork": ("cerdo", "cortezas"),
                "meat": ("fuet", "chorizo", "salami"),
                "mixed": ("cocktail", "mix"),
            },
        )
        if base:
            facets["snack_base"] = base
        form = _first(
            observed,
            {
                "chips": ("chips", "pringles"),
                "rings": ("aros",),
                "cones": ("conos",),
                "balls": ("balls", "bolas", "pelotazos"),
                "sticks": ("palitos",),
            },
        )
        if form:
            facets["snack_form"] = form
        flavor = _first(
            observed,
            {
                "barbecue": ("barbacoa", "bbq"),
                "cheese": ("queso",),
                "ham": ("jamon",),
                "paprika": ("paprika",),
                "bacon": ("bacon",),
                "plain_salted": ("sal", "salado", "salada"),
            },
        )
        if flavor:
            facets["snack_flavor"] = flavor

    if family == "seasoning":
        kind = _first(
            observed,
            {
                "garlic": ("ajo",),
                "onion": ("cebolla",),
                "ginger": ("jengibre",),
                "paprika": ("pimenton",),
                "pepper": ("pimienta",),
                "cinnamon": ("canela",),
                "turmeric": ("curcuma",),
                "cumin": ("comino",),
                "oregano": ("oregano",),
                "saffron": ("azafran",),
                "mixed": ("mezcla especias", "especias surtidas", "chimichurri"),
            },
        )
        if kind:
            facets["seasoning_type"] = kind
        form = _first(
            observed,
            {
                "whole": ("rama", "grano", "hebra", "guindillas"),
                "ground": ("molido", "molida", "polvo"),
                "granulated": ("granulado", "granulada"),
            },
        )
        if form:
            facets["seasoning_form"] = form

    if family == "baking_ingredient":
        kind = _first(
            observed,
            {
                "fresh_yeast": ("levadura fresca",),
                "dry_yeast": ("levadura seca", "levadura panaderia"),
                "baking_powder": ("levadura quimica", "impulsor", "gasificante"),
            },
        )
        if kind:
            facets["baking_kind"] = kind
        if observed & {"pan", "panaderia"}:
            facets["baking_use"] = "bread"
        elif observed & {"bizcocho", "reposteria"}:
            facets["baking_use"] = "pastry"

    if family in {"detergent", "fabric_softener"}:
        if observed & {"concentrado", "concentrada", "ultra"}:
            facets["concentration"] = "concentrated"
        elif observed & {"diluido", "diluida"}:
            facets["concentration"] = "diluted"
    if family == "fabric_softener":
        scent = _first(
            observed,
            {
                "blue": ("azul",),
                "floral": ("floral", "flores"),
                "talc": ("talco",),
                "lavender": ("lavanda",),
                "nenuco": ("nenuco",),
                "violet": ("violeta", "violets"),
            },
        )
        if scent:
            facets["scent"] = scent
        if observed & {"hipoalergenico", "hipoalergenica", "sensitive"}:
            facets["hypoallergenic"] = "yes"

    if family == "bleach":
        use = _first(
            observed,
            {
                "laundry": ("lavadora", "ropa"),
                "bathroom": ("bano", "wc"),
                "multiuse": ("multiusos", "hogar"),
            },
        )
        if use:
            facets["bleach_use"] = use
        if "detergente" in observed:
            facets["detergent_added"] = "yes"
        elif not for_query:
            facets["detergent_added"] = "not_observed"
        if observed & {"perfumada", "perfumado", "limon", "pino"}:
            facets["scented"] = "yes"

    if family == "household_cleaner":
        use = _first(
            observed,
            {
                "toilet": ("wc", "inodoro"),
                "bathroom": ("bano", "banos"),
                "floor": ("suelo", "suelos"),
                "wood": ("madera",),
                "limescale": ("antical", "cal"),
                "kitchen": ("cocina", "grasa"),
                "multiuse": ("multiusos",),
            },
        )
        if use:
            facets["cleaner_use"] = use
        form = _first(
            observed,
            {
                "gel": ("gel",),
                "spray": ("spray", "pistola"),
                "liquid": ("liquido", "liquida"),
            },
        )
        if form:
            facets["cleaner_form"] = form
        if "lejia" in observed:
            facets["bleach_added"] = "yes"

    if family == "dishwasher_additive":
        additive = _first(
            observed,
            {
                "salt": ("sal",),
                "rinse_aid": ("abrillantador",),
                "cleaner": ("limpiamaquinas", "limpiador"),
            },
        )
        if additive:
            facets["dishwasher_additive_type"] = additive

    if family == "trash_bag":
        capacity = re.search(r"\b(\d{1,3})\s*l(?:itros?)?\b", normalized)
        if capacity:
            facets["capacity_l"] = capacity.group(1)
        if observed & {"autocierre", "cierre"}:
            facets["bag_closure"] = "drawstring"
        elif not for_query:
            facets["bag_closure"] = "plain_or_unspecified"
        if observed & {"perfumada", "perfumadas"}:
            facets["scented"] = "yes"

    if family == "kitchen_paper":
        if observed & {"gigante", "gigarrollo", "jumbo"}:
            facets["paper_form"] = "jumbo_roll"
        elif not for_query:
            facets["paper_form"] = "standard_roll"
        ply = re.search(r"\b(\d)\s*capas?\b", normalized)
        if ply:
            facets["paper_ply"] = ply.group(1)

    if family == "shampoo":
        need = _first(
            observed,
            {
                "damaged": ("danado", "danados", "repair", "repara"),
                "dry": ("seco", "secos"),
                "oily": ("graso", "grasos"),
                "colored": ("tenido", "color"),
                "curly": ("rizado", "rizos"),
                "sensitive": ("sensible",),
                "all_hair": ("todo tipo", "familiar", "clasico"),
            },
        )
        if need:
            facets["hair_need"] = need
        if observed & {"anticaspa", "caspa"}:
            facets["anti_dandruff"] = "yes"

    if family in {"shower_gel", "soap", "skincare"}:
        need = _first(
            observed,
            {
                "sensitive": ("sensible", "dermo"),
                "dry": ("seca", "seco", "nutritivo"),
                "normal": ("normal",),
                "hydrating": ("hidratante", "hidratacion"),
            },
        )
        if need:
            facets["skin_need"] = need
    if family == "soap":
        if "manos" in observed:
            facets["soap_use"] = "hands"
        elif observed & {"ducha", "cuerpo", "bano"} or not for_query:
            facets["soap_use"] = "body"
        if observed & {"liquido", "dosificador"}:
            facets["soap_form"] = "liquid"
        elif observed & {"pastilla", "pastillas"} or not for_query:
            facets["soap_form"] = "bar"
    if family == "skincare":
        use = _first(
            observed,
            {
                "face": ("facial", "cara"),
                "body": ("corporal", "cuerpo"),
                "hands": ("manos",),
                "feet": ("pies",),
                "sun": ("solar",),
                "depilatory": ("depilatoria",),
            },
        )
        if use:
            facets["skincare_use"] = use

    if family == "deodorant":
        form = _first(
            observed,
            {
                "roll_on": ("roll on", "rollon"),
                "stick": ("stick",),
                "cream": ("crema",),
                "spray": ("spray", "aerosol"),
            },
        )
        if form:
            facets["deodorant_form"] = form
        if observed & {"antitranspirante", "antiperspirant"}:
            facets["antiperspirant"] = "yes"
        user = _first(
            observed,
            {
                "men": ("hombre", "men"),
                "women": ("mujer", "women"),
            },
        )
        if user:
            facets["target_user"] = user

    if family in {"toothpaste", "mouthwash"}:
        need = _first(
            observed,
            {
                "whitening": ("blanqueador", "blanqueadora", "white"),
                "gum_care": ("encias", "gingival"),
                "sensitive": ("sensible", "sensibilidad"),
                "cavity": ("anticaries", "caries"),
                "fresh": ("fresh", "frescor", "menta"),
            },
        )
        if need:
            facets["oral_need"] = need
    if family == "mouthwash" and _has(observed, "sin alcohol"):
        facets["alcohol_free"] = "yes"
    if family == "toothbrush":
        hardness = _first(
            observed,
            {
                "soft": ("suave",),
                "medium": ("medio", "media"),
                "hard": ("duro", "dura"),
            },
        )
        if hardness:
            facets["brush_hardness"] = hardness
        if observed & {"electrico", "electrica", "recambio"}:
            facets["brush_kind"] = "electric_or_refill"
        elif not for_query:
            facets["brush_kind"] = "manual"
    if family in {"shampoo", "shower_gel", "toothpaste", "toothbrush", "mouthwash"}:
        if observed & {"infantil", "nino", "nina", "kids"}:
            facets["target_age"] = "child"
        elif not for_query:
            facets["target_age"] = "adult_or_unspecified"

    if family == "feminine_hygiene":
        hygiene_type = _first(
            observed,
            {
                "tampon": ("tampon", "tampones"),
                "liner": ("protegeslip",),
                "pad": ("compresa", "compresas"),
            },
        )
        if hygiene_type:
            facets["hygiene_type"] = hygiene_type
        absorbency = _first(
            observed,
            {
                "night": ("noche", "nocturna"),
                "super": ("super",),
                "normal": ("normal",),
            },
        )
        if absorbency:
            facets["absorbency"] = absorbency
        if observed & {"alas"}:
            facets["with_wings"] = "yes"

    if family == "baby_hygiene":
        facets["baby_hygiene_type"] = (
            "diaper" if observed & {"panal", "panales"} else "wipes"
        )
        size = re.search(r"\btalla\s*(\d|[xsml]+)\b", normalized)
        if size:
            facets["baby_size"] = size.group(1)

    if family == "baby_food":
        food_type = _first(
            observed,
            {
                "pouch": ("bolsita", "pouch"),
                "jar": ("potito", "tarrito", "tarro"),
                "porridge": ("papilla", "cereales"),
                "snack": ("panecitos", "snack"),
            },
        )
        if food_type:
            facets["baby_food_type"] = food_type
        age = re.search(r"\+?\s*(\d{1,2})\s*meses?\b", normalized)
        if age:
            facets["age_band"] = f"{age.group(1)}m_plus"
        ingredient = _first(
            observed,
            {
                "fish": ("merluza", "pescado"),
                "chicken": ("pollo",),
                "beef": ("ternera", "buey"),
                "mixed_meat": ("pollo ternera",),
                "fruit": ("fruta", "frutas", "manzana", "platano", "fresa"),
                "vegetable": ("verdura", "verduras", "zanahoria"),
                "cereal": ("cereal", "cereales"),
            },
        )
        if ingredient:
            facets["main_ingredient"] = ingredient

    if family == "pet_food":
        species = _first(
            observed,
            {
                "dog": ("perro", "perros"),
                "cat": ("gato", "gatos"),
                "rabbit": ("conejo", "conejos"),
                "hamster": ("hamster", "hamsters"),
            },
        )
        if species:
            facets["pet_species"] = species
        food_form = _first(
            observed,
            {
                "snack": ("snack", "snacks", "biscrock", "barrita", "palitos"),
                "wet": ("pate", "lata", "tarrina", "salsa", "gelatina", "bocadito"),
                "dry": ("pienso", "saco", "croqueta", "croquetas"),
            },
        )
        if food_form:
            facets["pet_food_form"] = food_form
        stage = _first(
            observed,
            {
                "junior": ("junior", "junior", "cachorro"),
                "senior": ("senior",),
                "adult": ("adulto", "adulta"),
            },
        )
        if stage:
            facets["pet_life_stage"] = stage
        need = _first(
            observed,
            {
                "sterilized": ("esterilizado", "esterilizados"),
                "mini": ("mini",),
                "light": ("light",),
                "hairball": ("malta", "bolas"),
            },
        )
        if need:
            facets["pet_need"] = need
        ingredient = _first(
            observed,
            {
                "salmon": ("salmon",),
                "tuna": ("atun",),
                "chicken": ("pollo",),
                "turkey": ("pavo",),
                "beef": ("ternera", "buey", "vacuno"),
                "meat": ("carne", "carnes"),
                "mixed": ("pollo ternera", "salmon atun"),
            },
        )
        if ingredient:
            facets["main_ingredient"] = ingredient

    return facets


def _family_facets(
    family: str | None,
    words: set[str],
    text: str,
    *,
    for_query: bool,
    primary_words: set[str] | None = None,
) -> dict[str, str]:
    facets: dict[str, str] = {}
    _dietary_facets(text, facets)
    facets.update(
        _remaining_family_facets(
            family,
            words,
            text,
            for_query=for_query,
            primary_words=primary_words,
        )
    )
    if family == "prepared_meal":
        facets.update(
            _prepared_meal_facets(
                words,
                text,
                for_query=for_query,
                primary_words=primary_words,
            )
        )
    if family in {"milk", "yogurt"}:
        value = _first(words, _FACET_ALIASES["milk_fat"])
        if value:
            facets["milk_fat"] = value
    if family == "oil":
        value = _first(words, _FACET_ALIASES["oil_source"])
        if value:
            facets["oil_source"] = value
        normalized = _normalize(text)
        if "virgen extra" in normalized:
            facets["oil_grade"] = "extra_virgin"
        elif "virgen" in words:
            facets["oil_grade"] = "virgin"
        elif "suave" in words or "0 4" in normalized:
            facets["oil_grade"] = "mild"
        oil_use = _first(
            words,
            {
                "frying": ("freir", "fritura"),
                "salad": ("ensalada", "crudo"),
                "baking": ("reposteria",),
            },
        )
        if oil_use:
            facets["oil_use"] = oil_use
    if family == "coffee":
        for key in ("coffee_form",):
            value = _first(words, _FACET_ALIASES[key])
            if value:
                facets[key] = value
        roast = _first(
            words,
            {
                "torrefacto": ("torrefacto",),
                "blend": ("mezcla",),
                "natural": ("natural",),
            },
        )
        if roast:
            facets["coffee_roast"] = roast
        origin = _first(
            words,
            {country: (country,) for country in ("colombia", "brasil", "etiopia", "kenia", "peru")},
        )
        if origin:
            facets["origin"] = origin
        intensity = re.search(r"\bintensidad\s*(\d{1,2})\b", _normalize(text))
        if intensity:
            facets["coffee_intensity"] = intensity.group(1)
    if family == "detergent":
        if words & {"lavavajillas", "lavaplatos", "vajillas"}:
            if words & {"mano", "manual", "diluido", "diluida"}:
                facets["detergent_use"] = "hand_dishwashing"
            else:
                facets["detergent_use"] = "dishwasher_machine"
        elif not for_query:
            facets["detergent_use"] = "laundry"
        value = _first(words, _FACET_ALIASES["detergent_form"])
        if value:
            facets["detergent_form"] = value
        if facets.get("detergent_use") in {None, "laundry"}:
            specialty = _first(
                words,
                {
                    "dark": ("negro", "negra", "oscuros"),
                    "delicates": ("delicadas", "delicados"),
                    "colors": ("color", "colores"),
                },
            )
            detergent_text = _normalize(text)
            if "ropa blanca" in detergent_text or "colada blanca" in detergent_text:
                specialty = "white"
            if specialty:
                facets["laundry_specialty"] = specialty
    if family == "cheese":
        for key in ("cheese_cure", "cheese_form"):
            value = _first(words, _FACET_ALIASES[key])
            if value:
                facets[key] = value
        designation = _first(words, _DESIGNATIONS)
        if designation:
            facets["designation"] = designation
        variety = _first(
            words,
            {
                "grana_padano": ("grana padano",),
                "parmigiano": ("parmigiano", "parmesano"),
                "mozzarella": ("mozzarella",),
                "feta": ("feta",),
                "greek_style": ("griego", "griega"),
                "cottage": ("cottage",),
                "burgos": ("burgos",),
                "cheddar": ("cheddar",),
                "gouda": ("gouda",),
                "edam": ("edam",),
                "emmental": ("emmental",),
                "brie": ("brie",),
                "camembert": ("camembert",),
                "blue": ("azul", "blue", "roquefort", "gorgonzola"),
                "mascarpone": ("mascarpone",),
                "ricotta": ("ricotta",),
                "provolone": ("provolone",),
                "manchego": ("manchego",),
                "idiazabal": ("idiazabal",),
                "cabrales": ("cabrales",),
                "tetilla": ("tetilla",),
                "arzua_ulloa": ("arzua", "ulloa"),
            },
        )
        if variety:
            facets["cheese_variety"] = variety
        if "fundido" in words or "fundida" in words:
            facets["cheese_processing"] = "processed"
        if words & {"light", "ligero", "ligera", "desnatado", "desnatada"} or re.search(
            r"\b0\s*%\s*(?:mg|materia grasa)", _normalize(text)
        ):
            facets["cheese_fat"] = "light"
        elif not for_query:
            facets["cheese_fat"] = "standard"
        milk_sources = [
            source
            for source, aliases in (
                ("cow", {"vaca", "vacuno"}),
                ("goat", {"cabra"}),
                ("sheep", {"oveja"}),
                ("buffalo", {"bufala"}),
            )
            if words & aliases
        ]
        if "mezcla" in words or len(milk_sources) > 1:
            facets["cheese_milk"] = "mixed"
        elif milk_sources:
            facets["cheese_milk"] = milk_sources[0]
    if family == "rice":
        value = _first(
            words,
            {
                **_FACET_ALIASES["rice_variety"],
                "arborio": ("arborio",),
                "carnaroli": ("carnaroli",),
                "sushi": ("sushi",),
                "wild": ("salvaje",),
            },
        )
        if value:
            facets["rice_variety"] = value
        if "vaporizado" in words:
            facets["rice_treatment"] = "parboiled"
        elif "integral" in words:
            facets["rice_treatment"] = "brown"
        elif not for_query:
            facets["rice_treatment"] = "standard"
        rice_use = _first(
            words,
            {
                "soupy": ("caldoso", "caldosos"),
                "dessert": ("postre", "postres"),
                "risotto": ("risotto",),
                "sushi": ("sushi",),
            },
        )
        if rice_use:
            facets["rice_use"] = rice_use
        elif not for_query:
            facets["rice_use"] = "general"
        if words & {"vasito", "vasitos", "cocido", "listo"}:
            facets["preparation"] = "ready"
        elif not for_query:
            facets["preparation"] = "dry"
    if family == "yogurt":
        style = _first(words, _FACET_ALIASES["yogurt_style"])
        if style:
            facets["yogurt_style"] = style
        elif not for_query:
            facets["yogurt_style"] = "standard"
        flavor = _first(
            words,
            {
                "plain": ("natural",),
                "strawberry": ("fresa",),
                "lemon_lime": ("limon", "lima limon"),
                "stracciatella": ("stracciatella",),
                "caramel": ("caramelo",),
                "blueberry": ("arandano", "arandanos"),
                "peach": ("melocoton",),
                "banana": ("platano", "banana"),
                "vanilla": ("vainilla",),
                "coconut": ("coco",),
                "mango": ("mango",),
                "coffee": ("cafe",),
                "chocolate": ("chocolate", "cacao"),
                "mixed_fruit": ("frutas", "multifrutas"),
            },
        )
        if flavor:
            facets["yogurt_flavor"] = flavor
        if words & {"bebible", "liquido", "actimel"} or _has(words, "l casei"):
            facets["yogurt_format"] = "drinkable"
        elif not for_query:
            facets["yogurt_format"] = "spoonable"
    if family == "chocolate":
        if "blanco" in words:
            facets["chocolate_type"] = "white"
        elif _has(words, "con leche"):
            facets["chocolate_type"] = "milk"
        elif "negro" in words:
            facets["chocolate_type"] = "dark"
        normalized = _normalize(text)
        percentage = None
        for match in _PERCENT_RE.finditer(normalized):
            candidate = int(match.group(1))
            following = normalized[match.end() : match.end() + 24]
            if candidate == 0 or re.match(r"\s*(?:azucar|materia grasa|mg)\b", following):
                continue
            percentage = candidate
            break
        if percentage is not None:
            if percentage >= 95:
                facets["cacao_band"] = "ultra_dark"
            elif percentage >= 85:
                facets["cacao_band"] = "extra_dark"
            elif percentage >= 70:
                facets["cacao_band"] = "high_dark"
            elif percentage >= 50:
                facets["cacao_band"] = "medium_dark"
            else:
                facets["cacao_band"] = "low_cacao"
        if _has(words, "a la taza"):
            facets["chocolate_form"] = "drinking"
        elif words & {"cobertura", "postres", "reposteria", "fundir"}:
            facets["chocolate_form"] = "baking"
        elif not for_query:
            facets["chocolate_form"] = "table"
        addition_aliases = {
            "almond": ("almendra", "almendras"),
            "hazelnut": ("avellana", "avellanas"),
            "orange": ("naranja",),
            "cookie": ("galleta", "galletas", "oreo", "lotus"),
            "filled": ("relleno", "rellena", "rellenos", "rellenas"),
            "caramel": ("caramelo", "caramel", "dulce de leche"),
            "cheesecake": ("cheesecake",),
            "candy": ("lacasitos", "smarties"),
        }
        additions = sorted(
            addition
            for addition, aliases in addition_aliases.items()
            if any(_has(words, alias) for alias in aliases)
        )
        if additions:
            facets["additions"] = "+".join(additions)
        elif not for_query:
            facets["additions"] = "plain"
    if family == "tuna":
        aquatic_words = primary_words or words
        species = _first(aquatic_words, _FACET_ALIASES["tuna_species"])
        if species and (not for_query or species != "tuna"):
            facets["tuna_species"] = species
        tuna_cut = _first(aquatic_words, _FACET_ALIASES["tuna_cut"])
        if tuna_cut:
            facets["tuna_cut"] = tuna_cut
        elif not for_query:
            facets["tuna_cut"] = "regular"
        medium = _preserving_medium(aquatic_words)
        if medium:
            facets["preserving_medium"] = medium
        if medium or aquatic_words & {"lata", "latas", "frasco", "conserva"}:
            facets["preparation"] = "canned"
        elif aquatic_words & {"ahumado", "ahumada"}:
            facets["preparation"] = "smoked"
        elif aquatic_words & {"crudo", "cruda", "crudos", "crudas"}:
            facets["preparation"] = "fresh_or_plain"
        elif not for_query:
            facets["preparation"] = "fresh_or_plain"
        if aquatic_words & {"descongelado", "descongelada"}:
            facets["preservation"] = "thawed"
        elif aquatic_words & {
            "congelado",
            "congelada",
            "congelados",
            "congeladas",
            "ultracongelado",
            "ultracongelada",
        }:
            facets["preservation"] = "frozen"
    if family == "salmon":
        category_words = words
        if "ahumado" in words:
            facets["preparation"] = "smoked"
        elif words & {"congelado", "congelados", "ultracongelado", "ultracongelados"}:
            facets["preparation"] = "frozen"
        elif words & {"marinado", "marinada", "marinados", "marinadas"}:
            facets["preparation"] = "marinated"
        elif _has(words, "al natural") or "conserva" in category_words:
            facets["preparation"] = "canned"
        elif words & {"fresco", "fresca", "frescos", "frescas"}:
            facets["preparation"] = "fresh"
        elif not for_query:
            facets["preparation"] = "fresh_or_unspecified"
        medium = _preserving_medium(words)
        if medium:
            facets["preserving_medium"] = medium
        if words & {"descongelado", "descongelada", "descongelados", "descongeladas"}:
            facets["preservation"] = "thawed"
        elif words & {"congelado", "congelada", "congelados", "congeladas", "ultracongelado", "ultracongelados"}:
            facets["preservation"] = "frozen"
        elif words & {"fresco", "fresca", "frescos", "frescas"}:
            facets["preservation"] = "fresh"
        fish_cut = _first(
            words,
            {
                "whole": ("entero",),
                "half": ("medio salmon",),
                "fillet": ("filete",),
                "loin": ("lomo", "lomos"),
                "steak": ("rodaja", "escalopin"),
                "medallion": ("medallon", "medallones"),
                "portion": ("porcion", "porciones"),
            },
        )
        if fish_cut:
            facets["fish_cut"] = fish_cut
        elif not for_query:
            facets["fish_cut"] = "unspecified"
        if words & {"salvaje", "wild"}:
            facets["production"] = "wild"
        elif words & {"crianza", "acuicultura"}:
            facets["production"] = "farmed"
    if family == "fish":
        aquatic_words = primary_words or words
        species = _first(aquatic_words, _FISH_SPECIES_ALIASES)
        if species:
            facets["fish_species"] = species

        fish_cut = _first(
            aquatic_words,
            {
                "butterflied": ("abierta", "espalda"),
                "fillet": ("filete", "filetes"),
                "loin": ("lomo", "lomos", "centro", "centros", "corazon", "corazones"),
                "portion": ("porcion", "porciones"),
                "medallion": ("medallon", "medallones"),
                "steak": ("rodaja", "rodajas"),
                "slice": ("tajada", "tajadas"),
                "sticks": ("palito", "palitos"),
                "pieces": ("trozo", "trozos", "menu"),
                "tail": ("cola", "colas"),
                "crumbs": ("miga", "migas", "desmigado", "desmigada"),
                "belly": ("ventresca", "ventrescas"),
                "whole": ("entero", "entera", "pieza", "sin cabeza", "limpia"),
            },
        )
        if fish_cut:
            facets["fish_cut"] = fish_cut

        medium = _preserving_medium(aquatic_words)
        if medium:
            facets["preserving_medium"] = medium
        if aquatic_words & {"ahumado", "ahumada", "ahumados", "ahumadas"}:
            facets["preparation"] = "smoked"
        elif aquatic_words & {"rebozado", "rebozada", "empanado", "empanada"}:
            facets["preparation"] = "breaded"
        elif aquatic_words & {"desalado", "desalada", "desalados", "desaladas"}:
            facets["preparation"] = "desalted"
        elif _has(aquatic_words, "punto de sal") or _has(aquatic_words, "punto sal"):
            facets["preparation"] = "lightly_salted"
        elif aquatic_words & {"salado", "salada", "salados", "saladas"}:
            facets["preparation"] = "salted"
        elif medium or aquatic_words & {"lata", "latas", "frasco", "conserva"}:
            facets["preparation"] = "canned"
        elif aquatic_words & {"cocido", "cocida", "vapor"}:
            facets["preparation"] = "cooked"
        elif aquatic_words & {"crudo", "cruda", "crudos", "crudas"}:
            facets["preparation"] = "plain"
        elif not for_query:
            facets["preparation"] = "plain"

        if aquatic_words & {"descongelado", "descongelada", "descongelados"}:
            facets["preservation"] = "thawed"
        elif aquatic_words & {
            "congelado",
            "congelada",
            "congelados",
            "congeladas",
            "ultracongelado",
            "ultracongelada",
            "ultracongelados",
            "ultracongeladas",
        }:
            facets["preservation"] = "frozen"
        elif aquatic_words & {"fresco", "fresca", "vivo", "viva"}:
            facets["preservation"] = "fresh"

        if _has(aquatic_words, "sin piel"):
            facets["skin"] = "skinless"
        elif _has(aquatic_words, "con piel"):
            facets["skin"] = "skin_on"
        if _has(aquatic_words, "sin espina") or _has(aquatic_words, "sin espinas"):
            facets["bones"] = "boneless"
        elif _has(aquatic_words, "con espina") or _has(aquatic_words, "con espinas"):
            facets["bones"] = "bone_in"
        if aquatic_words & {"salvaje", "wild"}:
            facets["production"] = "wild"
        elif aquatic_words & {"crianza", "acuicultura"}:
            facets["production"] = "farmed"
    if family == "seafood":
        aquatic_words = primary_words or words
        species = _first(aquatic_words, _SEAFOOD_SPECIES_ALIASES)
        if species:
            facets["seafood_species"] = species

        medium = _preserving_medium(aquatic_words)
        if medium:
            facets["preserving_medium"] = medium
        if aquatic_words & {"rebozado", "rebozada", "empanado", "empanada"}:
            facets["preparation"] = "breaded"
        elif aquatic_words & {"relleno", "rellena", "rellenos", "rellenas"}:
            facets["preparation"] = "stuffed"
        elif _has(aquatic_words, "a la gallega"):
            facets["preparation"] = "galician_style"
        elif medium or aquatic_words & {"lata", "latas", "frasco", "conserva"}:
            facets["preparation"] = "canned"
        elif aquatic_words & {"cocido", "cocida", "cocidos", "cocidas", "vapor"}:
            facets["preparation"] = "cooked"
        elif aquatic_words & {"crudo", "cruda", "crudos", "crudas"}:
            facets["preparation"] = "plain"
        elif not for_query:
            facets["preparation"] = "plain"

        seafood_form = _first(
            aquatic_words,
            {
                "meat_only": ("carne",),
                "peeled": ("pelada", "peladas", "pelado", "pelados"),
                "legs": ("pata", "patas", "rejo", "rejos"),
                "rings": ("anilla", "anillas"),
                "tails": ("cola", "colas"),
                "chopped": ("troceado", "troceada", "trozos"),
                "half": ("medio", "media"),
                "cleaned": ("limpio", "limpia", "limpios", "limpias"),
            },
        )
        if seafood_form:
            facets["seafood_form"] = seafood_form
        elif _has(aquatic_words, "con concha"):
            facets["seafood_form"] = "shell_on"
        elif facets.get("preparation") == "canned":
            facets["seafood_form"] = "meat_only"
        elif species in {"shrimp", "king_prawn", "prawn", "crayfish"} and not for_query:
            facets["seafood_form"] = "shell_on"
        elif species in {"mussel", "clam", "scallop", "cockle", "razor_clam"} and not for_query:
            facets["seafood_form"] = "shell_on"
        elif species in {"octopus", "squid", "pota", "cuttlefish"} and not for_query:
            facets["seafood_form"] = "whole_or_unspecified"

        if aquatic_words & {"descongelado", "descongelada", "descongelados"}:
            facets["preservation"] = "thawed"
        elif aquatic_words & {
            "congelado",
            "congelada",
            "congelados",
            "congeladas",
            "ultracongelado",
            "ultracongelada",
        }:
            facets["preservation"] = "frozen"
        elif aquatic_words & {"fresco", "fresca", "vivo", "viva"}:
            facets["preservation"] = "fresh"

        size_band = _first(
            aquatic_words,
            {
                "small": ("pequeno", "pequena", "pequenos", "pequenas"),
                "medium": ("mediano", "mediana", "medianos", "medianas"),
                "large": ("grande", "grandes"),
            },
        )
        if size_band:
            facets["size_band"] = size_band
    if family == "ham":
        facets["ham_source"] = "shoulder" if "paleta" in words else "ham"
        style = _first(words, _FACET_ALIASES["ham_style"])
        if style:
            facets["ham_style"] = style
        format_value = _first(words, _FACET_ALIASES["ham_format"])
        if not format_value and not for_query:
            mass = _MASS_RE.search(_normalize(text))
            if mass:
                amount = float(mass.group(1).replace(",", "."))
                grams = amount * 1000 if mass.group(2).casefold() == "kg" else amount
                if grams >= 3000:
                    format_value = "whole"
                elif grams <= 200:
                    format_value = "sliced"
        if format_value:
            facets["ham_format"] = format_value
        grade = _first(
            words,
            {
                "acorn": ("bellota",),
                "field_fed": ("cebo campo", "cebo de campo"),
                "cebo": ("cebo",),
                "grand_reserve": ("gran reserva",),
                "reserve": ("reserva",),
            },
        )
        if grade:
            facets["ham_grade"] = grade
    if family == "meat":
        meat_words = primary_words or words
        observed_species = [
            species
            for species, aliases in _MEAT_SPECIES_ALIASES.items()
            if any(_has(meat_words, alias) for alias in aliases)
        ]
        if not observed_species and meat_words is not words:
            observed_species = [
                species
                for species, aliases in _MEAT_SPECIES_ALIASES.items()
                if any(_has(words, alias) for alias in aliases)
            ]
        if (
            "mixta" in meat_words
            or "mixto" in meat_words
            or len(observed_species) > 1
        ):
            facets["meat_species"] = "mixed"
        elif observed_species:
            facets["meat_species"] = observed_species[0]
        elif "lomo" in meat_words and "embuchado" in meat_words:
            facets["meat_species"] = "pork"
        elif "lomo" in meat_words:
            facets["meat_species"] = "pork"

        cut = _first(meat_words, _MEAT_CUT_ALIASES)
        if cut:
            facets["meat_cut"] = cut

        treatments: list[str] = []
        if meat_words & {
            "embuchado",
            "embuchada",
            "curado",
            "curada",
            "cecina",
            "chorizo",
            "salchichon",
        } or ("cecina" in words and "lomo" in meat_words):
            treatments.append("cured")
        if meat_words & {
            "cocido",
            "cocida",
            "asado",
            "asada",
            "braseado",
            "braseada",
            "fiambre",
        } or _has(meat_words, "al horno"):
            treatments.append("cooked")
        if meat_words & {
            "empanado",
            "empanada",
            "empanados",
            "empanadas",
            "milanesa",
        }:
            treatments.append("breaded")
        if meat_words & {
            "adobado",
            "adobada",
            "marinado",
            "marinada",
            "marinadas",
        }:
            treatments.append("marinated")
        if meat_words & {"salado", "salada"}:
            treatments.append("salted")
        if meat_words & {"relleno", "rellena", "rellenos", "rellenas"}:
            treatments.append("stuffed")
        if (
            "lomo" in meat_words
            and "iberico" in meat_words
            and "sobre" in meat_words
            and not treatments
        ):
            treatments.append("cured")
        if treatments:
            facets["meat_preparation"] = "+".join(treatments)
        elif not for_query:
            facets["meat_preparation"] = "plain"

        meat_format = _first(
            meat_words,
            {
                "deli_slices": ("loncha", "lonchas", "loncheado"),
                "ground": ("carne picada", "picada", "picado", "burger meat"),
                "patty": ("hamburguesa", "hamburguesas", "burger", "burgers"),
                "meatballs": ("albondiga", "albondigas"),
                "sausage": ("chorizo", "salchichon", "salchicha", "salchichas"),
                "fillets": ("filete", "filetes", "fileteada", "escalopin", "escalopines"),
                "strips": ("tira", "tiras"),
                "diced": ("taco", "tacos", "dados"),
                "chunks": ("troceado", "troceada", "trozo", "trozos"),
                "chops": ("chuleta", "chuletas", "chuletillas"),
                "half": ("medio", "media"),
                "whole": ("entero", "entera", "pieza"),
            },
        )
        if meat_format:
            facets["meat_format"] = meat_format

        if meat_words & {"congelado", "congelada", "congelados", "congeladas"}:
            facets["preservation"] = "frozen"
        elif not for_query:
            facets["preservation"] = "not_frozen"

        if _has(meat_words, "sin hueso") or meat_words & {
            "deshuesado",
            "deshuesada",
        }:
            facets["bone"] = "boneless"
        elif _has(meat_words, "con hueso"):
            facets["bone"] = "bone_in"

        breed = _first(
            meat_words,
            {
                "iberian": ("iberico", "iberica"),
                "duroc": ("duroc",),
                "angus": ("angus",),
            },
        )
        if breed:
            facets["meat_breed"] = breed

        if _has(meat_words, "cebo campo") or _has(meat_words, "cebo de campo"):
            facets["meat_grade"] = "cebo_field"
        elif "bellota" in meat_words:
            facets["meat_grade"] = "acorn"
        elif "cebo" in meat_words:
            facets["meat_grade"] = "cebo"
    if family == "pasta":
        value = _first(words, _FACET_ALIASES["pasta_base"])
        if value:
            facets["pasta_base"] = value
        if words & {
            "rellena",
            "relleno",
            "rellenos",
            "ravioli",
            "raviolis",
            "tortellini",
        }:
            facets["pasta_style"] = "filled"
        elif not for_query:
            facets["pasta_style"] = "unfilled"
        if "fresca" in words:
            facets["preparation"] = "fresh"
        elif not for_query:
            facets["preparation"] = "dry"
        shape = _first(
            words,
            {
                "spaghetti": ("espagueti", "espaguetis", "spaghetti", "spaguetti"),
                "macaroni": ("macarron", "macarrones"),
                "spirals": ("espiral", "espirales", "helice", "helices", "fusilli"),
                "noodles": ("fideo", "fideos"),
                "tagliatelle": ("tallarines", "tagliatelle"),
                "penne": ("penne", "plumas"),
                "farfalle": ("farfalle", "pajaritas"),
                "ravioli": ("ravioli", "raviolis"),
                "tortellini": ("tortellini",),
                "lasagna_sheets": ("lasana", "placas", "hojas"),
            },
        )
        if shape:
            facets["pasta_shape"] = shape
    if family == "tofu":
        value = _first(words, _FACET_ALIASES["tofu_style"])
        if value:
            facets["tofu_style"] = value
        firmness = _first(
            words,
            {
                "silken": ("sedoso", "silken"),
                "soft": ("suave", "blando"),
                "extra_firm": ("extra firme",),
                "firm": ("firme",),
            },
        )
        if firmness:
            facets["tofu_firmness"] = firmness
    if family == "toilet_paper":
        if words & {"humedo", "humedecido"}:
            facets["paper_form"] = "wet"
        elif not for_query:
            facets["paper_form"] = "dry"
    if family == "banana":
        if "canarias" in words or "canario" in words:
            facets["banana_type"] = "canary_plantain"
        elif "banana" in words:
            facets["banana_type"] = "banana"
    beverage_families = {
        "water",
        "soft_drink",
        "juice",
        "plant_drink",
        "beverage",
        "beer",
        "spirit",
        "cider",
        "wine",
    }
    if family in beverage_families:
        beverage_words = primary_words or words
        container = _beverage_container(beverage_words)
        if container:
            facets["beverage_container"] = container
        package = _beverage_package(text, beverage_words)
        if package:
            facets["beverage_package"] = package

    if family == "water":
        if _has(beverage_words, "agua de coco"):
            facets["water_type"] = "coconut"
            facets["beverage_flavor"] = "coconut"
        elif beverage_words & {"mineral"}:
            facets["water_type"] = "mineral"
        elif beverage_words & {"manantial"}:
            facets["water_type"] = "spring"
        elif not for_query:
            facets["water_type"] = "drinking_water"
        if _has(beverage_words, "sin gas"):
            facets["carbonation"] = "still"
        elif _has(beverage_words, "con gas") or beverage_words & {"gaseosa"}:
            facets["carbonation"] = "sparkling"
        elif not for_query:
            facets["carbonation"] = "still"
        flavor = _beverage_flavor(beverage_words)
        if flavor:
            facets["beverage_flavor"] = flavor

    if family == "soft_drink":
        flavor = _beverage_flavor(beverage_words)
        if flavor:
            facets["beverage_flavor"] = flavor
        if flavor == "cola":
            variant = _first(
                beverage_words,
                {
                    "lime": ("lima",),
                    "lemon": ("limon",),
                    "cherry": ("cereza", "cherry"),
                    "vanilla": ("vainilla",),
                },
            )
            if variant:
                facets["flavor_variant"] = variant
        normalized = _normalize(text)
        if (
            beverage_words & {"zero", "cero", "light"}
            or _has(beverage_words, "sin azucar")
        ):
            facets["sugar_profile"] = "sugar_free"
        elif not for_query:
            facets["sugar_profile"] = "regular"
        if _has(beverage_words, "sin gas"):
            facets["carbonation"] = "still"
        elif not for_query or _has(beverage_words, "con gas"):
            facets["carbonation"] = "carbonated"
        caffeine_free = (
            _has(beverage_words, "sin cafeina")
            or bool(
                re.search(
                    r"\b(?:zero|cero)\s+(?:zero|cero|cafeina)\b",
                    normalized,
                )
            )
        )
        if caffeine_free:
            facets["caffeine_profile"] = "caffeine_free"
        elif flavor == "cola" and not for_query:
            facets["caffeine_profile"] = "caffeinated"
        if beverage_words & {"concentrado", "concentrada", "jarabe"}:
            facets["concentration"] = "concentrate"
        elif not for_query:
            facets["concentration"] = "ready_to_drink"

    if family == "juice":
        observed_fruits = _observed_alias_values(
            beverage_words, _BEVERAGE_FRUIT_ALIASES
        )
        observed_fruits = list(dict.fromkeys(observed_fruits))
        if len(observed_fruits) > 1:
            facets["juice_fruit"] = "mixed"
        elif observed_fruits:
            facets["juice_fruit"] = observed_fruits[0]
        if beverage_words & {"smoothie"}:
            facets["juice_style"] = "smoothie"
        elif beverage_words & {"nectar"}:
            facets["juice_style"] = "nectar"
        elif beverage_words & {"zumo", "zumos"}:
            facets["juice_style"] = "juice"
        if beverage_words & {
            "exprimido",
            "exprimida",
            "exprimidos",
            "exprimidas",
        }:
            facets["preparation"] = "squeezed"
        if _has(beverage_words, "sin pulpa"):
            facets["pulp"] = "without_pulp"
        elif _has(beverage_words, "con pulpa"):
            facets["pulp"] = "with_pulp"
        normalized = _normalize(text)
        if "a partir de concentrado" in normalized or "concentrado" in beverage_words:
            facets["concentration"] = "from_concentrate"
        elif beverage_words & {"exprimido", "exprimida", "exprimidos", "exprimidas"}:
            facets["concentration"] = "not_from_concentrate"

    if family == "plant_drink":
        observed_bases = _observed_alias_values(
            beverage_words, _PLANT_DRINK_BASE_ALIASES
        )
        observed_bases = list(dict.fromkeys(observed_bases))
        if len(observed_bases) > 1:
            facets["plant_base"] = "mixed"
        elif observed_bases:
            facets["plant_base"] = observed_bases[0]
        elif beverage_words & {"horchata", "chufa"}:
            facets["plant_base"] = "tigernut"
        if beverage_words & {"barista", "baristas"}:
            facets["plant_style"] = "barista"
        elif not for_query:
            facets["plant_style"] = "standard"
        flavor = _beverage_flavor(beverage_words)
        if flavor:
            facets["beverage_flavor"] = flavor
        if beverage_words & {"calcio"}:
            facets["fortification"] = "calcium"
        if beverage_words & {"zero", "cero"}:
            facets["no_added_sugar"] = "yes"

    if family == "beverage":
        kind = _first(
            beverage_words,
            {
                "sports": ("isotonica", "isotonico"),
                "energy": ("energetica", "energetico", "energy"),
                "shake": ("batido", "batidos"),
                "iced_tea": ("te frio", "ice tea"),
            },
        )
        if kind:
            facets["beverage_kind"] = kind
        flavor = _beverage_flavor(beverage_words)
        if flavor:
            facets["beverage_flavor"] = flavor
        if beverage_words & {"zero", "cero", "light"} or _has(
            beverage_words, "sin azucar"
        ):
            facets["sugar_profile"] = "sugar_free"
        elif not for_query:
            facets["sugar_profile"] = "regular"
        if _has(beverage_words, "sin gas"):
            facets["carbonation"] = "still"
        elif _has(beverage_words, "con gas"):
            facets["carbonation"] = "carbonated"
        if beverage_words & {"concentrado", "concentrada", "polvo"}:
            facets["concentration"] = "concentrate"
        elif not for_query:
            facets["concentration"] = "ready_to_drink"

    if family in {"beer", "spirit", "cider", "wine"}:
        alcohol_profile = _alcohol_profile(
            text,
            beverage_words,
            for_query=for_query,
        )
        if alcohol_profile:
            facets["alcohol_profile"] = alcohol_profile
        strength = _alcohol_strength(text)
        if strength:
            facets["alcohol_strength"] = strength

    if family == "beer":
        style = _first(
            beverage_words,
            {
                "radler": ("radler", "shandy"),
                "ipa": ("ipa",),
                "wheat": ("trigo",),
                "dark": ("negra", "stout"),
                "toasted": ("tostada",),
                "lager": ("lager", "rubia"),
            },
        )
        if style:
            facets["beer_style"] = style
    if family == "spirit":
        spirit_type = _first(beverage_words, _SPIRIT_TYPE_ALIASES)
        if spirit_type:
            facets["spirit_type"] = spirit_type
    if family == "cider":
        cider_style = _first(
            beverage_words,
            {
                "natural": ("natural",),
                "sweet": ("dulce",),
                "dry": ("seca", "dry"),
            },
        )
        if cider_style:
            facets["cider_style"] = cider_style
    if family in {"fruit", "vegetable"}:
        produce_words = primary_words or words
        species_aliases = (
            _FRUIT_SPECIES_ALIASES
            if family == "fruit"
            else _VEGETABLE_SPECIES_ALIASES
        )
        observed_species = _observed_alias_values(produce_words, species_aliases)
        if family == "fruit" and produce_words & {"platano", "platanos", "banana"}:
            observed_species.append("banana")
        observed_species = list(dict.fromkeys(observed_species))
        generic_mix = (
            family == "fruit"
            and (
                _has(produce_words, "frutos rojos")
                or bool(produce_words & {"frutas", "macedonia"})
            )
        ) or (
            family == "vegetable"
            and bool(produce_words & {"verduras", "hortalizas"})
        )
        if len(observed_species) > 1 or generic_mix:
            species = "mixed"
        else:
            species = observed_species[0] if observed_species else None
        species_facet = "fruit_species" if family == "fruit" else "vegetable_species"
        if species:
            facets[species_facet] = species

        variety = _produce_variety(species, produce_words)
        pepper_colours = produce_words & {
            "rojo",
            "rojos",
            "verde",
            "verdes",
            "amarillo",
            "amarillos",
        }
        if species == "pepper" and (
            "tricolor" in produce_words or len(pepper_colours) >= 2
        ):
            variety = "mixed"
        if variety:
            facets["produce_variety"] = variety

        processed_potato_brand = species == "potato" and bool(
            produce_words & {"mccain", "maheso"}
        )

        if processed_potato_brand or produce_words & {
            "ultracongelado",
            "ultracongelada",
            "ultracongelados",
            "ultracongeladas",
            "congelado",
            "congelada",
            "congelados",
            "congeladas",
        }:
            preservation = "frozen"
        elif produce_words & {"deshidratado", "deshidratada", "seco", "seca", "polvo"}:
            preservation = "dried"
        elif produce_words & {
            "lata",
            "frasco",
            "bote",
            "tarro",
            "conserva",
            "almibar",
        }:
            preservation = "canned"
        elif produce_words & {"refrigerado", "refrigerada"}:
            preservation = "refrigerated"
        else:
            preservation = None

        preparation = (
            "pre_fried"
            if processed_potato_brand
            else _first(
                produce_words,
                {
                    "caramelized": ("caramelizada", "caramelizado"),
                    "concentrated": ("concentrado", "concentrada"),
                    "pre_fried": (
                        "prefrita",
                        "prefrito",
                        "prefritas",
                        "prefritos",
                    ),
                    "fried": ("frita", "frito"),
                    "roasted": ("asada", "asado"),
                    "cooked": ("cocida", "cocido", "microondas"),
                    "steamed": ("al vapor", "vapor"),
                    "washed": ("lavada", "lavado"),
                    "plain": ("natural", "cruda", "crudo"),
                },
            )
        )
        if not preparation and not for_query:
            preparation = "plain"
        if preparation:
            facets["preparation"] = preparation
        if preservation is None and not for_query:
            preservation = "fresh" if preparation in {"plain", "washed"} else "processed"
        if preservation:
            facets["preservation"] = preservation

        produce_form = _first(
            produce_words,
            {
                "pureed": ("pure",),
                "crushed": ("triturado", "triturada"),
                "grated": ("rallada", "rallado"),
                "diced": ("dados", "cubos"),
                "florets": ("floreta", "floretas"),
                "wedges": ("gajo", "gajos"),
                "strips": ("tira", "tiras"),
                "concentrated": ("concentrado", "concentrada"),
                "thin_cut": ("corte fino",),
                "thick_cut": ("corte grueso",),
                "sliced": ("rodaja", "rodajas", "laminada", "laminado"),
                "whole": ("entero", "entera", "enteros", "enteras"),
                "cut": (
                    "troceada",
                    "troceado",
                    "trozos",
                    "cortada",
                    "cortado",
                    "partida",
                    "partido",
                    "al corte",
                ),
                "peeled": ("pelada", "pelado", "peladas", "pelados"),
                "hearts": ("corazon", "corazones", "cogollo", "cogollos"),
            },
        )
        if not produce_form and species == "mixed":
            produce_form = "mixed"
        elif not produce_form and not for_query:
            produce_form = (
                "whole"
                if preservation == "fresh" and preparation in {"plain", "washed"}
                else "processed"
            )
        if produce_form:
            facets["produce_form"] = produce_form

        if "freir" in produce_words and "cocer" in produce_words:
            produce_use = "multi_purpose"
        else:
            produce_use = _first(
                produce_words,
                {
                    "juice": ("de zumo", "para zumo"),
                    "table": ("de mesa", "mesa", "para postre"),
                    "frying": ("para freir", "freir"),
                    "cooking": ("para cocer", "cocer"),
                    "salad": ("ensalada",),
                },
            )
        if produce_use:
            facets["produce_use"] = produce_use

        if produce_words & {"ecologico", "ecologica", "ecologicos", "ecologicas", "bio"}:
            facets["produce_production"] = "organic"

        if produce_words & {
            "bolsa",
            "malla",
            "bandeja",
            "pack",
            "envase",
            "frasco",
            "bote",
            "tarro",
            "lata",
        }:
            facets["sale_basis"] = "package"
        elif produce_words & {"unidad", "unidades", "ud", "uds", "u"}:
            facets["sale_basis"] = "item"
        elif (
            _has(produce_words, "al peso")
            or produce_words & {"granel", "kilo"}
            or bool(_MASS_RE.search(_normalize(text)))
        ):
            facets["sale_basis"] = "weight"

        package_form = _first(
            produce_words,
            {
                "net": ("malla",),
                "bag": ("bolsa",),
                "tray": ("bandeja",),
                "bunch": ("manojo",),
            },
        )
        if package_form:
            facets["package_form"] = package_form

        calibre = _produce_calibre(text, produce_words)
        if calibre:
            facets["size_band"] = calibre
        origin = _produce_origin(text)
        if origin:
            facets["origin"] = origin
    if family == "legume":
        legume_type = _first(
            words,
            {
                "lentil": ("lenteja", "lentejas"),
                "chickpea": ("garbanzo", "garbanzos"),
                "bean": ("alubia", "alubias", "judia", "judias"),
            },
        )
        if legume_type:
            facets["legume_type"] = legume_type
        if words & {"cocida", "cocidas", "cocido", "cocidos", "bote", "tarro"}:
            facets["preparation"] = "cooked"
        elif not for_query:
            facets["preparation"] = "dry"
    if family == "bread":
        bread_form = _first(
            words,
            {
                "sliced_loaf": ("molde",),
                "soft_bun": ("hamburguesa", "burguer", "burger", "hot dog"),
                "breadcrumbs": ("rallado",),
                "toast": ("tostado", "tostada", "tostadas"),
                "baguette": ("baguette", "barra"),
                "loaf": ("hogaza",),
                "roll": ("panecillo", "panecillos"),
                "breadstick": ("palito", "palitos", "pico", "picos"),
            },
        )
        if bread_form:
            facets["bread_form"] = bread_form
        elif not for_query:
            facets["bread_form"] = "generic"
        if "integral" in words:
            facets["bread_grain"] = "wholegrain"
        elif not for_query:
            facets["bread_grain"] = "standard"
        source = _first(
            words,
            {
                "rye": ("centeno",),
                "spelt": ("espelta",),
                "corn": ("maiz",),
                "oat": ("avena",),
                "wheat": ("trigo",),
                "mixed": ("multicereales",),
            },
        )
        if source:
            facets["bread_source"] = source
        style = _first(
            words,
            {
                "sourdough": ("masa madre",),
                "rye": ("centeno",),
                "spelt": ("espelta",),
                "corn": ("maiz",),
                "multigrain": ("multicereales", "semillas"),
                "milk_bread": ("pan de leche",),
            },
        )
        if style:
            facets["bread_style"] = style
    if family == "eggs":
        if "codorniz" in words:
            facets["egg_bird"] = "quail"
        elif not for_query:
            facets["egg_bird"] = "hen"
        egg_size = re.search(
            r"\b(?:huevos?\b|clase|talla|calibre)\s*(xl|l|m|s)\b",
            _normalize(text),
        )
        if egg_size:
            facets["egg_size"] = egg_size.group(1)
        production = _first(
            words,
            {
                "organic": ("ecologico", "ecologicos", "bio"),
                "free_range": ("campero", "camperos"),
                "barn": ("suelo",),
                "caged": ("jaula",),
            },
        )
        if production:
            facets["egg_production"] = production
        egg_format = _first(
            words,
            {
                "liquid": ("liquido", "liquida"),
                "boiled": ("cocido", "cocidos"),
                "white_only": ("claras", "clara"),
            },
        )
        if egg_format:
            facets["egg_format"] = egg_format
        elif not for_query:
            facets["egg_format"] = "shell"
    if family == "wine":
        color = _first(
            words,
            {
                "red": ("tinto",),
                "white": ("blanco",),
                "rose": ("rosado",),
            },
        )
        if color:
            facets["wine_color"] = color
        if words & {"espumoso", "cava", "champan", "champagne"}:
            facets["wine_style"] = "sparkling"
        elif not for_query:
            facets["wine_style"] = "still"
        if _has(words, "sin alcohol"):
            facets["alcohol_free"] = "yes"
    return facets


@lru_cache(maxsize=8192)
def _analyze_text(
    name: str,
    category: str | None,
    *,
    for_query: bool,
) -> SemanticProfile:
    text = " ".join(filter(None, (str(name or ""), str(category or ""))))
    words = _words(text)
    designation = _first(words, _DESIGNATIONS)
    family = _family(words, text)
    if family is None and designation:
        family = "cheese"
    facets = _family_facets(
        family,
        words,
        text,
        for_query=for_query,
        primary_words=_words(name),
    )
    if designation and family == "cheese":
        facets["designation"] = designation
    concepts = tuple(
        [family] if family else []
    ) + tuple(f"{key}:{value}" for key, value in sorted(facets.items()))
    return SemanticProfile(family=family, facets=facets, concepts=concepts)


def analyze_product_text(name: str, category: str | None = None) -> SemanticProfile:
    return _analyze_text(name, category, for_query=False)


def analyze_query_text(query: str) -> SemanticProfile:
    if _normalize(query).strip() in {"conserva", "conservas"}:
        # This is intentionally cross-family: vegetable, fruit, fish, seafood,
        # tuna and legume preserves keep their underlying food identity.
        return SemanticProfile(family=None, facets={}, concepts=())
    return _analyze_text(query, None, for_query=True)


def profile_product(product: Product) -> SemanticProfile:
    return analyze_product_text(product.name, product.category)


def compare_profiles(left: SemanticProfile, right: SemanticProfile) -> dict[str, Any]:
    reasons: list[str] = []
    conflicts: dict[str, list[str]] = {}
    uncertain: list[str] = []
    if left.family and right.family and left.family != right.family:
        return {
            "verdict": "different",
            "score": 0.0,
            "reasons": [f"different product families: {left.family} vs {right.family}"],
            "conflicts": {"family": [left.family, right.family]},
            "uncertain_facets": [],
        }
    family = left.family or right.family
    if family is None:
        return {
            "verdict": "unknown",
            "score": 0.0,
            "reasons": ["no recognized product family"],
            "conflicts": {},
            "uncertain_facets": [],
        }
    critical = _CRITICAL_FACETS.get(family, frozenset())
    for key in critical:
        left_value = left.facets.get(key)
        right_value = right.facets.get(key)
        if left_value and right_value and left_value != right_value:
            conflicts[key] = [left_value, right_value]
        elif bool(left_value) != bool(right_value):
            if key in _ASYMMETRIC_STRICT:
                conflicts[key] = [left_value or "not_observed", right_value or "not_observed"]
            else:
                uncertain.append(key)
        elif left_value and right_value:
            reasons.append(f"same {key}: {left_value}")
    if conflicts:
        return {
            "verdict": "different",
            "score": 0.0,
            "reasons": reasons or ["an explicit semantic facet conflicts"],
            "conflicts": conflicts,
            "uncertain_facets": uncertain,
        }
    score = min(0.95, 0.58 + 0.07 * len(reasons))
    verdict = "equivalent" if not uncertain and reasons else "compatible"
    if not reasons:
        reasons.append(f"same recognized family: {family}")
    return {
        "verdict": verdict,
        "score": round(score, 4),
        "reasons": reasons,
        "conflicts": {},
        "uncertain_facets": uncertain,
    }


def assess_product_equivalence(left: Product, right: Product) -> dict[str, Any]:
    left_profile = profile_product(left)
    right_profile = profile_product(right)
    result = compare_profiles(left_profile, right_profile)
    return {
        **result,
        "left_profile": left_profile.to_dict(),
        "right_profile": right_profile.to_dict(),
    }


def assess_query_candidate(query: str, product: Product) -> dict[str, Any]:
    query_profile = analyze_query_text(query)
    product_profile = profile_product(product)
    if query_profile.family and product_profile.family != query_profile.family:
        observed = product_profile.family or "unrecognized"
        result = {
            "verdict": "different",
            "score": 0.0,
            "reasons": [
                f"candidate family {observed} does not satisfy query family {query_profile.family}"
            ],
            "conflicts": {"family": [query_profile.family, observed]},
            "uncertain_facets": [],
        }
    elif query_profile.family is None:
        result = {
            "verdict": "unknown",
            "score": 0.0,
            "reasons": ["query has no recognized product family"],
            "conflicts": {},
            "uncertain_facets": [],
        }
    else:
        conflicts: dict[str, list[str]] = {}
        uncertain: list[str] = []
        reasons: list[str] = []
        critical = _CRITICAL_FACETS.get(query_profile.family, frozenset())
        for key, query_value in query_profile.facets.items():
            if key not in critical:
                continue
            product_value = product_profile.facets.get(key)
            if product_value and product_value != query_value:
                conflicts[key] = [query_value, product_value]
            elif product_value:
                reasons.append(f"requested {key} matches: {query_value}")
            elif key in _QUERY_REQUIRED_IF_OBSERVED:
                conflicts[key] = [query_value, "not_observed"]
            else:
                uncertain.append(key)
        if conflicts:
            result = {
                "verdict": "different",
                "score": 0.0,
                "reasons": reasons or ["candidate conflicts with a requested semantic facet"],
                "conflicts": conflicts,
                "uncertain_facets": uncertain,
            }
        else:
            result = {
                "verdict": "compatible" if uncertain else "equivalent",
                "score": round(min(0.95, 0.65 + 0.08 * len(reasons)), 4),
                "reasons": reasons or [f"candidate belongs to requested family: {query_profile.family}"],
                "conflicts": {},
                "uncertain_facets": uncertain,
            }
    return {
        **result,
        "query_profile": query_profile.to_dict(),
        "product_profile": product_profile.to_dict(),
    }


def semantic_query_expansions(query: str, *, maximum: int = 12) -> list[str]:
    """Return bounded, deterministic aliases for recognized query concepts."""

    profile = analyze_query_text(query)
    expansions: list[str] = []
    if profile.facets.get("designation") == "arzua_ulloa":
        expansions.extend(aliases_for("cheese_arzua_ulloa"))
    elif profile.facets.get("designation"):
        expansions.extend(
            _DESIGNATION_SEARCH_ALIASES[profile.facets["designation"]]
        )
    elif profile.family == "ham" and profile.facets.get("ham_format") == "solid_block":
        style = profile.facets.get("ham_style")
        expansions.extend(
            aliases_for(
                "ham_serrano_solid_block" if style == "serrano" else "ham_solid_block"
            )
        )
    elif profile.family == "prepared_meal" and profile.facets.get("meal_type") == "lasagna":
        expansions.extend(aliases_for("lasagna"))
    elif profile.family == "toothpaste":
        expansions.extend(aliases_for("toothpaste"))
    elif profile.family == "shower_gel":
        expansions.extend(aliases_for("shower_gel"))
    elif profile.family == "soap" and profile.facets.get("soap_use") == "hands":
        expansions.extend(aliases_for("hand_soap"))
    elif profile.family == "baby_food" and profile.facets.get("baby_food_type") == "jar":
        expansions.extend(aliases_for("baby_food_jar"))
    elif profile.family == "seasoning" and not profile.facets.get("seasoning_type"):
        expansions.extend(aliases_for("generic_seasoning"))
    elif profile.family in {
        "meat",
        "fish",
        "seafood",
        "fruit",
        "vegetable",
        "banana",
        "water",
        "soft_drink",
        "juice",
        "plant_drink",
        "beverage",
        "beer",
        "spirit",
        "cider",
        "wine",
    }:
        # Species and cuts inside these families are alternatives, not aliases.
        # Expanding ``pavo`` or ``merluza`` to every sibling would increase recall by
        # introducing products that the semantic filter must immediately reject.
        pass
    elif profile.family:
        aliases = _FAMILY_ALIASES.get(profile.family, ())
        if len(aliases) > 1:
            expansions.extend(aliases)

    seen = {" ".join(str(query or "").casefold().split())}
    result: list[str] = []
    for value in expansions:
        cleaned = " ".join(value.split())
        # Preserve accent variants for retailer engines that do not fold them.
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
        if len(result) >= maximum:
            break
    return result


def semantic_profile_cache_info() -> dict[str, int]:
    info = _analyze_text.cache_info()
    return {
        "hits": info.hits,
        "misses": info.misses,
        "maxsize": int(info.maxsize or 0),
        "currsize": info.currsize,
    }


__all__ = [
    "SemanticProfile",
    "analyze_product_text",
    "analyze_query_text",
    "assess_product_equivalence",
    "assess_query_candidate",
    "compare_profiles",
    "profile_product",
    "semantic_query_expansions",
    "semantic_profile_cache_info",
]
