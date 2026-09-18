from __future__ import annotations

import asyncio
from pathlib import Path

from scripts.demo_sqlite import run_demo


def test_demo_sqlite_reports_normalized_synthetic_portfolio(tmp_path: Path) -> None:
    summary = asyncio.run(run_demo(tmp_path / "portfolio.db"))

    assert summary["total"] == 24150.0
    assert summary["spot_total"] == 16000.0
    assert summary["futures_total"] == 1450.0
    assert summary["dex_total"] == 6700.0
    assert summary["exchanges_count"] == 1
    assert len(summary["dex_wallets"]) == 2
    assert summary["database"].endswith("portfolio.db")


def test_demo_sqlite_creates_a_fresh_database_each_run(tmp_path: Path) -> None:
    target = tmp_path / "portfolio.db"

    first = asyncio.run(run_demo(target))
    second = asyncio.run(run_demo(target))

    assert first["total"] == second["total"]
