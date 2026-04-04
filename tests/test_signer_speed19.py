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
    
    print("\nBenchmarking PARALLEL raw JS injects...")
    wallet = "0x463452C356322D463B84891eBDa33DAED274cB40"
    
    start_total = time.time()
    
    captured = {}
    
    async def route_handler(route):
        req = route.request
        if 'api.debank.com' in req.url and 'x-api-sign' in req.headers:
            captured[req.url] = req.headers
            await route.abort()
        else:
            await route.continue_()
            
    await signer._page.route("**/*", route_handler)
    
    chains = ["eth", "bsc", "arb", "op", "matic", "avax", "ftm", "base", "linea", "scrl", "mobm", "metis"]
    
    t0 = time.time()
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
    
    await signer._page.evaluate(js_code)
    
    while len(captured) < len(chains) and time.time() - t0 < 3.0:
        await asyncio.sleep(0.01)
        
    print(f"Captured {len(captured)}/{len(chains)} signatures in {time.time() - t0:.3f}s")
    print(f"Speed: {len(captured)/(time.time() - t0):.2f} sigs/sec")
    
    print(f"Total time: {time.time() - start_total:.2f}s")
    await signer.close()

if __name__ == "__main__":
    asyncio.run(test_speed())
