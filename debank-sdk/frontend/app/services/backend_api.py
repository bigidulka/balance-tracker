from __future__ import annotations

from dataclasses import dataclass

import aiohttp


class BackendError(RuntimeError):
    pass


@dataclass(slots=True)
class WatchlistItem:
    telegramUserId: int
    address: str
    label: str | None
    createdAt: str


@dataclass(slots=True)
class WalletSummary:
    address: str
    totalUsd: float
    tracked: bool
    label: str | None
    fetchedAt: int
    chains: list[str]
    chainCount: int
    tokenCount: int
    topTokens: list[dict[str, float | str]]
    recentTransactions: int
    scamTransactions: int


@dataclass(slots=True)
class PortfolioChain:
    id: str
    name: str
    iconFile: str | None
    totalUsd: float
    tokenCount: int


@dataclass(slots=True)
class PortfolioToken:
    id: str
    chain: str
    chainName: str
    symbol: str
    name: str
    amount: float
    price: float
    amountUsd: float
    isScam: bool


@dataclass(slots=True)
class WalletPortfolio:
    address: str
    tracked: bool
    label: str | None
    fetchedAt: int
    totalUsd: float
    page: int
    pageSize: int
    totalTokens: int
    hasPrevPage: bool
    hasNextPage: bool
    chains: list[PortfolioChain]
    tokens: list[PortfolioToken]


@dataclass(slots=True)
class TransactionTransfer:
    symbol: str
    name: str
    amount: float
    usdValue: float


@dataclass(slots=True)
class WalletTransaction:
    key: str
    chain: str
    chainName: str
    timeAt: int
    txName: str
    status: int
    otherAddr: str
    isScam: bool
    receivedUsd: float
    sentUsd: float
    receives: list[TransactionTransfer]
    sends: list[TransactionTransfer]


@dataclass(slots=True)
class WalletTransactionsPage:
    address: str
    fetchedAt: int
    cursor: int
    nextCursor: int | None
    pageSize: int
    hideScam: bool
    items: list[WalletTransaction]


class BackendApi:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=35)

    async def _request(self, method: str, path: str, **kwargs):
        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            async with session.request(method, f"{self._base_url}{path}", **kwargs) as response:
                data = await response.json()
                if response.status >= 400:
                    raise BackendError(data.get("error", "Backend request failed"))
                return data

    async def get_watchlist(self, telegram_user_id: int) -> list[WatchlistItem]:
        data = await self._request("GET", f"/users/{telegram_user_id}/watchlist")
        return [WatchlistItem(**item) for item in data["items"]]

    async def add_watch_wallet(self, telegram_user_id: int, address: str, label: str | None = None) -> None:
        await self._request(
            "POST",
            f"/users/{telegram_user_id}/watchlist",
            json={"address": address, "label": label or ""},
        )

    async def rename_watch_wallet(self, telegram_user_id: int, address: str, label: str) -> None:
        await self.add_watch_wallet(telegram_user_id, address, label=label)

    async def remove_watch_wallet(self, telegram_user_id: int, address: str) -> None:
        await self._request("DELETE", f"/users/{telegram_user_id}/watchlist/{address}")

    async def get_summary(self, telegram_user_id: int, address: str) -> WalletSummary:
        data = await self._request("GET", f"/users/{telegram_user_id}/wallets/{address}/summary")
        return WalletSummary(**data)

    async def get_portfolio(self, telegram_user_id: int, address: str, page: int = 0, page_size: int = 8) -> WalletPortfolio:
        data = await self._request(
            "GET",
            f"/users/{telegram_user_id}/wallets/{address}/portfolio?page={page}&pageSize={page_size}",
        )
        return WalletPortfolio(
            address=data["address"],
            tracked=data["tracked"],
            label=data["label"],
            fetchedAt=data["fetchedAt"],
            totalUsd=float(data["totalUsd"]),
            page=int(data["page"]),
            pageSize=int(data["pageSize"]),
            totalTokens=int(data["totalTokens"]),
            hasPrevPage=bool(data["hasPrevPage"]),
            hasNextPage=bool(data["hasNextPage"]),
            chains=[PortfolioChain(**item) for item in data["chains"]],
            tokens=[PortfolioToken(**item) for item in data["tokens"]],
        )

    async def get_transactions(
        self,
        telegram_user_id: int,
        address: str,
        cursor: int = 0,
        page_size: int = 8,
        hide_scam: bool = False,
    ) -> WalletTransactionsPage:
        data = await self._request(
            "GET",
            (
                f"/users/{telegram_user_id}/wallets/{address}/transactions"
                f"?cursor={cursor}&pageSize={page_size}&hideScam={'true' if hide_scam else 'false'}"
            ),
        )
        return WalletTransactionsPage(
            address=data["address"],
            fetchedAt=int(data["fetchedAt"]),
            cursor=int(data["cursor"]),
            nextCursor=data["nextCursor"],
            pageSize=int(data["pageSize"]),
            hideScam=bool(data["hideScam"]),
            items=[
                WalletTransaction(
                    key=item["key"],
                    chain=item["chain"],
                    chainName=item["chainName"],
                    timeAt=int(item["timeAt"]),
                    txName=item["txName"],
                    status=int(item["status"]),
                    otherAddr=item["otherAddr"],
                    isScam=bool(item["isScam"]),
                    receivedUsd=float(item["receivedUsd"]),
                    sentUsd=float(item["sentUsd"]),
                    receives=[TransactionTransfer(**transfer) for transfer in item["receives"]],
                    sends=[TransactionTransfer(**transfer) for transfer in item["sends"]],
                )
                for item in data["items"]
            ],
        )
