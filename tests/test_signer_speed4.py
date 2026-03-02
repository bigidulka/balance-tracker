import asyncio
import time
import sys
import os

sys.path.insert(0, os.path.abspath('.'))
from tests.debank_playwright_signer import DeBankSigner

async def test_speed():
    signer = DeBankSigner()
    print("Initializing signer...")
    
    wallet = "0x463452C356322D463B84891eBDa33DAED274cB40"
    
    # Initialize properly
    await signer._init_browser()
    await asyncio.sleep(5) # Give it more time
    
    endpoints = [
        ("/token/balance_list", {"user_addr": wallet.lower(), "chain": "eth"}),
    ]
    
    print("Sending evaluation request directly to check if fetch works...")
    try:
        await signer._page.evaluate("""
            async () => {
                try {
                    console.log('Sending fetch...');
                    await fetch('https://api.debank.com/user/config?id=0x463452c356322d463b84891ebda33daed274cb40', {headers: {source: 'web'}});
                    console.log('Fetch done!');
                } catch(e) {
                    console.error('Fetch error:', e);
                }
            }
        """)
        print("Fetch injected!")
    except Exception as e:
        print(f"Error injecting fetch: {e}")
        
    await asyncio.sleep(5)
    
    await signer.close()

if __name__ == "__main__":
    asyncio.run(test_speed())
