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
    headers1 = await signer.sign_request("/portfolio/project_list", {"user_addr": wallet.lower()})
    
    if not headers1:
        print("Warmup failed!")
        await signer.close()
        return
        
    print(f"Signer warmed up! First sig: {headers1.get('x-api-sign')[:10]}...")
    
    # In DeBank's VM, if you send requests too fast, the interceptor might fail.
    # We must ensure playwright page evaluate finishes before creating a new one.
    
    signatures_to_generate = 10
    start_sign = time.time()
    
    success_count = 0
    for i in range(signatures_to_generate):
        # Must use DIFFERENT valid endpoints that the VM won't reject, or add sleep
        # because the VM caches signatures for the identical request within the same second
        
        start_req = time.time()
        
        # We simulate a tiny delay to ensure Playwright evaluates the script
        await asyncio.sleep(0.5) 
        
        chains = ["eth", "bsc", "arb", "op", "matic", "avax", "ftm", "base", "linea", "scrl"]
        
        headers = await signer.sign_request("/token/balance_list", {
            "user_addr": wallet.lower(),
            "chain": chains[i % len(chains)]
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
