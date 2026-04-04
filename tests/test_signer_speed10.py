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
        
        async def on_req(req):
            if 'api.debank.com' in req.url and 'x-api-sign' in req.headers:
                import urllib.parse
                parsed = urllib.parse.urlparse(req.url)
                qs = urllib.parse.parse_qs(parsed.query)
                cache_val = qs.get('_test', [''])[0]
                
                captured_headers[cache_val] = req.headers
                if cache_val in events:
                    events[cache_val].set()
                    
                # ABORT the request so we don't actually hit DeBank's API and get 429 IP banned
                # We can't abort here because it's a listener, not a route interceptor.
                # But it's fine for the benchmark.
                
        # To abort requests and ONLY get signatures, we use page.route
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
            
            # Continue normally for non-api, abort API calls so we don't spam their server
            if 'api.debank.com' in req.url:
                await route.abort()
            else:
                await route.continue_()
                
        await page.route("**/*", handle_route)
        
        await page.goto("https://debank.com/profile/0x463452c356322d463b84891ebda33daed274cb40", wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(3000)
        
        print("Sequential signing speed WITH ROUTE ABORT:")
        start_seq = time.time()
        
        for i in range(10):
            t0 = time.time()
            c = str(i)
            events[c] = asyncio.Event()
            url = f"https://api.debank.com/token/balance_list?user_addr=0x463452c356322d463b84891ebda33daed274cb40&chain=eth&_test={c}"
            
            # The app overrides fetch, but if we aborted it, the promise rejects
            await page.evaluate(f"fetch('{url}').catch(e=>{{}})")
            
            try:
                await asyncio.wait_for(events[c].wait(), timeout=1.0)
                t1 = time.time()
                print(f"  Req {i}: {(t1-t0)*1000:.1f}ms")
            except Exception as e:
                print(f"  Req {i}: TIMEOUT")
                
        seq_time = time.time() - start_seq
        print(f"Total time: {seq_time:.2f}s\n")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(test_pure_speed())
