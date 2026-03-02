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
        print("Loading page completely...")
        try:
            await page.goto(f"https://debank.com/profile/{wallet.lower()}", wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_selector(".container", timeout=20000)
            await asyncio.sleep(3)
        except Exception as e:
            print("Selector wait failed", e)
        
        print("\nBenchmarking parallel request execution...")
        t0 = time.time()
        
        chains = ["eth", "bsc", "arb", "op", "matic", "avax", "ftm", "base", "linea", "scrl"]
        
        js_code = "() => {\n"
        for c in chains:
            url = f"https://api.debank.com/token/balance_list?user_addr={wallet.lower()}&chain={c}"
            js_code += f"""
                const xhr_{c} = new XMLHttpRequest();
                xhr_{c}.open('GET', '{url}');
                xhr_{c}.setRequestHeader('accept', 'application/json');
                xhr_{c}.setRequestHeader('source', 'web');
                xhr_{c}.send();
            """
        js_code += "}"
        await page.evaluate(js_code)
        
        while len(captured) < len(chains) and time.time() - t0 < 3.0:
            await asyncio.sleep(0.01)
            
        print(f"Captured {len(captured)}/{len(chains)} signatures in {time.time() - t0:.3f}s")
        if len(captured) > 0:
            print(f"Speed: {len(captured)/(time.time() - t0):.2f} sigs/sec")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(test_speed())
