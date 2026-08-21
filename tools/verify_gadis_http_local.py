#!/usr/bin/env python3
"""Read-only live verification of the Gadis provider used by the MCP.

This command uses the owner's existing local browser session, but never prints
cookies, tokens, product names, addresses or other account values. It performs
no retailer write and exits non-zero when the integrated provider falls back to
Playwright instead of using the captured authenticated HTTP cart contract.
"""

from __future__ import annotations

import argparse
import json
from typing import Any, Mapping

from open_grocery_mcp.providers.gadis_full import GadisFullProvider


def _safe_status(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "authenticated": bool(value.get("authenticated")),
        "authenticated_session": bool(value.get("authenticated_session")),
        "validated_live": bool(value.get("validated_live")),
        "http_session_checked": bool(value.get("http_session_checked")),
        "bearer_token_available": bool(value.get("bearer_token_available")),
        "account_backend": value.get("account_backend"),
        "cart_backend": value.get("cart_backend"),
        "checkout_backend": value.get("checkout_backend"),
    }


def _safe_cart(value: Mapping[str, Any]) -> dict[str, Any]:
    lines = value.get("lines", [])
    line_count = len(lines) if isinstance(lines, list) else 0
    return {
        "version_present": bool(value.get("version")),
        "products_count": int(value.get("products_count") or line_count),
        "line_count": line_count,
        "total_text": str(value.get("total_text") or "0.00"),
        "currency": str(value.get("currency") or "EUR"),
        "cart_backend": value.get("cart_backend"),
        "browser_driven": bool(value.get("browser_driven")),
    }


def verify(*, allow_browser_fallback: bool = False) -> tuple[int, dict[str, Any]]:
    provider = GadisFullProvider()
    try:
        status = provider.account_status()
        public_status = _safe_status(status)
        if not public_status["authenticated"]:
            return 1, {
                "ok": False,
                "reason": "the saved Gadis session is not authenticated",
                "account": public_status,
                "retailer_write_performed": False,
                "order_or_payment_attempted": False,
            }

        cart = provider.real_cart()
        public_cart = _safe_cart(cart)
        backend = str(public_cart.get("cart_backend") or "")
        if backend != "gadis_http" and not allow_browser_fallback:
            return 1, {
                "ok": False,
                "reason": (
                    "the integrated provider did not use the authenticated Gadis "
                    "HTTP cart; inspect http_fallback_reason locally"
                ),
                "account": public_status,
                "cart": public_cart,
                "retailer_write_performed": False,
                "order_or_payment_attempted": False,
            }

        return 0, {
            "ok": True,
            "account": public_status,
            "cart": public_cart,
            "retailer_write_performed": False,
            "order_or_payment_attempted": False,
        }
    except Exception as exc:
        return 1, {
            "ok": False,
            "reason": f"{type(exc).__name__}: {exc}",
            "retailer_write_performed": False,
            "order_or_payment_attempted": False,
        }
    finally:
        provider.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the live Gadis session and integrated HTTP cart without "
            "modifying the retailer account."
        )
    )
    parser.add_argument(
        "--allow-browser-fallback",
        action="store_true",
        help="do not fail when the provider falls back to Playwright",
    )
    args = parser.parse_args()
    code, payload = verify(allow_browser_fallback=args.allow_browser_fallback)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
