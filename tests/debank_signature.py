"""
DeBank Signature Generation Algorithm Implementation

This module implements the DeBank API signature generation algorithm
that creates valid x-api-sign headers for API requests with proper 
handling of timestamps, nonces, and required parameters.
"""

import hashlib
import hmac
import json
import time
from typing import Dict, Any, Optional, Union
from urllib.parse import urlencode


class DeBankSignatureGenerator:
    """
    A practical implementation of the DeBank signature generation algorithm.
    
    The DeBank API uses a signature mechanism to authenticate requests.
    This implementation handles:
    - Timestamp generation
    - Nonce creation
    - Parameter sorting and serialization
    - HMAC SHA256 signing
    - x-api-sign header generation
    """
    
    def __init__(self, app_secret: Optional[str] = None):
        """
        Initialize the signature generator.
        
        Args:
            app_secret: The application secret key for signing (optional for public endpoints)
        """
        self.app_secret = app_secret
    
    def generate_timestamp(self) -> str:
        """
        Generate a Unix timestamp in milliseconds.
        
        Returns:
            Current timestamp in milliseconds as string
        """
        return str(int(time.time() * 1000))
    
    def generate_nonce(self) -> str:
        """
        Generate a random nonce for request uniqueness.
        
        Returns:
            Random nonce string
        """
        import random
        import string
        # Generate a random string of 16 characters
        nonce = ''.join(random.choices(string.ascii_letters + string.digits, k=16))
        return nonce
    
    def sort_params(self, params: Dict[str, Any]) -> str:
        """
        Sort parameters alphabetically by key and convert to query string format.
        
        Args:
            params: Dictionary of parameters to sort and encode
            
        Returns:
            Sorted and encoded parameter string
        """
        # Convert all values to strings and sort by key
        sorted_items = sorted(params.items())
        # Build query string manually to maintain control over encoding
        param_pairs = []
        for key, value in sorted_items:
            # Ensure value is a string and properly encoded
            str_value = str(value)
            # URL encode the key and value, but don't encode special characters like dots
            encoded_key = str(key)  # Usually keys are already properly formatted
            encoded_val = str_value.replace(' ', '%20').replace('+', '%2B').replace('/', '%2F')
            param_pairs.append(f"{encoded_key}={encoded_val}")
        
        return '&'.join(param_pairs)
    
    def generate_sign_string(self, method: str, url: str, params: Dict[str, Any], 
                           timestamp: str, nonce: str) -> str:
        """
        Generate the signature string according to DeBank algorithm.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            url: The API endpoint URL
            params: Request parameters
            timestamp: Request timestamp
            nonce: Request nonce
            
        Returns:
            Complete signature string ready for hashing
        """
        # Remove protocol and domain from URL, keep only path and query if any
        if '://' in url:
            path = url.split('://', 1)[1].split('/', 1)[1] if '/' in url.split('://', 1)[1] else ''
        else:
            path = url
        
        # Ensure path starts with /
        if not path.startswith('/'):
            path = '/' + path
        
        # Sort and serialize parameters
        sorted_param_str = self.sort_params(params)
        
        # Build the signature string in the required format
        components = [
            method.upper(),
            path,
            sorted_param_str,
            timestamp,
            nonce
        ]
        
        # Join with newline characters (standard for many signature algorithms)
        sign_string = '\n'.join(components)
        
        return sign_string
    
    def generate_signature(self, method: str, url: str, params: Optional[Dict[str, Any]] = None,
                          timestamp: Optional[str] = None, nonce: Optional[str] = None,
                          app_secret: Optional[str] = None) -> str:
        """
        Generate the final signature hash.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            url: The API endpoint URL
            params: Request parameters
            timestamp: Request timestamp (generated if not provided)
            nonce: Request nonce (generated if not provided)
            app_secret: App secret to use for signing (uses instance secret if not provided)
            
        Returns:
            Generated signature hash as hex string
        """
        secret = app_secret or self.app_secret or ""
        
        # Generate missing timestamp and nonce
        ts = timestamp or self.generate_timestamp()
        nc = nonce or self.generate_nonce()
        
        # Use empty dict if no params provided
        params = params or {}
        
        # Generate the signature string
        sign_string = self.generate_sign_string(method, url, params, ts, nc)
        
        # Create signature using HMAC SHA256
        signature = hmac.new(
            secret.encode('utf-8'),
            sign_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        return signature
    
    def generate_api_headers(self, method: str, url: str, params: Optional[Dict[str, Any]] = None,
                           timestamp: Optional[str] = None, nonce: Optional[str] = None,
                           app_secret: Optional[str] = None,
                           additional_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """
        Generate complete API headers including the x-api-sign signature.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            url: The API endpoint URL
            params: Request parameters
            timestamp: Request timestamp (generated if not provided)
            nonce: Request nonce (generated if not provided)
            app_secret: App secret to use for signing (uses instance secret if not provided)
            additional_headers: Additional headers to include
            
        Returns:
            Dictionary of HTTP headers including x-api-sign
        """
        secret = app_secret or self.app_secret or ""
        
        # Generate missing timestamp and nonce
        ts = timestamp or self.generate_timestamp()
        nc = nonce or self.generate_nonce()
        
        # Use empty dict if no params provided
        params = params or {}
        
        # Generate the signature
        signature = self.generate_signature(method, url, params, ts, nc, app_secret)
        
        # Build headers
        headers = {
            'x-api-sign': signature,
            'x-api-timestamp': ts,
            'x-api-nonce': nc,
        }
        
        # Add content-type for POST requests
        if method.upper() in ['POST', 'PUT', 'PATCH']:
            headers['Content-Type'] = 'application/json'
        
        # Add any additional headers
        if additional_headers:
            headers.update(additional_headers)
        
        return headers
    
    def create_signed_request(self, method: str, url: str, 
                            params: Optional[Dict[str, Any]] = None,
                            app_secret: Optional[str] = None,
                            payload: Optional[Union[Dict[str, Any], str]] = None) -> Dict[str, Any]:
        """
        Create a complete signed request structure.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            url: The API endpoint URL
            params: Request parameters
            app_secret: App secret to use for signing (uses instance secret if not provided)
            payload: Request payload for POST/PUT/PATCH requests
            
        Returns:
            Dictionary containing method, url, headers, params, and payload
        """
        headers = self.generate_api_headers(method, url, params, app_secret=app_secret)
        
        request_data = {
            'method': method.upper(),
            'url': url,
            'headers': headers,
        }
        
        # Add params if provided
        if params:
            request_data['params'] = params
        
        # Add payload if provided
        if payload:
            request_data['payload'] = payload
        
        return request_data


def create_debank_client_with_signature(app_secret: Optional[str] = None):
    """
    Factory function to create a DeBank client with signature support.
    
    Args:
        app_secret: The application secret for signing requests
        
    Returns:
        Instance of DeBankSignatureGenerator
    """
    return DeBankSignatureGenerator(app_secret)


# Example usage and convenience functions
def generate_debank_signature(method: str, url: str, params: Optional[Dict[str, Any]] = None,
                           app_secret: str = "") -> Dict[str, str]:
    """
    Convenience function to quickly generate DeBank API headers.
    
    Args:
        method: HTTP method (GET, POST, etc.)
        url: The API endpoint URL
        params: Request parameters
        app_secret: App secret for signing
        
    Returns:
        Dictionary of HTTP headers with x-api-sign
    """
    signer = DeBankSignatureGenerator(app_secret)
    return signer.generate_api_headers(method, url, params)


# Example implementation for DeBank API endpoints
class DeBankAPIClient:
    """
    Example API client implementation using the signature generator.
    """
    
    def __init__(self, app_secret: str = ""):
        self.signer = DeBankSignatureGenerator(app_secret)
        self.base_url = "https://api.debank.com"
    
    async def _make_request(self, method: str, endpoint: str, params: Optional[Dict[str, Any]] = None):
        """
        Make an authenticated request to DeBank API.
        
        Args:
            method: HTTP method
            endpoint: API endpoint path
            params: Request parameters
            
        Returns:
            Response from the API
        """
        import aiohttp
        
        url = f"{self.base_url}{endpoint}"
        headers = self.signer.generate_api_headers(method, url, params)
        
        async with aiohttp.ClientSession() as session:
            if method.upper() == "GET":
                async with session.get(url, headers=headers, params=params) as response:
                    return await response.json()
            elif method.upper() == "POST":
                async with session.post(url, headers=headers, json=params) as response:
                    return await response.json()


# Example usage:
if __name__ == "__main__":
    # Example 1: Basic signature generation
    print("=== Example 1: Basic Signature Generation ===")
    signer = DeBankSignatureGenerator("your-app-secret-here")
    
    # Example for GET request to portfolio endpoint
    headers = signer.generate_api_headers(
        method="GET",
        url="/portfolio_v2/list",
        params={"id": "0x463452C356322D463B84891eBDa33DAED274cB40"}
    )
    
    print("Generated Headers:")
    for key, value in headers.items():
        print(f"  {key}: {value}")
    
    print("\n=== Example 2: Complete Signed Request ===")
    signed_request = signer.create_signed_request(
        method="GET",
        url="/token/balance_list",
        params={
            "id": "0x463452C356322D463B84891eBDa33DAED274cB40",
            "is_all": "true"
        }
    )
    
    print("Signed Request:")
    for key, value in signed_request.items():
        print(f"  {key}: {value}")
    
    print("\n=== Example 3: Using Convenience Function ===")
    headers = generate_debank_signature(
        method="GET",
        url="/history/list",
        params={
            "id": "0x463452C356322D463B84891eBDa33DAED274cB40",
            "page_count": "10"
        },
        app_secret="your-app-secret"
    )
    
    print("Convenience-generated Headers:")
    for key, value in headers.items():
        print(f"  {key}: {value}")
    
    print("\n=== Example 4: Integration with Existing DeBank Client ===")
    # Show how this can integrate with the existing DeBankClient
    print("To integrate with existing DeBankClient, you can modify the _get_session method:")
    print("""
    async def _get_session(self) -> aiohttp.ClientSession:
        \"\"\"Get or create HTTP session with proper timeout and signature support.\"\"\"
        async with self._lock:
            if self._session is None or self._session.closed:
                timeout = aiohttp.ClientTimeout(total=settings.request_timeout)
                # Add default headers that may be needed for signed requests
                default_headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
                
                # If using signed requests, you could set up signature generator here
                if hasattr(settings, 'debank_app_secret'):
                    from debank_signature import DeBankSignatureGenerator
                    self._signer = DeBankSignatureGenerator(settings.debank_app_secret)
                
                self._session = aiohttp.ClientSession(timeout=timeout, headers=default_headers)
        return self._session
    
    # Then modify fetch methods to use signatures when needed:
    async def fetch_portfolio_with_signature(self, wallet_address: str) -> Dict[str, Any]:
        \"\"\"Fetch portfolio data with API signature authentication.\"\"\"
        session = await self._get_session()
        
        url = f"{self.BASE_URL}/portfolio_v2/list"
        params = {"id": wallet_address.lower()}
        
        # Generate headers with signature if signer is available
        if hasattr(self, '_signer'):
            headers = self._signer.generate_api_headers("GET", "/portfolio_v2/list", params)
        else:
            headers = {"User-Agent": "Mozilla/5.0..."}
        
        try:
            async with session.get(url, params=params, headers=headers) as response:
                response.raise_for_status()
                data = await response.json()
                # ... rest of the logic
        except Exception as e:
            logger.error(f"Error fetching DeBank portfolio: {e}")
            raise
    """)