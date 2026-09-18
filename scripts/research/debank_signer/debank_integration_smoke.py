import asyncio
from tests.debank_client import debank_client  # unit-tested client kept under tests/


async def main():
    print(
        "Testing DeBank API with wallet address 0x463452C356322D463B84891eBDa33DAED274cB40..."
    )
    try:
        # Test the portfolio endpoint
        addr = "0x463452C356322D463B84891eBDa33DAED274cB40"
        portfolio = await debank_client.fetch_portfolio(addr)
        print(f"Portfolio data received: {len(portfolio) if portfolio else 0} items")

        # Test the tokens endpoint
        tokens = await debank_client.fetch_tokens(addr)
        print(f"Token data received: {len(tokens) if tokens else 0} items")

        # Test the wallet balance function
        balance = await debank_client.fetch_wallet_balance(addr)
        print(f"Wallet balance total: ${balance.total_usd}")
        print(f"Number of assets: {len(balance.assets)}")

        # Show first few assets
        for i, asset in enumerate(balance.assets[:5]):
            print(f"  Asset {i+1}: {asset.coin} = {asset.amount} (${asset.value_usd})")

        print("\nSUCCESS: DeBank API integration working!")

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_api())


if __name__ == "__main__":
    asyncio.run(main())
