"""Command-line entry point."""

from __future__ import annotations

import argparse
import os
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="open-grocery-mcp",
        description="Run the Open Grocery Model Context Protocol server.",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default=os.getenv("OPEN_GROCERY_TRANSPORT", "stdio"),
        help="MCP transport (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("OPEN_GROCERY_HOST", "127.0.0.1"),
        help="HTTP bind host; localhost by default for safety",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("OPEN_GROCERY_PORT", "8000")),
        help="HTTP port (default: 8000)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    from open_grocery_mcp.server import mcp

    if args.transport == "stdio":
        mcp.run(transport="stdio")
        return

    kwargs: dict[str, Any] = {
        "host": args.host,
        "port": args.port,
        "json_response": True,
        "stateless_http": True,
    }
    try:
        mcp.run(transport="streamable-http", **kwargs)
    except TypeError:
        # Compatibility path for older FastMCP versions. Keep localhost/port in
        # the server settings where that SDK expects them.
        settings = getattr(mcp, "settings", None)
        if settings is not None:
            if hasattr(settings, "host"):
                settings.host = args.host
            if hasattr(settings, "port"):
                settings.port = args.port
        mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
