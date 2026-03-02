import re

with open('tests/debank_playwright_signer.py', 'r') as f:
    content = f.read()

# Fix the URL match logic:
# `data['url'] in request.url`
# Some endpoints have query parameters that might get re-ordered.
# We should match based on path and key params.
# The simplest fix is checking if the PATH matches and ALL params from our request exist in the intercepted URL.
# But even simpler: We can just use the `req_id` by injecting it as a dummy header!

fixed = content.replace("""
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
""", """
        # Intercept network requests to capture headers
        async def handle_request(request):
            headers = request.headers
            if 'api.debank.com' in request.url and 'x-api-sign' in headers:
                req_id_str = headers.get('x-req-id')
                if req_id_str and req_id_str.isdigit():
                    req_id = int(req_id_str)
                    if req_id in self._pending_requests:
                        self._pending_requests[req_id]['headers'] = headers
                        self._pending_requests[req_id]['event'].set()
                else:
                    # Fallback if x-req-id isn't passed for some reason
                    for req_id, data in list(self._pending_requests.items()):
                        # Check if base path matches
                        req_path = request.url.split('?')[0]
                        target_path = data['url'].split('?')[0]
                        if req_path == target_path:
                            data['headers'] = headers
                            data['event'].set()
                            break
""")

# We need to inject `x-req-id` into the fetch!
fixed = fixed.replace("""
                fetch("{target_url}", {{
                    method: 'GET',
                    headers: {{
                        "accept": "application/json",
                        "source": "web"
                    }}
                }}).catch(e => console.log(e));
""", """
                fetch("{target_url}", {{
                    method: 'GET',
                    headers: {{
                        "accept": "application/json",
                        "source": "web",
                        "x-req-id": "${{req_id}}"
                    }}
                }}).catch(e => console.log(e));
""")

with open('tests/debank_playwright_signer_fast.py', 'w') as f:
    f.write(fixed)

print("Fixed signer logic created.")
