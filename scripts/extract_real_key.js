const fs = require('fs');

async function main() {
    try {
        const { chromium } = require('playwright');
        const browser = await chromium.launch({ headless: true });
        const context = await browser.newContext();
        const page = await context.newPage();
        
        let foundSecret = null;
        let lastHeaders = null;
        
        // Intercept requests to get the final headers
        await page.route('**/api.debank.com/**', route => {
            const request = route.request();
            const headers = request.headers();
            const url = request.url();
            
            if (headers['x-api-sign'] && headers['x-api-nonce']) {
                console.log(`\nFound signed request to: ${url}`);
                console.log(`x-api-sign: ${headers['x-api-sign']}`);
                console.log(`x-api-nonce: ${headers['x-api-nonce']}`);
                console.log(`x-api-time: ${headers['x-api-time']}`);
                
                lastHeaders = {
                    sign: headers['x-api-sign'],
                    nonce: headers['x-api-nonce'],
                    time: headers['x-api-time'],
                    url: url,
                    method: request.method()
                };
            }
            
            route.continue();
        });
        
        console.log('Navigating to DeBank...');
        await page.goto('https://debank.com/profile/0x463452c356322d463b84891ebda33daed274cb40', { waitUntil: 'networkidle' });
        
        // Try to evaluate the signature function directly if it's exposed globally
        try {
            const webpackChunks = await page.evaluate(() => {
                // DeBank uses webpack, we can try to inspect the modules
                return Object.keys(window).filter(k => k.includes('webpack'));
            });
            console.log('Webpack chunks:', webpackChunks);
        } catch(e) {
            console.error('Error inspecting window:', e);
        }
        
        await browser.close();
        
        if (lastHeaders) {
            fs.writeFileSync('tests/last_headers.json', JSON.stringify(lastHeaders, null, 2));
            console.log('Saved headers for reverse engineering the secret locally');
        } else {
            console.log('Did not capture any signed requests');
        }
        
    } catch(e) {
        console.error(e);
    }
}

main();
