"""Fail closed when a sanitized capture still contains secret-like values."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]{4,})?\b")
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_COOKIE = re.compile(r"(?i)(?:^|[;\s])(?:session|sessionid|csrftoken|xsrf-token)=[^;<\s]+")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_PHONE = re.compile(r"(?<!\d)(?:\+?34[ .-]?)?[6789](?:[ .-]?\d){8}(?!\d)")
_LOCAL_PATH = re.compile(r"(?i)(?:/(?:home|users)/[^/\s]+/|[A-Z]:\\Users\\[^\\\s]+\\)")
_PRIVATE_FIELD = re.compile(
    r"(?i)(?:^|_)(?:customer|user|account|address|cart|checkout|order|payment|"
    r"session|transaction)(?:_?(?:id|uuid|token|secret|path))?(?:$|_)"
)
_PUBLIC_ID_FIELD = re.compile(
    r"(?i)^(?:product|sku|ean|category|site|store|warehouse)(?:_?(?:id|code|uuid))?$"
)
_ALLOWED_PLACEHOLDERS = {
    "<redacted>",
    "<str>",
    "<value>",
    "<id>",
    "<number>",
    "<non-json-body>",
    "<multipart-form-body>",
    "<invalid-url>",
    "<redacted-email>",
    "<redacted-phone>",
    "<redacted-token>",
    "<redacted-id>",
}


class CaptureLeakError(RuntimeError):
    """A supposedly shareable capture still contains a secret-like value."""


def _scan(value: Any, path: str = "root") -> list[str]:
    leaks: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            key_name = str(key).casefold()
            if (
                not _PUBLIC_ID_FIELD.fullmatch(key_name)
                and (key_name == "id" or _PRIVATE_FIELD.search(key_name))
                and child not in _ALLOWED_PLACEHOLDERS
                and child not in (None, False, True, 0, "")
            ):
                leaks.append(f"{child_path}: private field is not redacted")
            leaks.extend(_scan(child, child_path))
        return leaks
    if isinstance(value, list):
        for index, child in enumerate(value):
            leaks.extend(_scan(child, f"{path}[{index}]"))
        return leaks
    if not isinstance(value, str) or value in _ALLOWED_PLACEHOLDERS:
        return leaks

    if _EMAIL.search(value):
        leaks.append(f"{path}: email-like value")
    if _JWT.search(value):
        leaks.append(f"{path}: JWT-like value")
    if _BEARER.search(value):
        leaks.append(f"{path}: bearer token")
    if _COOKIE.search(value):
        leaks.append(f"{path}: cookie-like value")
    if _PHONE.search(value):
        leaks.append(f"{path}: phone-like value")
    if _LOCAL_PATH.search(value):
        leaks.append(f"{path}: local user path")
    if _CARD.search(value):
        digits = re.sub(r"\D", "", value)
        if 13 <= len(digits) <= 19:
            leaks.append(f"{path}: payment-number-like value")
    return leaks


def assert_shareable_capture(path: Path) -> None:
    """Raise and refuse the bundle if any secret-like value survived sanitization."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    leaks = _scan(payload)
    if leaks:
        preview = "; ".join(leaks[:10])
        raise CaptureLeakError(
            f"capture failed the shareability scan ({len(leaks)} finding(s)): {preview}"
        )
