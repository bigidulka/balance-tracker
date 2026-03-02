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
    
    print("\nBenchmarking EXTREME MASS PARALLEL raw JS injects...")
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
    
    # Generate 100 requests (DeBank supports ~60 chains, let's just do 100 random endpoints)
    t0 = time.time()
    
    urls = []
    for i in range(100):
        urls.append(f"https://api.debank.com/token/balance_list?user_addr={wallet.lower()}&chain=chain_{i}")
        
    js_code = "() => {\n"
    for i, url in enumerate(urls):
        js_code += f"""
            const xhr_{i} = new XMLHttpRequest();
            xhr_{i}.open('GET', '{url}');
            xhr_{i}.setRequestHeader('accept', 'application/json');
            xhr_{i}.setRequestHeader('source', 'web');
            xhr_{i}.send();
        """
    js_code += "}"
    
    await signer._page.evaluate(js_code)
    
    while len(captured) < 100 and time.time() - t0 < 5.0:
        await asyncio.sleep(0.01)
        
    print(f"Captured {len(captured)}/100 signatures in {time.time() - t0:.3f}s")
    print(f"Speed: {len(captured)/(time.time() - t0):.2f} sigs/sec")
    
    await signer.close()

if __name__ == "__main__":
    asyncio.run(test_speed())
