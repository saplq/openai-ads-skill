# OpenAI Ads Manager

Manage OpenAI Ads with Codex: understand performance, prepare campaigns, and connect Pixel+CAPI without exposing your API key.

[![Skill version](https://img.shields.io/badge/skill-v0.3.0-111827)](CHANGELOG.md)
[![Ads API](https://img.shields.io/badge/Ads_API-v1-10A37F)](https://developers.openai.com/ads)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

## What it does

- Audits the last 7 and 30 completed days and explains CTR, CPC, CPM, CVR, CPA, pacing, and tracking gaps.
- Prepares and manages campaigns, ad groups, ads, audiences, feeds, and conversions across the documented Ads API.
- Helps add Pixel+CAPI with consent, hashing, server-side secrets, and event deduplication.
- Keeps new ads paused and requires a reviewable plan plus confirmation before sensitive changes.

## Install in one minute

1. Ask Codex: `Use $skill-installer to install https://github.com/saplq/openai-ads-skill`.
2. Download your key from OpenAI Ads Manager.
3. Put the downloaded file beside `SKILL.md` and name it exactly `ads-manager-api-key.txt`.
4. Ask Codex to use `$openai-ads-manager`.

No terminal auth is needed. On first use, the skill validates `GET /ad_account`, stores the key in protected local storage, and removes the copy beside `SKILL.md`.

## Try it

```text
Use $openai-ads-manager to audit the last 7 and 30 completed days.
Use $openai-ads-manager to prepare a paused campaign for this landing page.
Use $openai-ads-manager to add Pixel+CAPI to this repository with consent and dedupe.
```

## Safe by default

- The downloaded key filename is gitignored; never commit, share, or archive it.
- Credentials use local `0700`/`0600` storage. Secrets and audience identifiers stay out of output and audit logs.
- Writes require confirmation and readback. Preview APIs require an explicit feature gate and live capability check.

Skill `0.3.0` · Ads API `/v1` · OpenAPI `2.3.0` · Ads Policy `v1.5` · oCPC open beta · Bulk limited preview

<details>
<summary>CLI and development</summary>

```bash
python3 scripts/openai_ads.py report account --profile main
python3 scripts/openai_ads.py api request GET /campaigns --profile main --all-pages
python3 -m unittest discover -s tests
```

See [CHANGELOG.md](CHANGELOG.md) for releases and [compatibility.json](references/compatibility.json) for version pins.

</details>

Community-built and not affiliated with OpenAI. MIT licensed. If this helps, a star helps others find it.
