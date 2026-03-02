import asyncio
import time
import sys
import os

sys.path.insert(0, os.path.abspath('.'))
from tests.debank_playwright_signer_fast import DeBankSigner

async def test_speed():
    signer = DeBankSigner()
    wallet = "0x463452C356322D463B84891eBDa33DAED274cB40"
    
    print("\nBenchmarking the FIXED signature generation...")
    
    start_total = time.time()
    
    # 1. Warm up / initial load
    t0 = time.time()
    await signer.sign_request("/portfolio/project_list", {"user_addr": wallet.lower()})
    print(f"Initial setup & sign took: {time.time() - t0:.2f}s")
    
    # 2. Subsequent requests
    chains = ["eth", "bsc", "arb", "op", "matic", "avax"]
    for c in chains:
        await asyncio.sleep(0.5) # Prevent Debank fetch throttle
        t0 = time.time()
        headers = await signer.sign_request("/token/balance_list", {"user_addr": wallet.lower(), "chain": c})
        print(f"Sign for {c} took: {time.time() - t0:.2f}s. Success: {headers is not None}")

    print(f"Total time: {time.time() - start_total:.2f}s")
    await signer.close()

if __name__ == "__main__":
    asyncio.run(test_speed())
