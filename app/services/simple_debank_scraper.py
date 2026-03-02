"""Simple DeBank web scraper using Playwright to extract portfolio data from DeBank website."""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from playwright.async_api import async_playwright

from app.schemas.balance import AssetSchema, AccountBalanceSchema, ServiceBalanceSchema


logger = logging.getLogger(__name__)


class SimpleDeBankScraper:
    """Simple web scraper for DeBank using Playwright to extract wallet data."""
    
    BASE_URL = "https://debank.com"
    
    def __init__(self):
        self.browser = None
        self._initialized = False
        self._lock = asyncio.Lock()

    async def initialize(self):
        """Initialize the browser instance."""
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
                    
                    return {
                        portfolio_items: portfolioItems,
                        title: document.title,
                        url: window.location.href,
                        timestamp: Date.now()
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

    async def close(self):
        """Close the browser instance."""
        if self.browser:
            await self.browser.close()
            self.browser = None


simple_debank_scraper = SimpleDeBankScraper()