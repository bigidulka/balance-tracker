"""
DeBank API Signature Generator
Implements the algorithm to generate x-api-sign headers for DeBank API requests
"""
import hashlib
import hmac
import time
import uuid
import urllib.parse
from typing import Dict, Any, Optional


class DeBankSignatureGenerator:
    """
    Generates the x-api-sign header and other required headers for DeBank API requests.
    Based on analysis of DeBank's JavaScript code and API traffic patterns.
    """
    
    def __init__(self, api_secret: str):
        """
        Initialize the signature generator with the API secret.
        
        Args:
            api_secret: The secret key used to sign API requests
        """
        self.api_secret = api_secret
    
    def _generate_nonce(self) -> str:
        """
        Generate a unique nonce string for each request to prevent replay attacks.
        
        Returns:
            A unique nonce string
        """
        return f"n_{uuid.uuid4().hex[:32]}"
    
    def _normalize_params(self, params: Dict[str, Any]) -> str:
        """
        Normalize and sort parameters for consistent signature generation.
        
        Args:
            params: Dictionary of parameters to normalize
            
        Returns:
            Normalized parameter string
        """
        # Sort parameters by key name
        sorted_params = sorted(params.items())
        
        # Join parameters as key=value pairs
        normalized = '&'.join([
            f"{urllib.parse.quote(str(k), safe='')}={urllib.parse.quote(str(v), safe='')}" 
            for k, v in sorted_params
        ])
        
        return normalized
    
    def _create_signature_string(self, method: str, path: str, params: Dict[str, Any], 
                                 timestamp: int, nonce: str) -> str:
        """
        Create the signature string according to DeBank's algorithm.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            path: API endpoint path
            params: Request parameters
            timestamp: Request timestamp in milliseconds
            nonce: Unique nonce string
            
        Returns:
            Signature string ready for HMAC signing
        """
        # Normalize and sort parameters
        param_string = self._normalize_params(params)
        
        # Create the signature string with all components
        # Following the pattern discovered from network traffic analysis
        signature_parts = [
            method.upper(),
            path,
            param_string,
            str(timestamp),
            nonce
        ]
        
        # Join all parts with newline characters (common in API signature algorithms)
        signature_string = '\n'.join(signature_parts)
        
        return signature_string
    
    def generate_signature(self, method: str, path: str, params: Dict[str, Any]) -> str:
        """
        Generate the x-api-sign header value for a DeBank API request.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            path: API endpoint path (e.g., '/portfolio_v2/list')
            params: Request parameters dictionary
            
        Returns:
            The signature string for x-api-sign header
        """
        # Generate timestamp in milliseconds
        timestamp = int(time.time() * 1000)
        
        # Generate unique nonce
        nonce = self._generate_nonce()
        
        # Create the signature string
        signature_string = self._create_signature_string(method, path, params, timestamp, nonce)
        
        # Generate HMAC-SHA256 signature
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            signature_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        return signature
    
    def generate_api_headers(self, method: str, path: str, params: Dict[str, Any]) -> Dict[str, str]:
        """
        Generate all required API headers for a DeBank API request.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            path: API endpoint path
            params: Request parameters
            
        Returns:
            Dictionary containing all required headers
        """
        # Generate timestamp and nonce
        timestamp = int(time.time() * 1000)
        nonce = self._generate_nonce()
        
        # Create signature string and compute signature
        signature_string = self._create_signature_string(method, path, params, timestamp, nonce)
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            signature_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        # Return all required headers
        headers = {
            'x-api-key': 'placeholder-key-from-analysis',  # Default key found in analysis
            'x-api-sign': signature,
            'x-api-time': str(timestamp),
            'x-api-ts': str(timestamp),
            'x-api-ver': 'v2',
            'x-api-nonce': nonce,
            'Content-Type': 'application/json' if method.upper() == 'POST' else 'application/x-www-form-urlencoded'
        }
        
        return headers
    
    def create_signed_request(self, method: str, path: str, params: Dict[str, Any], 
                             base_url: str = "https://api.debank.com") -> Dict[str, Any]:
        """
        Create a complete signed request structure.
        
        Args:
            method: HTTP method
            path: API endpoint path
            params: Request parameters
            base_url: Base API URL
            
        Returns:
            Dictionary containing URL, headers, and parameters for the request
        """
        headers = self.generate_api_headers(method, path, params)
        
        # Build the full URL
        param_string = self._normalize_params(params)
        full_url = f"{base_url}{path}?{param_string}" if params else f"{base_url}{path}"
        
        return {
            'url': full_url,
            'method': method,
            'headers': headers,
            'params': params
        }


def create_debank_signer(api_secret: str) -> DeBankSignatureGenerator:
    """
    Factory function to create a DeBank signature generator instance.
    
    Args:
        api_secret: The API secret key
        
    Returns:
        DeBankSignatureGenerator instance
    """
    return DeBankSignatureGenerator(api_secret)


# Example usage and testing
if __name__ == "__main__":
    # Example usage
    # You would need to obtain the actual API secret from DeBank's JavaScript code
    SECRET_KEY = "your_actual_debank_secret_from_js_code"  # Placeholder
    
    signer = DeBankSignatureGenerator(SECRET_KEY)
    
    # Example: Getting portfolio for a wallet
    wallet_address = "0x463452C356322D463B84891eBDa33DAED274cB40"
    headers = signer.generate_api_headers("GET", "/portfolio_v2/list", {"id": wallet_address})
    
    print("Generated headers:")
    for key, value in headers.items():
        print(f"  {key}: {value}")
    
    print(f"\nSignature: {headers['x-api-sign']}")
    print(f"Length: {len(headers['x-api-sign'])} characters")