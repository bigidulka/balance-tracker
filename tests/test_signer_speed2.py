import asyncio
import time
import sys
import os

sys.path.insert(0, os.path.abspath('.'))
from tests.debank_playwright_signer import DeBankSigner

async def test_speed():
    signer = DeBankSigner()
    print("Initializing signer...")
    start_init = time.time()
    await signer.sign_request("/portfolio/project_list", {"user_addr": "0x463452c356322d463b84891ebda33daed274cb40"})
    init_time = time.time() - start_init
    print(f"Initialization took {init_time:.2f} seconds")
    
    print("\nTesting signature generation speed (valid endpoints only)...")
    wallet = "0x463452c356322d463b84891ebda33daed274cb40"
    
    # We must use EXACTLY the endpoints DeBank's VM recognizes.
    # It seems adding random query parameters breaks their internal routing/validation.
    endpoints = [
        ("/token/balance_list", {"user_addr": wallet, "chain": "eth"}),
        ("/token/balance_list", {"user_addr": wallet, "chain": "bsc"}),
        ("/token/balance_list", {"user_addr": wallet, "chain": "arb"}),
        ("/token/balance_list", {"user_addr": wallet, "chain": "op"}),
        ("/portfolio/project_list", {"user_addr": wallet}),
    ]
    
    signatures_to_generate = len(endpoints)
    start_sign = time.time()
    
    success_count = 0
    for ep, params in endpoints:
        headers = await signer.sign_request(ep, params)
        if headers:
            success_count += 1
            print(f"Signed {ep} -> {headers.get('x-api-sign')[:10]}...")
        else:
            print(f"Failed {ep}")
            
    total_sign_time = time.time() - start_sign
    avg_sign_time = total_sign_time / signatures_to_generate if signatures_to_generate > 0 else 0
    
    print(f"\nResults:")
    print(f"Successful signatures: {success_count}/{signatures_to_generate}")
    print(f"Total time for {signatures_to_generate} signatures: {total_sign_time:.2f}s")
    if success_count > 0:
        print(f"Average time per signature: {avg_sign_time:.4f}s")
        print(f"Signatures per second: {1/avg_sign_time:.2f}")
    
    await signer.close()

if __name__ == "__main__":
    asyncio.run(test_speed())
