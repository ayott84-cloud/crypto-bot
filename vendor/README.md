# vendor/

Third-party Python files vendored into this repo so the bot can run on a
clean machine (e.g. the DigitalOcean droplet) without depending on any
Claude Code skills installed locally.

## weex_contract_api.py

Copied from `~/.claude/skills/weex-trader-skill/scripts/weex_contract_api.py`
(commit-stamped Apr 12 2026). Stdlib-only. Re-vendor by copying the latest
version of the same file when WEEX adds endpoints.
