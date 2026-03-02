import asyncio
import time
import sys
import os

sys.path.insert(0, os.path.abspath('.'))
from tests.debank_playwright_signer import debank_signer

async def test_speed():
    print("Initializing signer...")
    start_init = time.time()
    # Forces initialization
    await debank_signer.sign_request("/ping", {})
    init_time = time.time() - start_init
    print(f"Initialization took {init_time:.2f} seconds")
    
    print("\nTesting signature generation speed...")
    wallet = "0x463452c356322d463b84891ebda33daed274cb40"
    
    signatures_to_generate = 50
    start_sign = time.time()
    
    for i in range(signatures_to_generate):
        # We just generate the signature, we don't make the actual HTTP request to DeBank
        # This tests purely the speed of our Playwright bridge
        await debank_signer.sign_request("/token/balance_list", {
            "user_addr": wallet,
            "chain": "eth",
            "dummy_cache_buster": str(i) # Ensure unique params
        })
        
        if (i+1) % 10 == 0:
            print(f"Generated {i+1} signatures...")
            
    total_sign_time = time.time() - start_sign
    avg_sign_time = total_sign_time / signatures_to_generate
    
    print(f"\nResults:")
    print(f"Total time for {signatures_to_generate} signatures: {total_sign_time:.2f}s")
    print(f"Average time per signature: {avg_sign_time:.4f}s")
    print(f"Signatures per second: {1/avg_sign_time:.2f}")
    
    await debank_signer.close()

if __name__ == "__main__":
    asyncio.run(test_speed())
