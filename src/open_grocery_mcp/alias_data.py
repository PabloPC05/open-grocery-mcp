"""Versioned semantic search aliases loaded from repository data."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from open_grocery_mcp.data_files import data_path


@lru_cache(maxsize=1)
def semantic_alias_data() -> dict[str, Any]:
    path = data_path("semantic_aliases.json")
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or not isinstance(payload.get("concepts"), dict):
        raise ValueError("semantic alias data has an invalid schema")
    return payload


def aliases_for(concept: str) -> tuple[str, ...]:
    values = semantic_alias_data()["concepts"].get(concept, ())
    return tuple(str(value) for value in values if str(value).strip())


__all__ = ["aliases_for", "semantic_alias_data"]
