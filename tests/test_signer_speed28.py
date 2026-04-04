import asyncio
import time
import sys
import os

sys.path.insert(0, os.path.abspath('.'))
from tests.debank_playwright_signer_v3 import DeBankSigner

# It seems that NO MATTER WHAT, DeBank's VM only signs exactly 1 request per ~1 second!
# That's why mass injection fails to sign them all.
# Let's verify the exact rate limit of the WASM VM!

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
    
    print("Testing max signatures per second of the VM itself...")
    
    for delay in [1.0, 0.5, 0.3, 0.1]:
        print(f"\nTesting {delay}s delay between XHR injections:")
        captured.clear()
        t0 = time.time()
        
        for i in range(5):
            url = f"https://api.debank.com/token/balance_list?user_addr={wallet.lower()}&chain=chain_{i}"
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
            await asyncio.sleep(delay)
                
        await asyncio.sleep(0.5)
        print(f"Captured {len(captured)}/5 signatures using {delay}s delay")
        
    await signer.close()

if __name__ == "__main__":
    asyncio.run(test_speed())
