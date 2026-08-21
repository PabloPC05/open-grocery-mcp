"""Build a compact, value-free endpoint manifest from sanitized capture events."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


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


def add_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    response_statuses: dict[tuple[str, str, str], set[int]] = defaultdict(set)

    for event in payload.get("events", []):
        url = urlsplit(str(event.get("url", "")))
        key = (str(event.get("method", "GET")), url.netloc, url.path)
        row = grouped.setdefault(
            key,
            {
                "method": key[0],
                "host": key[1],
                "path": key[2],
                "query_keys": sorted({name for name, _ in __import__("urllib.parse", fromlist=["parse_qsl"]).parse_qsl(url.query)}),
                "phases": set(),
                "request_header_names": set(),
                "request_body_schema": None,
                "response_body_schema": None,
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
    manifest.sort(key=lambda item: (item["host"], item["path"], item["method"]))
    payload["endpoint_manifest"] = manifest
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload
