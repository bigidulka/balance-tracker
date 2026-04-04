# Scripts

This directory contains non-runtime tooling.

- `probes/`: repeatable diagnostics used to compare transports and inspect exchange payload normalization.
- `research/`: reverse-engineering and one-off helper scripts kept out of the main application path.

Nothing here is imported by the production API, worker, or bot entrypoints.
