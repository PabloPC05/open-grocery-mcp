#!/usr/bin/env python3
"""Request or complete a retailer password reset without logging secrets."""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from playwright.sync_api import Request, Response, Route, sync_playwright

from capture_http_contract import ContractProbe
from http_capture.common import (
    DANGEROUS,
    RELEVANT,
    STORES,
    click_words,
    first_visible,
    safe_headers,
    safe_message,
    safe_url,
)


def now() -> str:
    return datetime.now(UTC).isoformat()


class ResetBrowser:
    def __init__(self, store: str, payload: dict[str, Any]) -> None:
        self.store = store
        self.spec = STORES[store]
        self.payload = payload
        self.phase = "init"
        self.events: list[dict[str, Any]] = []
        self.blocked: list[dict[str, Any]] = []
        self.errors: list[dict[str, str]] = []

    def record_error(self, phase: str, exc: BaseException) -> None:
        self.errors.append(
            {
                "phase": phase,
                "type": type(exc).__name__,
                "message": safe_message(str(exc)),
            }
        )

    def relevant(self, request: Request) -> bool:
        host = (urlsplit(request.url).hostname or "").casefold()
        if any(
            value in host
            for value in (
                "google-analytics",
                "analytics.google",
                "googletagmanager",
                "doubleclick",
                "facebook",
                "hotjar",
                "sentry",
            )
        ):
            return False
        return request.method != "GET" or request.resource_type in {
            "xhr",
            "fetch",
            "document",
        } or bool(RELEVANT.search(request.url))

    def route(self, route: Route, request: Request) -> None:
        body = request.post_data or ""
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and (
            DANGEROUS.search(request.url) or DANGEROUS.search(body)
        ):
            self.blocked.append(
                {
                    "phase": self.phase,
                    "method": request.method,
                    "url": safe_url(request.url),
                    "reason": "potential order/payment request",
                }
            )
            route.abort("blockedbyclient")
            return
        route.continue_()

    def on_request(self, request: Request) -> None:
        if not self.relevant(request):
            return
        self.events.append(
            {
                "kind": "request",
                "phase": self.phase,
                "method": request.method,
                "url": safe_url(request.url),
                "resource_type": request.resource_type,
                "headers": safe_headers(request.headers),
                # Password-reset forms intentionally omit request bodies. The
                # endpoint and header contract are sufficient for discovery.
                "body": "<redacted-reset-form>" if request.post_data else None,
            }
        )

    def on_response(self, response: Response) -> None:
        if not self.relevant(response.request):
            return
        self.events.append(
            {
                "kind": "response",
                "phase": self.phase,
                "method": response.request.method,
                "url": safe_url(response.url),
                "status": response.status,
                "headers": safe_headers(response.headers),
                "body": None,
            }
        )

    @staticmethod
    def accept_cookies(page: Any) -> None:
        click_words(
            page,
            ("aceptar todas", "aceptar cookies", "permitir todas", "accept all"),
            ("button",),
        )

    def _open_login(self, page: Any) -> None:
        page.goto(self.spec.base_url, wait_until="domcontentloaded")
        self.accept_cookies(page)
        if click_words(page, self.spec.login_words):
            page.wait_for_timeout(800)
            return
        target = first_visible(
            page.locator(
                "a[href*='login' i],a[href*='account' i],a[href*='cuenta' i],"
                "button[data-testid*='login' i]"
            )
        )
        if target is None:
            raise RuntimeError("login entry point not found")
        target.click()
        page.wait_for_timeout(800)

    def request_reset(self, page: Any) -> dict[str, Any]:
        email = str(self.payload.get("email") or "").strip()
        if not email or "@" not in email:
            raise RuntimeError("encrypted payload has no valid email")
        self.phase = "open_login"
        self._open_login(page)
        self.phase = "forgot_password"
        if not click_words(
            page,
            (
                "olvidé mi contraseña",
                "he olvidado mi contraseña",
                "recuperar contraseña",
                "restablecer contraseña",
                "forgot password",
            ),
        ):
            target = first_visible(
                page.locator(
                    "a[href*='forgot' i],a[href*='recover' i],"
                    "a[href*='password' i],button[data-testid*='forgot' i]"
                )
            )
            if target is None:
                raise RuntimeError("password-reset entry point not found")
            target.click()
        page.wait_for_timeout(700)
        field = first_visible(
            page.locator(
                "input[type='email'],input[name*='email' i],"
                "input[autocomplete='username'],input[name*='user' i]"
            )
        )
        if field is None:
            raise RuntimeError("password-reset email field not found")
        field.fill(email)
        self.phase = "submit_reset_request"
        submit = first_visible(
            page.locator("button[type='submit'],input[type='submit']")
        )
        if submit:
            submit.click()
        else:
            field.press("Enter")
        page.wait_for_timeout(1800)
        text = safe_message(page.locator("body").inner_text()[:2000])
        acknowledged = bool(
            re.search(
                r"(?i)(correo|email|enviado|revisa|recibirás|recibiras|instrucciones)",
                text,
            )
        )
        return {
            "operation": "request_reset",
            "request_submitted": True,
            "page_acknowledged": acknowledged,
            "page_url": safe_url(page.url),
        }

    def complete_reset(self, page: Any) -> dict[str, Any]:
        reset_url = str(self.payload.get("reset_url") or "").strip()
        password = str(self.payload.get("new_password") or "")
        email = str(self.payload.get("email") or "").strip()
        expected_host = (urlsplit(self.spec.base_url).hostname or "").casefold()
        actual_host = (urlsplit(reset_url).hostname or "").casefold()
        if not reset_url or not actual_host or not (
            actual_host == expected_host or actual_host.endswith("." + expected_host)
        ):
            raise RuntimeError("reset URL is outside the retailer domain")
        if len(password) < 12:
            raise RuntimeError("new password must contain at least 12 characters")
        if not email or "@" not in email:
            raise RuntimeError("encrypted payload has no valid email")

        password_env = self.spec.password_env
        username_env = self.spec.username_env
        os.environ[password_env] = password
        os.environ[username_env] = email

        self.phase = "open_reset_link"
        page.goto(reset_url, wait_until="domcontentloaded")
        self.accept_cookies(page)
        fields = page.locator(
            "input[type='password'],input[autocomplete='new-password']"
        )
        visible = []
        for index in range(min(fields.count(), 5)):
            item = fields.nth(index)
            if item.is_visible():
                visible.append(item)
        if not visible:
            raise RuntimeError("new-password fields not found")
        for field in visible:
            field.fill(password)
        self.phase = "submit_new_password"
        submit = first_visible(
            page.locator("button[type='submit'],input[type='submit']")
        )
        if submit:
            submit.click()
        else:
            visible[-1].press("Enter")
        page.wait_for_timeout(1800)
        page_text = page.locator("body").inner_text()[:2500]
        if re.search(
            r"(?i)(token.*caduc|enlace.*caduc|no válido|no valido|invalid token)",
            page_text,
        ):
            raise RuntimeError("retailer rejected the password-reset link")
        return {
            "operation": "complete_reset",
            "password_reset_submitted": True,
            "page_url": safe_url(page.url),
        }

    def run(self, operation: str) -> dict[str, Any]:
        result: dict[str, Any] = {
            "store": self.store,
            "operation": operation,
            "started_at": now(),
        }
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    headless=False,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                try:
                    context = browser.new_context(
                        locale="es-ES",
                        viewport={"width": 1440, "height": 1000},
                        user_agent=(
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/131.0.0.0 Safari/537.36"
                        ),
                    )
                    context.route("**/*", self.route)
                    page = context.new_page()
                    page.set_default_timeout(20_000)
                    page.on("request", self.on_request)
                    page.on("response", self.on_response)
                    if operation == "request_reset":
                        result.update(self.request_reset(page))
                    elif operation == "complete_reset":
                        result.update(self.complete_reset(page))
                    else:
                        raise RuntimeError(f"unsupported operation {operation!r}")
                finally:
                    browser.close()
        except Exception as exc:
            self.record_error(self.phase, exc)
            result["completed"] = False
        else:
            result["completed"] = True
        result.update(
            {
                "finished_at": now(),
                "events": self.events,
                "blocked": self.blocked,
                "errors": self.errors,
                "safety": {
                    "credentials_logged": False,
                    "final_order_clicked": False,
                    "payment_opened": False,
                },
            }
        )
        return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", required=True, choices=sorted(STORES))
    parser.add_argument(
        "--operation",
        required=True,
        choices=("request_reset", "complete_reset"),
    )
    parser.add_argument("--payload", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--capture-output", type=Path)
    args = parser.parse_args()

    payload = json.loads(args.payload.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("payload must be a JSON object")

    browser = ResetBrowser(args.store, payload)
    result = browser.run(args.operation)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    if args.operation == "complete_reset" and result.get("completed"):
        capture_output = args.capture_output or args.output.with_name(
            f"{args.store}-authenticated.json"
        )
        capture_status = ContractProbe(
            args.store,
            "authenticated",
            capture_output,
        ).run()
        result["authenticated_capture_exit_code"] = capture_status
        result["authenticated_capture_path"] = str(capture_output)
        args.output.write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        if capture_status != 0:
            return capture_status

    return 0 if result.get("completed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
