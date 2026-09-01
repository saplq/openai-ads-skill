# OpenAI Ads Manager Skill

Zero-dependency Codex skill for safe OpenAI Ads management, analytics, Pixel+CAPI, audiences, feeds, and gated preview APIs.

## Install

Copy this folder into your skills directory as `openai-ads-manager`, or clone it there. macOS/Linux and Python 3.11+ are supported.

```bash
python3 scripts/openai_ads.py version
python3 scripts/openai_ads.py doctor --offline
python3 scripts/openai_ads.py auth login --profile main
```

`auth login` asks for the Ads API key in a hidden terminal prompt, validates `GET /ad_account`, then stores it locally. Never paste a key into chat or pass it through argv.

## Use

Ask Codex:

```text
Use $openai-ads-manager to audit the last 7 and 30 completed days.
Use $openai-ads-manager to prepare a paused campaign for this landing page.
Use $openai-ads-manager to add Pixel+CAPI to this repository with consent and dedupe.
```

Direct CLI examples:

```bash
python3 scripts/openai_ads.py report account --profile main
python3 scripts/openai_ads.py api request GET /campaigns --profile main --all-pages
python3 scripts/openai_ads.py capi send --profile main --key-name production \
  --pixel-id PIXEL_ID --body-file events.json --validate-only --consent-confirmed
```

Writes first return a redacted plan and confirmation hash. Apply the unchanged plan with:

```bash
python3 scripts/openai_ads.py api request POST /campaigns --body-file campaign.json \
  --profile main --apply --confirm HASH
```

## Security and versions

Credentials live in `~/.config/openai-ads-manager/`: directory `0700`, files `0600`, atomic writes, strict owner and symlink checks. Ads and CAPI keys are separate. Audit logs exclude secrets and audience identifiers.

Version pins are independent: skill `0.1.0`; Ads API `/v1`; OpenAPI `2.3.0`; oCPC open beta; Bulk limited preview; Ads Policy `v1.5`. Run `doctor --check-updates` before beta, preview, or creative changes.

No live writes are run during installation or tests. The only optional real smoke is `GET /ad_account` after you enter a key locally.

## Verify

```bash
python3 -m compileall scripts tests
python3 -m unittest discover -s tests
python3 /path/to/skill-creator/scripts/quick_validate.py .
```

MIT licensed. See `CHANGELOG.md` for releases.
