"""Command-line entry point."""

from __future__ import annotations

import argparse
import os
from typing import Any


def _env_enabled(name: str) -> bool:
    return os.getenv(name, "").casefold() in {"1", "true", "yes", "on"}


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
    parser.add_argument(
        "--allow-retailer-writes",
        action="store_true",
        default=_env_enabled("OPEN_GROCERY_ENABLE_RETAILER_WRITES"),
        help="allow confirmation-gated changes to authenticated retailer state",
    )
    parser.add_argument(
        "--allow-order-submission",
        action="store_true",
        default=_env_enabled("OPEN_GROCERY_ENABLE_ORDER_SUBMISSION"),
        help="allow the final confirmation-gated order submission endpoint",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.allow_retailer_writes:
        os.environ["OPEN_GROCERY_ENABLE_RETAILER_WRITES"] = "1"
    if args.allow_order_submission:
        os.environ["OPEN_GROCERY_ENABLE_ORDER_SUBMISSION"] = "1"

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
