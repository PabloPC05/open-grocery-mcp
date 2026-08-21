from __future__ import annotations

import json
from pathlib import Path

from tools.http_capture_sanitize import sanitize_har


def test_har_sanitizer_removes_credentials_and_personal_data(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("GADIS_TEST_USERNAME", "test-user@example.com")
    monkeypatch.setenv("GADIS_TEST_PASSWORD", "unique-test-password")

    raw_path = tmp_path / "capture.har"
    output_path = tmp_path / "capture.json"
    raw_path.write_text(
        json.dumps(
            {
                "log": {
                    "entries": [
                        {
                            "startedDateTime": "2026-08-21T00:00:00Z",
                            "time": 15,
                            "_resourceType": "fetch",
                            "request": {
                                "method": "POST",
                                "url": (
                                    "https://example.test/api/cart?token=top-secret"
                                    "&page=1"
                                ),
                                "headers": [
                                    {"name": "Authorization", "value": "Bearer aaa"},
                                    {"name": "Cookie", "value": "session=bbb"},
                                    {"name": "X-CSRF-Token", "value": "ccc"},
                                    {"name": "Content-Type", "value": "application/json"},
                                ],
                                "postData": {
                                    "mimeType": "application/json",
                                    "text": json.dumps(
                                        {
                                            "email": "test-user@example.com",
                                            "password": "unique-test-password",
                                            "customer_id": "customer-123",
                                            "address": "Calle Prueba 12, 28050 Madrid",
                                            "product_id": "sku-1",
                                            "quantity": 2,
                                        }
                                    ),
                                },
                            },
                            "response": {
                                "status": 200,
                                "headers": [
                                    {"name": "Set-Cookie", "value": "session=ddd"},
                                    {"name": "Content-Type", "value": "application/json"},
                                ],
                                "content": {
                                    "mimeType": "application/json",
                                    "text": json.dumps(
                                        {
                                            "phone": "+34 612 345 678",
                                            "postal_code": "28050",
                                            "cart": {"id": "cart-1", "total": 3.5},
                                        }
                                    ),
                                },
                            },
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    payload = sanitize_har(
        raw_path,
        output_path,
        {"store": "gadis", "mode": "authenticated"},
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    assert "unique-test-password" not in serialized
    assert "test-user@example.com" not in serialized
    assert "Bearer aaa" not in serialized
    assert "session=bbb" not in serialized
    assert "session=ddd" not in serialized
    assert "+34 612 345 678" not in serialized
    assert "28050" not in serialized
    assert "top-secret" not in serialized
    assert payload["entries"][0]["request"]["body"]["product_id"] == "sku-1"
    assert payload["entries"][0]["request"]["body"]["quantity"] == 2
    assert payload["safety"]["credentials_recorded"] is False
    assert payload["safety"]["final_order_clicked"] is False
    assert output_path.exists()
