"""Test script to check balance for specific wallet address"""

import asyncio
import logging
import sys
from datetime import datetime

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

from tests.debank_sdk import get_debank_portfolio, get_debank_summary, get_debank_tokens


async def check_wallet_balance():
    """Check balance for the specific wallet address."""
    wallet_address = "0x463452C356322D463B84891eBDa33DAED274cB40"

    logger.info(f"Checking balance for wallet: {wallet_address}")
    logger.info("=" * 60)

    try:
        # Get portfolio data
        logger.info("Fetching portfolio data...")
        portfolio = await get_debank_portfolio(wallet_address)

        logger.info(f"Service: {portfolio.service}")
        logger.info(f"Total USD: ${portfolio.total_usd:,.2f}")
        logger.info(f"Updated at: {portfolio.updated_at}")
        logger.info(f"Actual: {portfolio.actual}")
        logger.info(f"Number of assets: {len(portfolio.assets)}")

        logger.info("\nAccounts:")
        for i, account in enumerate(portfolio.accounts):
            logger.info(f"  Account {i+1}: {account.account_type}")
            logger.info(f"    Total USD: ${account.total_usd:,.2f}")
            logger.info(f"    Assets: {len(account.assets)}")

        logger.info("\nAssets:")
        for i, asset in enumerate(portfolio.assets):
            logger.info(f"  Asset {i+1}: {asset.coin}")
            logger.info(f"    Amount: {asset.amount:,.6f}")
            logger.info(f"    Value: ${asset.value_usd:,.2f}")

        # Get summary
        logger.info("\n" + "=" * 60)
        logger.info("Fetching wallet summary...")
        summary = await get_debank_summary(wallet_address)

        logger.info(f"Wallet Address: {summary.get('wallet_address', 'N/A')}")
        logger.info(f"Total USD: ${summary.get('total_usd', 0):,.2f}")
        logger.info(f"Token Count: {summary.get('token_count', 0)}")
        logger.info(f"Chains: {summary.get('chains', [])}")

        # Get detailed tokens
        logger.info("\n" + "=" * 60)
        logger.info("Fetching detailed token balances...")
        tokens = await get_debank_tokens(wallet_address)

        logger.info(f"Number of tokens: {len(tokens)}")
        for i, token in enumerate(tokens):
            logger.info(
                f"  Token {i+1}: {token.get('name', 'Unknown')} ({token.get('symbol', 'N/A')})"
            )
            logger.info(f"    Chain: {token.get('chain', 'unknown')}")
            logger.info(f"    Amount: {token.get('amount', 0):,.6f}")
            logger.info(f"    Price: ${token.get('price', 0):,.4f}")
            logger.info(f"    Value: ${token.get('usd_value', 0):,.2f}")
            logger.info(f"    Contract: {token.get('contract_address', 'N/A')[:20]}...")

        logger.info("\n" + "=" * 60)
        logger.info("BALANCE CHECK COMPLETED SUCCESSFULLY!")
        logger.info(f"Wallet: {wallet_address}")
        logger.info(f"Total Portfolio Value: ${portfolio.total_usd:,.2f}")

        # Return the results for potential use
        return {"portfolio": portfolio, "summary": summary, "tokens": tokens}

    except Exception as e:
        logger.error(f"Error checking wallet balance: {e}")
        import traceback

        logger.error(f"Full traceback: {traceback.format_exc()}")
        raise


async def main():
    """Main function to run the wallet balance check."""
    logger.info("DeBank Wallet Balance Checker")
    logger.info(f"Current time: {datetime.now()}")

    try:
        results = await check_wallet_balance()
        return True
    except Exception as e:
        logger.error(f"Failed to check wallet balance: {e}")
        return False


if __name__ == "__main__":
    success = asyncio.run(main())
    if success:
        print("\n✅ Wallet balance check completed successfully!")
    else:
        print("\n❌ Wallet balance check failed!")
        sys.exit(1)
