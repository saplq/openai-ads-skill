# Changelog

## 0.4.0 — 2026-09-01

- Packages the standalone skill as an installable Codex/ChatGPT plugin with a public repo marketplace manifest.
- Adds no-terminal key discovery from the plugin folder or `~/Downloads`, with the same live validation and secure local import.

## 0.3.0 — 2026-09-01

- Adds zero-command onboarding: drop `ads-manager-api-key.txt` beside `SKILL.md`; the first authenticated action validates, securely stores, and removes it automatically.
- Keeps manual hidden-prompt and file-import commands as advanced fallbacks.

## 0.2.0 — 2026-09-01

- Adds `auth import-file` for one-time import of downloaded Ads API key files, with owner/symlink checks, automatic `0600` hardening, live account validation, and optional source removal.
- Ignores downloaded `ads-manager-api-key*.txt` files as defense in depth; the repository is not a credential store.

## 0.1.0 — 2026-09-01

- Initial standalone skill for safe OpenAI Ads API access, reporting, Pixel+CAPI, audiences, feeds, and gated preview surfaces.
- Adds local multi-profile credentials, confirmation-bound mutations, redacted audit logs, drift checks, and zero-dependency tests.
