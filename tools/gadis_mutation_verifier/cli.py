"""CLI for the opt-in reversible Gadis cart verifier."""
from __future__ import annotations

import argparse
import json
from decimal import Decimal

from . import MAX_ADDED_VALUE, verify


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reversible live Gadis cart verification over authenticated HTTP."
    )
    parser.add_argument(
        "--allow-reversible-cart-write",
        action="store_true",
        help="allow add/change/remove only; never enables checkout or orders",
    )
    parser.add_argument(
        "--max-added-value",
        type=Decimal,
        default=MAX_ADDED_VALUE,
        help="maximum temporary value added to the cart (hard limit: 5.00 EUR)",
    )
    args = parser.parse_args()
    code, payload = verify(
        allow_reversible_cart_write=args.allow_reversible_cart_write,
        max_added_value=args.max_added_value,
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return code
