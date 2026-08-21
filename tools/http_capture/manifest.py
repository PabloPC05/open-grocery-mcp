"""Build compact, value-free endpoint manifests from sanitized capture events."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

RETAILER_SUFFIXES = {
    "gadis": ("gadisline.com",),
    "froiz": ("froiz.com", "empathy.co"),
}
STATIC_PATH = re.compile(
    r"(?i)(?:^|/)(?:_next/static|static|assets|fonts|images?)(?:/|$)|"
    r"\.(?:css|js|mjs|map|svg|png|jpe?g|gif|webp|woff2?|ttf|ico)$"
)


def _schema(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _schema(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return {"type": "array", "items": _schema(value[0]) if value else None}
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "string"


def _operation_hint(method: str, host: str, path: str) -> str:
    text = f"{host}{path}".casefold()
    if any(token in text for token in ("auth", "login", "session", "token")):
        return "auth"
    if any(token in text for token in ("checkout", "finalizar-compra")):
        return "checkout"
    if any(token in text for token in ("address", "direccion", "domicilio")):
        return "addresses"
    if any(token in text for token in ("slot", "delivery", "entrega", "franja")):
        return "delivery"
    if any(token in text for token in ("cart", "basket", "cesta", "carrito")):
        return "cart"
    if any(token in text for token in ("postal", "/stores", "/store")):
        return "location"
    if any(token in text for token in ("catalog", "product", "category", "search")):
        return "catalog"
    if method.upper() not in {"GET", "HEAD", "OPTIONS"}:
        return "write-other"
    return "other"


def _is_retailer_endpoint(store: str, host: str, path: str) -> bool:
    suffixes = RETAILER_SUFFIXES.get(store, ())
    normalized_host = host.casefold()
    if not any(
        normalized_host == suffix or normalized_host.endswith("." + suffix)
        for suffix in suffixes
    ):
        return False
    return not STATIC_PATH.search(path)


def add_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    response_statuses: dict[tuple[str, str, str], set[int]] = defaultdict(set)

    for event in payload.get("events", []):
        url = urlsplit(str(event.get("url", "")))
        method = str(event.get("method", "GET")).upper()
        key = (method, url.netloc, url.path)
        row = grouped.setdefault(
            key,
            {
                "method": key[0],
                "host": key[1],
                "path": key[2],
                "query_keys": sorted({name for name, _ in parse_qsl(url.query)}),
                "phases": set(),
                "request_header_names": set(),
                "request_body_schema": None,
                "response_body_schema": None,
                "operation_hint": _operation_hint(method, url.netloc, url.path),
            },
        )
        row["phases"].add(str(event.get("phase", "unknown")))
        if event.get("kind") == "request":
            row["request_header_names"].update(event.get("headers", {}).keys())
            if event.get("body") is not None:
                row["request_body_schema"] = _schema(event.get("body"))
        elif event.get("kind") == "response":
            status = event.get("status")
            if isinstance(status, int):
                response_statuses[key].add(status)
            if event.get("body") is not None:
                row["response_body_schema"] = _schema(event.get("body"))

    manifest = []
    for key, row in grouped.items():
        row["phases"] = sorted(row["phases"])
        row["request_header_names"] = sorted(row["request_header_names"])
        row["response_statuses"] = sorted(response_statuses[key])
        manifest.append(row)
    manifest.sort(
        key=lambda item: (
            item["operation_hint"],
            item["host"],
            item["path"],
            item["method"],
        )
    )

    store = str(payload.get("store", "")).casefold()
    retailer_manifest = [
        row
        for row in manifest
        if _is_retailer_endpoint(store, row["host"], row["path"])
    ]
    payload["endpoint_manifest"] = manifest
    payload["retailer_endpoint_manifest"] = retailer_manifest
    payload["manifest_summary"] = {
        "all_endpoint_count": len(manifest),
        "retailer_endpoint_count": len(retailer_manifest),
        "operation_counts": {
            operation: sum(
                1 for row in retailer_manifest if row["operation_hint"] == operation
            )
            for operation in sorted(
                {row["operation_hint"] for row in retailer_manifest}
            )
        },
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload
