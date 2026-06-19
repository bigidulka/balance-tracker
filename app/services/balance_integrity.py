import math
from dataclasses import dataclass

from app.core.config import get_settings
from app.repositories.balance import BalanceRepository
from app.schemas.balance import ServiceBalanceSchema

settings = get_settings()


class BalanceIntegrityError(ValueError):
    pass


@dataclass(frozen=True)
class BalanceIntegritySnapshot:
    assets_sum: float
    accounts_sum: float
    total_usd: float


def _safe_float(value: object) -> float:
    try:
        result = float(value or 0.0)
    except (TypeError, ValueError):
        raise BalanceIntegrityError(f"Invalid numeric balance value: {value!r}")
    if not math.isfinite(result):
        raise BalanceIntegrityError(f"Invalid non-finite balance value: {value!r}")
    return result


def _balance_snapshot(balance: ServiceBalanceSchema) -> BalanceIntegritySnapshot:
    total_usd = _safe_float(balance.total_usd)
    if total_usd < 0:
        raise BalanceIntegrityError(
            f"Negative balance total for {balance.service}: {total_usd}"
        )

    assets_sum = 0.0
    for asset in balance.assets or []:
        amount = _safe_float(asset.amount)
        value_usd = _safe_float(asset.value_usd)
        if amount < 0 or value_usd < 0:
            raise BalanceIntegrityError(
                f"Negative asset balance for {balance.service}: {asset.coin}"
            )
        assets_sum += value_usd

    accounts_sum = 0.0
    for account in balance.accounts or []:
        account_total = _safe_float(account.total_usd)
        if account_total < 0:
            raise BalanceIntegrityError(
                f"Negative account balance for {balance.service}: {account.account_type}"
            )
        accounts_sum += account_total
        for asset in account.assets or []:
            amount = _safe_float(asset.amount)
            value_usd = _safe_float(asset.value_usd)
            if amount < 0 or value_usd < 0:
                raise BalanceIntegrityError(
                    f"Negative account asset balance for {balance.service}: {asset.coin}"
                )

    return BalanceIntegritySnapshot(
        assets_sum=assets_sum,
        accounts_sum=accounts_sum,
        total_usd=total_usd,
    )


def _allows_portfolio_total_mismatch(service: str) -> bool:
    normalized = (service or "").strip().lower()
    return normalized.startswith(("debank_sdk_", "evm_"))


def _is_wallet_portfolio_service(service: str) -> bool:
    normalized = (service or "").strip().lower()
    return normalized.startswith(("debank_sdk_", "evm_", "sol_", "tron_", "ton_", "sui_"))


def _has_recent_history_support(current_total: float, recent_totals: list[float]) -> bool:
    if current_total <= 0 or not recent_totals:
        return False
    tolerance = max(100.0, abs(current_total) * 0.10)
    matches = [
        value
        for value in recent_totals
        if value > 0 and abs(value - current_total) <= tolerance
    ]
    return len(matches) >= 3


def validate_balance_shape(balance: ServiceBalanceSchema) -> None:
    if not settings.balance_integrity_enabled:
        return

    snapshot = _balance_snapshot(balance)
    total_usd = snapshot.total_usd
    has_assets = bool(balance.assets)
    has_accounts = bool(balance.accounts)
    reference_sum = snapshot.assets_sum if has_assets else snapshot.accounts_sum
    allow_portfolio_mismatch = _allows_portfolio_total_mismatch(balance.service)

    if total_usd > 0 and not has_assets and not has_accounts:
        if not allow_portfolio_mismatch:
            raise BalanceIntegrityError(
                f"Empty positive balance payload for {balance.service}: total_usd={total_usd:.2f}"
            )
        return

    if (has_assets or has_accounts) and not allow_portfolio_mismatch:
        reference_name = "assets" if has_assets else "accounts"
        reference_value = snapshot.assets_sum if has_assets else snapshot.accounts_sum
        mismatch = abs(total_usd - reference_value)
        tolerance = max(
            settings.balance_integrity_total_mismatch_abs_usd,
            max(abs(total_usd), abs(reference_value))
            * settings.balance_integrity_total_mismatch_rel,
        )
        if mismatch > tolerance:
            raise BalanceIntegrityError(
                f"Balance total/{reference_name} mismatch for "
                f"{balance.service}: total_usd={total_usd:.2f} "
                f"{reference_name}_sum={reference_value:.2f} mismatch={mismatch:.2f}"
            )

    if total_usd == 0 and reference_sum > settings.balance_integrity_total_mismatch_abs_usd:
        raise BalanceIntegrityError(
            f"Zero balance total conflicts with payload sum for {balance.service}: "
            f"payload_sum={reference_sum:.2f}"
        )


async def validate_balance_for_persistence(
    repo: BalanceRepository,
    *,
    organization_id: int,
    service: str,
    balance: ServiceBalanceSchema,
    integration_id: int | None,
) -> None:
    if not settings.balance_integrity_enabled:
        return

    validate_balance_shape(balance)

    previous = await repo.get_latest_balance(
        service,
        organization_id=organization_id,
        integration_id=integration_id,
    )
    if previous is None:
        return

    previous_total = _safe_float(getattr(previous, "total_usd", 0.0))
    current_total = _safe_float(balance.total_usd)
    if previous_total < settings.balance_integrity_outlier_min_previous_usd:
        return

    delta = abs(current_total - previous_total)
    if delta < settings.balance_integrity_outlier_min_abs_usd:
        return

    relative_change = delta / max(previous_total, 1.0)
    is_wallet_service = _is_wallet_portfolio_service(service)
    suspicious_large_change = (
        relative_change > settings.balance_integrity_outlier_max_relative_change
    )
    suspicious_wallet_drop = (
        is_wallet_service
        and previous_total >= 1000.0
        and current_total < previous_total * 0.5
    )
    if suspicious_large_change or suspicious_wallet_drop:
        recent_totals = await repo.get_recent_history_totals(
            service=service,
            organization_id=organization_id,
            integration_id=integration_id,
            limit=30,
        )
        if is_wallet_service and _has_recent_history_support(current_total, recent_totals):
            return
        raise BalanceIntegrityError(
            f"Balance outlier for {service}: previous={previous_total:.2f} "
            f"current={current_total:.2f} delta={delta:.2f} "
            f"relative_change={relative_change:.2f}"
        )
