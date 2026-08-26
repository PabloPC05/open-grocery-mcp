"""High-recall catalogue search across one or more retailer providers.

Retailer search engines disagree about accents, stop words, synonyms and AND/OR
semantics.  A single query is therefore useful for interactive lookup but is a
weak basis for claims such as "all products" or "the cheapest product".  This
module unions several explicit query variants, deduplicates retailer products,
applies transparent name filters and reports every observed coverage limit.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from math import inf
from threading import Lock
from time import monotonic
from typing import Any, Sequence

from open_grocery_mcp.equivalence import (
    analyze_query_text,
    assess_query_candidate,
    semantic_query_expansions,
)
from open_grocery_mcp.errors import InvalidRequest, OpenGroceryError
from open_grocery_mcp.matching import normalize_text, score_product, tokens
from open_grocery_mcp.models import Product
from open_grocery_mcp.providers.base import GroceryProvider
from open_grocery_mcp.registry import ProviderRegistry

_QUERY_STOPWORDS = {"de", "del", "la", "el", "los", "las", "un", "una"}
_SORT_OPTIONS = {"relevance", "price", "unit_price"}
_CACHE_LOCK = Lock()
_SEARCH_CACHE: dict[tuple[Any, ...], tuple[float, tuple[Product, ...], dict[str, Any]]] = {}


def clear_catalogue_cache() -> None:
    with _CACHE_LOCK:
        _SEARCH_CACHE.clear()


def _clean_text(value: object, *, name: str, maximum: int = 200) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise InvalidRequest(f"{name} cannot be empty")
    if len(text) > maximum:
        raise InvalidRequest(f"{name} cannot exceed {maximum} characters")
    return text


def _query_variants(
    query: str,
    aliases: Sequence[str] | None,
    *,
    auto_equivalences: bool,
) -> list[str]:
    if aliases is not None and len(aliases) > 12:
        raise InvalidRequest("aliases are limited to 12 query variants")
    variants = [_clean_text(query, name="query")]
    accent_folded = normalize_text(variants[0])
    if accent_folded and accent_folded != variants[0].casefold():
        variants.append(accent_folded)
    without_stopwords = " ".join(
        word for word in variants[0].split() if normalize_text(word) not in _QUERY_STOPWORDS
    )
    if without_stopwords:
        variants.append(without_stopwords)
    if auto_equivalences:
        variants.extend(semantic_query_expansions(variants[0]))
        variants.extend(_morphological_variants(variants[0]))
    for position, alias in enumerate(aliases or ()):
        variants.append(_clean_text(alias, name=f"aliases[{position}]"))

    deduplicated: list[str] = []
    seen: set[str] = set()
    for variant in variants:
        # Retailer engines do not agree on accent folding.  Keep ``atún`` and
        # ``atun`` as separate requests even though our own matcher considers
        # them equivalent; only collapse whitespace/case-identical variants.
        key = " ".join(variant.casefold().split())
        if key not in seen:
            seen.add(key)
            deduplicated.append(variant)
    if len(deduplicated) > 20:
        raise InvalidRequest("expanded searches are limited to 20 total query variants")
    return deduplicated


_GENDERED_ENDINGS = (
    ("congelados", "congeladas"),
    ("congelado", "congelada"),
    ("frescos", "frescas"),
    ("fresco", "fresca"),
    ("enteros", "enteras"),
    ("entero", "entera"),
    ("cortados", "cortadas"),
    ("cortado", "cortada"),
    ("rallados", "ralladas"),
    ("rallado", "rallada"),
    ("cocidos", "cocidas"),
    ("cocido", "cocida"),
    ("adobados", "adobadas"),
    ("adobado", "adobada"),
)


def _morphological_variants(query: str) -> list[str]:
    """Return conservative provider-query variants; semantic filtering stays authoritative."""

    words = query.split()
    if not words:
        return []
    variants: list[str] = []
    last = normalize_text(words[-1])
    if last.endswith("es") and len(last) > 4:
        variants.append(" ".join([*words[:-1], last[:-2]]))
    elif last.endswith("s") and len(last) > 3:
        variants.append(" ".join([*words[:-1], last[:-1]]))
    elif last[-1:] in "aeiou":
        variants.append(" ".join([*words[:-1], f"{last}s"]))
    elif len(last) > 2:
        variants.append(" ".join([*words[:-1], f"{last}es"]))
    for masculine, feminine in _GENDERED_ENDINGS:
        for source, target in ((masculine, feminine), (feminine, masculine)):
            if last == source:
                variants.append(" ".join([*words[:-1], target]))
    return variants


def _token_roots(value: str) -> set[str]:
    roots: set[str] = set()
    for token in tokens(value):
        roots.add(token)
        if token.endswith("es") and len(token) > 4:
            roots.add(token[:-2])
        elif token.endswith("s") and len(token) > 3:
            roots.add(token[:-1])
    return roots


def _expansion_evidence(
    query: str,
    variants: Sequence[str],
    aliases: Sequence[str] | None,
    *,
    auto_equivalences: bool,
) -> list[dict[str, str]]:
    explicit = {" ".join(value.casefold().split()) for value in aliases or ()}
    semantic = {
        " ".join(value.casefold().split())
        for value in semantic_query_expansions(query)
    } if auto_equivalences else set()
    morphology = {
        " ".join(value.casefold().split())
        for value in _morphological_variants(query)
    } if auto_equivalences else set()
    without_stopwords = " ".join(
        word for word in query.split() if normalize_text(word) not in _QUERY_STOPWORDS
    ).casefold()
    evidence: list[dict[str, str]] = []
    for position, variant in enumerate(variants):
        key = " ".join(variant.casefold().split())
        if position == 0:
            source = "original_query"
        elif key in explicit:
            source = "explicit_alias"
        elif key in semantic:
            source = "versioned_semantic_alias"
        elif key in morphology:
            source = "morphological_variant"
        elif variant == normalize_text(query):
            source = "accent_folded"
        elif key == without_stopwords:
            source = "stopwords_removed"
        else:
            source = "derived_variant"
        evidence.append({"query": variant, "source": source})
    return evidence


def _term_groups(
    groups: Sequence[Sequence[str]] | None,
) -> tuple[tuple[str, ...], ...]:
    if groups is None:
        return ()
    if len(groups) > 8:
        raise InvalidRequest("required_term_groups are limited to 8 groups")
    parsed: list[tuple[str, ...]] = []
    for group_position, group in enumerate(groups):
        if not group:
            raise InvalidRequest(
                f"required_term_groups[{group_position}] cannot be empty"
            )
        if len(group) > 8:
            raise InvalidRequest("each required term group is limited to 8 aliases")
        parsed.append(
            tuple(
                _clean_text(
                    term,
                    name=f"required_term_groups[{group_position}][{term_position}]",
                    maximum=80,
                )
                for term_position, term in enumerate(group)
            )
        )
    return tuple(parsed)


def _excluded_terms(values: Sequence[str] | None) -> tuple[str, ...]:
    if values is None:
        return ()
    if len(values) > 20:
        raise InvalidRequest("excluded_terms are limited to 20 values")
    return tuple(
        _clean_text(term, name=f"excluded_terms[{position}]", maximum=80)
        for position, term in enumerate(values)
    )


def _product_tokens(product: Product) -> set[str]:
    return set(tokens(" ".join(filter(None, (product.name, product.brand, product.category)))))


def _contains_term(observed: set[str], term: str) -> bool:
    wanted = set(tokens(term))
    return bool(wanted) and wanted <= observed


def _passes_filters(
    product: Product,
    *,
    required_groups: tuple[tuple[str, ...], ...],
    excluded: tuple[str, ...],
) -> bool:
    observed = _product_tokens(product)
    if any(_contains_term(observed, term) for term in excluded):
        return False
    return all(
        any(_contains_term(observed, term) for term in group)
        for group in required_groups
    )


def _sort_key(row: dict[str, Any], sort_by: str) -> tuple[Any, ...]:
    product = row["_product"]
    if sort_by == "price":
        return (product.price, -row["match_score"], product.name.casefold())
    if sort_by == "unit_price":
        unit_price = product.price_per_unit
        return (
            float(unit_price) if unit_price is not None else inf,
            product.unit or "",
            -row["match_score"],
            product.name.casefold(),
        )
    return (
        -row["match_score"],
        float(product.price_per_unit) if product.price_per_unit is not None else inf,
        product.name.casefold(),
    )


def _search_pages(
    provider: GroceryProvider,
    query: str,
    *,
    limit: int,
    postal_code: str | None,
    eco: bool,
    cache_ttl_seconds: int,
) -> tuple[list[Product], dict[str, Any]]:
    cache_safe = provider.catalogue_contract().get("cache_safe", True) is not False
    effective_cache_ttl = cache_ttl_seconds if cache_safe else 0
    cache_key = (
        provider,
        query,
        postal_code,
        eco,
        limit,
    )
    now = monotonic()
    if effective_cache_ttl > 0:
        with _CACHE_LOCK:
            cached = _SEARCH_CACHE.get(cache_key)
            if cached and cached[0] > now:
                return list(cached[1]), {**cached[2], "cache_hit": True}

    products: list[Product] = []
    cursor: str | None = None
    pages = 0
    last_page: dict[str, Any] = {}
    stable_page_size = min(100, limit)
    while len(products) < limit and pages < 20:
        page = provider.search_page(
            query,
            page_size=stable_page_size,
            cursor=cursor,
            postal_code=postal_code,
            eco=eco,
        )
        current = list(page.get("products", ()))
        products.extend(current)
        pages += 1
        last_page = page
        next_cursor = page.get("next_cursor")
        if not next_cursor or not current:
            break
        cursor = str(next_cursor)

    products = products[:limit]
    total = last_page.get("total")
    has_next = last_page.get("has_next")
    if total is not None:
        has_next = len(products) < int(total)
    diagnostic = {
        "returned": len(products),
        "pages_fetched": pages,
        "pagination": last_page.get("pagination", "bounded_unknown"),
        "total": total,
        "has_next": has_next,
        "possibly_saturated": (
            bool(has_next)
            if has_next is not None
            else len(products) >= min(limit, 100)
        ),
        "cache_hit": False,
        "cache_enabled": effective_cache_ttl > 0,
    }
    if effective_cache_ttl > 0:
        with _CACHE_LOCK:
            _SEARCH_CACHE[cache_key] = (
                now + effective_cache_ttl,
                tuple(products),
                dict(diagnostic),
            )
    return products, diagnostic


def _search_store(
    provider: GroceryProvider,
    *,
    query: str,
    variants: Sequence[str],
    required_groups: tuple[tuple[str, ...], ...],
    excluded: tuple[str, ...],
    postal_code: str | None,
    eco: bool,
    limit_per_query: int,
    result_limit: int,
    sort_by: str,
    auto_equivalences: bool,
    cache_ttl_seconds: int,
    category_search: bool,
) -> dict[str, Any]:
    found: dict[str, dict[str, Any]] = {}
    query_diagnostics: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    raw_hits = 0

    local_variants = list(variants)
    category_hints: list[str] = []
    if category_search:
        try:
            stack = list(provider.categories(depth=3, postal_code=postal_code))
            query_tokens = _token_roots(query)
            while stack:
                row = stack.pop()
                if not isinstance(row, dict):
                    continue
                name = next(
                    (str(row[key]) for key in ("name", "label", "title") if row.get(key)),
                    "",
                )
                if name and query_tokens & _token_roots(name):
                    category_hints.append(name)
                for key in ("children", "categories", "items"):
                    if isinstance(row.get(key), list):
                        stack.extend(row[key])
            for hint in category_hints[:3]:
                if hint.casefold() not in {value.casefold() for value in local_variants}:
                    local_variants.append(hint)
        except OpenGroceryError:
            category_hints = []

    for variant in local_variants:
        try:
            products, diagnostic = _search_pages(
                provider,
                variant,
                limit=limit_per_query,
                postal_code=postal_code,
                eco=eco,
                cache_ttl_seconds=cache_ttl_seconds,
            )
        except OpenGroceryError as exc:
            errors.append({"query": variant, "error": str(exc)})
            continue
        raw_hits += len(products)
        query_diagnostics.append({"query": variant, **diagnostic})
        for position, product in enumerate(products):
            key = f"{product.store.casefold()}:{product.id}"
            score, _ = score_product(variant, product, position=position)
            existing = found.get(key)
            if existing is None:
                found[key] = {
                    "_product": product,
                    "match_score": score,
                    "matched_queries": [variant],
                }
                continue
            if variant not in existing["matched_queries"]:
                existing["matched_queries"].append(variant)
            existing["match_score"] = max(existing["match_score"], score)

    unique_before_filter = len(found)
    rows: list[dict[str, Any]] = []
    rejection_reasons: dict[str, int] = {}
    for row in found.values():
        product = row["_product"]
        if not _passes_filters(
            product,
            required_groups=required_groups,
            excluded=excluded,
        ):
            rejection_reasons["explicit_term_filter"] = rejection_reasons.get("explicit_term_filter", 0) + 1
            continue
        if auto_equivalences:
            semantic_match = assess_query_candidate(query, product)
            if semantic_match["verdict"] == "different":
                conflict_keys = semantic_match.get("conflicts", {}).keys() or ("semantic_conflict",)
                for key in conflict_keys:
                    reason = f"semantic_{key}"
                    rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
                continue
            row["semantic_match"] = semantic_match
        rows.append(row)
    filtered_count = len(rows)
    rows.sort(key=lambda row: _sort_key(row, sort_by))
    truncated = filtered_count > result_limit
    rows = rows[:result_limit]
    products_out: list[dict[str, Any]] = []
    for row in rows:
        payload = row["_product"].to_dict()
        payload["match_score"] = round(row["match_score"], 4)
        payload["matched_queries"] = row["matched_queries"]
        if "semantic_match" in row:
            semantic = row["semantic_match"]
            payload["semantic_match"] = {
                "verdict": semantic["verdict"],
                "score": semantic["score"],
                "reasons": semantic["reasons"],
                "conflicts": semantic["conflicts"],
                "uncertain_facets": semantic["uncertain_facets"],
                "product_profile": semantic["product_profile"],
            }
        products_out.append(payload)

    saturated = [
        item["query"] for item in query_diagnostics if item["possibly_saturated"]
    ]
    bounded_complete = not errors and not saturated and not truncated
    return {
        "store": provider.info.key,
        "query": query,
        "count": len(products_out),
        "raw_hits": raw_hits,
        "unique_before_filter": unique_before_filter,
        "rejected_by_filters": unique_before_filter - filtered_count,
        "rejection_reasons": dict(sorted(rejection_reasons.items())),
        "filtered_before_result_limit": filtered_count,
        "result_limit_truncated": truncated,
        "possibly_saturated_queries": saturated,
        "query_diagnostics": query_diagnostics,
        "errors": errors,
        "bounded_complete": bounded_complete,
        "catalogue_contract": provider.catalogue_contract(),
        "category_hints_used": category_hints[:3],
        "products": products_out,
    }


def search_catalogues_expanded(
    registry: ProviderRegistry,
    *,
    query: str,
    stores: Sequence[str] | None = None,
    aliases: Sequence[str] | None = None,
    required_term_groups: Sequence[Sequence[str]] | None = None,
    excluded_terms: Sequence[str] | None = None,
    postal_code: str | None = None,
    eco: bool = False,
    limit_per_query: int = 100,
    result_limit: int = 500,
    sort_by: str = "relevance",
    auto_equivalences: bool = True,
    cache_ttl_seconds: int = 120,
    category_search: bool = False,
) -> dict[str, Any]:
    """Union several bounded catalogue searches with explicit coverage evidence."""

    if limit_per_query < 1 or limit_per_query > 500:
        raise InvalidRequest("limit_per_query must be between 1 and 500")
    if result_limit < 1 or result_limit > 500:
        raise InvalidRequest("result_limit must be between 1 and 500")
    if sort_by not in _SORT_OPTIONS:
        raise InvalidRequest("sort_by must be relevance, price or unit_price")
    if cache_ttl_seconds < 0 or cache_ttl_seconds > 3600:
        raise InvalidRequest("cache_ttl_seconds must be between 0 and 3600")
    variants = _query_variants(
        query,
        aliases,
        auto_equivalences=auto_equivalences,
    )
    groups = _term_groups(required_term_groups)
    excluded = _excluded_terms(excluded_terms)

    keys: list[str] = []
    seen_stores: set[str] = set()
    selected_stores = registry.keys() if stores is None else stores
    for raw_key in selected_stores:
        key = _clean_text(raw_key, name="store", maximum=50).casefold()
        if key not in seen_stores:
            registry.get(key)  # Validate before starting concurrent work.
            seen_stores.add(key)
            keys.append(key)
    if not keys:
        raise InvalidRequest("at least one store is required")
    if len(keys) > 20:
        raise InvalidRequest("a single expanded search is limited to 20 stores")

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(
        max_workers=min(8, len(keys)),
        thread_name_prefix="grocery-expanded-search",
    ) as pool:
        futures = {
            pool.submit(
                _search_store,
                registry.get(key),
                query=variants[0],
                variants=variants,
                required_groups=groups,
                excluded=excluded,
                postal_code=postal_code,
                eco=eco,
                limit_per_query=limit_per_query,
                result_limit=result_limit,
                sort_by=sort_by,
                auto_equivalences=auto_equivalences,
                cache_ttl_seconds=cache_ttl_seconds,
                category_search=category_search,
            ): key
            for key in keys
        }
        for future in as_completed(futures):
            results.append(future.result())

    order = {key: position for position, key in enumerate(keys)}
    results.sort(key=lambda result: order[result["store"]])
    partial = any(not result["bounded_complete"] for result in results)
    return {
        "query": variants[0],
        "query_variants": variants,
        "query_expansions": _expansion_evidence(
            variants[0],
            variants,
            aliases,
            auto_equivalences=auto_equivalences,
        ),
        "required_term_groups": [list(group) for group in groups],
        "excluded_terms": list(excluded),
        "postal_code": postal_code,
        "sort_by": sort_by,
        "auto_equivalences": auto_equivalences,
        "cache_ttl_seconds": cache_ttl_seconds,
        "category_search": category_search,
        "query_profile": analyze_query_text(variants[0]).to_dict(),
        "partial": partial,
        "total_count": sum(result["count"] for result in results),
        "stores": results,
        "note": (
            "Results are the deduplicated union of the submitted query variants. "
            "bounded_complete only means no submitted query hit an observed limit or "
            "provider error; it is not proof that a retailer indexed every relevant SKU."
        ),
    }


def audit_catalogue_geography(
    registry: ProviderRegistry,
    *,
    query: str,
    postal_codes: Sequence[str],
    stores: Sequence[str] | None = None,
    limit_per_query: int = 50,
) -> dict[str, Any]:
    """Compare public assortments across representative postal codes."""

    codes = list(dict.fromkeys(str(code).strip() for code in postal_codes if str(code).strip()))
    if not 2 <= len(codes) <= 8:
        raise InvalidRequest("postal_codes must contain between 2 and 8 unique values")
    snapshots: list[dict[str, Any]] = []
    for code in codes:
        result = search_catalogues_expanded(
            registry,
            query=query,
            stores=stores,
            postal_code=code,
            limit_per_query=limit_per_query,
            result_limit=500,
        )
        snapshots.append(result)
    by_store: dict[str, dict[str, set[str]]] = {}
    for snapshot in snapshots:
        for store in snapshot["stores"]:
            by_store.setdefault(store["store"], {})[str(snapshot["postal_code"])] = {
                str(product["id"]) for product in store["products"]
            }
    differences: dict[str, Any] = {}
    for store, regions in by_store.items():
        sets = list(regions.values())
        union = set().union(*sets) if sets else set()
        common = set.intersection(*sets) if sets else set()
        differences[store] = {
            "counts": {code: len(values) for code, values in regions.items()},
            "common_products": len(common),
            "union_products": len(union),
            "regional_products": len(union - common),
        }
    return {
        "query": query,
        "postal_codes": codes,
        "stores": differences,
        "partial": any(snapshot["partial"] for snapshot in snapshots),
        "note": "Differences describe bounded public samples and never bind ontology rules to one region.",
    }


__all__ = [
    "audit_catalogue_geography",
    "clear_catalogue_cache",
    "search_catalogues_expanded",
]
