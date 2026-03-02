"""DeBank web scraper using Playwright to extract portfolio data from DeBank website."""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
try:
    from playwright.async_api import async_playwright
    from playwright.async_api._generated import Browser, Page
except ImportError:
    logging.warning("Playwright not available. Web scraping functionality will be disabled.")
    async_playwright = None
    Browser = None
    Page = None
import json

from app.schemas.balance import AssetSchema, AccountBalanceSchema, ServiceBalanceSchema


logger = logging.getLogger(__name__)


class DeBankScraper:
    """Web scraper for DeBank using Playwright to extract wallet data."""
    
    BASE_URL = "https://debank.com"
    
    def __init__(self):
        self.browser = None
        self._initialized = False
        self._lock = asyncio.Lock()

    async def initialize(self):
        """Initialize the browser instance."""
        if not async_playwright:
            raise RuntimeError("Playwright is not available. Please install it with 'pip install playwright'")
        
        async with self._lock:
            if not self._initialized:
                playwright = await async_playwright().start()
                self.browser = await playwright.chromium.launch(
                    headless=True,
                    args=[
                        '--no-sandbox',
                        '--disable-blink-features=AutomationControlled',
                        '--disable-dev-shm-usage',
                    ]
                )
                self._initialized = True

    async def scrape_wallet_balance(self, wallet_address: str) -> ServiceBalanceSchema:
        """Scrape wallet balance data from DeBank website."""
        if not self.browser:
            await self.initialize()
        
        page = None
        try:
            page = await self.browser.new_page()
            
            # Set realistic user agent
            await page.set_extra_http_headers({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            })
            
            # Navigate to the wallet page
            url = f"{self.BASE_URL}/profile/{wallet_address}"
            logger.info(f"Scraping DeBank wallet: {url}")
            
            # Try different wait strategies
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            
            # Wait for page content to load
            try:
                # Wait for the main content container
                await page.wait_for_selector('.container, .main-content, [data-testid="portfolio"], .token-list', timeout=15000)
                
                # Additional wait to ensure data has loaded
                await page.wait_for_timeout(2000)
            except:
                # If standard selectors don't work, try general page content
                logger.info("Using alternative selector strategy")
                await page.wait_for_timeout(3000)
            
            # Check if page shows error/warning
            page_content = await page.text_content()
            if "not found" in page_content.lower() or "404" in page_content.lower() or "no data" in page_content.lower():
                logger.warning(f"Wallet {wallet_address} may not exist or have no data")
                # Return empty portfolio instead of raising error
                service_name = f"debank_scraper_{wallet_address[:8].lower()}"
                
                account = AccountBalanceSchema(
                    account_type="spot",
                    assets=[],
                    total_usd=0.0,
                )

                return ServiceBalanceSchema(
                    service=service_name,
                    accounts=[],
                    assets=[],
                    total_usd=0.0,
                    updated_at=datetime.now(timezone.utc),
                    actual=True,
                )
            
            # Extract data using JavaScript evaluation
            wallet_data = await page.evaluate("""
                () => {
                    // Look for portfolio data in the page
                    const portfolioItems = [];
                    
                    // Try multiple selectors to find portfolio data
                    const selectors = [
                        '[data-testid="portfolio"] [data-testid*="token"]',
                        '.token-item',
                        '[class*="token"][class*="item"]',
                        '[data-testid*="balance"]',
                        '.balance-container [data-testid]',
                        '.asset-card',
                        '.token-card'
                    ];
                    
                    for (const selector of selectors) {
                        const elements = document.querySelectorAll(selector);
                        if (elements.length > 0) {
                            for (const elem of elements) {
                                const symbolEl = elem.querySelector('[data-testid*="symbol"]') || 
                                               elem.querySelector('.token-symbol') || 
                                               elem.querySelector('.symbol') || 
                                               elem.querySelector('.name');
                                const amountEl = elem.querySelector('[data-testid*="amount"]') || 
                                               elem.querySelector('.token-amount') || 
                                               elem.querySelector('.amount');
                                const usdEl = elem.querySelector('[data-testid*="usd"]') || 
                                            elem.querySelector('.token-usd-value') || 
                                            elem.querySelector('.usd-value') ||
                                            elem.querySelector('.usd'); // Common class name
                
                                if (symbolEl && amountEl && usdEl) {
                                    const amountText = amountEl.textContent?.replace(/,/g, '')?.trim();
                                    const usdText = usdEl.textContent?.replace(/[,$€¥£]/g, '')?.trim();
                                    
                                    if (amountText && usdText) {
                                        const amount = parseFloat(amountText) || 0;
                                        const usdValue = parseFloat(usdText) || 0;
                                        
                                        if (amount > 0 || usdValue > 0) {
                                            portfolioItems.push({
                                                symbol: symbolEl.textContent?.trim() || 'UNKNOWN',
                                                amount: amount,
                                                usd_value: usdValue,
                                                chain: elem.getAttribute('data-chain') || 
                                                       elem.getAttribute('data-network') || 
                                                       elem.getAttribute('data-testid')?.split('-')[0] || 
                                                       'unknown'
                                            });
                                        }
                                    }
                                }
                            }
                            break; // Found data, don't try other selectors
                        }
                    }
                    
                    // Alternative: look for script tags with JSON data or window object
                    let jsonData = null;
                    const scripts = document.querySelectorAll('script[type="application/json"], script:not([src])');
                    for (const script of scripts) {
                        try {
                            const content = script.textContent.trim();
                            if (content && content.startsWith('{') && content.endsWith('}')) {
                                jsonData = JSON.parse(content);
                                break;
                            }
                        } catch (e) {
                            continue;
                        }
                    }
                    
                    // Try to access common global variables used by SPAs
                    try {
                        if (typeof window !== 'undefined' && window.hasOwnProperty('__APOLLO_STATE__')) {
                            jsonData = { ...jsonData, apollo: window.__APOLLO_STATE__ };
                        }
                        if (typeof window !== 'undefined' && window.hasOwnProperty('initialState')) {
                            jsonData = { ...jsonData, initialState: window.initialState };
                        }
                        if (typeof window !== 'undefined' && window.hasOwnProperty('preloadedState')) {
                            jsonData = { ...jsonData, preloadedState: window.preloadedState };
                        }
                    } catch (e) {
                        // Ignore errors accessing window properties
                    }
                    
                    return {
                        portfolio_items: portfolioItems,
                        json_data: jsonData,
                        title: document.title,
                        url: window.location.href,
                        timestamp: Date.now(),
                        page_html: document.documentElement.outerHTML.substring(0, 1000) // First 1000 chars for debugging
                    };
                }
            """)
            
            # Parse the scraped data
            assets: List[AssetSchema] = []
            total_usd = 0.0
            
            # Process portfolio items from DOM
            for item in wallet_data.get('portfolio_items', []):
                if item.get('amount', 0) > 0 or item.get('usd_value', 0) > 0:
                    asset = AssetSchema(
                        coin=f"{item.get('symbol', 'UNKNOWN')}_{item.get('chain', 'unknown')}",
                        amount=item.get('amount', 0),
                        value_usd=item.get('usd_value', 0)
                    )
                    assets.append(asset)
                    total_usd += item.get('usd_value', 0)
            
            # If no items found in DOM, try parsing from JSON data if it contains asset info
            if not assets and wallet_data.get('json_data'):
                json_data = wallet_data['json_data']
                if isinstance(json_data, dict):
                    # Look for nested objects that might contain token/portfolio data
                    def extract_assets_recursive(obj, parent_key=""):
                        extracted = []
                        if isinstance(obj, dict):
                            for key, value in obj.items():
                                full_key = f"{parent_key}.{key}" if parent_key else key
                                if isinstance(value, list):
                                    # Check if this looks like a token list
                                    if any(isinstance(item, dict) and 
                                          ('symbol' in item or 'token' in item or 'amount' in item or 'balance' in item or 'usd_value' in item)
                                          for item in value):
                                        for item in value:
                                            if isinstance(item, dict):
                                                symbol = item.get('symbol') or item.get('token') or item.get('name') or 'UNKNOWN'
                                                amount = item.get('amount') or (item.get('balance', {}).get('amount') if isinstance(item.get('balance'), dict) else 0) or 0
                                                usd_value = item.get('usd_value') or item.get('balance', {}).get('usd_value') or 0
                                                
                                                if amount > 0 or usd_value > 0:
                                                    extracted.append({
                                                        'symbol': symbol,
                                                        'amount': amount,
                                                        'usd_value': usd_value,
                                                        'chain': item.get('chain', item.get('network', key.split('.')[-1] if '.' in full_key else 'unknown'))
                                                    })
                                elif isinstance(value, dict):
                                    extracted.extend(extract_assets_recursive(value, full_key))
                        return extracted
                    
                    # Try to extract assets from JSON
                    extracted_assets = extract_assets_recursive(json_data)
                    for item in extracted_assets:
                        asset = AssetSchema(
                            coin=f"{item.get('symbol', 'UNKNOWN')}_{item.get('chain', 'unknown')}",
                            amount=item.get('amount', 0),
                            value_usd=item.get('usd_value', 0)
                        )
                        assets.append(asset)
                        total_usd += item.get('usd_value', 0)
            
            service_name = f"debank_scraper_{wallet_address[:8].lower()}"
            
            account = AccountBalanceSchema(
                account_type="spot",
                assets=assets,
                total_usd=total_usd,
            )

            return ServiceBalanceSchema(
                service=service_name,
                accounts=[account] if assets else [],
                assets=assets,
                total_usd=total_usd,
                updated_at=datetime.now(timezone.utc),
                actual=True,
            )
            
        except Exception as e:
            logger.error(f"Error scraping DeBank wallet {wallet_address}: {e}")
            # Return empty portfolio instead of raising error to allow graceful degradation
            service_name = f"debank_scraper_{wallet_address[:8].lower()}_error"
            
            account = AccountBalanceSchema(
                account_type="spot",
                assets=[],
                total_usd=0.0,
            )

            return ServiceBalanceSchema(
                service=service_name,
                accounts=[],
                assets=[],
                total_usd=0.0,
                updated_at=datetime.now(timezone.utc),
                actual=True,
            )
        finally:
            if page:
                await page.close()

    async def get_wallet_summary(self, wallet_address: str) -> Dict[str, Any]:
        """Get a summary of the wallet including overall stats."""
        if not self.browser:
            await self.initialize()
        
        page = None
        try:
            page = await self.browser.new_page()
            await page.set_extra_http_headers({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            })
            
            url = f"{self.BASE_URL}/profile/{wallet_address}"
            await page.goto(url, wait_until="networkidle", timeout=30000)
            
            # Extract summary information
            summary_data = await page.evaluate("""
                () => {
                    // Look for portfolio value elements
                    const totalPortfolioElements = [
                        ...document.querySelectorAll('[data-testid*="portfolio-total"], .portfolio-total, .total-value, [class*="portfolio"] .value, [data-testid*="balance"]'),
                    ];
                    
                    let portfolioValue = 0;
                    for (const el of totalPortfolioElements) {
                        const text = el.textContent;
                        if (text) {
                            const match = text.match(/[\$€¥₹£]\s*([0-9,]+\.?[0-9]*)|([0-9,]+\.?[0-9]*)\s*[\$€¥₹£]/);
                            if (match) {
                                portfolioValue = parseFloat(match[1] || match[2] || '0');
                                break;
                            }
                        }
                    }
                    
                    // Look for chain and protocol information
                    const chains = [...document.querySelectorAll('[data-testid*="chain"], .chain-label, .network-tag, [data-chain]')]
                        .map(el => el.textContent?.trim() || el.getAttribute('data-chain'))
                        .filter(Boolean);
                    
                    // Count tokens
                    const tokenElements = document.querySelectorAll('[data-testid*="token-item"]');
                    
                    return {
                        portfolio_value: portfolioValue > 0 ? portfolioValue : 0,
                        chains: [...new Set(chains.filter(c => c && c.toLowerCase() !== 'unknown'))],
                        token_count: tokenElements.length,
                        title: document.title,
                        url: window.location.href,
                        timestamp: Date.now()
                    };
                }
            """)
            
            return summary_data
            
        except Exception as e:
            logger.error(f"Error getting DeBank summary for {wallet_address}: {e}")
            raise
        finally:
            if page:
                await page.close()

    async def close(self):
        """Close the browser instance."""
        if self.browser:
            await self.browser.close()
            self.browser = None


debank_scraper = DeBankScraper()