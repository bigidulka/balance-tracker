import asyncio
import time
import sys
import os

sys.path.insert(0, os.path.abspath('.'))
from playwright.async_api import async_playwright

async def test_speed():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        
        captured = []
        
        async def route_handler(route):
            req = route.request
            if 'api.debank.com' in req.url and 'x-api-sign' in req.headers:
                captured.append(req.url)
            await route.continue_()
                
        await page.route("**/*", route_handler)
        
        wallet = "0x463452C356322D463B84891eBDa33DAED274cB40"
        await page.goto(f"https://debank.com/profile/{wallet.lower()}", wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(4)
        
        print("\nInjecting via fetch instead of XHR...")
        
        captured.clear()
        chains = ["eth", "bsc", "arb", "op", "matic"]
        
        js_code = "() => {\n"
        for c in chains:
            url = f"https://api.debank.com/token/balance_list?user_addr={wallet.lower()}&chain={c}"
            js_code += f"""
                fetch('{url}', {{headers: {{source: 'web', accept: 'application/json'}}}}).catch(e => console.log(e));
            """
        js_code += "}"
        await page.evaluate(js_code)
        
        await asyncio.sleep(2)
        
        print(f"Captured {len(captured)}/{len(chains)} custom signatures")
        print(f"Urls signed:")
        for url in captured:
            print(f"  {url}")
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(test_speed())
