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
        
        captured = {}
        
        async def route_handler(route):
            req = route.request
            if 'api.debank.com' in req.url and 'x-api-sign' in req.headers:
                captured[req.url] = req.headers
                await route.abort()
            else:
                await route.continue_()
                
        await page.route("**/*", route_handler)
        
        wallet = "0x463452C356322D463B84891eBDa33DAED274cB40"
        print("Loading page with domcontentloaded...")
        try:
            await page.goto(f"https://debank.com/profile/{wallet.lower()}", wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(8)
        except:
            pass
        
        print("\nBenchmarking raw JS injects...")
        t0 = time.time()
        
        chains = ["eth", "bsc", "arb", "op", "matic", "avax"]
        for c in chains:
            t1 = time.time()
            url = f"https://api.debank.com/token/balance_list?user_addr={wallet.lower()}&chain={c}"
            
            js = f"""
            () => {{
                const xhr = new XMLHttpRequest();
                xhr.open('GET', '{url}');
                xhr.setRequestHeader('accept', 'application/json');
                xhr.setRequestHeader('source', 'web');
                xhr.send();
            }}
            """
            await page.evaluate(js)
            
            while url not in captured and time.time() - t1 < 2.0:
                await asyncio.sleep(0.05)
                
            if url in captured:
                print(f"[{c}] Took: {time.time() - t1:.3f}s. Success")
            else:
                print(f"[{c}] Failed")

        print(f"Total time: {time.time() - t0:.2f}s")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(test_speed())
