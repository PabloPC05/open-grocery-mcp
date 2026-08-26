from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


VALIDATOR = Path(__file__).parents[1] / "tools" / "validate_capture.py"


def run_validator(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), str(path), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def test_validator_rejects_zero_events(tmp_path: Path) -> None:
    capture = tmp_path / "empty.json"
    capture.write_text(
        json.dumps({"store": "gadis", "mode": "interactive-local", "events": []}),
        encoding="utf-8",
    )

    result = run_validator(capture, "--minimum-events", "1")

    assert result.returncode == 1
    summary = json.loads(result.stdout)
    assert summary["ok"] is False
    assert summary["events"] == 0
    assert "no HTTP request was captured" in summary["failures"]


def test_validator_accepts_sanitized_request_and_response(tmp_path: Path) -> None:
    capture = tmp_path / "valid.json"
    capture.write_text(
        json.dumps(
            {
                "store": "gadis",
                "mode": "interactive-local",
                "events": [
                    {
                        "kind": "request",
                        "phase": "cart_read",
                        "method": "GET",
                        "url": "https://example.invalid/api/cart/<id>",
                        "headers": {"cookie": "<redacted>"},
                    },
                    {
                        "kind": "response",
                        "phase": "cart_read",
                        "method": "GET",
                        "url": "https://example.invalid/api/cart/<id>",
                        "status": 200,
                        "headers": {},
                    },
                ],
                "blocked": [],
                "errors": [],
            }
        ),
        encoding="utf-8",
    )

    result = run_validator(
        capture,
        "--minimum-events",
        "2",
        "--require-response",
        "--require-phase",
        "cart_read",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout)
    assert summary["ok"] is True
    assert summary["requests"] == 1
    assert summary["responses"] == 1


def test_validator_rejects_unredacted_sensitive_header(tmp_path: Path) -> None:
    capture = tmp_path / "unsafe.json"
    capture.write_text(
        json.dumps(
            {
                "store": "gadis",
                "events": [
                    {
                        "kind": "request",
                        "phase": "login",
                        "headers": {"authorization": "Bearer actual-secret"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_validator(capture)

    assert result.returncode == 1
    summary = json.loads(result.stdout)
    assert any("was not redacted" in item for item in summary["failures"])


def test_validator_requires_verified_cart_restoration_when_requested(
    tmp_path: Path,
) -> None:
    capture = tmp_path / "mutation.json"
    capture.write_text(
        json.dumps(
            {
                "store": "froiz",
                "events": [
                    {
                        "kind": "request",
                        "phase": "cleanup",
                        "method": "DELETE",
                        "url": "https://example.invalid/api/cart/<id>",
                        "headers": {},
                    }
                ],
                "safety": {"original_cart_restored": False},
            }
        ),
        encoding="utf-8",
    )

    failed = run_validator(capture, "--require-restored-cart")
    assert failed.returncode == 1
    assert "original cart restoration was not verified" in json.loads(
        failed.stdout
    )["failures"]

    payload = json.loads(capture.read_text(encoding="utf-8"))
    payload["safety"]["original_cart_restored"] = True
    capture.write_text(json.dumps(payload), encoding="utf-8")
    passed = run_validator(capture, "--require-restored-cart")
    assert passed.returncode == 0


def test_validator_requires_an_observed_retailer_cart_write(tmp_path: Path) -> None:
    capture = tmp_path / "cart-write.json"
    payload = {
        "store": "froiz",
        "events": [
            {
                "kind": "request",
                "phase": "add",
                "method": "GET",
                "url": "https://supermercado.froiz.com/cart",
                "headers": {},
            }
        ],
        "retailer_endpoint_manifest": [
            {"method": "GET", "operation_hint": "cart"}
        ],
    }
    capture.write_text(json.dumps(payload), encoding="utf-8")

    failed = run_validator(capture, "--require-cart-write")
    assert failed.returncode == 1
    assert "no retailer cart write endpoint was captured" in json.loads(
        failed.stdout
    )["failures"]

    payload["retailer_endpoint_manifest"].append(
        {"method": "PUT", "operation_hint": "cart"}
    )
    capture.write_text(json.dumps(payload), encoding="utf-8")
    passed = run_validator(capture, "--require-cart-write")
    assert passed.returncode == 0


def test_validator_can_fail_on_probe_errors(tmp_path: Path) -> None:
    capture = tmp_path / "errors.json"
    capture.write_text(
        json.dumps(
            {
                "store": "froiz",
                "events": [
                    {
                        "kind": "request",
                        "phase": "cart_initial",
                        "method": "GET",
                        "url": "https://supermercado.froiz.com/cart",
                        "headers": {},
                    }
                ],
                "errors": [{"phase": "add", "message": "failed"}],
            }
        ),
        encoding="utf-8",
    )
    result = run_validator(capture, "--fail-on-reported-errors")
    assert result.returncode == 1
    assert "the probe reported action errors" in json.loads(result.stdout)[
        "failures"
    ]
