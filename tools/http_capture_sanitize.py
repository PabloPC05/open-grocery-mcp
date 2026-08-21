"""Sanitize Playwright HAR output for retailer API reverse engineering."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SENSITIVE = re.compile(
    r"(?i)(password|passwd|secret|token|authorization|cookie|csrf|xsrf|email|phone|"
    r"mobile|address|street|postal|zip|first.?name|last.?name|surname|dni|nif|card|"
    r"iban|bic|cvv|cvc|payment|birth|session|customer.?id|user.?id|account.?id)"
)
EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
PHONE = re.compile(r"(?<!\d)(?:\+?34[ .-]?)?[6789](?:[ .-]?\d){8}(?!\d)")
POSTAL = re.compile(r"(?<!\d)\d{5}(?!\d)")
STREET = re.compile(r"(?i)\b(?:calle|c/|avenida|avda|paseo|plaza|rúa|rua|estrada|camino)\b[^\n]{0,120}\d")
RELEVANT = re.compile(
    r"(?i)(api|graphql|auth|login|session|customer|user|profile|cart|basket|cesta|"
    r"carrito|checkout|address|direccion|delivery|entrega|slot|order|pedido|postal|store)"
)
ANALYTICS = re.compile(
    r"(?i)(google-analytics|googletagmanager|doubleclick|facebook|hotjar|clarity|"
    r"sentry|datadog|newrelic|segment|optimizely)"
)


def _safe_text(value: str) -> str:
    text = value
    for name in (
        "GADIS_TEST_USERNAME", "GADIS_TEST_PASSWORD",
        "FROIZ_TEST_USERNAME", "FROIZ_TEST_PASSWORD",
    ):
        secret = os.getenv(name, "")
        if secret:
            text = text.replace(secret, "<redacted>")
    text = EMAIL.sub("<redacted-email>", text)
    text = PHONE.sub("<redacted-phone>", text)
    text = POSTAL.sub("<redacted-postal-code>", text)
    return "<redacted-address>" if STREET.search(text) else text


def _clean(value: Any, key: str = "") -> Any:
    if SENSITIVE.search(key):
        return "<redacted>"
    if isinstance(value, Mapping):
        return {str(k): _clean(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v, key) for v in value[:200]]
    if isinstance(value, str):
        return _safe_text(value[:8000])
    return value


def _url(value: str) -> str:
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    query = []
    for key, raw in parse_qsl(parts.query, keep_blank_values=True):
        query.append((key, "<redacted>" if SENSITIVE.search(key) else _safe_text(raw)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def _headers(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    result = []
    for row in rows:
        name = str(row.get("name", ""))
        value = str(row.get("value", ""))
        lower = name.casefold()
        if lower in {"authorization", "cookie", "set-cookie", "proxy-authorization"} or SENSITIVE.search(name):
            value = "<redacted>"
        elif not (lower in {"accept", "content-type", "origin", "referer", "user-agent", "x-requested-with"} or lower.startswith("x-")):
            continue
        result.append({"name": name, "value": _safe_text(value[:2000])})
    return result


def _body(post_data: Mapping[str, Any] | None) -> Any:
    if not post_data:
        return None
    mime = str(post_data.get("mimeType", ""))
    text = str(post_data.get("text", ""))
    if "multipart/form-data" in mime.casefold():
        return "<redacted-multipart-body>"
    if "json" in mime.casefold() or text.lstrip().startswith(("{", "[")):
        try:
            return _clean(json.loads(text))
        except json.JSONDecodeError:
            pass
    params = post_data.get("params")
    if isinstance(params, list):
        return {str(p.get("name", "")): _clean(p.get("value"), str(p.get("name", ""))) for p in params}
    return _safe_text(text[:4000])


def _response_body(content: Mapping[str, Any]) -> Any:
    mime = str(content.get("mimeType", ""))
    text = str(content.get("text", ""))
    if "json" not in mime.casefold() or not text:
        return None
    try:
        return _clean(json.loads(text))
    except json.JSONDecodeError:
        return None


def sanitize_har(raw_path: Path, output_path: Path, metadata: Mapping[str, Any]) -> dict[str, Any]:
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    entries = raw.get("log", {}).get("entries", [])
    sanitized = []
    for entry in entries:
        request = entry.get("request", {})
        url = str(request.get("url", ""))
        host = urlsplit(url).hostname or ""
        method = str(request.get("method", "GET"))
        resource = str(entry.get("_resourceType", ""))
        if ANALYTICS.search(host) or resource in {"image", "font", "media", "stylesheet"}:
            continue
        if method == "GET" and resource not in {"xhr", "fetch", "document"} and not RELEVANT.search(url):
            continue
        response = entry.get("response", {})
        sanitized.append(
            {
                "started_at": entry.get("startedDateTime"),
                "duration_ms": entry.get("time"),
                "resource_type": resource,
                "request": {
                    "method": method,
                    "url": _url(url),
                    "headers": _headers(request.get("headers", [])),
                    "body": _body(request.get("postData")),
                },
                "response": {
                    "status": response.get("status"),
                    "headers": _headers(response.get("headers", [])),
                    "body": _response_body(response.get("content", {})),
                },
            }
        )
    payload = {
        "schema_version": 1,
        **dict(metadata),
        "safety": {
            "credentials_recorded": False,
            "sensitive_values_redacted": True,
            "raw_har_deleted_after_sanitization": True,
            "final_order_clicked": False,
        },
        "entries": sanitized,
    }
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload
