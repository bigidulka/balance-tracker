import asyncio
import time
import sys
import os

sys.path.insert(0, os.path.abspath('.'))
from playwright.async_api import async_playwright

async def test_pure_speed():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        captured_headers = {}
        events = {}
        
        async def handle_route(route):
            req = route.request
            if 'api.debank.com' in req.url and 'x-api-sign' in req.headers:
                import urllib.parse
                parsed = urllib.parse.urlparse(req.url)
                qs = urllib.parse.parse_qs(parsed.query)
                cache_val = qs.get('_test', [''])[0]
                
                captured_headers[cache_val] = req.headers
                if cache_val in events:
                    events[cache_val].set()
            
            await route.continue_()
            
        await page.route("**/*", handle_route)
        
        # WE CANNOT ADD RANDOM QUERY PARAMS - THE VM COMPUTES A SIGNATURE FOR EXACT MATCHING URLS ONLY
        # The VM regexes the path. If the path doesn't match their expected ones, it fails internally.
        # "M.recordedWasm=!0; k.eN({message:"getSignature Failed", ...})"
        
        # This is why our random strings fail. DeBank explicitly whitelists endpoints that need signatures.
        
        await page.goto("https://debank.com/profile/0x463452c356322d463b84891ebda33daed274cb40", wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(3000)
        
        print("Sequential signing speed WITH KNOWN ENDPOINTS:")
        
        chains = ["eth", "bsc", "arb", "op", "matic", "avax", "ftm", "base", "linea", "scrl"]
        start_seq = time.time()
        
        for c in chains:
            t0 = time.time()
            events[c] = asyncio.Event()
            url = f"https://api.debank.com/token/balance_list?user_addr=0x463452c356322d463b84891ebda33daed274cb40&chain={c}"
            
            # Use XHR directly to bypass DeBank's fetch deduplication throttle!
            await page.evaluate(f"""
            () => {{
                var xhr = new XMLHttpRequest();
                xhr.open('GET', '{url}');
                xhr.setRequestHeader('accept', 'application/json');
                xhr.send();
            }}
            """)
            
            try:
                await asyncio.wait_for(events[c].wait(), timeout=1.0)
                t1 = time.time()
                print(f"  {c}: {(t1-t0)*1000:.1f}ms")
            except Exception as e:
                print(f"  {c}: TIMEOUT")
                
        seq_time = time.time() - start_seq
        print(f"Total time: {seq_time:.2f}s\n")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(test_pure_speed())
