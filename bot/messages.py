"""Bot message templates."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


WELCOME = "📊 <b>Balance Overview</b>"
LOADING = "⏳ Loading..."
ACCESS_DENIED = "Access denied"
ERROR_PREFIX = "❌ Error"
NOOP = "noop"


def format_usd(value: float) -> str:
    if value >= 1000:
        return f"${value:,.2f}"
    return f"${value:.2f}"


def format_balance_overview(parsed: dict[str, Any], freshness: str) -> str:
    return (
        "📊 Balance Overview\n"
        f"{'=' * 30}\n\n"
        f"💰 Total: {format_usd(parsed['total'])}\n\n"
        f"📈 Exchanges: {parsed['exchanges_count']}\n"
        f"• Spot: {format_usd(parsed['spot_total'])}\n"
        f"• Futures: {format_usd(parsed['futures_total'])}\n"
        f"🔗 DEX Wallet: {format_usd(parsed['dex_total'])}\n\n"
        f"⏱ {freshness}"
    )


def format_dashboard_summary(summary: dict[str, Any]) -> str:
    total = float(summary.get("total_usd") or 0.0)
    exchanges_count = int(summary.get("exchanges_count") or 0)
    spot_total = float(summary.get("spot_total") or 0.0)
    futures_total = float(summary.get("futures_total") or 0.0)
    dex_total = float(summary.get("dex_total") or 0.0)

    plan = summary.get("plan") or {}
    plan_name = plan.get("name") or plan.get("code") or "unknown"

    throttling = summary.get("throttling") or {}
    capabilities = summary.get("capabilities") or {}
    can_refresh = bool(capabilities.get("can_refresh", capabilities.get("refresh", True)))
    retry_after = int(throttling.get("retry_after_seconds") or 0)

    integrations = summary.get("integrations") or {}
    integrations_total = int(integrations.get("total") or 0)
    integrations_active = int(integrations.get("active") or 0)

    tx24h = summary.get("transactions_24h") or {}
    tx_total = int(tx24h.get("total") or 0)
    tx_pending = int(tx24h.get("pending") or 0)

    freshness_raw = str(summary.get("freshness") or "No data")
    freshness = freshness_raw.replace("_", " ")

    refresh_state = "available" if can_refresh else f"cooldown ({retry_after}s)"

    return (
        "📊 SaaS Dashboard\n"
        f"{'=' * 30}\n\n"
        f"💰 Total: {format_usd(total)}\n"
        f"📈 Exchanges: {exchanges_count}\n"
        f"• Spot: {format_usd(spot_total)}\n"
        f"• Futures: {format_usd(futures_total)}\n"
        f"🔗 DEX Wallet: {format_usd(dex_total)}\n\n"
        f"🧩 Integrations: {integrations_active}/{integrations_total} active\n"
        f"🧾 Transactions 24h: {tx_total} (pending: {tx_pending})\n\n"
        f"📦 Plan: {plan_name}\n"
        f"🔄 Refresh: {refresh_state}\n"
        f"⏱ {freshness}"
    )


def format_timestamp(data: dict[str, Any]) -> str:
    services = data.get("services", [])
    if not services:
        return "No data"

    latest_time = None
    for svc in services:
        updated_at = svc.get("updated_at")
        if not updated_at:
            continue
        try:
            dt = (
                datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                if isinstance(updated_at, str)
                else updated_at
            )
            if latest_time is None or dt > latest_time:
                latest_time = dt
        except Exception:
            continue

    if latest_time is None:
        return "Unknown"

    now = datetime.now(timezone.utc)
    if latest_time.tzinfo is None:
        latest_time = latest_time.replace(tzinfo=timezone.utc)

    minutes = int((now - latest_time).total_seconds() / 60)

    if minutes < 1:
        return "🟢 Updated just now"
    if minutes < 5:
        return f"🟢 Updated {minutes}m ago"
    if minutes < 15:
        return f"🟡 Updated {minutes}m ago"
    return f"🔴 Updated {minutes}m ago"


def section_title(title: str, total_usd: float) -> list[str]:
    return [title, f"{'=' * 30}", f"Total: {format_usd(total_usd)}", ""]


def format_refresh_state(capabilities: dict[str, Any], throttling: dict[str, Any]) -> str:
    can_refresh = bool(capabilities.get("can_refresh", capabilities.get("refresh", True)))
    if can_refresh:
        return "Refresh: available"
    retry_after = int(throttling.get("retry_after_seconds") or 0)
    if retry_after > 0:
        return f"Refresh: cooldown ({retry_after}s)"
    return "Refresh: unavailable"
