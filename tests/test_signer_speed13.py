import asyncio
import time
import sys
import os

sys.path.insert(0, os.path.abspath('.'))
from tests.debank_playwright_signer import DeBankSigner

async def test_speed():
    signer = DeBankSigner()
    await signer._init_browser()
    await asyncio.sleep(5)
    
    wallet = "0x463452C356322D463B84891eBDa33DAED274cB40"
    
    print("\nReal signature generation benchmark...")
    
    # Let's use portfolio endpoints which seem more reliable
    endpoints = [
        f"/token/balance_list?user_addr={wallet.lower()}&chain=eth",
        f"/portfolio/project_list?user_addr={wallet.lower()}"
    ]
    
    for url in endpoints:
        start = time.time()
        js = f"""
        () => {{
            fetch('https://api.debank.com{url}', {{
                method: 'GET',
                headers: {{"accept": "application/json", "source": "web"}}
            }}).catch(e => console.log(e));
        }}
        """
        
        req_id = signer._req_counter + 1
        signer._req_counter = req_id
        event = asyncio.Event()
        
        signer._pending_requests[req_id] = {
            'url': url,
            'event': event,
            'headers': None
        }
        
        await signer._page.evaluate(js)
        
        try:
            await asyncio.wait_for(event.wait(), timeout=10.0)
            headers = signer._pending_requests[req_id]['headers']
            if headers:
                print(f"Success for {url}: {time.time() - start:.2f}s")
            else:
                print(f"Failed for {url}")
        except:
            print(f"Timeout for {url}")
            
        await asyncio.sleep(1)

    await signer.close()

if __name__ == "__main__":
    asyncio.run(test_speed())
