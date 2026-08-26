from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from api.index import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app, base_url="http://testserver") as test_client:
        yield test_client


def test_health_routes_do_not_disclose_secrets() -> None:
    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["retailer_writes"] is False
    assert response.json()["order_submission"] is False


def test_mcp_fails_closed_without_access_token(client, monkeypatch) -> None:
    monkeypatch.delenv("OPEN_GROCERY_MCP_ACCESS_TOKEN", raising=False)
    response = client.post("/api/index", json={})

    assert response.status_code == 503


def test_mcp_rejects_wrong_bearer_token(client, monkeypatch) -> None:
    monkeypatch.setenv("OPEN_GROCERY_MCP_ACCESS_TOKEN", "expected-token")
    response = client.post(
        "/api/index",
        headers={"Authorization": "Bearer wrong-token"},
        json={},
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_authenticated_mcp_initialization(client, monkeypatch) -> None:
    monkeypatch.setenv("OPEN_GROCERY_MCP_ACCESS_TOKEN", "expected-token")
    response = client.post(
        "/api/index",
        headers={
            "Authorization": "Bearer expected-token",
            "Accept": "application/json, text/event-stream",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "vercel-test", "version": "1.0"},
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"] == "open-grocery-mcp"
