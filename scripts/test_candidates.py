import sys
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

candidates = []
wallet = "0x463452C356322D463B84891eBDa33DAED274cB40"

print("Testing candidates against live API...")
success = False

for secret in candidates:
    print(f"\nTesting secret: {secret}")
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
    print("\nNone of the candidates worked. The algorithm might have changed or we need a different header.")
