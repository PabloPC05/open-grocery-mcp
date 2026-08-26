#!/usr/bin/env python3
"""Value-free, local verification of the Eroski delivery page.

The default mode only opens the saved browser session and observes rendered
delivery/address/slot controls. No form is submitted and the storage state is
never written back. ``--allow-delivery-read-post`` may allow only the exact
observed Tapestry address-zone POST bodies needed by some sessions to render
store/slot data. ``--allow-slot-summary-post`` can reuse an already-selected
slot to reach the GET summary; every other write and all order/payment routes
remain blocked before the request leaves Chromium.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote_plus, urlsplit

BASE_HOST = "supermercado.eroski.es"
DELIVERY_PATH = "/es/bookingdelivery/shopdelivery/"
ORDER_PAYMENT_MARKERS = (
    "/order",
    "/orders",
    "order/create",
    "orders/create",
    "submitorder",
    "placeorder",
    "purchase",
    "payment",
    "redsys",
    "3ds",
    "securepay",
)
FINAL_SLOT_MARKERS = (
    "slotform",
    "pickupaddressselector.slotform",
)
SLOT_SUMMARY_PATH = (
    "/es/bookingdelivery.selectdelivery.addressselector."
    "pickupaddressselector.slotform"
)
SLOT_SUMMARY_BODY_KEYS = {
    "checkoutBasketType_0",
    "radiogroup",
    "selectedAddressRef",
    "selectedSlotRef_0",
    "selectedSlotTime_0",
    "t:formdata",
    "t:zoneid",
}
DELIVERY_READ_POST_CONTRACTS = {
    "addresslistselector:update_map": {
        "confirm", "mobile", "ref", "selected"
    },
    "addresslistselector:address_select": {
        "confirm", "mobile", "ref", "selected"
    },
    "homeaddressselector.selectdeliveryaddress:change": {
        "t:selectvalue", "t:zoneid"
    },
}
ORDER_TEXT = re.compile(
    r"(?i)(realizar pedido|confirmar pedido|comprar ahora|pagar|finalizar pedido|"
    r"place order|3d secure|redsys)"
)
_DANGEROUS_URL_TEXT = re.compile(
    r"(?i)(?:submitorder|placeorder|purchase|payment|redsys|3d\s*secure|"
    r"(?:^|[/._:-])(?:orders?|confirm)(?:[/._:-]|$))"
)
_DANGEROUS_BODY_TEXT = re.compile(
    r"(?i)(?:submitorder|placeorder|confirm(?:order|pedido|purchase|payment)|"
    r"realizar\s*pedido|finalizar\s*pedido|purchase|payment|redsys|3d\s*secure|"
    r"(?:^|[/._:-])orders?(?:[/._:-]|$))"
)
_FINAL_SLOT_TEXT = re.compile(r"(?i)slotform")


def enabled(name: str) -> bool:
    return os.getenv(name, "").casefold() in {"1", "true", "yes", "on"}


def state_path() -> Path:
    configured = os.getenv("OPEN_GROCERY_EROSKI_STATE_PATH")
    if configured:
        return Path(configured).expanduser()
    root = Path(os.getenv("OPEN_GROCERY_STATE_DIR", "~/.open-grocery-mcp")).expanduser()
    return root / "eroski" / "storage_state.json"


def _request_text(value: Any) -> str:
    """Return URL/body text for marker matching without logging its value."""

    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return unquote_plus(str(value)).casefold()


def classify_request(
    method: str,
    url: str,
    *,
    allow_delivery_read_post: bool,
    allow_slot_summary_post: bool = False,
    body: Any = None,
) -> str:
    """Return a non-sensitive route decision used by the Chromium interceptor."""

    verb = str(method or "GET").upper()
    parsed = urlsplit(str(url))
    hostname = (parsed.hostname or "").casefold()
    path_text = _request_text(parsed.path)
    # Tracking vendors frequently put ``purchase`` in analytics query/body
    # data even on a read-only page. Treat external traffic as order/payment
    # only for a payment host/path; all other external writes are still
    # blocked, but are not misreported as a retailer order attempt.
    if hostname != BASE_HOST:
        external_payment = any(
            marker in hostname or marker in path_text
            for marker in ("redsys", "securepay", "3ds", "payment")
        )
        if external_payment:
            return "block_order_or_payment"
        if verb in {"GET", "HEAD", "OPTIONS"}:
            return "allow_read"
        return "block_other_non_get"
    # Static storefront assets can legitimately contain words such as
    # ``quick-purchase`` in their filenames. A safe-method request below the
    # immutable asset/module prefixes cannot submit an order.
    if verb in {"GET", "HEAD", "OPTIONS"} and parsed.path.casefold().startswith(
        ("/assets/", "/modules/")
    ):
        return "allow_read"
    body_text = _request_text(body)
    # Check URL and body before the HTTP verb. A dangerous GET must not pass
    # merely because it is technically a read, and a harmless-looking route
    # can still carry an order/payment operation in a POST body.
    query_text = _request_text(parsed.query)
    if (
        _DANGEROUS_URL_TEXT.search(path_text)
        or _DANGEROUS_BODY_TEXT.search(query_text)
        or _DANGEROUS_BODY_TEXT.search(body_text)
    ):
        return "block_order_or_payment"
    if (
        allow_slot_summary_post
        and verb == "POST"
        and parsed.path.casefold() == SLOT_SUMMARY_PATH
    ):
        pairs = parse_qsl(str(body or ""), keep_blank_values=True)
        keys = {key for key, _ in pairs}
        values = " ".join(value for _, value in pairs)
        if (
            keys
            and keys.issubset(SLOT_SUMMARY_BODY_KEYS)
            and not _DANGEROUS_BODY_TEXT.search(_request_text(values))
        ):
            return "allow_slot_summary_post"
    if (
        _FINAL_SLOT_TEXT.search(path_text)
        or _FINAL_SLOT_TEXT.search(query_text)
        or _FINAL_SLOT_TEXT.search(body_text)
    ):
        return "block_final_slot_form"
    path = parsed.path.casefold()
    if verb in {"GET", "HEAD", "OPTIONS"}:
        return "allow_read"
    if allow_delivery_read_post and hostname == BASE_HOST:
        keys = {
            key for key, _ in parse_qsl(str(body or ""), keep_blank_values=True)
        }
        for marker, expected_keys in DELIVERY_READ_POST_CONTRACTS.items():
            if marker in path and keys == expected_keys:
                return "allow_delivery_read_post"
    return "block_other_non_get"


def _count_visible(page: Any, selector: str) -> int:
    try:
        locator = page.locator(selector)
        return sum(1 for index in range(min(locator.count(), 100)) if locator.nth(index).is_visible())
    except Exception:
        return 0


def _count_present(page: Any, selector: str) -> int:
    try:
        return min(page.locator(selector).count(), 100)
    except Exception:
        return 0


def _snapshot_page(page: Any) -> dict[str, Any]:
    try:
        text = page.locator("body").inner_text(timeout=5000)
    except Exception:
        text = ""
    try:
        password_fields = _count_visible(page, "input[type=password]")
    except Exception:
        password_fields = 0
    delivery_modes = _count_visible(
        page,
        "input[type=radio][name*='delivery' i], input[type=radio][name*='entrega' i]",
    )
    address_controls = _count_present(
        page,
        "input[type=radio][name*='address' i]:not([name*='pickup' i]):not([name*='store' i]), "
        "input[type=radio][name*='addressref' i]:not([name*='pickup' i]), "
        "input[type=radio][name*='direccion' i]:not([name*='recogida' i]):not([name*='tienda' i]), "
        "select[name*='address' i], select[name*='direccion' i]",
    )
    slot_controls = _count_present(
        page,
        "input[type=radio][name*='slot' i], input[type=radio][name*='franja' i], "
        "input[type=radio][name*='hora' i], select[name*='slot' i], "
        "select[name*='franja' i], td.delivery-table, .delivery-slot.available, "
        "[data-slot-id]",
    )
    try:
        selected_slot_refs = page.locator(
            'input[name*="selectedSlotRef" i]'
        ).evaluate_all("xs => xs.filter(x => Boolean(x.value)).length")
        slot_controls += min(int(selected_slot_refs), 100)
    except Exception:
        pass
    try:
        final_slot_form = bool(
            page.locator("form").evaluate_all(
                "forms => forms.some(form => "
                "(form.getAttribute('action') || '').toLowerCase().includes('slotform'))"
            )
        )
    except Exception:
        final_slot_form = False
    path = urlsplit(str(page.url)).path or "/"
    body_lower = text.casefold()
    authenticated = "/login" not in path.casefold() and password_fields == 0
    return {
        "page_path": path,
        "authenticated": authenticated,
        "delivery_mode_controls": delivery_modes,
        "address_controls": address_controls,
        "slot_controls": slot_controls,
        "addresses_read": address_controls > 0,
        "calendar_read": slot_controls > 0,
        "slot_listing_observed": slot_controls > 0,
        "final_slot_form_present": final_slot_form,
        "order_payment_controls_present": bool(ORDER_TEXT.search(body_lower)),
    }


def _session_storage(state: Path) -> dict[str, Any]:
    sidecar = state.with_name("session_storage.json")
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _probe(
    state: Path,
    *,
    allow_delivery_read_post: bool,
    allow_slot_summary_post: bool,
) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return {"ok": False, "reason": f"PlaywrightUnavailable:{type(exc).__name__}"}

    report: dict[str, Any] = {
        "ok": False,
        "store": "eroski",
        "backend": "browser_delivery_observer",
        "retailer_write_performed": False,
        "order_or_payment_attempted": False,
        "secrets_exposed": False,
        "storage_state_written": False,
        "requests": 0,
        "responses": 0,
        "non_get_requests": 0,
        "allowed_delivery_read_posts": 0,
        "allowed_slot_summary_posts": 0,
        "blocked_order_payment_requests": 0,
        "blocked_final_slot_forms": 0,
        "blocked_other_non_get": 0,
        "steps": {
            "authenticated_page_read": False,
            "addresses_read": False,
            "calendar_read": False,
            "final_slot_form_not_submitted": True,
            "delivery_summary_reached": False,
            "preselected_slot_preserved": None,
        },
    }
    blocked: list[str] = []
    allowed_non_get = 0
    allowed_slot_posts = 0

    def route_handler(route: Any, request: Any) -> None:
        nonlocal allowed_non_get, allowed_slot_posts
        try:
            request_body = request.post_data
        except Exception:
            request_body = None
        decision = classify_request(
            request.method,
            request.url,
            allow_delivery_read_post=allow_delivery_read_post,
            allow_slot_summary_post=allow_slot_summary_post,
            body=request_body,
        )
        if decision == "allow_delivery_read_post":
            allowed_non_get += 1
            route.continue_()
            return
        if decision == "allow_slot_summary_post":
            allowed_slot_posts += 1
            route.continue_()
            return
        if decision.startswith("block_"):
            blocked.append(decision)
            route.abort("blockedbyclient")
            return
        route.continue_()

    def on_request(request: Any) -> None:
        report["requests"] += 1
        if str(request.method).upper() not in {"GET", "HEAD", "OPTIONS"}:
            report["non_get_requests"] += 1

    def on_response(_: Any) -> None:
        report["responses"] += 1

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context_args: dict[str, Any] = {
                    "storage_state": str(state),
                    "locale": "es-ES",
                    "viewport": {"width": 1440, "height": 1000},
                }
                stored = _session_storage(state)
                context = browser.new_context(**context_args)
                if stored:
                    context.add_init_script(
                        "(() => { const data = "
                        + json.dumps(stored)
                        + "; for (const [origin, entries] of Object.entries(data)) {"
                        " if (location.origin !== origin) continue;"
                        " for (const [key, value] of Object.entries(entries || {})) "
                        "{ if (!sessionStorage.getItem(key)) sessionStorage.setItem(key, value); }"
                        " } })()"
                    )
                context.route("**/*", route_handler)
                context.on("request", on_request)
                context.on("response", on_response)
                page = context.new_page()
                page.set_default_timeout(15000)
                try:
                    page.goto(
                        f"https://{BASE_HOST}{DELIVERY_PATH}",
                        wait_until="domcontentloaded",
                        timeout=45000,
                    )
                except Exception as exc:
                    report["navigation_error"] = type(exc).__name__
                page.wait_for_timeout(3000)
                snapshot = _snapshot_page(page)
                report.update(snapshot)
                report["steps"]["authenticated_page_read"] = bool(
                    snapshot["authenticated"]
                )
                report["steps"]["addresses_read"] = bool(snapshot["addresses_read"])
                report["steps"]["calendar_read"] = bool(snapshot["calendar_read"])
                if allow_delivery_read_post:
                    # Tapestry loads the selected saved address zone through
                    # an event POST. Dispatching change for the already
                    # selected option performs no form/order submission.
                    triggered = page.evaluate(
                        """() => {
                          const select = document.querySelector(
                            'select[name="selectDeliveryAddress"]'
                          );
                          if (!select || !select.value) return false;
                          select.dispatchEvent(new Event('change', {bubbles: true}));
                          return true;
                        }"""
                    )
                    if triggered:
                        page.wait_for_timeout(2000)
                        refreshed = _snapshot_page(page)
                        report.update(refreshed)
                        report["steps"]["addresses_read"] = bool(
                            refreshed["addresses_read"]
                        )
                        report["steps"]["calendar_read"] = bool(
                            refreshed["calendar_read"]
                        )
                if allow_slot_summary_post:
                    form = page.locator(
                        f'form[action="{SLOT_SUMMARY_PATH}"]'
                    ).first
                    checked = form.locator('input[type="radio"]:checked')
                    submit = form.locator('button[type="submit"]').first
                    if form.count() == 1 and checked.count() == 1 and submit.is_visible():
                        before_ref = str(checked.get_attribute("value") or "")
                        before_hash = hashlib.sha256(before_ref.encode()).hexdigest()
                        submit.click()
                        report["steps"]["final_slot_form_not_submitted"] = False
                        page.wait_for_timeout(3000)
                        report["steps"]["delivery_summary_reached"] = (
                            urlsplit(str(page.url)).path
                            == "/es/bookingdeliverysummary/"
                        )
                        # Re-open the selection page by GET and require the
                        # same already-selected opaque radio value. No value is
                        # included in the report.
                        page.goto(
                            f"https://{BASE_HOST}{DELIVERY_PATH}",
                            wait_until="domcontentloaded",
                            timeout=45000,
                        )
                        page.wait_for_timeout(1500)
                        after = page.locator(
                            f'form[action="{SLOT_SUMMARY_PATH}"] '
                            'input[type="radio"]:checked'
                        )
                        after_ref = (
                            str(after.first.get_attribute("value") or "")
                            if after.count() == 1
                            else ""
                        )
                        report["steps"]["preselected_slot_preserved"] = bool(
                            before_ref
                            and hashlib.sha256(after_ref.encode()).hexdigest()
                            == before_hash
                        )
            finally:
                browser.close()
    except Exception as exc:
        report["error_type"] = type(exc).__name__

    report["allowed_delivery_read_posts"] = allowed_non_get
    report["allowed_slot_summary_posts"] = allowed_slot_posts
    report["retailer_write_performed"] = (
        allowed_non_get > 0 or allowed_slot_posts > 0
    )
    report["blocked_order_payment_requests"] = blocked.count("block_order_or_payment")
    report["blocked_final_slot_forms"] = blocked.count("block_final_slot_form")
    report["blocked_other_non_get"] = blocked.count("block_other_non_get")
    report["order_or_payment_attempted"] = False
    slot_summary_ok = (
        not allow_slot_summary_post
        or (
            report["steps"]["delivery_summary_reached"]
            and report["steps"]["preselected_slot_preserved"]
            and allowed_slot_posts == 1
        )
    )
    report["ok"] = bool(
        report["steps"]["authenticated_page_read"]
        and report["steps"]["addresses_read"]
        and report["steps"]["calendar_read"]
        and (
            report["steps"]["final_slot_form_not_submitted"]
            or allow_slot_summary_post
        )
        and report.get("error_type") is None
        and report["requests"] > 0
        and report["responses"] > 0
        and report["blocked_order_payment_requests"] == 0
        and slot_summary_ok
    )
    return report


def verify(
    *,
    allow_delivery_read_post: bool = False,
    allow_slot_summary_post: bool = False,
) -> tuple[int, dict[str, Any]]:
    report: dict[str, Any] = {
        "ok": False,
        "store": "eroski",
        "backend": "browser_delivery_observer",
        "retailer_write_performed": False,
        "order_or_payment_attempted": False,
        "secrets_exposed": False,
        "storage_state_written": False,
        "read_only": not (
            allow_delivery_read_post or allow_slot_summary_post
        ),
        "requests": 0,
        "responses": 0,
        "steps": {
            "authenticated_page_read": False,
            "addresses_read": False,
            "calendar_read": False,
            "final_slot_form_not_submitted": True,
            "delivery_summary_reached": False,
            "preselected_slot_preserved": None,
        },
    }
    if any(
        enabled(name)
        for name in (
            "OPEN_GROCERY_ENABLE_ORDER_SUBMISSION",
            "OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION",
        )
    ):
        return 2, {**report, "reason": "order-submission opt-ins must be disabled"}
    if (
        allow_delivery_read_post or allow_slot_summary_post
    ) and not enabled("OPEN_GROCERY_ENABLE_RETAILER_WRITES"):
        return 2, {
            **report,
            "reason": "OPEN_GROCERY_ENABLE_RETAILER_WRITES=1 is required for delivery POST reads",
        }
    path = state_path()
    if not path.is_file():
        return 1, {**report, "reason": "saved Eroski storage state is missing"}
    result = _probe(
        path,
        allow_delivery_read_post=allow_delivery_read_post,
        allow_slot_summary_post=allow_slot_summary_post,
    )
    return (0 if result.get("ok") else 1), {**report, **result}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Eroski delivery verification. Read-only by default; the exact "
            "preselected-slot summary POST needs an explicit opt-in and all "
            "order/payment routes are always blocked."
        )
    )
    parser.add_argument(
        "--allow-delivery-read-post",
        action="store_true",
        help=(
            "allow only observed address-map POSTs needed to render delivery "
            "data; never allows the final slot form"
        ),
    )
    parser.add_argument(
        "--allow-slot-summary-post",
        action="store_true",
        help=(
            "reuse the already-selected pickup slot to reach the GET delivery "
            "summary; all other writes and every order/payment route stay blocked"
        ),
    )
    args = parser.parse_args()
    code, payload = verify(
        allow_delivery_read_post=args.allow_delivery_read_post,
        allow_slot_summary_post=args.allow_slot_summary_post,
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
