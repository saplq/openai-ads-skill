---
name: openai-ads-manager
description: Safely inspect, report on, create, and manage OpenAI Ads campaigns; configure Pixel and Conversions API; work with audiences, feeds, and gated preview APIs. Use for OpenAI Ads accounts, performance, campaign operations, conversion tracking, or Ads API access.
metadata:
  version: "0.4.0"
---

# OpenAI Ads Manager

Run `../../scripts/openai_ads.py` for deterministic API, credential, reporting, and conversion work. Never request an API key in chat.

1. Authenticate automatically from `ads-manager-api-key.txt` in the plugin folder or `~/Downloads`; validation uses `GET /ad_account`, then removes the downloaded file after secure local storage.
2. Inspect first with `doctor`, `report account`, or a read-only `api request`; exhaust pagination and exclude the incomplete account-local day.
3. Propose changes with evidence, confidence, risk, reversibility, and a measurement window. Ask for the business goal or KPI before spend advice.
4. Apply mutations only after returning a redacted plan and confirmation hash; verify post-write readback.

Read [management](../../references/management.md) for API operations, [performance](../../references/performance.md) for analysis, [conversions](../../references/conversions.md) for Pixel+CAPI, and [policy/privacy](../../references/policy-privacy.md) before creatives or audiences. Keep preview APIs capability-gated, new ads paused, secrets and raw PII out of output, and targeting limited to documented fields.
