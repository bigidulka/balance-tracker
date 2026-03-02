import asyncio
import time
import sys
import os

sys.path.insert(0, os.path.abspath('.'))
from tests.debank_playwright_signer_v3 import DeBankSigner

async def test_speed():
    signer = DeBankSigner()
    print("Initializing...")
    await signer._init_browser()
    await asyncio.sleep(5)
    
    print("\nBenchmarking sequential signer interface...")
    wallet = "0x463452C356322D463B84891eBDa33DAED274cB40"
    
    chains = ["eth", "bsc", "arb", "op"]
    start_total = time.time()
    
    for c in chains:
        t0 = time.time()
        headers = await signer.sign_request("/token/balance_list", {"user_addr": wallet.lower(), "chain": c})
        if headers:
            print(f"[{c}] Took: {time.time() - t0:.2f}s")
        else:
            print(f"[{c}] Failed")
            
        await asyncio.sleep(0.5)

    print(f"Total time: {time.time() - start_total:.2f}s")
    await signer.close()

if __name__ == "__main__":
    asyncio.run(test_speed())
