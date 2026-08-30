"""One-shot prepare purchase workflow with smart defaults."""

from __future__ import annotations

from typing import Any

from open_grocery_mcp.basket_optimization import optimize_semantic_basket
from open_grocery_mcp.comparison import compare_baskets, parse_basket, price_basket
from open_grocery_mcp.drafts import DraftCartStore
from open_grocery_mcp.errors import InvalidRequest
from open_grocery_mcp.models import as_decimal
from open_grocery_mcp.registry import ProviderRegistry


def prepare_purchase(
    registry: ProviderRegistry,
    drafts: DraftCartStore,
    *,
    items: list[str | dict[str, Any]] | None = None,
    list_id: str | None = None,
    shopping_list_items: list[dict[str, Any]] | None = None,
    store: str | None = None,
    postal_code: str | None = None,
    max_total: float | None = None,
    search_limit: int = 10,
    eco: bool = False,
    include_loyalty: bool = False,
    profile: dict[str, Any] | None = None,
    multi_store: bool = False,
) -> dict[str, Any]:
    """Prepare a complete purchase from items or a shopping list.
    
    Main user-facing tool that:
    - Accepts ad-hoc items or a shopping list
    - Uses default postal_code from shared addresses if not provided
    - Applies profile defaults (max_total, include_loyalty, excluded_terms)
    - Compares baskets or optimizes across stores
    - Returns recommended store(s) with line-item matches
    - Includes product+delivery+minimum totals
    - Creates local cart draft(s) ready for prepare_real_cart_update
    - Does NOT write to any retailer
    
    Args:
        items: Ad-hoc list of items (strings or dicts with quantity/notes)
        list_id: Shopping list ID to use instead of ad-hoc items
        shopping_list_items: Pre-loaded shopping list items (from shopping_lists module)
        store: Preferred store, or None to compare/optimize
        postal_code: Postal code for delivery estimates
        max_total: Maximum total spend
        search_limit: Number of search results per item
        eco: Prefer eco products
        include_loyalty: Include loyalty prices
        profile: Shopping profile with defaults and preferences
        multi_store: If True, allow split across multiple stores (uses optimize)
    
    Returns:
        Dict with recommendation, matches, totals, and draft ID(s)
    """
    
    # Resolve items from list or ad-hoc
    if items is None and shopping_list_items is None:
        raise InvalidRequest("must provide either items or shopping_list_items")
    
    if items is not None and shopping_list_items is not None:
        raise InvalidRequest("provide either items or shopping_list_items, not both")
    
    # Convert shopping list items to basket format
    basket_items = items
    if shopping_list_items is not None:
        basket_items = [
            {
                "query": entry["item"],
                "quantity": entry.get("quantity", 1.0),
            }
            for entry in shopping_list_items
        ]
    
    if not basket_items:
        raise InvalidRequest("no items to purchase")
    
    # Apply profile defaults
    profile = profile or {}
    effective_max_total = max_total or profile.get("default_max_total")
    effective_loyalty = include_loyalty or profile.get("include_loyalty_default", False)
    excluded_terms = profile.get("excluded_terms", [])
    allergies = profile.get("allergies", [])
    
    # Build constraints for substitution
    constraints: dict[str, Any] = {}
    if excluded_terms:
        constraints["excluded_terms"] = excluded_terms
    if allergies:
        constraints["allergies"] = allergies
    
    private_label_pref = profile.get("private_label_preference", "any")
    if private_label_pref == "never":
        constraints["no_private_label"] = True
    elif private_label_pref == "only":
        constraints["private_label_only"] = True
    
    # Multi-store optimization
    if multi_store:
        stores_list = None if not store else [store]
        result = optimize_semantic_basket(
            registry,
            items=basket_items,
            stores=stores_list,
            postal_code=postal_code,
            constraints=constraints,
            search_limit=search_limit,
            eco=eco,
            include_loyalty=effective_loyalty,
        )
        
        # Create drafts for each store
        draft_ids = []
        for allocation in result.get("stores", []):
            store_key = allocation["store"]
            provider = registry.get(store_key)
            
            store_items = [
                item["query"] if isinstance(item, dict) and "query" in item
                else {"query": item.get("name", item.get("item", str(item)))}
                for item in allocation.get("items", [])
            ]
            
            if not store_items:
                continue
            
            parsed = parse_basket(store_items)
            priced = price_basket(
                provider,
                parsed,
                postal_code=postal_code,
                search_limit=search_limit,
                eco=eco,
                include_loyalty=effective_loyalty,
            )
            
            draft = drafts.create(priced)
            draft_ids.append(draft["draft_id"])
        
        return {
            "strategy": "multi_store",
            "optimization_result": result,
            "draft_ids": draft_ids,
            "total_stores": len(draft_ids),
            "max_total": effective_max_total,
            "profile_applied": {
                "include_loyalty": effective_loyalty,
                "excluded_terms": excluded_terms,
                "allergies": allergies,
                "private_label_preference": private_label_pref,
            },
        }
    
    # Single store or comparison
    if store:
        provider = registry.get(store)
        parsed = parse_basket(basket_items)
        result = price_basket(
            provider,
            parsed,
            postal_code=postal_code,
            search_limit=search_limit,
            eco=eco,
            include_loyalty=effective_loyalty,
        )
        
        draft = drafts.create(result)
        
        # Check max_total constraint
        total = as_decimal(result.get("total", 0))
        delivery_total = result.get("delivery", {}).get("estimated_checkout_total", total)
        exceeds_max = (
            effective_max_total is not None
            and delivery_total > effective_max_total
        )
        
        return {
            "strategy": "single_store",
            "store": store,
            "basket_result": result,
            "draft_id": draft["draft_id"],
            "exceeds_max_total": exceeds_max,
            "max_total": effective_max_total,
            "profile_applied": {
                "include_loyalty": effective_loyalty,
                "excluded_terms": excluded_terms,
                "allergies": allergies,
                "private_label_preference": private_label_pref,
            },
        }
    
    # Compare across all stores
    stores_list = list(registry.keys())
    
    # Parse basket_items before using it
    parsed = parse_basket(basket_items)
    
    result = compare_baskets(
        registry,
        items=basket_items,
        stores=stores_list,
        postal_code=postal_code,
        search_limit=search_limit,
        eco=eco,
        include_loyalty=effective_loyalty,
    )
    
    # Find cheapest with delivery
    cheapest = None
    cheapest_store = None
    for store_result in result.get("stores", []):
        store_total = store_result.get("delivery", {}).get(
            "estimated_checkout_total",
            store_result.get("total", 0),
        )
        if cheapest is None or store_total < cheapest:
            cheapest = store_total
            cheapest_store = store_result["store"]
    
    # Create draft for cheapest
    draft_id = None
    if cheapest_store:
        provider = registry.get(cheapest_store)
        priced = price_basket(
            provider,
            parsed,
            postal_code=postal_code,
            search_limit=search_limit,
            eco=eco,
            include_loyalty=effective_loyalty,
        )
        draft = drafts.create(priced)
        draft_id = draft["draft_id"]
    
    exceeds_max = (
        effective_max_total is not None
        and cheapest is not None
        and cheapest > effective_max_total
    )
    
    return {
        "strategy": "comparison",
        "comparison_result": result,
        "recommended_store": cheapest_store,
        "recommended_total": cheapest,
        "draft_id": draft_id,
        "exceeds_max_total": exceeds_max,
        "max_total": effective_max_total,
        "profile_applied": {
            "include_loyalty": effective_loyalty,
            "excluded_terms": excluded_terms,
            "allergies": allergies,
            "private_label_preference": private_label_pref,
        },
    }


__all__ = ["prepare_purchase"]
