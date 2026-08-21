#!/usr/bin/env python3
"""Capture sanitized Gadis/Froiz HTTP contracts without submitting an order."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from http_capture.bundle_scan import endpoint_literals
from http_capture.common import STORES, safe_message, safe_url, shape
from http_capture.dom import collect_dom_inventory
from http_capture.manifest import add_manifest
from http_capture.probe import Probe


class ContractProbe(Probe):
    """Add form discovery, JS route extraction and error redaction."""

    def __init__(self, store: str, mode: str, output: Path) -> None:
        super().__init__(store, mode, output)
        self.bundle_candidates: list[dict[str, object]] = []
        self._scanned_bundles: set[str] = set()

    def record_error(self, phase: str, exc: BaseException) -> None:
        self.errors.append(
            {
                "phase": phase,
                "type": type(exc).__name__,
                "message": safe_message(str(exc)),
            }
        )

    def login(self, page) -> None:
        self.accept_cookies(page)
        super().login(page)

    def on_request(self, request):  # Playwright provides the runtime types.
        before = len(self.events)
        super().on_request(request)
        if len(self.events) == before:
            return
        event = self.events[-1]
        if event.get("kind") != "request":
            return

        # Gadis' public microservices use these context headers without an X-
        # prefix. Their values identify public site/store/catalogue context and
        # are needed to reproduce the HTTP contract; account/session headers
        # remain redacted by the shared sanitizer.
        headers = event.setdefault("headers", {})
        for name in (
            "site-id",
            "store-id",
            "x-site-id",
            "x-store-id",
            "x-customer-wh",
            "accept-language",
        ):
            value = request.headers.get(name)
            if value:
                headers[name] = value[:300]

        if event.get("body") != "<non-json-body>":
            return
        content_type = request.headers.get("content-type", "").casefold()
        if "application/x-www-form-urlencoded" in content_type:
            event["body"] = {
                key: shape(value, key)
                for key, value in parse_qsl(
                    request.post_data or "",
                    keep_blank_values=True,
                )
            }
        elif "multipart/form-data" in content_type:
            event["body"] = "<multipart-form-body>"

    def _scan_bundle(self, response) -> None:
        content_type = response.headers.get("content-type", "").casefold()
        path = urlsplit(response.url).path.casefold()
        if "javascript" not in content_type and not path.endswith((".js", ".mjs")):
            return
        host = (urlsplit(response.url).hostname or "").casefold()
        suffix = "gadisline.com" if self.spec.key == "gadis" else "froiz.com"
        if not (host == suffix or host.endswith("." + suffix)):
            return
        source = safe_url(response.url)
        if source in self._scanned_bundles or len(self._scanned_bundles) >= 80:
            return
        self._scanned_bundles.add(source)
        try:
            candidates = endpoint_literals(response.text(), response.url)
        except Exception as exc:
            self.record_error("bundle_scan", exc)
            return
        if candidates:
            self.bundle_candidates.append(
                {"source": source, "endpoint_candidates": candidates}
            )

    def on_response(self, response):
        self._scan_bundle(response)
        super().on_response(response)

    def run(self) -> int:
        status = super().run()
        payload = json.loads(self.output.read_text(encoding="utf-8"))
        payload["bundle_candidates"] = self.bundle_candidates
        self.output.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        collect_dom_inventory(self.spec.key, self.mode, self.output)
        add_manifest(self.output)
        return status


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Capture a value-free Gadis/Froiz HTTP contract. Final order and "
            "payment requests are blocked before leaving the browser."
        )
    )
    parser.add_argument("--store", required=True, choices=sorted(STORES))
    parser.add_argument(
        "--mode",
        choices=("guest", "authenticated"),
        default="guest",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    return ContractProbe(args.store, args.mode, args.output).run()


if __name__ == "__main__":
    raise SystemExit(main())
