import re

with open('tests/debank_playwright_signer_fast.py', 'r') as f:
    content = f.read()

# Fix the fact that `headers` isn't None at the end if it timed out!
# Our `sign_request` function returns `{ "accept": "...", "user-agent": ... }` EVEN if it timed out!
# So `headers is not None` was TRUE, but it DIDN'T actually have `x-api-sign`!

fixed = content.replace("""
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
""", """
            if headers and "x-api-sign" in headers:
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
            
            return None
""")

with open('tests/debank_playwright_signer_v3.py', 'w') as f:
    f.write(fixed)

print("Fixed")
