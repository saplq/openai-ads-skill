# Management workflow

Official sources: [overview](https://developers.openai.com/ads/api-overview), [quickstart](https://developers.openai.com/ads/api-quickstart), [OpenAPI](https://developers.openai.com/ads/openapi.json), [Bulk API](https://developers.openai.com/ads/bulk-api).

The structure follows reusable patterns observed in [openai/plugins](https://github.com/openai/plugins), [google/skills](https://github.com/google/skills), [cli/cli](https://github.com/cli/cli), [agentskills/agentskills](https://github.com/agentskills/agentskills), and [anthropics/skills](https://github.com/anthropics/skills). No source code was copied.

## Intake before a campaign

Collect: business goal, offer, geo, landing page, conversion event, target KPI, budget, dates, and approved brand assets. Do not fill gaps with guesses.

Use only documented targeting:

- `targeting.locations.include`
- first-party custom audiences when eligible
- product sets for feed campaigns
- `context_hints`

Do not invent age, gender, interests, dayparting, or lookalike controls.

## Safe sequence

1. Run `doctor --check-updates`, then inspect `/ad_account` and current hierarchy.
2. Check account/brand review, dates, budget units, bid type, geo, landing page, conversion source, and parent status.
3. Draft creative within current API limits and run the policy/privacy review.
4. Upload only a user-provided asset unless the user explicitly requests image generation and that tool is available.
5. Create the ad with `status: paused`.
6. Review the CLI plan and confirmation hash with the user.
7. Apply, inspect post-write readback, then separately confirm activation.

Example dry run:

```bash
python3 scripts/openai_ads.py api request POST /ads \
  --body-file /secure/path/ad.json --policy-reviewed
```

Repeat with `--apply --confirm HASH` only after approval. Budget, bid, targeting, activation, and archive are separate confirmed operations. Archive is irreversible.

## Resources and routing

- Documented: account, campaigns, ad groups, ads, files, geo, insights, conversions, custom audiences, product feeds/delta.
- Bulk: use `--surface bulk_preview`; limited preview, account capability must be explicit.
- Spec-only: use `--surface spec_preview`; disabled unless the live account advertises it.
- OAuth-only: unsupported in `0.4.0`.
- Secret-generating API and SFTP credential endpoints: never use generic `api request`.

Product feed initial setup and SFTP credentials stay in Ads Manager unless current human docs explicitly document the requested API operation. Delta is for updates to existing feed variants; verify current feed docs before applying.

Custom-audience inputs come only from stdin/file. Confirm first-party rights and consent and block EEA/Switzerland use. Persist one `Idempotency-Key` with the exact body for every membership operation; reuse it only to recover/retry that same operation. Read `membership_revision` before changes, bind Add/Remove when available, and require `expected_revision` for Replace. Poll the returned operation ID. On recovery-required or a lost response, resend the identical body/key; on mutation or revision conflict, re-read state and reconsider rather than blindly retrying. Never log identifiers.

## SemVer

- Patch: safety, validation, reporting, or docs fixes without interface breakage.
- Minor: newly supported Ads beta/features/endpoints.
- Major: incompatible CLI/config changes or Ads API v2.
