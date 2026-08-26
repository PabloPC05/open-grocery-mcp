#!/usr/bin/env python3
"""Validate a sanitized local HTTP capture before it is used for development.

The validator deliberately checks observable outcomes rather than trusting that
an operator clicked every phase. It exits non-zero when the capture is empty,
missing explicitly required phases, or appears to contain an unredacted
credential/session header.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
SENSITIVE_HEADER = re.compile(
    r"(?i)^(authorization|cookie|set-cookie|proxy-authorization|x-csrf-token|x-xsrf-token)$"
)
SAFE_REDACTIONS = {
    "<redacted>",
    "<value>",
    "<str>",
    "<id>",
    "<number>",
    "",
    None,
}


def _events(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = payload.get("events", [])
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, Mapping)]


def _phase_names(payload: Mapping[str, Any], events: Iterable[Mapping[str, Any]]) -> Counter[str]:
    phases: Counter[str] = Counter()
    for event in events:
        phase = str(event.get("phase", "")).strip()
        if phase:
            phases[phase] += 1
    marks = payload.get("phase_marks", [])
    if isinstance(marks, list):
        for mark in marks:
            if isinstance(mark, Mapping):
                phase = str(mark.get("phase", "")).strip()
                if phase and phase not in phases:
                    phases[phase] += 0
    return phases


def _sensitive_header_errors(events: Iterable[Mapping[str, Any]]) -> list[str]:
    errors: list[str] = []
    for index, event in enumerate(events):
        headers = event.get("headers")
        if not isinstance(headers, Mapping):
            continue
        for key, value in headers.items():
            if SENSITIVE_HEADER.fullmatch(str(key)) and value not in SAFE_REDACTIONS:
                errors.append(
                    f"event {index}: sensitive header {key!r} was not redacted"
                )
    return errors


def _email_errors(value: Any, path: str = "capture") -> list[str]:
    """Flag likely personal emails while ignoring explicit redaction markers."""

    errors: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            errors.extend(_email_errors(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(_email_errors(child, f"{path}[{index}]"))
    elif isinstance(value, str):
        lowered = value.casefold()
        if "<redacted" not in lowered and EMAIL.search(value):
            errors.append(f"{path}: possible unredacted email address")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reject empty, incomplete or visibly unsafe local capture JSON files."
    )
    parser.add_argument("capture", type=Path)
    parser.add_argument("--minimum-events", type=int, default=1)
    parser.add_argument(
        "--require-phase",
        action="append",
        default=[],
        help="phase that must contain at least one event; repeat as needed",
    )
    parser.add_argument(
        "--require-response",
        action="store_true",
        help="require at least one captured response in addition to a request",
    )
    parser.add_argument(
        "--require-restored-cart",
        action="store_true",
        help="require the probe to have reread and verified the original cart state",
    )
    parser.add_argument(
        "--require-cart-write",
        action="store_true",
        help="require a captured non-GET retailer cart endpoint",
    )
    parser.add_argument(
        "--fail-on-reported-errors",
        action="store_true",
        help="reject a capture whose probe reported any action errors",
    )
    args = parser.parse_args()

    failures: list[str] = []
    warnings: list[str] = []

    if not args.capture.exists():
        print(json.dumps({"ok": False, "failures": ["capture file does not exist"]}, indent=2))
        return 2

    try:
        payload = json.loads(args.capture.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(
            json.dumps(
                {"ok": False, "failures": [f"could not parse capture: {exc}"]},
                indent=2,
            )
        )
        return 2

    if not isinstance(payload, Mapping):
        print(json.dumps({"ok": False, "failures": ["capture root is not an object"]}, indent=2))
        return 2

    events = _events(payload)
    kinds = Counter(str(event.get("kind", "")) for event in events)
    phases = _phase_names(payload, events)

    if len(events) < max(0, args.minimum_events):
        failures.append(
            f"only {len(events)} events were captured; minimum is {args.minimum_events}"
        )
    if kinds.get("request", 0) == 0:
        failures.append("no HTTP request was captured")
    if args.require_response and kinds.get("response", 0) == 0:
        failures.append("no HTTP response was captured")
    elif kinds.get("response", 0) == 0:
        warnings.append("no HTTP response was captured")

    for required in args.require_phase:
        if phases.get(required, 0) <= 0:
            failures.append(f"required phase {required!r} has no captured events")

    if args.require_restored_cart:
        safety = payload.get("safety")
        if not isinstance(safety, Mapping) or safety.get(
            "original_cart_restored"
        ) is not True:
            failures.append("original cart restoration was not verified")

    if args.require_cart_write:
        manifest = payload.get("retailer_endpoint_manifest")
        has_cart_write = isinstance(manifest, list) and any(
            isinstance(row, Mapping)
            and str(row.get("method") or "").upper()
            in {"POST", "PUT", "PATCH", "DELETE"}
            and row.get("operation_hint") == "cart"
            for row in manifest
        )
        if not has_cart_write:
            failures.append("no retailer cart write endpoint was captured")

    reported_errors = payload.get("errors", [])
    if args.fail_on_reported_errors and (
        not isinstance(reported_errors, list) or reported_errors
    ):
        failures.append("the probe reported action errors")

    failures.extend(_sensitive_header_errors(events))
    failures.extend(_email_errors(payload))

    summary = {
        "ok": not failures,
        "capture": str(args.capture),
        "store": payload.get("store"),
        "mode": payload.get("mode"),
        "events": len(events),
        "requests": kinds.get("request", 0),
        "responses": kinds.get("response", 0),
        "blocked": len(payload.get("blocked", []))
        if isinstance(payload.get("blocked", []), list)
        else 0,
        "phases": dict(sorted(phases.items())),
        "reported_errors": reported_errors,
        "warnings": warnings,
        "failures": failures,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
