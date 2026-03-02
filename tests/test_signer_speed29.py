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
    
    print("Why did it stop signing completely?")
    
    # Check if we need to reload the page to get the VM to work again
    for attempt in range(3):
        print(f"\nAttempt {attempt+1}, page reload...")
        await signer._page.reload(wait_until="domcontentloaded")
        await asyncio.sleep(3)
        
        captured.clear()
        
        url = f"https://api.debank.com/token/balance_list?user_addr={wallet.lower()}&chain=eth"
        js_code = f"""
        () => {{
            const xhr = new XMLHttpRequest();
            xhr.open('GET', '{url}');
            xhr.setRequestHeader('accept', 'application/json');
            xhr.setRequestHeader('source', 'web');
            xhr.setRequestHeader('x-dummy', '1');
            xhr.send();
        }}
        """
        await signer._page.evaluate(js_code)
        await asyncio.sleep(1.0)
        
        print(f"Captured: {len(captured)}")
        
    await signer.close()

if __name__ == "__main__":
    asyncio.run(test_speed())
