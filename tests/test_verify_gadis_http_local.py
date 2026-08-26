from __future__ import annotations

import json

from tools import verify_gadis_http_local as verifier


class FakeProvider:
    def __init__(self, *, backend: str = "gadis_http", authenticated: bool = True) -> None:
        self.backend = backend
        self.authenticated = authenticated
        self.closed = False

    def account_status(self):
        return {
            "authenticated": self.authenticated,
            "authenticated_session": self.authenticated,
            "validated_live": True,
            "http_session_checked": True,
            "bearer_token_available": True,
            "account_backend": "gadis_http",
            "cart_backend": "gadis_http_with_browser_fallback",
            "checkout_backend": "browser",
            "cookie_names": ["private-cookie-name"],
        }

    def real_cart(self):
        return {
            "version": 42,
            "products_count": 1,
            "total_text": "2.00",
            "currency": "EUR",
            "cart_backend": self.backend,
            "browser_driven": self.backend != "gadis_http",
            "lines": [
                {
                    "product_id": "private-product-id",
                    "name": "Private product name",
                    "quantity": 1,
                }
            ],
        }

    def close(self):
        self.closed = True


class FailingProvider(FakeProvider):
    def account_status(self):
        raise RuntimeError("private response body must not escape")


def test_read_only_verifier_accepts_authenticated_http_cart(monkeypatch) -> None:
    provider = FakeProvider()
    monkeypatch.setattr(verifier, "GadisFullProvider", lambda: provider)

    code, payload = verifier.verify()

    assert code == 0
    assert payload["ok"] is True
    assert payload["cart"]["cart_backend"] == "gadis_http"
    assert payload["retailer_write_performed"] is False
    assert payload["order_or_payment_attempted"] is False
    serialized = json.dumps(payload)
    assert "private-product-id" not in serialized
    assert "Private product name" not in serialized
    assert "private-cookie-name" not in serialized
    assert provider.closed is True


def test_read_only_verifier_rejects_silent_browser_fallback(monkeypatch) -> None:
    provider = FakeProvider(backend="browser")
    monkeypatch.setattr(verifier, "GadisFullProvider", lambda: provider)

    code, payload = verifier.verify()

    assert code == 1
    assert payload["ok"] is False
    assert "did not use" in payload["reason"]
    assert provider.closed is True


def test_read_only_verifier_reports_missing_authentication(monkeypatch) -> None:
    provider = FakeProvider(authenticated=False)
    monkeypatch.setattr(verifier, "GadisFullProvider", lambda: provider)

    code, payload = verifier.verify()

    assert code == 1
    assert payload["ok"] is False
    assert "not authenticated" in payload["reason"]
    assert provider.closed is True


def test_read_only_verifier_redacts_exception_messages(monkeypatch) -> None:
    provider = FailingProvider()
    monkeypatch.setattr(verifier, "GadisFullProvider", lambda: provider)

    code, payload = verifier.verify()

    assert code == 1
    assert payload["reason"] == "Gadis read-only verification failed"
    assert payload["failure_stage"] == "account_status"
    assert payload["failure_type"] == "RuntimeError"
    assert "private response body" not in json.dumps(payload)
    assert provider.closed is True
