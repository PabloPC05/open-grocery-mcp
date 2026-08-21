from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "tools" / "verify_gadis_cart_mutation_local.py"


def test_mutation_verifier_runs_directly_and_refuses_without_explicit_flag() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "--allow-reversible-cart-write" in payload["reason"]
    assert payload["retailer_write_performed"] is False
    assert payload["order_or_payment_attempted"] is False
