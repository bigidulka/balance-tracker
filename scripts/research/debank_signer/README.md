# DeBank signer research (not part of the runtime)

These scripts document the reverse-engineering work behind DeBank's web API signature:
how the `x-api-sign` header is produced in the browser, how fast it can be reproduced
headlessly, and how the resulting portfolio payload maps onto the application schemas.

They are **research tooling**, not production code and not part of the test suite. They
reach the public DeBank web API through a headless browser, so they need network access
and a Playwright browser; nothing here runs under `pytest` (the suite only collects
`tests/`, see `pytest.ini`).

| File | Purpose |
|---|---|
| `debank_playwright_signer.py` | First Playwright signer: loads the page, captures the signing module, signs requests |
| `debank_playwright_signer_fast.py` | Speed variant that keeps the browser context warm between signatures |
| `debank_playwright_signer_v3.py` | Latest variant with the smallest capture surface |
| `benchmark_signer_latency.py` | Measures signatures per second for a warmed signer |
| `debank_scraper.py` | Playwright DOM/JSON scraper that maps portfolio items to `app.schemas.balance` |
| `debank_re_analysis.py` | Dumps the page's signing-related JavaScript for inspection |
| `debank_signature_generator.py` | Standalone HMAC/`x-api-sign` generator kept for comparison with the browser path |
| `debank_integration_with_signer.py` | End-to-end check: signed request → portfolio payload |

## Running

```bash
python -m playwright install chromium      # one-off browser download
python -m scripts.research.debank_signer.benchmark_signer_latency
```

Historical iterations (`signer_speed2..40.py`) were removed as near-duplicates; they stay
available in the repository history. DeBank remains a third-party service: use these
scripts against your own account and within its terms of service.
