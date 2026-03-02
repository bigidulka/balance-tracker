import asyncio
import time
import sys
import os

sys.path.insert(0, os.path.abspath('.'))
from tests.debank_playwright_signer import DeBankSigner

async def test_speed():
    signer = DeBankSigner()
    wallet = "0x463452C356322D463B84891eBDa33DAED274cB40"
    
    print("Pre-warming signer...")
    await signer.sign_request("/portfolio/project_list", {"user_addr": wallet.lower()})
    print("Signer warmed up!")
    
    print("\nStarting speed benchmark for 10 signatures...")
    
    signatures_to_generate = 10
    start_sign = time.time()
    
    success_count = 0
    for i in range(signatures_to_generate):
        # Must use valid endpoints that the VM won't reject
        start_req = time.time()
        headers = await signer.sign_request("/token/balance_list", {
            "user_addr": wallet.lower(),
            "chain": "eth"
        })
        req_time = time.time() - start_req
        
        if headers:
            success_count += 1
            print(f"[{i+1}/10] Signed in {req_time:.3f}s -> sign: {headers.get('x-api-sign')[:10]}...")
        else:
            print(f"[{i+1}/10] Failed in {req_time:.3f}s")
            
    total_sign_time = time.time() - start_sign
    avg_sign_time = total_sign_time / signatures_to_generate if signatures_to_generate > 0 else 0
    
    print(f"\nBenchmark Results:")
    print(f"Successful signatures: {success_count}/{signatures_to_generate}")
    print(f"Total time for {signatures_to_generate} signatures: {total_sign_time:.2f}s")
    if success_count > 0:
        print(f"Average time per signature: {avg_sign_time:.4f}s")
        print(f"Signatures per second: {1/avg_sign_time:.2f} req/s")
        print(f"Theoretical signatures per hour: {int(3600 * (1/avg_sign_time))} req/hr")
    
    await signer.close()

if __name__ == "__main__":
    asyncio.run(test_speed())
