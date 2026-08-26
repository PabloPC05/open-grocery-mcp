"""Run the public, anonymized semantic corpus audit."""

from __future__ import annotations

import argparse
import json

from open_grocery_mcp.quality_audit import audit_corpus, audit_live_catalogues
from open_grocery_mcp.registry import default_registry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", action="append", help="Run a read-only live catalogue audit")
    parser.add_argument("--store", action="append", dest="stores")
    parser.add_argument("--postal-code")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    if args.query:
        registry = default_registry()
        try:
            result = audit_live_catalogues(
                registry,
                queries=args.query,
                stores=args.stores,
                postal_code=args.postal_code,
                limit_per_query=args.limit,
            )
        finally:
            registry.close()
        exit_code = 0 if result["totals"]["errors"] == 0 else 2
    else:
        result = audit_corpus()
        exit_code = 0 if result["failed"] == 0 else 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
