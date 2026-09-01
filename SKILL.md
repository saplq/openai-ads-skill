---
name: openai-ads-manager
description: Safely inspect, report on, create, and manage OpenAI Ads campaigns; configure Pixel and Conversions API; work with audiences, product feeds, and gated preview APIs. Use when a user asks about an OpenAI Ads account, performance, campaign operations, conversion tracking, or Ads API access.
metadata:
  version: "0.2.0"
---

# OpenAI Ads Manager

Use the local CLI for deterministic API, credential, reporting, and conversion work. Never ask the user to paste a key into chat.

## Route the request

1. **Authenticate** — use hidden `auth login`, or `auth import-file --file PATH` for a downloaded key file. Both validate `GET /ad_account` before secure local storage; never keep the key in the repository.
2. **Inspect/report** — read first. Run `doctor`, `report account`, or a read-only `api request`. Exhaust pagination and exclude the incomplete account-local day.
3. **Propose** — show evidence, confidence, risk, reversibility, and a measurement window. Ask for the business goal or target KPI before recommending spend changes.
4. **Apply only after confirmation** — mutations first return a redacted plan and confirmation hash. Apply with `--apply --confirm HASH`, then verify the readback.

Use [management.md](references/management.md) for campaign operations and API routing; [performance.md](references/performance.md) for analysis; [conversions.md](references/conversions.md) for Pixel+CAPI and target-repository work; and [policy-privacy.md](references/policy-privacy.md) before creatives, audiences, or restricted categories.

## Hard rules

- Keep Ads API, OpenAPI, feature maturity, docs, and policy versions separate; inspect [compatibility.json](references/compatibility.json).
- Use only the fixed Ads host. Reject OAuth-only paths, generic secret endpoints, and undocumented targeting.
- Create ads paused. Treat activation, budget/bid/targeting changes, audience uploads, and archive as high risk.
- Accept audience identifiers and conversion events only from stdin/files, never argv; never log secrets or raw PII.
- Gate Bulk and spec-only paths behind explicit preview opt-in plus a live capability check.
- Check current official Ads docs and policy before beta/preview operations or creative changes. Do not promise approval.
