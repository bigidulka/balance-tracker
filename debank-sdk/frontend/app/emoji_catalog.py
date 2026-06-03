from __future__ import annotations

import csv
from pathlib import Path


CSV_PATH = Path(__file__).resolve().parent / "assets" / "debank-emoji-ids.csv"


def load_chain_emoji_map() -> dict[str, str]:
    chain_map: dict[str, str] = {}
    if not CSV_PATH.exists():
        return chain_map

    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as file_obj:
        for row in csv.DictReader(file_obj):
            chain_id = (row.get("chain_id") or "").strip()
            emoji_id = (row.get("emoji_id") or "").strip()
            if chain_id and emoji_id:
                chain_map[chain_id] = emoji_id
    return chain_map


CHAIN_EMOJI_MAP = load_chain_emoji_map()
