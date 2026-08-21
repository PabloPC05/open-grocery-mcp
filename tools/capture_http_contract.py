#!/usr/bin/env python3
"""Capture sanitized Gadis/Froiz HTTP contracts without submitting an order."""
from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import parse_qsl

from http_capture.common import STORES, shape
from http_capture.manifest import add_manifest
from http_capture.probe import Probe


class ContractProbe(Probe):
    """Add safe form-field discovery to the in-memory capture."""

    def on_request(self, request):  # type annotations come from Playwright at runtime
        before = len(self.events)
        super().on_request(request)
        if len(self.events) == before:
            return
        event = self.events[-1]
        if event.get("kind") != "request" or event.get("body") != "<non-json-body>":
            return
        content_type = request.headers.get("content-type", "").casefold()
        if "application/x-www-form-urlencoded" in content_type:
            event["body"] = {
                key: shape(value, key)
                for key, value in parse_qsl(request.post_data or "", keep_blank_values=True)
            }
        elif "multipart/form-data" in content_type:
            event["body"] = "<multipart-form-body>"

    def run(self) -> int:
        status = super().run()
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
    parser.add_argument("--mode", choices=("guest", "authenticated"), default="guest")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    return ContractProbe(args.store, args.mode, args.output).run()


if __name__ == "__main__":
    raise SystemExit(main())
