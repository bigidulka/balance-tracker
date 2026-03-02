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
    
    print("\nBenchmarking raw JS injects...")
    wallet = "0x463452C356322D463B84891eBDa33DAED274cB40"
    
    start_total = time.time()
    
    # We will use the 'route' interceptor which guarantees we capture ALL network activity reliably
    captured = {}
    
    async def route_handler(route):
        req = route.request
        if 'api.debank.com' in req.url and 'x-api-sign' in req.headers:
            captured[req.url] = req.headers
            await route.abort() # Don't actually hit the network!
        else:
            await route.continue_()
            
    await signer._page.route("**/*", route_handler)
    
    chains = ["eth", "bsc", "arb", "op", "matic", "avax"]
    for c in chains:
        t0 = time.time()
        url = f"https://api.debank.com/token/balance_list?user_addr={wallet.lower()}&chain={c}"
        
        # Inject raw XMLHTTPRequest directly bypassing fetch throttle
        js = f"""
        () => {{
            const xhr = new XMLHttpRequest();
            xhr.open('GET', '{url}');
            xhr.setRequestHeader('accept', 'application/json');
            xhr.setRequestHeader('source', 'web');
            xhr.send();
        }}
        """
        await signer._page.evaluate(js)
        
        # Wait until it appears in captured
        while url not in captured and time.time() - t0 < 2.0:
            await asyncio.sleep(0.1)
            
        print(f"[{c}] Took: {time.time() - t0:.3f}s. Success: {url in captured}")

    print(f"Total time: {time.time() - start_total:.2f}s")
    await signer.close()

if __name__ == "__main__":
    asyncio.run(test_speed())
