"""Public ASGI entrypoint for Vercel's Python runtime."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from starlette.responses import JSONResponse, Response

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


app = mcp.streamable_http_app(
    streamable_http_path="/api/index",
    json_response=True,
    stateless_http=True,
    transport_security=_transport_security(),
)
