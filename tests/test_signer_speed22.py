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
                   
    print("Testing chunked parallel injection (batches of 5)...")
    
    t0 = time.time()
    batch_size = 5
    
    for i in range(0, len(real_chains), batch_size):
        batch = real_chains[i:i+batch_size]
        js_code = "() => {\n"
        for c in batch:
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
        
        # Wait a tiny bit for the batch to finish
        t_batch = time.time()
        while len(captured) < i + len(batch) and time.time() - t_batch < 1.0:
            await asyncio.sleep(0.01)
            
    print(f"Captured {len(captured)}/{len(real_chains)} signatures in {time.time() - t0:.3f}s")
    print(f"Effective Speed: {len(captured)/(time.time() - t0):.2f} sigs/sec")
    
    await signer.close()

if __name__ == "__main__":
    asyncio.run(test_speed())
