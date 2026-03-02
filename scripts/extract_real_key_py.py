import asyncio
from playwright.async_api import async_playwright
import json
import time

async def main():
    try:
        async with async_playwright() as p:
            print("Launching browser...")
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            
            last_headers = None
            
            async def handle_request(request):
                nonlocal last_headers
                url = request.url
                if 'api.debank.com' in url:
                    headers = request.headers
                    if 'x-api-sign' in headers and 'x-api-nonce' in headers:
                        print(f"Captured signed request: {url}")
                        last_headers = {
                            "sign": headers["x-api-sign"],
                            "nonce": headers["x-api-nonce"],
                            "time": headers.get("x-api-time", ""),
                            "url": url,
                            "method": request.method,
                            "headers": headers
                        }
            
            page.on("request", handle_request)
            
            print("Navigating to DeBank...")
            await page.goto("https://debank.com/profile/0x463452c356322d463b84891ebda33daed274cb40")
            
            # Wait for some requests to complete
            await page.wait_for_timeout(5000)
            
            if last_headers:
                with open("tests/last_headers.json", "w") as f:
                    json.dump(last_headers, f, indent=2)
                print("Saved captured headers to tests/last_headers.json")
            else:
                print("Failed to capture signed requests")
                
            await browser.close()
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
