import asyncio
import time
import sys
import os

sys.path.insert(0, os.path.abspath('.'))
from tests.debank_playwright_signer_v3 import DeBankSigner

async def test_speed():
    signer = DeBankSigner()
    await signer._init_browser()
    await asyncio.sleep(2)
    
    wallet = "0x463452C356322D463B84891eBDa33DAED274cB40"
    
    captured = {}
    
    async def route_handler(route):
        req = route.request
        if 'api.debank.com' in req.url and 'x-api-sign' in req.headers:
            captured[req.headers.get('x-dummy', '0')] = req.headers
            await route.abort()
        else:
            await route.continue_()
            
    await signer._page.route("**/*", route_handler)
    
    print("Testing sleep interval required to not get deduplicated...")
    
    t0 = time.time()
    
    for i in range(10):
        url = f"https://api.debank.com/token/balance_list?user_addr={wallet.lower()}&chain=eth"
        js_code = f"""
        () => {{
            const xhr = new XMLHttpRequest();
            xhr.open('GET', '{url}');
            xhr.setRequestHeader('accept', 'application/json');
            xhr.setRequestHeader('source', 'web');
            xhr.setRequestHeader('x-dummy', '{i}');
            xhr.send();
        }}
        """
        await signer._page.evaluate(js_code)
        
        # What is the minimum sleep required to get a new signature for the EXACT same URL?
        # Let's try 0.5 seconds
        await asyncio.sleep(0.5)
            
    await asyncio.sleep(1.0)
    print(f"Captured {len(captured)}/10 signatures in {time.time() - t0:.3f}s")
    
    await signer.close()

if __name__ == "__main__":
    asyncio.run(test_speed())
