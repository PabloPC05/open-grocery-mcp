#!/usr/bin/env python3
"""Repository-aware launcher for the local HTTP contract capture.

This entry point makes the ``src`` layout and the repository root importable
before loading the implementation in ``tools/capture_http_local.py``. It can be
run directly from a fresh clone without relying on ``PYTHONPATH``.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parent
for _path in (_REPOSITORY_ROOT / "src", _REPOSITORY_ROOT):
    _value = str(_path)
    if _value not in sys.path:
        sys.path.insert(0, _value)

from tools.capture_http_local import main


if __name__ == "__main__":
    raise SystemExit(main())
