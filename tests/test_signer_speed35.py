import asyncio
import time
import sys
import os

sys.path.insert(0, os.path.abspath('.'))
from playwright.async_api import async_playwright

# The reason evaluate is failing is probably because the DOM isn't fully ready
# or DeBank has anti-eval protections.

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
        
        print("Using window.__ggn = () => 'debank.com' ? ")
        # No need, we are just executing on the page context.
        
        await page.goto(f"https://debank.com/profile/{wallet.lower()}", wait_until="networkidle", timeout=30000)
        await asyncio.sleep(2)
        
        captured.clear()
        
        print("\nUsing exactly how DeBank calls fetch internally...")
        chains = ["eth", "bsc", "arb", "op", "matic"]
        
        for c in chains:
            url = f"https://api.debank.com/token/balance_list?user_addr={wallet.lower()}&chain={c}"
            
            # The app expects request to happen within its React context maybe?
            # Or it checks if it comes from an interaction?
            
            await page.evaluate(f"""
            () => {{
                var request = new Request('{url}', {{
                    method: 'GET',
                    headers: new Headers({{
                        'Accept': 'application/json, text/plain, */*',
                        'Source': 'web'
                    }})
                }});
                fetch(request).catch(console.error);
            }}
            """)
            await asyncio.sleep(0.5)
            
        print(f"Captured {len(captured)}/{len(chains)} custom signatures")
        print(f"Urls signed:")
        for url in captured:
            print(f"  {url}")
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(test_speed())
