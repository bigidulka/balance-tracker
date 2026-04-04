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
            captured[req.url] = req.headers
            await route.abort()
        else:
            await route.continue_()
            
    await signer._page.route("**/*", route_handler)
    
    real_chains = ["eth", "bsc", "arb", "op", "matic", "avax", "ftm", "base", "linea", "scrl", 
                   "mobm", "metis", "btt", "okt", "movr", "celo", "heco", "cro", "boba", "kcc"]
                   
    print("Testing sequential fast injection (wait for each)...")
    
    t0 = time.time()
    
    for c in real_chains:
        url = f"https://api.debank.com/token/balance_list?user_addr={wallet.lower()}&chain={c}"
        js_code = f"""
        () => {{
            const xhr = new XMLHttpRequest();
            xhr.open('GET', '{url}');
            xhr.setRequestHeader('accept', 'application/json');
            xhr.setRequestHeader('source', 'web');
            xhr.send();
        }}
        """
        await signer._page.evaluate(js_code)
        
        # Wait for this specific one to finish
        t_req = time.time()
        while url not in captured and time.time() - t_req < 1.0:
            await asyncio.sleep(0.01)
            
    print(f"Captured {len(captured)}/{len(real_chains)} signatures in {time.time() - t0:.3f}s")
    print(f"Effective Speed: {len(captured)/(time.time() - t0):.2f} sigs/sec")
    
    await signer.close()

if __name__ == "__main__":
    asyncio.run(test_speed())
