#!/usr/bin/env python3
"""DeBank Reverse Engineering Analysis using Playwright"""

import asyncio
import json
import logging
from datetime import datetime
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class DeBankReverseEngineer:
    """Class to perform reverse engineering of DeBank using Playwright"""
    
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.page = None
    
    async def setup(self):
        """Setup Playwright and browser"""
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=False,  # Set to False to see the browser for analysis
            args=[
                '--no-sandbox',
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
            ]
        )
        self.page = await self.browser.new_page()
        
        # Set realistic user agent
        await self.page.set_extra_http_headers({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
    
    async def analyze_page_structure(self, url: str):
        """Analyze the page structure and collect information"""
        logger.info(f"Analyzing page: {url}")
        await self.page.goto(url, wait_until="domcontentloaded")
        
        # Wait a bit for SPA to load
        await self.page.wait_for_timeout(5000)
        
        # Get page title and URL
        title = await self.page.title()
        current_url = self.page.url
        logger.info(f"Title: {title}")
        logger.info(f"URL: {current_url}")
        
        # Get all network requests to identify API calls
        network_requests = []
        self.page.on("request", lambda request: network_requests.append({
            'method': request.method,
            'url': request.url,
            'headers': dict(request.headers),
            'timestamp': datetime.now().isoformat()
        }))
        
        # Take a snapshot of the page
        await self.page.wait_for_timeout(3000)  # Wait for all requests to settle
        
        # Get HTML content
        html_content = await self.page.content()
        
        # Extract data attributes and classes that might be significant
        element_info = await self.page.evaluate("""
            () => {
                const elements = [];
                const allElements = document.querySelectorAll('*');
                
                for (const el of allElements) {
                    const attrs = {};
                    for (const attr of el.attributes) {
                        attrs[attr.name] = attr.value;
                    }
                    
                    if (Object.keys(attrs).length > 0 || el.tagName.includes('DIV') || el.classList.length > 0) {
                        elements.push({
                            tagName: el.tagName,
                            className: el.className,
                            attributes: attrs,
                            textContent: el.textContent.trim().substring(0, 100),
                            rect: el.getBoundingClientRect ? el.getBoundingClientRect() : null
                        });
                    }
                    
                    if (elements.length > 1000) break; // Limit to avoid huge data
                }
                
                // Also look for script tags that might contain app state
                const scripts = Array.from(document.querySelectorAll('script[type="application/json"], script:not([src])')).map(script => ({
                    type: script.type,
                    content: script.textContent.substring(0, 2000)
                }));
                
                // Get window properties that might contain app state
                const windowProps = [];
                for (const prop in window) {
                    if (prop.startsWith('__') && typeof window[prop] === 'object' && window[prop] !== null) {
                        try {
                            windowProps.push({
                                name: prop,
                                type: typeof window[prop],
                                hasData: !!window[prop]
                            });
                        } catch (e) {
                            // Skip if property access throws
                        }
                    }
                }
                
                return {
                    elements: elements,
                    scripts: scripts,
                    windowProps: windowProps,
                    localStorage: localStorage.length > 0 ? Array.from(Array(localStorage.length).keys()).map(i => ({key: localStorage.key(i), value: localStorage.getItem(localStorage.key(i)).substring(0, 200)})) : [],
                    sessionStorage: sessionStorage.length > 0 ? Array.from(Array(sessionStorage.length).keys()).map(i => ({key: sessionStorage.key(i), value: sessionStorage.getItem(sessionStorage.key(i)).substring(0, 200)})) : []
                };
            }
        """)
        
        return {
            'html': html_content,
            'element_info': element_info,
            'network_requests': network_requests,
            'url': current_url,
            'title': title
        }
    
    async def inspect_network_traffic(self, url: str):
        """Inspect network traffic to identify API endpoints"""
        logger.info(f"Monitoring network traffic for: {url}")
        
        # Clear previous requests
        network_requests = []
        failed_requests = []
        
        def on_request(request):
            network_requests.append({
                'method': request.method,
                'url': request.url,
                'resourceType': request.resource_type,
                'headers': dict(list(request.headers.items())[:5]),  # Limit headers
                'timestamp': datetime.now().isoformat()
            })
        
        def on_response(response):
            try:
                if response.status >= 400:
                    failed_requests.append({
                        'url': response.url,
                        'status': response.status,
                        'statusText': response.status_text
                    })
            except:
                pass
        
        def on_request_failed(request):
            failed_requests.append({
                'url': request.url,
                'failure': request.failure
            })
        
        self.page.on("request", on_request)
        self.page.on("response", on_response)
        self.page.on("requestfailed", on_request_failed)
        
        await self.page.goto(url, wait_until="networkidle")
        await self.page.wait_for_timeout(5000)  # Additional wait for all network activity
        
        return {
            'requests': network_requests,
            'failed_requests': failed_requests
        }
    
    async def analyze_api_patterns(self, wallet_address: str):
        """Analyze API patterns for a specific wallet"""
        logger.info(f"Analyzing API patterns for wallet: {wallet_address}")
        
        # Go to the wallet page
        url = f"https://debank.com/profile/{wallet_address}"
        analysis = await self.inspect_network_traffic(url)
        
        # Look for API calls related to the wallet
        api_calls = []
        for req in analysis['requests']:
            if 'api' in req['url'] or 'v1' in req['url'] or 'v2' in req['url'] or wallet_address.lower() in req['url']:
                api_calls.append(req)
        
        logger.info(f"Found {len(api_calls)} potential API calls")
        for call in api_calls:
            logger.info(f"  {call['method']} {call['url']}")
        
        return api_calls
    
    async def extract_javascript_state(self, url: str):
        """Extract JavaScript application state"""
        logger.info(f"Extracting JavaScript state from: {url}")
        await self.page.goto(url, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(3000)
        
        # Extract various JavaScript state variables
        js_state = await self.page.evaluate("""
            () => {
                const state = {};
                
                // Look for common React/Apollo state patterns
                if (typeof window.__APOLLO_STATE__ !== 'undefined') {
                    state.apollo = window.__APOLLO_STATE__;
                }
                
                if (typeof window.__NEXT_DATA__ !== 'undefined') {
                    state.nextjs = window.__NEXT_DATA__;
                }
                
                if (typeof window.__INITIAL_STATE__ !== 'undefined') {
                    state.redux = window.__INITIAL_STATE__;
                }
                
                if (typeof window.__PRELOADED_STATE__ !== 'undefined') {
                    state.preloaded = window.__PRELOADED_STATE__;
                }
                
                if (typeof window.APP_CONFIG !== 'undefined') {
                    state.appConfig = window.APP_CONFIG;
                }
                
                if (typeof window.__REDUX_DEVTOOLS_EXTENSION__ !== 'undefined') {
                    state.reduxDevTools = 'Available';
                }
                
                // Get all global object keys that might contain state
                const globals = [];
                for (const key in window) {
                    if (typeof window[key] === 'object' && window[key] !== null && !key.startsWith('_')) {
                        try {
                            if (Object.keys(window[key]).length > 0) {
                                globals.push({
                                    key: key,
                                    keys: Object.keys(window[key]),
                                    size: Object.keys(window[key]).length
                                });
                            }
                        } catch (e) {
                            // Skip if access throws
                        }
                    }
                }
                
                state.globalObjects = globals;
                
                return state;
            }
        """)
        
        return js_state
    
    async def run_full_analysis(self, wallet_address: str):
        """Run full analysis of DeBank for the given wallet"""
        logger.info("=" * 60)
        logger.info(f"DEBANK REVERSE ENGINEERING ANALYSIS")
        logger.info(f"WALLET: {wallet_address}")
        logger.info(f"TIME: {datetime.now()}")
        logger.info("=" * 60)
        
        await self.setup()
        
        try:
            # Step 1: Analyze page structure
            logger.info("\n1. ANALYZING PAGE STRUCTURE...")
            page_analysis = await self.analyze_page_structure(f"https://debank.com/profile/{wallet_address}")
            
            # Step 2: Inspect network traffic
            logger.info("\n2. INSPECTING NETWORK TRAFFIC...")
            network_analysis = await self.inspect_network_traffic(f"https://debank.com/profile/{wallet_address}")
            
            # Step 3: Analyze API patterns
            logger.info("\n3. ANALYZING API PATTERNS...")
            api_patterns = await self.analyze_api_patterns(wallet_address)
            
            # Step 4: Extract JavaScript state
            logger.info("\n4. EXTRACTING JAVASCRIPT STATE...")
            js_state = await self.extract_javascript_state(f"https://debank.com/profile/{wallet_address}")
            
            # Compile results
            results = {
                'wallet_address': wallet_address,
                'analysis_timestamp': datetime.now().isoformat(),
                'page_analysis': page_analysis,
                'network_analysis': network_analysis,
                'api_patterns': api_patterns,
                'javascript_state': js_state
            }
            
            # Save results to file
            output_file = f"debank_analysis_{wallet_address.replace('0x', '').lower()[:8]}.json"
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(results, f, indent=2, ensure_ascii=False, default=str)
            
            logger.info(f"\n5. RESULTS SAVED TO: {output_file}")
            
            # Print summary
            logger.info("\n6. SUMMARY:")
            logger.info(f"  - Total network requests: {len(network_analysis['requests'])}")
            logger.info(f"  - Failed requests: {len(network_analysis['failed_requests'])}")
            logger.info(f"  - Potential API calls: {len(api_patterns)}")
            logger.info(f"  - JavaScript state objects: {len(js_state)}")
            
            # Show interesting API calls
            if api_patterns:
                logger.info("\n  INTERESTING API CALLS:")
                for call in api_patterns[:5]:  # Show first 5
                    logger.info(f"    {call['method']} {call['url']}")
            
            return results
            
        finally:
            await self.cleanup()
    
    async def cleanup(self):
        """Clean up resources"""
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()


async def main():
    """Main function to run DeBank reverse engineering"""
    wallet_address = "0x463452C356322D463B84891eBDa33DAED274cB40"
    
    engineer = DeBankReverseEngineer()
    results = await engineer.run_full_analysis(wallet_address)
    
    print(f"\n🔍 REVERSE ENGINEERING COMPLETE!")
    print(f"Wallet analyzed: {wallet_address}")
    print(f"Analysis saved to: debank_analysis_{wallet_address.replace('0x', '').lower()[:8]}.json")


if __name__ == "__main__":
    asyncio.run(main())