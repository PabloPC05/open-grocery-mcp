"""Locate versioned data in editable checkouts and built wheels."""

from __future__ import annotations

from pathlib import Path


def data_path(name: str) -> Path:
    packaged = Path(__file__).resolve().parent / "data" / name
    if packaged.is_file():
        return packaged
    checkout = Path(__file__).resolve().parents[2] / "data" / name
    if checkout.is_file():
        return checkout
    raise FileNotFoundError(f"required Open Grocery data file is missing: {name}")


__all__ = ["data_path"]
