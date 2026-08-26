"""Reproducible semantic corpus audit without private retailer state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from open_grocery_mcp.catalogue_search import search_catalogues_expanded
from open_grocery_mcp.data_files import data_path
from open_grocery_mcp.equivalence import analyze_product_text, compare_profiles
from open_grocery_mcp.errors import InvalidRequest
from open_grocery_mcp.registry import ProviderRegistry


def default_corpus_path() -> Path:
    return data_path("equivalence_corpus.json")


def audit_corpus(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else default_corpus_path()
    with target.open(encoding="utf-8") as handle:
        corpus = json.load(handle)
    failures: list[dict[str, Any]] = []
    confusion = {
        "true_positive": 0,
        "true_negative": 0,
        "false_positive": 0,
        "false_negative": 0,
    }
    classifications = corpus.get("classification_cases", ())
    pairs = corpus.get("pair_cases", ())
    for case in classifications:
        observed = analyze_product_text(case["name"]).family
        if observed != case["family"]:
            failures.append({"case": case, "observed_family": observed})
            confusion["false_negative"] += 1
        else:
            confusion["true_positive"] += 1
    for case in pairs:
        result = compare_profiles(
            analyze_product_text(case["left"]),
            analyze_product_text(case["right"]),
        )
        expected = case["verdict"]
        passed = (
            result["verdict"] in {"equivalent", "compatible"}
            if expected == "equivalent_or_compatible"
            else result["verdict"] == expected
        )
        if not passed:
            failures.append({"case": case, "observed_verdict": result["verdict"]})
        expected_positive = expected == "equivalent_or_compatible"
        if passed and expected_positive:
            confusion["true_positive"] += 1
        elif passed:
            confusion["true_negative"] += 1
        elif expected_positive:
            confusion["false_negative"] += 1
        else:
            confusion["false_positive"] += 1
    total = len(classifications) + len(pairs)
    passed_count = total - len(failures)
    return {
        "corpus_version": corpus.get("version"),
        "privacy": corpus.get("privacy"),
        "total_cases": total,
        "passed": passed_count,
        "failed": len(failures),
        "pass_rate": round(passed_count / total, 4) if total else 0.0,
        "recognized_family_coverage": round(
            sum(analyze_product_text(case["name"]).family is not None for case in classifications)
            / len(classifications),
            4,
        ) if classifications else 0.0,
        "confusion_matrix": confusion,
        "stores_represented": sorted(
            {
                str(store)
                for case in classifications
                for store in [case.get("store")]
                if store
            }
            | {
                str(store)
                for case in pairs
                for store in case.get("stores", ())
            }
        ),
        "failures": failures,
        "source": str(target),
    }


def audit_live_catalogues(
    registry: ProviderRegistry,
    *,
    queries: Sequence[str],
    stores: Sequence[str] | None = None,
    postal_code: str | None = None,
    limit_per_query: int = 50,
) -> dict[str, Any]:
    """Aggregate a bounded, read-only quality sample without returning product data."""

    cleaned = list(dict.fromkeys(" ".join(query.split()) for query in queries if query.strip()))
    if not cleaned or len(cleaned) > 20:
        raise InvalidRequest("queries must contain between 1 and 20 non-empty values")
    samples: list[dict[str, Any]] = []
    family_counts: dict[str, int] = {}
    totals = {"observed": 0, "accepted": 0, "rejected": 0, "uncertain": 0, "errors": 0, "saturated": 0}
    for query in cleaned:
        result = search_catalogues_expanded(
            registry,
            query=query,
            stores=stores,
            postal_code=postal_code,
            limit_per_query=limit_per_query,
            result_limit=500,
            cache_ttl_seconds=0,
            category_search=True,
        )
        stores_out: list[dict[str, Any]] = []
        for store in result["stores"]:
            uncertain = sum(
                bool(product.get("semantic_match", {}).get("uncertain_facets"))
                for product in store["products"]
            )
            for product in store["products"]:
                family = product.get("semantic_match", {}).get("product_profile", {}).get("family")
                if family:
                    family_counts[str(family)] = family_counts.get(str(family), 0) + 1
            rejected = store["unique_before_filter"] - store["filtered_before_result_limit"]
            saturated = len(store["possibly_saturated_queries"])
            totals["observed"] += store["unique_before_filter"]
            totals["accepted"] += store["filtered_before_result_limit"]
            totals["rejected"] += rejected
            totals["uncertain"] += uncertain
            totals["errors"] += len(store["errors"])
            totals["saturated"] += saturated
            stores_out.append(
                {
                    "store": store["store"],
                    "observed": store["unique_before_filter"],
                    "accepted": store["filtered_before_result_limit"],
                    "rejected": rejected,
                    "uncertain": uncertain,
                    "errors": store["errors"],
                    "saturated_queries": store["possibly_saturated_queries"],
                    "coverage_contract": store["catalogue_contract"],
                }
            )
        samples.append({"query": query, "partial": result["partial"], "stores": stores_out})
    return {
        "mode": "read_only_public_catalogue_audit",
        "postal_code": postal_code,
        "queries": cleaned,
        "totals": totals,
        "recognized_families": dict(sorted(family_counts.items())),
        "samples": samples,
        "contains_product_or_private_identifiers": False,
    }


__all__ = ["audit_corpus", "audit_live_catalogues", "default_corpus_path"]
