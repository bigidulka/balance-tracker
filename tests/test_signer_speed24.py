import asyncio
import time
import sys
import os
import random

sys.path.insert(0, os.path.abspath('.'))
from tests.debank_playwright_signer_v3 import DeBankSigner

# It seems the VM rejects signatures if the path+params matches something it has already signed recently!
# Let's try adding a totally random fake parameter `_foo=rand` or `req_id=rand` to see if that works.
# Wait, we saw earlier that random parameters fail DeBank's REAL API... but do they fail the VM generator?

async def test_speed():
    signer = DeBankSigner()
    await signer._init_browser()
    await asyncio.sleep(2)
    
    wallet = "0x463452C356322D463B84891eBDa33DAED274cB40"
    
    captured = {}
    
    async def route_handler(route):
        req = route.request
        if 'api.debank.com' in req.url and 'x-api-sign' in req.headers:
            captured[req.url] = req.headers
            await route.abort()
        else:
            await route.continue_()
            
    await signer._page.route("**/*", route_handler)
    
    print("Testing sequential fast injection WITH CACHE BUSTERS on the SAME chain...")
    
    t0 = time.time()
    
    for i in range(20):
        # Even though _cache is ignored by DeBank API sometimes, the VM might sign it?
        url = f"https://api.debank.com/token/balance_list?user_addr={wallet.lower()}&chain=eth"
        
        # Or wait, what if we use the EXACT same url, but XHR doesn't get signed if it's identical?
        # NO, XHR doesn't deduplicate natively, but DeBank's interceptor does.
        # How to bypass DeBank's fetch deduplication?
        
        # DeBank's deduplication probably uses `URL + method` as key.
        js_code = f"""
        () => {{
            const xhr = new XMLHttpRequest();
            xhr.open('GET', '{url}');
            // Add unique header so it doesn't get cached? No.
            xhr.setRequestHeader('accept', 'application/json');
            xhr.setRequestHeader('source', 'web');
            xhr.setRequestHeader('X-Dummy', '{i}');
            xhr.send();
        }}
        """
        await signer._page.evaluate(js_code)
        
        # Wait for this specific one to finish? We can't identify by URL anymore.
        # But we can just wait a bit.
        await asyncio.sleep(0.05)
            
    await asyncio.sleep(1.0)
    print(f"Captured {len(captured)}/20 signatures in {time.time() - t0:.3f}s")
    
    await signer.close()

if __name__ == "__main__":
    asyncio.run(test_speed())
