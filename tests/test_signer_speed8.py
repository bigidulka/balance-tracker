import asyncio
import time
import sys
import os

sys.path.insert(0, os.path.abspath('.'))
from playwright.async_api import async_playwright

# A pure, stripped down speed test for the interceptor methodology
async def test_pure_speed():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        captured_headers = {}
        events = {}
        
        async def on_req(req):
            if 'api.debank.com' in req.url and 'x-api-sign' in req.headers:
                # Extract chain from URL to identify it
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
        
        chains = ["eth", "bsc", "arb", "op", "matic", "avax", "ftm", "base", "linea", "scrl"]
        
        # Test sequential
        print("Sequential signing speed:")
        start_seq = time.time()
        for c in chains:
            t0 = time.time()
            events[c] = asyncio.Event()
            await page.evaluate(f"fetch('https://api.debank.com/token/balance_list?user_addr=0x463452c356322d463b84891ebda33daed274cb40&chain={c}').catch(e=>{{}})")
            try:
                await asyncio.wait_for(events[c].wait(), timeout=5.0)
                t1 = time.time()
                print(f"  {c}: {(t1-t0)*1000:.1f}ms")
            except:
                print(f"  {c}: TIMEOUT")
                
        seq_time = time.time() - start_seq
        print(f"Total sequential: {seq_time:.2f}s ({len(chains)/seq_time:.2f} req/s)\n")
        
        # Test parallel!
        print("Parallel signing speed:")
        captured_headers.clear()
        for c in chains:
            events[c] = asyncio.Event()
            
        start_par = time.time()
        
        # Inject all at once
        js_code = "\n".join([f"fetch('https://api.debank.com/token/balance_list?user_addr=0x463452c356322d463b84891ebda33daed274cb40&chain={c}').catch(e=>{{}});" for c in chains])
        await page.evaluate(f"() => {{ {js_code} }}")
        
        # Wait for all events
        await asyncio.gather(*[asyncio.wait_for(e.wait(), timeout=5.0) for e in events.values()], return_exceptions=True)
        
        par_time = time.time() - start_par
        success = len([c for c in chains if c in captured_headers])
        print(f"Total parallel: {par_time:.2f}s for {success} sigs ({success/par_time:.2f} req/s)")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(test_pure_speed())
