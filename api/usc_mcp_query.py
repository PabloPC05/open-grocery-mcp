"""Temporary keyed endpoint that invokes the public mcp-usc timetable tools."""

from __future__ import annotations

import hashlib
import hmac
import os
import unicodedata
from typing import Any
from urllib.parse import parse_qs

from starlette.responses import JSONResponse, Response

# Vercel exposes only /tmp as writable storage. This is public timetable data only.
os.environ.setdefault("XDG_DATA_HOME", "/tmp")

from mcp_usc.server import mcp  # noqa: E402

_PROBE_KEY_SHA256 = "a383486649f59ec367b6dea36847f5bb866af286829d55889a6cd7390cfcc524"
_SUBJECT_CODES = [
    "G4012223",
    "G4012227",
    "G4012322",
    "G4012224",
    "G4012328",
    "G4012455",
    "G4012326",
    "G4012329",
    "G4012421",
    "G1011449",
    "G1011442",
    "G1011132",
    "G1012226",
]


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


async def _call_tool(name: str, arguments: dict[str, Any]) -> Any:
    # Invoke the registered mcp-usc tool surface, preserving its validation and
    # official-source-only implementation. This branch is removed after the query.
    return await mcp._tool_manager.call_tool(name, arguments)  # noqa: SLF001


def _query(scope: dict[str, Any]) -> dict[str, list[str]]:
    raw = scope.get("query_string", b"").decode("utf-8", errors="ignore")
    return parse_qs(raw, keep_blank_values=True)


def _first(query: dict[str, list[str]], name: str, default: str = "") -> str:
    return query.get(name, [default])[0]


def _authorized(query: dict[str, list[str]]) -> bool:
    candidate = _first(query, "key")
    digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
    return hmac.compare_digest(digest, _PROBE_KEY_SHA256)


async def _handle(query: dict[str, list[str]]) -> dict[str, Any]:
    mode = _first(query, "mode", "locate")
    if mode == "degrees":
        result = await _call_tool("list_usc_degrees", {})
        candidates = [
            item
            for item in result.get("degrees", [])
            if any(marker in _fold(str(item.get("name", ""))) for marker in ("informat", "matemat"))
        ]
        return {"mode": mode, "candidates": candidates, "count": len(candidates)}

    if mode == "locate":
        academic_year = _first(query, "year", "2026/2027")
        degrees = await _call_tool("list_usc_degrees", {})
        candidates = [
            item
            for item in degrees.get("degrees", [])
            if any(marker in _fold(str(item.get("name", ""))) for marker in ("informat", "matemat"))
            and "dobre" not in _fold(str(item.get("name", "")))
            and "doble" not in _fold(str(item.get("name", "")))
        ]
        degree_urls = [str(item["url"]) for item in candidates]
        located = await _call_tool(
            "locate_usc_subject_codes",
            {
                "subject_codes": _SUBJECT_CODES,
                "academic_year": academic_year,
                "degree_urls": degree_urls,
                "concurrency": 4,
            },
        )
        return {
            "mode": mode,
            "academic_year": academic_year,
            "degree_candidates": candidates,
            "located": located,
        }

    if mode == "timetable":
        degree_url = _first(query, "degree_url")
        if not degree_url:
            raise ValueError("degree_url is required")
        course = int(_first(query, "course"))
        semester = int(_first(query, "semester", "1"))
        academic_year = _first(query, "year", "2026/2027")
        date_in_week = _first(query, "date") or None
        subject_query = _first(query, "subject")
        program_raw = _first(query, "program_id")
        result = await _call_tool(
            "get_degree_class_timetable",
            {
                "degree_url": degree_url,
                "course_number": course,
                "academic_year": academic_year,
                "semester": semester,
                "date_in_week": date_in_week,
                "subject_query": subject_query,
                "program_id": int(program_raw) if program_raw else None,
            },
        )
        return {
            "mode": mode,
            "degree_url": degree_url,
            "course": course,
            "semester": semester,
            "academic_year": academic_year,
            "date_in_week": date_in_week,
            "result": result,
        }

    raise ValueError(f"unsupported mode: {mode}")


async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    del receive
    if scope.get("type") != "http" or scope.get("method") != "GET":
        response: Response = JSONResponse({"error": "not_found"}, status_code=404)
        await response(scope, receive, send)
        return

    query = _query(scope)
    if not _authorized(query):
        response = JSONResponse({"error": "not_found"}, status_code=404)
    else:
        try:
            response = JSONResponse({"ok": True, "data": await _handle(query)}, status_code=200)
        except Exception as error:
            response = JSONResponse(
                {
                    "ok": False,
                    "error": type(error).__name__,
                    "message": str(error),
                },
                status_code=500,
            )
    response.headers["Cache-Control"] = "no-store"
    await response(scope, receive, send)
