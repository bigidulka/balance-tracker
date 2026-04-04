from __future__ import annotations

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.balance import LedgerEntry


_POSITIVE_ENTRY_TYPES = {"credit", "release"}
_NEGATIVE_ENTRY_TYPES = {"debit", "hold"}


class LedgerService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add_entry(
        self,
        *,
        organization_id: int,
        entry_type: str,
        amount: float,
        source_type: str,
        source_id: str | None = None,
        currency: str = "USD",
        created_by_user_id: int | None = None,
        note: str | None = None,
        metadata: dict | None = None,
    ) -> LedgerEntry:
        normalized_type = (entry_type or "").strip().lower()
        if normalized_type not in _POSITIVE_ENTRY_TYPES | _NEGATIVE_ENTRY_TYPES:
            raise ValueError("Unsupported ledger entry type")
        value = float(amount or 0.0)
        if value <= 0:
            raise ValueError("Ledger amount must be positive")

        entry = LedgerEntry(
            organization_id=organization_id,
            created_by_user_id=created_by_user_id,
            entry_type=normalized_type,
            amount=value,
            currency=(currency or "USD").upper(),
            source_type=(source_type or "manual").strip().lower(),
            source_id=source_id,
            note=note,
            metadata_json=dict(metadata or {}),
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def has_source_entry(
        self,
        *,
        organization_id: int,
        source_type: str,
        source_id: str,
    ) -> bool:
        result = await self.session.execute(
            select(LedgerEntry.id)
            .where(
                LedgerEntry.organization_id == organization_id,
                LedgerEntry.source_type == source_type,
                LedgerEntry.source_id == source_id,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def get_balance(self, organization_id: int, currency: str = "USD") -> float:
        result = await self.session.execute(
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (LedgerEntry.entry_type.in_(tuple(_POSITIVE_ENTRY_TYPES)), LedgerEntry.amount),
                            else_=-LedgerEntry.amount,
                        )
                    ),
                    0.0,
                )
            ).where(
                LedgerEntry.organization_id == organization_id,
                LedgerEntry.currency == (currency or "USD").upper(),
            )
        )
        value = result.scalar_one()
        return float(value or 0.0)

    async def list_entries(
        self,
        organization_id: int,
        *,
        limit: int = 20,
    ) -> list[LedgerEntry]:
        result = await self.session.execute(
            select(LedgerEntry)
            .where(LedgerEntry.organization_id == organization_id)
            .order_by(LedgerEntry.created_at.desc(), LedgerEntry.id.desc())
            .limit(max(1, min(limit, 100)))
        )
        return list(result.scalars().all())
