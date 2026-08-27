"""Temporary private-network probe for the deployed USC MCP."""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import parse_qs

from starlette.responses import JSONResponse, Response

_PROBE_KEY_SHA256 = "3ac4956cfd005fecb473106d03ff3eafe2e7087667607bc7af93b42c111f4528"
_ENDPOINT = "https://mcp-usc.vercel.app/mcp"


def _decode_response(raw: bytes, content_type: str) -> dict[str, Any] | None:
    if not raw.strip():
        return None
    text = raw.decode("utf-8")
    if "text/event-stream" not in content_type:
        return json.loads(text)

    payloads: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        data = line.removeprefix("data:").strip()
        if data and data != "[DONE]":
            payloads.append(json.loads(data))
    return payloads[-1] if payloads else None


def _text_preview(result: dict[str, Any], limit: int = 500) -> str:
    for item in result.get("content", []):
        if item.get("type") == "text" and isinstance(item.get("text"), str):
            return item["text"][:limit]
    return ""


def _tool_summary(response: dict[str, Any] | None) -> dict[str, Any]:
    if not response or "result" not in response:
        return {"ok": False, "protocol_response": repr(response)[:500]}
    result = response["result"]
    return {
        "ok": result.get("isError") is not True,
        "is_error": result.get("isError") is True,
        "preview": _text_preview(result),
        "content_types": [item.get("type") for item in result.get("content", [])],
    }


def _run_probe(bearer: str) -> dict[str, Any]:
    session_id: str | None = None

    def rpc(payload: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal session_id
        headers = {
            "Authorization": f"Bearer {bearer}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "open-grocery-mcp/usc-probe",
        }
        if session_id:
            headers["Mcp-Session-Id"] = session_id

        request = urllib.request.Request(
            _ENDPOINT,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                if discovered_session := response.headers.get("Mcp-Session-Id"):
                    session_id = discovered_session
                return _decode_response(
                    response.read(),
                    response.headers.get("Content-Type", ""),
                )
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:800]
            raise RuntimeError(f"USC MCP returned HTTP {error.code}: {detail}") from error

    initialize = rpc(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "vercel-live-probe", "version": "1.0"},
            },
        }
    )
    if not initialize or "result" not in initialize:
        raise RuntimeError(f"Invalid initialize response: {initialize!r}")

    rpc(
        {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
    )

    listed = rpc(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        }
    )
    if not listed or "result" not in listed:
        raise RuntimeError(f"Invalid tools/list response: {listed!r}")

    tools = listed["result"].get("tools", [])
    tool_names = [str(tool.get("name", "")) for tool in tools]
    expected = {"describe_mcp_usc", "list_exam_sources", "list_official_exam_degrees"}
    missing = sorted(expected.difference(tool_names))
    if missing:
        raise RuntimeError(f"Expected tools are missing: {missing}")

    calls: dict[str, dict[str, Any] | None] = {}
    for request_id, tool_name in enumerate(
        ("describe_mcp_usc", "list_exam_sources", "list_official_exam_degrees"),
        start=3,
    ):
        calls[tool_name] = rpc(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": {}},
            }
        )

    summaries = {name: _tool_summary(response) for name, response in calls.items()}
    required_successes = ("describe_mcp_usc", "list_exam_sources")
    if any(not summaries[name]["ok"] for name in required_successes):
        raise RuntimeError(f"Required tool calls failed: {summaries!r}")

    server_info = initialize["result"].get("serverInfo", {})
    return {
        "ok": True,
        "endpoint": _ENDPOINT,
        "protocol_version": initialize["result"].get("protocolVersion"),
        "server_name": server_info.get("name"),
        "server_version": server_info.get("version"),
        "tool_count": len(tools),
        "sample_tools": tool_names[:15],
        "tool_calls": summaries,
    }


async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    if scope.get("type") != "http":
        response: Response = JSONResponse({"error": "unsupported_scope"}, status_code=400)
        await response(scope, receive, send)
        return

    query = parse_qs(scope.get("query_string", b"").decode("utf-8", errors="ignore"))
    probe_key = query.get("key", [""])[0]
    bearer = query.get("token", [""])[0]
    supplied_hash = hashlib.sha256(probe_key.encode("utf-8")).hexdigest()

    if not hmac.compare_digest(supplied_hash, _PROBE_KEY_SHA256):
        response = JSONResponse({"error": "not_found"}, status_code=404)
    elif not bearer:
        response = JSONResponse({"error": "missing_token"}, status_code=400)
    else:
        try:
            response = JSONResponse(_run_probe(bearer), status_code=200)
        except Exception as error:
            response = JSONResponse(
                {"ok": False, "error": type(error).__name__, "message": str(error)},
                status_code=502,
            )

    response.headers["Cache-Control"] = "no-store"
    await response(scope, receive, send)
