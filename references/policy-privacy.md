# Policy and privacy gate

Current pinned source: [OpenAI Ads Policies v1.5](https://openai.com/policies/ad-policies/), updated 2026-08-31. Recheck the live policy before creative mutation; the pin is compatibility metadata, not permanent truth.

## Creative review

Verify title/body API limits, factual support, landing-page consistency, pricing/conditions, brand authorization, category restrictions, and account/ad review state.

Block clearly prohibited content, including deceptive or misleading claims, evasion, illegal goods/services, exploitation, unsafe products, or prohibited targeting. For financial, health, legal, political, housing/employment, sensitive-trait, age-restricted, or otherwise restricted/ambiguous content, stop at human/legal review. Never promise that OpenAI will approve an ad.

New ads remain paused until the user separately confirms activation after readback and review-state inspection.

## Data rights

Use custom audiences only from the advertiser's own first-party relationship and with applicable notice, rights, and consent. Do not use purchased/brokered lists. Block custom audiences for EEA/Switzerland in this skill version.

Never expose API keys, CAPI keys, cookies, audience identifiers, raw conversion PII, or authorization headers in stdout, argv, logs, audit files, diffs, or chat. Read sensitive payloads only from stdin or a local file. Audit records contain hashes and resource/request IDs only.
