import asyncio
from playwright.async_api import async_playwright

async def intercept_vm_hssss():
    try:
        async with async_playwright() as p:
            print("Launching browser...")
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            # DeBank loads the VM and exposes it, but it's hidden inside webpack.
            # We can use Playwright's CDP session to hook the function!
            # Or simpler: inject a script that intercepts the `createHmac` or the specific `u` function.
            
            # Since `u` does `const r=f(e),n=f(t)`, and `f(e)` uses `new TextEncoder().encode(e)`
            # We already intercepted TextEncoder but the secret wasn't there?
            # Wait, the secret in our python test was:
            # target_sign = "cbcf0eca9d798cc3e67bd1201a0c94d94196a38fcc033559feecec5d641016f2"
            # Which was from our HAR file!
            
            # Let's just mock the `hssss` or `crypto` methods directly in the page!
            await page.add_init_script("""
            window._cryptoCalls = [];
            
            // Wait for webpack to load
            let originalCall = Function.prototype.call;
            Function.prototype.call = function(...args) {
                // Try to catch the hssss function
                if (this.name === 'hssss' || (args[0] && args[0].name === 'hssss')) {
                    window._cryptoCalls.push({type: 'hssss', args: [...args].map(a => typeof a === 'string' ? a : 'obj')});
                }
                return originalCall.apply(this, args);
            };
            """)
            
            await page.goto("https://debank.com/profile/0x463452c356322d463b84891ebda33daed274cb40", wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(8000)
            
            calls = await page.evaluate("window._cryptoCalls")
            print(f"Captured {len(calls)} crypto calls.")
            for c in calls:
                print(c)
                
            await browser.close()
    except Exception as e:
        print(f"Error: {e}")

asyncio.run(intercept_vm_hssss())
