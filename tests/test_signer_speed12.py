import asyncio
import time
import sys
import os

sys.path.insert(0, os.path.abspath('.'))
from tests.debank_playwright_signer import DeBankSigner

async def test_speed():
    signer = DeBankSigner()
    await signer._init_browser()
    
    # Reload page to start fresh
    await signer._page.goto("https://debank.com/profile/0x463452c356322d463b84891ebda33daed274cb40", wait_until="domcontentloaded", timeout=15000)
    await signer._page.wait_for_timeout(4000)
    
    wallet = "0x463452C356322D463B84891eBDa33DAED274cB40"
    
    print("\nStarting pure XHR speed benchmark...")
    
    signatures_to_generate = 10
    start_sign = time.time()
    
    success_count = 0
    chains = ["eth", "bsc", "arb", "op", "matic", "avax", "ftm", "base", "linea", "scrl"]
    
    for i in range(signatures_to_generate):
        start_req = time.time()
        c = chains[i % len(chains)]
        
        # To bypass fetch cache, we use XHR and abort it before it hits the network!
        # By aborting using Playwright's `route.abort()`, the JS layer still generates the signature
        # before handing it over to the browser's network stack!
        
        # Actually DeBank's interceptor intercepts `fetch` only sometimes, and maybe ignores pure `XHR`?
        # Let's just use `fetch` but append a query param that they don't validate against, if any exist.
        # Wait, earlier we learned that modifying params breaks the signature if we use it for a REAL request,
        # but we are NOT using this signature for a real request! 
        # We want a signature for EXACTLY the real request.
        
        url = f"https://api.debank.com/token/balance_list?user_addr={wallet.lower()}&chain={c}"
        
        js = f"""
        new Promise((resolve) => {{
            fetch('{url}', {{headers: {{source: 'web', accept: 'application/json'}}}}).catch(e => console.log(e));
            setTimeout(resolve, 50);
        }})
        """
        
        event = asyncio.Event()
        req_id = signer._req_counter + 1
        signer._req_counter = req_id
        
        signer._pending_requests[req_id] = {
            'url': url,
            'event': event,
            'headers': None
        }
        
        await signer._page.evaluate(js)
        
        try:
            await asyncio.wait_for(event.wait(), timeout=1.0)
            headers = signer._pending_requests[req_id]['headers']
            if headers:
                success_count += 1
                req_time = time.time() - start_req
                print(f"[{i+1}/10] Signed {c} in {req_time:.3f}s -> {headers.get('x-api-sign')[:10]}...")
            else:
                print(f"[{i+1}/10] Failed (no headers)")
        except asyncio.TimeoutError:
            print(f"[{i+1}/10] Timeout")
            
        # VERY IMPORTANT: The DeBank fetch wrapper sets a lock/throttle. 
        # We need a small sleep between requests to let the throttle reset.
        await asyncio.sleep(0.5)
            
    total_sign_time = time.time() - start_sign
    avg_sign_time = total_sign_time / success_count if success_count > 0 else 0
    
    print(f"\nBenchmark Results:")
    print(f"Successful signatures: {success_count}/{signatures_to_generate}")
    print(f"Total time: {total_sign_time:.2f}s")
    if success_count > 0:
        print(f"Average time per signature: {avg_sign_time:.4f}s")
        print(f"Signatures per second: {1/avg_sign_time:.2f} req/s")
    
    await signer.close()

if __name__ == "__main__":
    asyncio.run(test_speed())
