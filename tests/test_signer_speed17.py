import asyncio
import time
import sys
import os

sys.path.insert(0, os.path.abspath('.'))
from tests.debank_playwright_signer_v3 import DeBankSigner

async def test_speed():
    signer = DeBankSigner()
    wallet = "0x463452C356322D463B84891eBDa33DAED274cB40"
    
    print("\nBenchmarking the REAL FIXED signature generation...")
    
    start_total = time.time()
    
    # 1. Warm up / initial load
    t0 = time.time()
    await signer.sign_request("/portfolio/project_list", {"user_addr": wallet.lower()})
    print(f"Initial setup took: {time.time() - t0:.2f}s")
    
    # 2. Let's see what it actually generated:
    for req_id, pd in signer._pending_requests.items():
        print(f"Pending: {req_id} -> {pd['headers']}")
    
    await signer.close()

if __name__ == "__main__":
    asyncio.run(test_speed())
