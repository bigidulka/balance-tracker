import asyncio
import json
import logging
import time
from typing import Dict, Any, Optional
from playwright.async_api import async_playwright
import urllib.parse

logger = logging.getLogger(__name__)

class DeBankSigner:
    """
    Programmatic Playwright-based signer for DeBank API.
    Runs a lightweight headless browser instance to generate valid signatures
    by utilizing DeBank's own obfuscated WASM/VM signing logic.
    """
    
    def __init__(self):
        self._browser = None
        self._page = None
        self._playwright = None
        self._lock = asyncio.Lock()
        self._pending_requests = {}
        self._req_counter = 0

    async def _init_browser(self):
        if self._browser is not None:
            return
            
        logger.info("Initializing DeBank headless signer engine...")
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=['--disable-dev-shm-usage', '--no-sandbox']
        )
        context = await self._browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        self._page = await context.new_page()
        
        # Intercept network requests to capture headers
        async def handle_request(request):
            headers = request.headers
            if 'api.debank.com' in request.url and 'x-api-sign' in headers:
                # Find which pending request this matches
                for req_id, data in list(self._pending_requests.items()):
                    if data['url'] in request.url:
                        data['headers'] = headers
                        data['event'].set()
                        break
        
        self._page.on("request", handle_request)
        
        try:
            # Navigate to a lightweight page on debank to load the VM
            await self._page.goto("https://debank.com/profile/0x463452c356322d463b84891ebda33daed274cb40", wait_until="domcontentloaded", timeout=15000)
            # Wait for VM initialization
            await self._page.wait_for_timeout(3000)
            logger.info("DeBank signer initialized successfully")
        except Exception as e:
            logger.error(f"Error initializing DeBank signer: {e}")

    async def sign_request(self, endpoint: str, params: Dict[str, str]) -> Dict[str, str]:
        """
        Dynamically signs a specific API request using DeBank's VM.
        """
        async with self._lock:
            await self._init_browser()
            
            self._req_counter += 1
            req_id = self._req_counter
            
            # Construct the target URL
            param_str = urllib.parse.urlencode(params)
            target_url = f"https://api.debank.com{endpoint}?{param_str}"
            
            # Setup the listener
            event = asyncio.Event()
            self._pending_requests[req_id] = {
                'url': target_url,
                'event': event,
                'headers': None
            }
            
            # Inject JS to perform the fetch. The page's monkeypatched fetch
            # will automatically sign it using the VM!
            js_code = f"""
            () => {{
                // Fire and forget, we just want the request interception to catch it
                fetch("{target_url}", {{
                    method: 'GET',
                    headers: {{
                        "accept": "application/json",
                        "source": "web"
                    }}
                }}).catch(e => console.log(e));
            }}
            """
            
            await self._page.evaluate(js_code)
            
            # Wait for the interceptor to catch the signed request
            try:
                await asyncio.wait_for(event.wait(), timeout=10.0)
                headers = self._pending_requests[req_id]['headers']
            except asyncio.TimeoutError:
                logger.warning(f"Timeout waiting for signature for {target_url}")
                headers = None
                
            # Cleanup
            if req_id in self._pending_requests:
                del self._pending_requests[req_id]
                
            if headers:
                return {
                    "accept": "application/json",
                    "origin": "https://debank.com",
                    "referer": "https://debank.com/",
                    "source": "web",
                    "user-agent": headers.get("user-agent", "Mozilla/5.0"),
                    "x-api-nonce": headers.get("x-api-nonce", ""),
                    "x-api-sign": headers.get("x-api-sign", ""),
                    "x-api-time": headers.get("x-api-time", ""),
                    "x-api-ts": headers.get("x-api-ts", ""),
                    "x-api-ver": headers.get("x-api-ver", "v2"),
                    "account": headers.get("account", "")
                }
            
            return {}

    async def close(self):
        """Clean up playwright resources."""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

# Global instance
debank_signer = DeBankSigner()

async def get_wallet_balance(wallet_address: str):
    import urllib.request
    import json
    
    signer = DeBankSigner()
    
    try:
        # Get portfolio balance
        headers = await signer.sign_request("/portfolio/project_list", {
            "user_addr": wallet_address.lower()
        })
        
        if not headers:
            print("Failed to generate signature")
            return
            
        url = f"https://api.debank.com/portfolio/project_list?user_addr={wallet_address.lower()}"
        req = urllib.request.Request(url, headers=headers)
        
        total_value = 0
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())
            if 'data' in data:
                print(f"Found {len(data['data'])} portfolio projects")
                for proj in data['data']:
                    val = proj.get('portfolio_item_list', [{}])[0].get('stats', {}).get('asset_usd_value', 0)
                    total_value += val
                    
        print(f"\n✅ Total approximate value for {wallet_address}: ${total_value:.2f}")
        
    except urllib.error.HTTPError as e:
        print(f"HTTP Error: {e.code} - {e.read().decode()}")
    finally:
        await signer.close()

if __name__ == "__main__":
    wallet = "0x463452C356322D463B84891eBDa33DAED274cB40"
    asyncio.run(get_wallet_balance(wallet))
