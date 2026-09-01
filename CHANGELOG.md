# Changelog

## 0.2.0 — 2026-09-01

- Adds `auth import-file` for one-time import of downloaded Ads API key files, with owner/symlink checks, automatic `0600` hardening, live account validation, and optional source removal.
- Ignores downloaded `ads-manager-api-key*.txt` files as defense in depth; the repository is not a credential store.

## 0.1.0 — 2026-09-01

- Initial standalone skill for safe OpenAI Ads API access, reporting, Pixel+CAPI, audiences, feeds, and gated preview surfaces.
- Adds local multi-profile credentials, confirmation-bound mutations, redacted audit logs, drift checks, and zero-dependency tests.
