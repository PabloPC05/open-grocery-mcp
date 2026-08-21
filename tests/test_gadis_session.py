from __future__ import annotations

import json
from pathlib import Path

import httpx

from open_grocery_mcp.providers.gadis_session import GadisSessionClient


def _state(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "domain": ".gadisline.com",
                        "name": "next-auth.session-token",
                        "value": "private-cookie-value",
                        "expires": -1,
                    },
                    {
                        "domain": ".example.com",
                        "name": "unrelated",
                        "value": "ignore-me",
                        "expires": -1,
                    },
                ],
                "origins": [],
            }
        ),
        encoding="utf-8",
    )


def test_gadis_http_session_status_is_value_free(tmp_path: Path) -> None:
    state_path = tmp_path / "storage_state.json"
    _state(state_path)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.path == "/api/auth/session"
        assert "next-auth.session-token=private-cookie-value" in request.headers["cookie"]
        assert "unrelated" not in request.headers["cookie"]
        return httpx.Response(
            200,
            json={
                "user": {
                    "name": "Private Name",
                    "email": "private@example.com",
                    "customer_id": "private-id",
                },
                "expires": "2030-01-01T00:00:00.000Z",
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    status = GadisSessionClient(state_path=state_path, client=client).status()
    assert status["authenticated"] is True
    assert status["http_session_checked"] is True
    assert status["http_status"] == 200
    assert status["user_fields"] == ["customer_id", "email", "name"]
    assert status["profile_values_exposed"] is False
    serialized = json.dumps(status)
    assert "Private Name" not in serialized
    assert "private@example.com" not in serialized
    assert "private-cookie-value" not in serialized
    assert len(seen) == 1
    client.close()


def test_gadis_http_session_reports_missing_state_without_network(tmp_path: Path) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    status = GadisSessionClient(
        state_path=tmp_path / "missing.json",
        client=client,
    ).status()
    assert status["session_present"] is False
    assert status["authenticated"] is False
    assert status["http_session_checked"] is False
    assert calls == 0
    client.close()


def test_gadis_http_session_handles_expired_or_rejected_cookie(tmp_path: Path) -> None:
    state_path = tmp_path / "storage_state.json"
    _state(state_path)
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(401, json={}))
    )
    status = GadisSessionClient(state_path=state_path, client=client).status()
    assert status["session_present"] is True
    assert status["http_session_checked"] is True
    assert status["authenticated"] is False
    assert status["http_status"] == 401
    client.close()
