import re
import urllib.request
import urllib.error
from urllib.parse import urljoin
import gzip
import io
import sys

def get_html(url):
    req = urllib.request.Request(
        url, 
        headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept-Language': 'en-US,en;q=0.9',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
            'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            'Sec-Ch-Ua-Mobile': '?0',
            'Sec-Ch-Ua-Platform': '"Windows"',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Upgrade-Insecure-Requests': '1'
        }
    )
    try:
        with urllib.request.urlopen(req) as response:
            encoding = response.info().get('Content-Encoding')
            if encoding == 'gzip':
                f = gzip.GzipFile(fileobj=io.BytesIO(response.read()))
                return f.read().decode('utf-8', errors='ignore')
            elif encoding == 'br':
                try:
                    import brotli
                    return brotli.decompress(response.read()).decode('utf-8', errors='ignore')
                except ImportError:
                    print("Brotli encoding detected but brotli package not installed. Try pip install brotli.")
                    return response.read().decode('utf-8', errors='ignore') # Best effort
            return response.read().decode('utf-8', errors='ignore')
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None

def extract_js_links(html):
    if not html:
        return []
    
    links = []
    
    # Simple regex for script src attributes
    matches = re.findall(r'<script[^>]*src=["\']([^"\']+\.js[^"\']*)["\']', html, re.IGNORECASE)
    links.extend(matches)
    
    # Also look for modulepreload links
    matches = re.findall(r'<link[^>]*rel=["\']modulepreload["\'][^>]*href=["\']([^"\']+\.js[^"\']*)["\']', html, re.IGNORECASE)
    links.extend(matches)
    
    return list(set(links))

def find_secret_in_js(js_content):
    if not js_content:
        return []
    
    found_secrets = set()
    
    # 1. Look specifically for the exact custom salt/secret logic DeBank uses
    # Often it's passed to hmac, SHA256, or used in a hashing function
    # We look for a 32-character hex string which is very common for DeBank's salt
    
    # Regex for 32 character lowercase hex strings
    hex_pattern = r'["\']([a-f0-9]{32})["\']'
    for match in re.finditer(hex_pattern, js_content):
        found_secrets.add(match.group(1))
        
    # DeBank specific patterns from previous analyses
    debank_patterns = [
        r'salt\s*[:=]\s*["\']([^"\']+)["\']',
        r'customKey\s*[:=]\s*["\']([^"\']+)["\']',
        r'signSecret\s*[:=]\s*["\']([^"\']+)["\']',
        r'sign\s*:\s*function.*?["\']([^"\']{20,40})["\']',
        r'hmac.*?["\']([^"\']{20,40})["\']',
        r'["\'](***REMOVED***)["\']', # Find the api key to orient
    ]
    
    for pattern in debank_patterns:
        matches = re.findall(pattern, js_content)
        for match in matches:
            if len(match) > 10:
                found_secrets.add(match)
                
    return list(found_secrets)

def analyze_context(js_content, secret):
    # Find surrounding context for a potential secret to verify it's the right one
    try:
        idx = js_content.index(secret)
        start = max(0, idx - 100)
        end = min(len(js_content), idx + 100)
        return js_content[start:end]
    except ValueError:
        return ""

def main():
    base_url = "https://debank.com"
    print(f"Fetching {base_url}...")
    html = get_html(base_url)
    
    if not html:
        print("Failed to get HTML from root. Trying portfolio page...")
        html = get_html("https://debank.com/profile/0x463452C356322D463B84891eBDa33DAED274cB40")
        if not html:
            return
            
    js_links = extract_js_links(html)
    print(f"Found {len(js_links)} JS files in HTML")
    
    all_potential_secrets = {}
    
    # Some common debank JS locations if not found in HTML
    if not js_links:
        print("No JS links found in HTML, trying default paths...")
        js_links = [
            "/static/js/main.js",
            "/_next/static/chunks/main.js",
            "/_next/static/chunks/pages/_app.js"
        ]
    
    for link in js_links:
        js_url = link if link.startswith('http') else urljoin(base_url, link)
        print(f"Scanning {js_url}...")
        
        js_content = get_html(js_url)
        if js_content:
            secrets = find_secret_in_js(js_content)
            if secrets:
                print(f"  Found {len(secrets)} potential secrets in this file")
                for secret in secrets:
                    if secret not in all_potential_secrets:
                        all_potential_secrets[secret] = []
                    all_potential_secrets[secret].append(js_url)
                    
                    # Print context for 32-char hex strings (most likely candidates)
                    if len(secret) == 32 and re.match(r'^[a-f0-9]+$', secret):
                        ctx = analyze_context(js_content, secret)
                        if 'hmac' in ctx.lower() or 'sign' in ctx.lower() or 'hash' in ctx.lower():
                            print(f"\nSTRONG CANDIDATE: {secret}")
                            print(f"Context: ...{ctx}...\n")
    
    print("\n--- Summary of Potential Secrets (32 char hex) ---")
    candidates = []
    for secret, urls in all_potential_secrets.items():
        if len(secret) == 32 and re.match(r'^[a-f0-9]+$', secret):
            candidates.append(secret)
            print(f"Secret: {secret} (Found in {len(urls)} files)")
            
    print("\n--- Testing the candidates ---")
    # Write a quick test script we can run with the candidates
    with open('scripts/test_candidates.py', 'w') as f:
        f.write('''import sys
import os
sys.path.insert(0, os.path.abspath('.'))
from tests.debank_signature_generator import DeBankSignatureGenerator
import urllib.request
import json
import traceback

# Try to import requests, if not use urllib
try:
    import requests
    USE_REQUESTS = True
except ImportError:
    USE_REQUESTS = False

candidates = %s
wallet = "0x463452C356322D463B84891eBDa33DAED274cB40"

print("Testing candidates against live API...")
success = False

for secret in candidates:
    print(f"\\nTesting secret: {secret}")
    signer = DeBankSignatureGenerator(secret)
    headers = signer.generate_api_headers("GET", "/portfolio_v2/list", {"id": wallet})
    url = f"https://api.debank.com/portfolio_v2/list?id={wallet}"
    
    try:
        if USE_REQUESTS:
            # Also add Origin/Referer headers as DeBank might check them
            headers['Origin'] = 'https://debank.com'
            headers['Referer'] = 'https://debank.com/'
            headers['Accept'] = 'application/json'
            
            resp = requests.get(url, headers=headers, timeout=5)
            print(f"Status: {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                if data.get('error_code') == 0 or 'data' in data:
                    print(f"SUCCESS!!! The correct secret is: {secret}")
                    print(f"Data snippet: {str(data)[:200]}...")
                    success = True
                    break
                else:
                    print(f"API Error: {data}")
            else:
                print(f"HTTP Error: {resp.text[:100]}")
        else:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode())
                if data.get('error_code') == 0 or 'data' in data:
                    print(f"SUCCESS!!! The correct secret is: {secret}")
                    print(f"Data snippet: {str(data)[:200]}...")
                    success = True
                    break
    except Exception as e:
        print(f"Failed: {e}")

if not success:
    print("\\nNone of the candidates worked. The algorithm might have changed or we need a different header.")
''' % candidates)
    
    print("\nCreated scripts/test_candidates.py. Run it to test the extracted secrets.")

if __name__ == "__main__":
    main()
