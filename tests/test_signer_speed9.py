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
                chain = qs.get('chain', ['unknown'])[0]
                
                captured_headers[chain] = req.headers
                if chain in events:
                    events[chain].set()
                    
        page.on("request", on_req)
        
        await page.goto("https://debank.com/profile/0x463452c356322d463b84891ebda33daed274cb40", wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(3000)
        
        # DeBank overrides fetch globally but ALSO limits identical or rapid API calls in its wrapper.
        # BUT we only care about getting a signature. We just need the headers.
        # Wait, if we use XHR instead of fetch, will it bypass the rate limit throttle inside the JS app?
        # Let's try sending 10 requests but with dummy cache parameters!
        
        print("Sequential signing speed with random query params:")
        start_seq = time.time()
        
        import random
        for i in range(10):
            t0 = time.time()
            c = str(i)
            events[c] = asyncio.Event()
            # Randomizing the query param tricks their internal throttle
            url = f"https://api.debank.com/token/balance_list?user_addr=0x463452c356322d463b84891ebda33daed274cb40&chain=eth&_cache={random.randint(1, 1000000)}"
            await page.evaluate(f"fetch('{url}').catch(e=>{{}})")
            try:
                # We need to extract the chain parameter we used
                import urllib.parse
                parsed = urllib.parse.urlparse(url)
                qs = urllib.parse.parse_qs(parsed.query)
                cache_val = qs.get('_cache', [''])[0]
                
                # Update event tracking for this specific cache value
                events[cache_val] = events[c]
                
                await asyncio.wait_for(events[cache_val].wait(), timeout=3.0)
                t1 = time.time()
                print(f"  Req {i}: {(t1-t0)*1000:.1f}ms")
            except Exception as e:
                print(f"  Req {i}: TIMEOUT")
                
        seq_time = time.time() - start_seq
        print(f"Total time: {seq_time:.2f}s\n")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(test_pure_speed())
