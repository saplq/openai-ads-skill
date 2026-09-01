# Performance playbook

Official sources: [Insights](https://developers.openai.com/ads/api-reference/insights), [Conversion Insights](https://developers.openai.com/ads/api-reference/insights#conversion-insights), [oCPC](https://developers.openai.com/ads/conversion-optimized-campaigns).

## Default audit

Use completed account-local days only. Compare trailing 7 and 30 days with the immediately preceding equal windows. Exhaust every page. Inspect:

- account and brand review state
- campaign → ad group → ad serving hierarchy
- rejected and in-review ads
- conversion source, recent-event health, and event settings
- zero delivery and schedule/date blockers
- pacing against explicit dates and budget
- product, country, and device segments when available

Metrics:

- CTR = clicks / impressions
- CPC = spend / clicks
- CPM = spend × 1,000 / impressions
- CVR = click-through conversions / clicks
- CPA = spend / click-through conversions

Return `null`, not zero or infinity, when a denominator is zero. Synthetic zero-impression product rows may omit metrics; do not invent missing values.

`conversions` equals click-through conversions. Keep `view_through_conversions` separate: it does not enter CPA, post-click CVR, bidding, billing, or conversion optimization.

## Recommendation contract

Every recommendation must include:

- concrete evidence and date window
- confidence (`high`, `medium`, or `low`)
- downside risk
- reversibility
- measurement window and success criterion

Without a target CPA/CPC/CTR or business goal, diagnose only. Ask for the target before recommending spend. There are no universal scale/pause thresholds and no automatic budget changes.

Change one causal layer at a time where practical. Verify tracking before judging the offer; verify delivery/review before judging creative; avoid conclusions from low-volume segments. oCPC is open beta, not a default recommendation: confirm an eligible conversion setting and enough trustworthy conversion history first.
