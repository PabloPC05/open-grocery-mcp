#!/usr/bin/env python3
"""Extract public endpoint-shaped strings from an Android APK/XAPK."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit

URL_RE = re.compile(
    rb"https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{4,500}",
    re.I,
)
PATH_RE = re.compile(
    rb"/[A-Za-z0-9._~!$&'()*+,;=:@%{}\[\]-]{1,80}"
    rb"(?:/[A-Za-z0-9._~!$&'()*+,;=:@%{}\[\]-]{1,80}){1,10}",
)
KEYWORDS = re.compile(
    r"(?i)(auth|login|signin|sign-in|password|passwd|forgot|recover|reset|"
    r"session|token|refresh|register|signup|sign-up|profile|customer|user|"
    r"cart|basket|cesta|carrito|checkout|address|delivery|slot|order|pedido|"
    r"postal|store|gadis|gadisa|gadisline)"
)
NOISE_HOST = re.compile(
    r"(?i)(google|facebook|doubleclick|firebase|crashlytics|appsflyer|"
    r"onetrust|cookielaw|sentry|youtube|wikipedia|w3\.org|schemas\.android)"
)
TOKENISH = re.compile(r"^[A-Za-z0-9._=-]{80,}$")


def printable(value: bytes) -> str | None:
    try:
        text = value.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    text = text.strip("\x00\t\r\n '\"),.;")
    if not text or TOKENISH.fullmatch(text):
        return None
    return text


def blobs(path: Path) -> Iterable[tuple[str, bytes]]:
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if info.is_dir() or info.file_size > 80_000_000:
                    continue
                suffix = Path(info.filename).suffix.casefold()
                if suffix not in {
                    ".dex",
                    ".xml",
                    ".json",
                    ".txt",
                    ".js",
                    ".html",
                    ".properties",
                    ".so",
                    ".apk",
                }:
                    continue
                try:
                    data = archive.read(info)
                except (KeyError, RuntimeError, zipfile.BadZipFile):
                    continue
                if suffix == ".apk" and zipfile.is_zipfile(Path(info.filename)):
                    # Nested APKs in XAPK bundles are handled by the caller after
                    # extraction; retain no opaque binary as a public diagnostic.
                    continue
                yield info.filename, data
    else:
        yield path.name, path.read_bytes()


def clean_url(value: str) -> str | None:
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return None
    host = parts.hostname.casefold()
    if NOISE_HOST.search(host):
        return None
    path = parts.path or "/"
    # Query values in a compiled public app can include static API keys. Keep
    # only parameter names and never publish values or URL fragments.
    keys = sorted(
        {
            item.split("=", 1)[0]
            for item in parts.query.split("&")
            if item and item.split("=", 1)[0]
        }
    )
    query = "&".join(f"{key}=<value>" for key in keys)
    return f"{parts.scheme}://{parts.netloc}{path}" + (f"?{query}" if query else "")


def scan(path: Path) -> dict[str, object]:
    urls: dict[str, set[str]] = {}
    paths: dict[str, set[str]] = {}
    file_types = Counter()

    for name, data in blobs(path):
        file_types[Path(name).suffix.casefold() or "<none>"] += 1
        for match in URL_RE.findall(data):
            text = printable(match)
            if not text:
                continue
            cleaned = clean_url(text)
            if not cleaned or not KEYWORDS.search(cleaned):
                continue
            urls.setdefault(cleaned, set()).add(name)
        for match in PATH_RE.findall(data):
            text = printable(match)
            if not text or not KEYWORDS.search(text):
                continue
            if text.startswith("//") or len(text) > 400:
                continue
            paths.setdefault(text, set()).add(name)

    return {
        "schema_version": 1,
        "captured_at": datetime.now(UTC).isoformat(),
        "input": {
            "filename": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size": path.stat().st_size,
        },
        "file_types_scanned": dict(sorted(file_types.items())),
        "urls": [
            {"url": key, "source_files": sorted(values)[:20]}
            for key, values in sorted(urls.items())
        ],
        "path_candidates": [
            {"path": key, "source_files": sorted(values)[:20]}
            for key, values in sorted(paths.items())
        ],
        "safety": {
            "user_data_present": False,
            "query_values_removed": True,
            "compiled_secret_values_not_published": True,
            "state_changes_performed": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = scan(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return 0 if result["urls"] or result["path_candidates"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
