#!/usr/bin/env python3
"""
Скрипт для изучения структуры истории вводов/выводов через CCXT для всех бирж.
Запускается через: python -m scripts.test_transactions
"""
import asyncio
import json
import logging
import sys
import os

# Добавляем корневую директорию в path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ccxt.pro as ccxtpro

from app.core.config import get_settings

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

settings = get_settings()

# Количество записей для тестирования
LIMIT = 5


async def test_exchange_transactions(exchange_id: str):
    """Тестирует получение транзакций для одной биржи"""

    config = settings.get_exchange_config(exchange_id)
    if not config.get("apiKey"):
        logger.warning(f"❌ {exchange_id}: No API key configured")
        return

    exchange_class = getattr(ccxtpro, exchange_id, None)
    if exchange_class is None:
        logger.warning(f"❌ {exchange_id}: Not supported by CCXT")
        return

    exchange_config = {
        "apiKey": config["apiKey"],
        "secret": config["secret"],
        "enableRateLimit": True,
        "timeout": 30000,
    }

    if config.get("password"):
        exchange_config["password"] = config["password"]
    if config.get("uid"):
        exchange_config["uid"] = config["uid"]

    if settings.proxy_url:
        exchange_config["aiohttp_proxy"] = settings.proxy_url

    exchange = exchange_class(exchange_config)

    print(f"\n{'='*60}")
    print(f"Testing: {exchange_id.upper()}")
    print(f"{'='*60}")

    # Проверяем наличие методов
    has_fetch_deposits = exchange.has.get("fetchDeposits", False)
    has_fetch_withdrawals = exchange.has.get("fetchWithdrawals", False)
    has_fetch_transactions = exchange.has.get("fetchTransactions", False)
    has_fetch_ledger = exchange.has.get("fetchLedger", False)

    print(f"API Support:")
    print(f"  - fetchDeposits: {has_fetch_deposits}")
    print(f"  - fetchWithdrawals: {has_fetch_withdrawals}")
    print(f"  - fetchTransactions: {has_fetch_transactions}")
    print(f"  - fetchLedger: {has_fetch_ledger}")

    try:
        # Пробуем fetchDeposits
        if has_fetch_deposits:
            print(f"\n📥 Deposits:")
            try:
                deposits = await exchange.fetch_deposits(limit=LIMIT)
                if deposits:
                    print(f"  Found {len(deposits)} deposits")
                    for dep in deposits[:2]:  # Показываем первые 2
                        print(f"  Sample: {json.dumps(dep, indent=4, default=str)}")
                else:
                    print("  No deposits found")
            except Exception as e:
                print(f"  Error: {e}")

        # Пробуем fetchWithdrawals
        if has_fetch_withdrawals:
            print(f"\n📤 Withdrawals:")
            try:
                withdrawals = await exchange.fetch_withdrawals(limit=LIMIT)
                if withdrawals:
                    print(f"  Found {len(withdrawals)} withdrawals")
                    for w in withdrawals[:2]:  # Показываем первые 2
                        print(f"  Sample: {json.dumps(w, indent=4, default=str)}")
                else:
                    print("  No withdrawals found")
            except Exception as e:
                print(f"  Error: {e}")

        # Пробуем fetchTransactions (если нет отдельных методов)
        if has_fetch_transactions and not (
            has_fetch_deposits and has_fetch_withdrawals
        ):
            print(f"\n📝 Transactions (combined):")
            try:
                transactions = await exchange.fetch_transactions(limit=LIMIT)
                if transactions:
                    print(f"  Found {len(transactions)} transactions")
                    for t in transactions[:2]:
                        print(f"  Sample: {json.dumps(t, indent=4, default=str)}")
                else:
                    print("  No transactions found")
            except Exception as e:
                print(f"  Error: {e}")

    except Exception as e:
        logger.error(f"Error testing {exchange_id}: {e}")
    finally:
        await exchange.close()


async def main():
    """Основная функция"""
    exchanges = settings.get_active_exchanges()

    print(f"Found {len(exchanges)} active exchanges: {exchanges}")

    for exchange_id in exchanges:
        try:
            await test_exchange_transactions(exchange_id)
        except Exception as e:
            print(f"❌ {exchange_id}: Failed - {e}")

        # Небольшая пауза между биржами
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
