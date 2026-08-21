#!/usr/bin/env python3
"""Extract a value-free HTTP contract from decompiled Android sources.

The scanner publishes symbols, HTTP methods, endpoint paths, parameter names and
safe public constants. It never publishes complete decompiled source files and
redacts secrets, credentials and token-shaped values.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SOURCE_SUFFIXES = {".java", ".kt"}
RELEVANT = re.compile(
    r"(?i)(auth|oauth|openid|keycloak|login|sign.?in|password|forgot|recover|reset|"
    r"token|refresh|client.?id|grant.?type|session|register|sign.?up|profile|"
    r"customer|client|cart|basket|cesta|checkout|address|delivery|slot|order|"
    r"pedido|postal|store|gadis|gadisa|gadisline)"
)
SENSITIVE_KEY = re.compile(
    r"(?i)(secret|password|passwd|authorization|access.?token|refresh.?token|"
    r"id.?token|api.?key|private.?key|cookie|session.?id|csrf|xsrf|bearer)"
)
TOKENISH = re.compile(r"^[A-Za-z0-9._~+/=-]{32,}$")
URL_RE = re.compile(r"https?://[^\s\"'<>]{4,500}", re.I)
ANNOTATION_RE = re.compile(
    r"@(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s*\(\s*[\"']([^\"']*)[\"']\s*\)",
    re.I,
)
PARAM_ANNOTATION_RE = re.compile(
    r"@(Field|Query|Header|Path|Part|FormUrlEncoded|Multipart|Body)"
    r"(?:\s*\(\s*[\"']([^\"']+)[\"']\s*\))?",
    re.I,
)
CONST_RE = re.compile(
    r"(?:(?:public|private|protected|internal|static|final|const|val|var)\s+)*"
    r"([A-Za-z_$][A-Za-z0-9_$]{2,})\s*(?::\s*[A-Za-z0-9_?.<>]+)?\s*=\s*"
    r"[\"']([^\"'\r\n]{1,500})[\"']"
)
CLASS_RE = re.compile(
    r"\b(?:class|interface|object|enum\s+class)\s+([A-Za-z_$][A-Za-z0-9_$]*)"
)
METHOD_RE = re.compile(
    r"(?:(?:public|private|protected|internal|static|final|suspend|abstract|override)\s+)*"
    r"(?:[A-Za-z0-9_?.<>\[\], ]+\s+)?([A-Za-z_$][A-Za-z0-9_$]*)\s*\(([^)]*)\)"
)


def safe_url(value: str) -> str | None:
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return None
    query = urlencode(
        (key, "<value>")
        for key, _ in parse_qsl(parts.query, keep_blank_values=True)
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def safe_value(name: str, value: str) -> str:
    value = value.strip()
    if SENSITIVE_KEY.search(name):
        # OAuth parameter *names* remain in the manifest, but credential values do not.
        return "<redacted>"
    if "@" in value or TOKENISH.fullmatch(value):
        return "<redacted>"
    url = safe_url(value)
    if url:
        return url
    if len(value) > 160:
        return "<redacted-long-value>"
    return value


def source_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.casefold() in SOURCE_SUFFIXES:
            yield path


def nearest_class(lines: list[str], index: int, fallback: str) -> str:
    for pos in range(index, max(-1, index - 250), -1):
        match = CLASS_RE.search(lines[pos])
        if match:
            return match.group(1)
    return fallback


def nearest_method(lines: list[str], index: int) -> dict[str, Any] | None:
    for pos in range(index, min(len(lines), index + 30)):
        match = METHOD_RE.search(lines[pos])
        if match and match.group(1) not in {"if", "for", "while", "switch", "when"}:
            return {
                "name": match.group(1),
                "parameter_annotations": sorted(
                    {
                        (ann.group(1).lower(), ann.group(2) or "")
                        for ann in PARAM_ANNOTATION_RE.finditer(match.group(2))
                    }
                ),
            }
    return None


def scan(root: Path) -> dict[str, Any]:
    endpoints: list[dict[str, Any]] = []
    constants: list[dict[str, Any]] = []
    symbols: dict[str, set[str]] = defaultdict(set)
    urls: dict[str, set[str]] = defaultdict(set)
    files_scanned = 0

    for path in source_files(root):
        files_scanned += 1
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not RELEVANT.search(text):
            continue
        relative = str(path.relative_to(root))
        lines = text.splitlines()
        fallback_class = path.stem

        for index, line in enumerate(lines):
            annotation = ANNOTATION_RE.search(line)
            if annotation:
                method_info = nearest_method(lines, index)
                endpoint = {
                    "source": relative,
                    "class": nearest_class(lines, index, fallback_class),
                    "http_method": annotation.group(1).upper(),
                    "path": annotation.group(2),
                    "method": method_info["name"] if method_info else None,
                    "parameters": [
                        {"kind": kind, "name": name or None}
                        for kind, name in (method_info or {}).get(
                            "parameter_annotations", []
                        )
                    ],
                }
                endpoints.append(endpoint)

            for url_match in URL_RE.finditer(line):
                cleaned = safe_url(url_match.group(0))
                if cleaned and RELEVANT.search(cleaned):
                    urls[cleaned].add(relative)

            for const in CONST_RE.finditer(line):
                name, raw_value = const.groups()
                if not (RELEVANT.search(name) or RELEVANT.search(raw_value)):
                    continue
                constants.append(
                    {
                        "source": relative,
                        "class": nearest_class(lines, index, fallback_class),
                        "name": name,
                        "value": safe_value(name, raw_value),
                    }
                )

            if RELEVANT.search(line):
                class_name = nearest_class(lines, index, fallback_class)
                # Publish only symbol names, never source text.
                symbols[class_name].add(relative)

    # Deduplicate stable structures.
    endpoint_seen: set[str] = set()
    unique_endpoints: list[dict[str, Any]] = []
    for endpoint in endpoints:
        key = json.dumps(endpoint, sort_keys=True)
        if key not in endpoint_seen:
            endpoint_seen.add(key)
            unique_endpoints.append(endpoint)

    constant_seen: set[tuple[str, str, str]] = set()
    unique_constants: list[dict[str, Any]] = []
    for constant in constants:
        key = (constant["source"], constant["name"], constant["value"])
        if key not in constant_seen:
            constant_seen.add(key)
            unique_constants.append(constant)

    unique_endpoints.sort(
        key=lambda item: (
            item["source"],
            item["class"],
            item["http_method"],
            item["path"],
        )
    )
    unique_constants.sort(
        key=lambda item: (item["source"], item["class"], item["name"])
    )

    return {
        "schema_version": 1,
        "captured_at": datetime.now(UTC).isoformat(),
        "input_root_sha256": hashlib.sha256(
            "\n".join(sorted(str(path.relative_to(root)) for path in source_files(root))).encode()
        ).hexdigest(),
        "files_scanned": files_scanned,
        "http_endpoints": unique_endpoints,
        "safe_constants": unique_constants,
        "relevant_urls": [
            {"url": url, "sources": sorted(sources)[:25]}
            for url, sources in sorted(urls.items())
        ],
        "relevant_symbols": [
            {"symbol": symbol, "sources": sorted(sources)[:25]}
            for symbol, sources in sorted(symbols.items())
        ],
        "safety": {
            "full_decompiled_source_published": False,
            "credential_values_redacted": True,
            "token_shaped_values_redacted": True,
            "query_values_removed": True,
            "application_executed": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not args.input.is_dir():
        raise SystemExit("input must be a decompiled source directory")
    result = scan(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return 0 if (
        result["http_endpoints"]
        or result["safe_constants"]
        or result["relevant_urls"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
