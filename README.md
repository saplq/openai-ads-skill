# OpenAI Ads Manager Skill

Zero-dependency Codex skill for safe OpenAI Ads management, analytics, Pixel+CAPI, audiences, feeds, and gated preview APIs.

## Install

1. Download or clone this folder as `openai-ads-manager`.
2. Download your key from OpenAI Ads Manager.
3. Put it beside `SKILL.md` with the exact name `ads-manager-api-key.txt`.
4. Ask Codex to use the skill.

On first authenticated use, the skill validates `GET /ad_account`, moves the key into protected local storage, and removes the copy beside `SKILL.md`. No terminal setup is required.

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

`ads-manager-api-key.txt` is gitignored, but never commit, share, or archive it. Delete the original copy left in Downloads after the first successful use. Credentials live in `~/.config/openai-ads-manager/`: directory `0700`, files `0600`, atomic writes, strict owner and symlink checks. Ads and CAPI keys are separate.

Version pins are independent: skill `0.3.0`; Ads API `/v1`; OpenAPI `2.3.0`; oCPC open beta; Bulk limited preview; Ads Policy `v1.5`. Run `doctor --check-updates` before beta, preview, or creative changes.

No live writes are run during installation or tests. The only optional real smoke is `GET /ad_account` after you enter a key locally.

## Verify

```bash
python3 -m compileall scripts tests
python3 -m unittest discover -s tests
python3 /path/to/skill-creator/scripts/quick_validate.py .
```

MIT licensed. See `CHANGELOG.md` for releases.
