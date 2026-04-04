import asyncio
import time
import sys
import os

sys.path.insert(0, os.path.abspath('.'))
from tests.debank_playwright_signer import DeBankSigner

async def test_speed():
    signer = DeBankSigner()
    wallet = "0x463452C356322D463B84891eBDa33DAED274cB40"
    
    print("\nSpeed testing without waiting for full 10s timeout...")
    
    # We found out that it DOES succeed but returns headers AFTER the wait because 
    # of a logic error where it logs warning and sets headers=None, but then returns 
    # the cached headers at the bottom of the function!
    # Wait, let's look at `sign_request` in `debank_playwright_signer.py`:
    # 
    # try:
    #     await asyncio.wait_for(event.wait(), timeout=10.0)
    #     headers = self._pending_requests[req_id]['headers']
    # except asyncio.TimeoutError:
    #     logger.warning(...)
    #     headers = None
    # 
    # Oh! It times out because the URL in `handle_request` doesn't match EXACTLY.
    # `if data['url'] in request.url:`
    # DeBank probably appends parameters or the encoding differs!
    
    # Let's fix the interceptor match logic
    print("Fixing the URL match logic in our interceptor...")
