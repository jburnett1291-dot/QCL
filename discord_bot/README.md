# QCL / URG Discord Bot

This build runs the QCL and URG systems only. The Federal Reserve bot is a
separate deployment: it is not imported, mounted, or started by this launcher.
The retained legacy QCL source imports a no-op compatibility stub at runtime so
it can run without the separate Federal Reserve module being present.

## Included systems

- `!qclreg`, QCL registration desk, GM/player onboarding
- Admin template upload/import/export, with registration people indexed by Discord ID
- `!qcladmin` operations desk for registrations, stats, box scores, and URG tools
- URG event, lockdown, registration, broadcast, and reporting tools
- Deterministic box-score parser, validation, correction, and Sheets push flow
- Scout, compare, leaderboard, analytics, team reports, and stat search
- Schedule creation, team add/remove, daily reveals, and pushbacks
- Protected end-of-season archive/reset workflow
- Prefix commands work with both `!` and `.`, including `!season_admin create`

## Run

1. Copy `.env.template` to `.env` and set the Discord/server configuration.
2. Install `requirements.txt`.
3. Start with `python run_bot.py` (or `python prefix_only.py` for a single run).

Do **not** launch `QCL2K.py` directly. The prefix-only launcher converts its
legacy handlers into text-command entry points and removes old slash commands
on startup. Existing buttons, dropdowns, and modal forms remain available where
a prefix command opens one. Use `!commands` for the text-command list and
subcommands. `FEDERAL_RESERVE_BOT.py` is not a runtime dependency and can be
deployed separately on its own host.

Arguments are space-separated; quote multi-word values, mention members or
channels, and attach files to the command message. Optional arguments can be
given as `name=value`. Admin PINs are requested in DMs; **never type a PIN in
a server command**.

## Admin desk and templates

- `!qcladmin` opens the QCL / URG admin operations desk.
- `!registration_templates` downloads the combined BYOT GM, Draft GM, and Draft
  Player JSON template. Fill it and attach it to `!registration_import`.
- `!registration_export [registration_id]` exports one record or the full
  registration list for editing. Imports validate the entire file before
  writing, update only listed IDs, rebuild the Discord-ID people index, and
  record an audit entry.
- `!admin_template` downloads the supported settings template. Apply a validated
  file with `!admin_apply`. Credentials, PINs, arbitrary paths, and executable
  changes are not accepted by this template.
- `!stats_upload` attaches a CSV/JSON stats file to the configured GitHub repo
  under `qcl_admin_uploads/`. `!publishstats` publishes calculated player
  totals. `!github_upload` uploads approved source/data files there; Python
  uploads are syntax-checked and never executed by the bot.

The supervisor validates the QCL source, prefix gateway, and admin tools before
launch, and keeps a last-known-good QCL source for crash recovery.

## GitHub safety

Runtime JSON, screenshots, local credentials, generated cards, and backups
should not be committed. The included `.gitignore` excludes them. For runtime
GitHub publishing, set `GITHUB_TOKEN`, `GITHUB_REPO`, and `GITHUB_BRANCH` in the
bot host's environment/secrets manager. Never paste a token into Discord or an
uploaded template.