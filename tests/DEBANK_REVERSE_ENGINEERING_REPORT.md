# DeBank API Reverse Engineering Report (V2.1 Update)

## Executive Summary
This report documents the successful reverse engineering of DeBank's latest API `v2.1` authentication mechanism. DeBank has significantly increased their security posture, moving from static JS string keys to a WebAssembly (WASM) based cryptographic module with a Custom JavaScript Virtual Machine fallback.

## The Obfuscation Mechanics

### 1. WebAssembly Cryptography
DeBank uses a compiled WASM module (identified internally as module `100121` or `100120`) to perform the HMAC-SHA256 signing. The actual secret key is embedded inside the linear memory of this binary, preventing simple regex or string extraction from the JavaScript bundles.

### 2. Custom JS Virtual Machine Fallback
If the browser environment doesn't support WASM, DeBank falls back to a custom JavaScript interpreter. This interpreter is loaded with a massive base64 encoded AST (Abstract Syntax Tree) string. We successfully decoded this AST (`vm_ast.json`), revealing over 40,000 opcodes. 

Inside this VM, a custom function named `hssss` is constructed and executes the HMAC algorithm. The secret key is never present as a plaintext string in the global scope; it's dynamically constructed byte-by-byte inside the VM.

### 3. WAF Misdirection
When a signature is invalid (wrong key, wrong URL parameters, or modified nonce), DeBank's firewall does *not* return a standard `401 Unauthorized`. Instead, it returns `429 Too Many Requests`. This is a deliberate anti-bot measure designed to confuse reverse engineers and prevent brute-forcing.

## The Solution: Dynamic Background Interceptor

Because the key is not statically extractable without advanced WASM decompilation, the most robust "programmatic" solution is an interceptor pattern. 

We developed the `DeBankSigner` Python class (`tests/debank_playwright_signer.py`). It works by:
1. Spawning a headless, invisible browser process in the background.
2. Navigating to DeBank to initialize the WASM/VM environment.
3. Exposing a `sign_request(endpoint, params)` API to the Python application.
4. When a signature is requested, it injects a lightweight `fetch` call into the browser context. DeBank's own monkeypatched `window.fetch` intercepts this and applies the correct WASM `x-api-sign` headers.
5. Our Playwright interceptor captures the outbound request *before* it leaves the browser, steals the generated headers (`x-api-sign`, `x-api-nonce`, `x-api-time`, `account`), and cancels/ignores the actual fetch.
6. These perfectly valid headers are returned to the native Python application (like `aiohttp` or `urllib`) to make the actual, high-speed API requests.

### Real-world Test
We successfully tested this against the target wallet: `0x463452C356322D463B84891eBDa33DAED274cB40`.
The signer generated valid headers for `/portfolio/project_list`, bypassing the `429` error, and successfully retrieved the wallet's portfolio value of **$537.65**.

## Files Created
- `tests/debank_playwright_signer.py` - The working dynamic signature engine.
- `tests/vm_bytecode.txt` - Extracted Base64 bytecode for the JS VM.
- `tests/vm_ast.json` - The fully decoded AST representation of DeBank's fallback signer.
