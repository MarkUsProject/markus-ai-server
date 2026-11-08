# Changelog

## [unreleased]

- Added audit logging for failed API key checks, shipped to Loki over OTLP
- Added intrusion-detection alert rules for brute-force and spray attacks
- Added `TRUSTED_PROXY_HOPS` so the recorded client IP cannot be spoofed
- Raised minimum supported Python version to 3.10 and repaired `uv.lock`

## [0.1.0]

- Modified project structure and configuration to use uv
- Modified paths to be OS-independent
