"""Authenticated ASGI entrypoint for Vercel's Python runtime."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any

from starlette.responses import JSONResponse, PlainTextResponse, Response

# Vercel executes this file from the repository root without installing the
# editable package, so make the src layout importable explicitly.
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402
from open_grocery_mcp import __version__  # noqa: E402
from open_grocery_mcp.server import mcp  # noqa: E402


def _transport_security() -> TransportSecuritySettings:
    hosts = {"127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*", "testserver"}
    for name in ("VERCEL_URL", "VERCEL_PROJECT_PRODUCTION_URL"):
        if value := os.getenv(name, "").strip():
            hosts.add(value.removeprefix("https://").removeprefix("http://").rstrip("/"))
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=sorted(hosts),
        allowed_origins=[f"https://{host}" for host in hosts if ":*" not in host],
    )


@mcp.custom_route("/api/health", methods=["GET"])
async def deployment_health(request: Any) -> Response:
    del request
    return JSONResponse(
        {
            "status": "ok",
            "service": "open-grocery-mcp",
            "version": __version__,
            "mcp_endpoint": "/mcp",
            "retailer_writes": False,
            "order_submission": False,
        }
    )


class BearerAuthMiddleware:
    """Require the deployment secret for every HTTP request."""

    def __init__(self, wrapped_app: Any) -> None:
        self.wrapped_app = wrapped_app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or (
            scope.get("method") == "GET" and scope.get("path") == "/api/health"
        ):
            await self.wrapped_app(scope, receive, send)
            return

        expected = os.getenv("OPEN_GROCERY_MCP_ACCESS_TOKEN", "")
        if not expected:
            response = PlainTextResponse("MCP access is not configured", status_code=503)
            await response(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        scheme, _, supplied = authorization.partition(" ")
        if scheme.casefold() != "bearer" or not secrets.compare_digest(supplied, expected):
            response = PlainTextResponse(
                "Unauthorized",
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        await self.wrapped_app(scope, receive, send)


# mcp-usc currently pins the v1 FastMCP API. Configure the existing server
# through its settings before creating the ASGI app so this preview can import.
mcp.settings.streamable_http_path = "/api/index"
mcp.settings.json_response = True
mcp.settings.stateless_http = True
mcp.settings.transport_security = _transport_security()

app = BearerAuthMiddleware(mcp.streamable_http_app())
