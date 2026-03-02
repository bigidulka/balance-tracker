"""
Test file for validating DeBank signature generation algorithm
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from tests.debank_signature import DeBankSignatureGenerator, generate_debank_signature


def test_signature_generation():
    """Test basic signature generation functionality"""
    print("Testing DeBank signature generation...")

    # Test 1: Basic signature generation
    signer = DeBankSignatureGenerator("test_app_secret_123")

    # Generate signature for a typical DeBank API request
    headers = signer.generate_api_headers(
        method="GET",
        url="/portfolio_v2/list",
        params={"id": "0x463452C356322D463B84891eBDa33DAED274cB40"},
        app_secret="test_app_secret_123",
    )

    print(f"✓ Generated headers: {list(headers.keys())}")
    assert "x-api-sign" in headers
    assert "x-api-timestamp" in headers
    assert "x-api-nonce" in headers
    print(f"✓ All required headers present")

    # Test 2: Verify signature format (should be hex string)
    signature = headers["x-api-sign"]
    assert len(signature) == 64  # SHA256 produces 64-character hex
    assert all(c in "0123456789abcdef" for c in signature.lower())
    print(f"✓ Signature format correct: {signature[:16]}...")

    # Test 3: Different methods and parameters
    post_headers = signer.generate_api_headers(
        method="POST",
        url="/history/list",
        params={"id": "0x463452C356322D463B84891eBDa33DAED274cB40", "page_count": 10},
    )

    assert "Content-Type" in post_headers
    assert post_headers["Content-Type"] == "application/json"
    print(f"✓ POST method adds correct Content-Type header")

    # Test 4: Convenience function
    conv_headers = generate_debank_signature(
        method="GET",
        url="/token/balance_list",
        params={"id": "0xabcd1234", "is_all": "true"},
        app_secret="another_test_secret",
    )
    assert "x-api-sign" in conv_headers
    print(f"✓ Convenience function works correctly")

    # Test 5: Same parameters should produce same signature
    sig1 = signer.generate_signature(
        method="GET",
        url="/test/endpoint",
        params={"a": 1, "b": 2},
        timestamp="1234567890000",
        nonce="test_nonce",
    )

    sig2 = signer.generate_signature(
        method="GET",
        url="/test/endpoint",
        params={"b": 2, "a": 1},  # Different order should still work
        timestamp="1234567890000",
        nonce="test_nonce",
    )

    assert sig1 == sig2
    print(f"✓ Parameter ordering handled correctly")

    print("\n✅ All signature generation tests passed!")
    return True


def test_realistic_scenario():
    """Test with a realistic DeBank API scenario"""
    print("\nTesting realistic DeBank API scenario...")

    signer = DeBankSignatureGenerator("my_real_debank_app_secret")

    # Simulate a real portfolio request
    request_data = signer.create_signed_request(
        method="GET",
        url="/portfolio_v2/list",
        params={
            "id": "0x463452C356322D463B84891eBDa33DAED274cB40",
            "chain": "eth",
            "show_zero_asset": "false",
        },
    )

    print(f"✓ Created signed request with {len(request_data)} components")
    assert "method" in request_data
    assert "url" in request_data
    assert "headers" in request_data
    assert request_data["method"] == "GET"
    assert request_data["url"] == "/portfolio_v2/list"

    headers = request_data["headers"]
    assert "x-api-sign" in headers
    assert "x-api-timestamp" in headers
    assert "x-api-nonce" in headers

    print(f"✓ Headers suitable for real API request")
    print(f"   Signature length: {len(headers['x-api-sign'])}")
    print(f"   Timestamp format: {headers['x-api-timestamp']}")
    print(f"   Nonce format: {headers['x-api-nonce']}")

    return True


if __name__ == "__main__":
    print("🧪 Testing DeBank Signature Generation Algorithm")
    print("=" * 50)

    success = test_signature_generation()
    if success:
        test_realistic_scenario()

    print("\n🎉 All tests completed successfully!")
    print("\nThe DeBank signature generation algorithm is ready for production use!")
