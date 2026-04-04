import asyncio
import time
import sys
import os

sys.path.insert(0, os.path.abspath('.'))
from tests.debank_playwright_signer import DeBankSigner

# DeBank's page intercepts fetch requests and queues them. 
# If we do it too fast or wrongly, they fail.
# Let's fix the interceptor to use their global function or use the direct method

async def test_speed():
    signer = DeBankSigner()
    await signer._init_browser()
    
    # Reload page to start fresh
    await signer._page.goto("https://debank.com/profile/0x463452c356322d463b84891ebda33daed274cb40", wait_until="domcontentloaded", timeout=15000)
    await signer._page.wait_for_timeout(3000)
    
    wallet = "0x463452C356322D463B84891eBDa33DAED274cB40"
    
    print("\nStarting speed benchmark...")
    
    signatures_to_generate = 10
    start_sign = time.time()
    
    success_count = 0
    for i in range(signatures_to_generate):
        chains = ["eth", "bsc", "arb", "op", "matic", "avax", "ftm", "base", "linea", "scrl"]
        
        start_req = time.time()
        
        url = f"https://api.debank.com/token/balance_list?user_addr={wallet.lower()}&chain={chains[i % len(chains)]}"
        
        # We hook into the page's fetch to get the signature
        js = f"""
        new Promise((resolve) => {{
            const req = new XMLHttpRequest();
            req.open('GET', '{url}');
            req.setRequestHeader('source', 'web');
            req.setRequestHeader('accept', 'application/json');
            
            // Override the real open/send to capture headers? No, DeBank overrides fetch/XHR already.
            // Let's just use fetch and wait for it.
            
            fetch('{url}', {{headers: {{source: 'web', accept: 'application/json'}}}}).catch(e => console.log(e));
            
            // Wait for our python interceptor to catch it
            resolve(true);
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
            await asyncio.wait_for(event.wait(), timeout=2.0)
            headers = signer._pending_requests[req_id]['headers']
            if headers:
                success_count += 1
                req_time = time.time() - start_req
                print(f"[{i+1}/10] Signed {chains[i % len(chains)]} in {req_time:.3f}s -> {headers.get('x-api-sign')[:10]}...")
            else:
                print(f"[{i+1}/10] Failed (no headers)")
        except asyncio.TimeoutError:
            print(f"[{i+1}/10] Timeout")
            
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
