"""Chain emoji catalog loaded from the debank-emoji-ids.csv asset."""

from __future__ import annotations

import csv
from pathlib import Path

_CSV_PATH = Path(__file__).resolve().parent / "assets" / "debank-emoji-ids.csv"


def _load() -> dict[str, str]:
    chain_map: dict[str, str] = {}
    if not _CSV_PATH.exists():
        return chain_map
    with _CSV_PATH.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            chain_id = (row.get("chain_id") or "").strip()
            emoji_id = (row.get("emoji_id") or "").strip()
            if chain_id and emoji_id:
                chain_map[chain_id] = emoji_id
    return chain_map


CHAIN_EMOJI_MAP: dict[str, str] = _load()
