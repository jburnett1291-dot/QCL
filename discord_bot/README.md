# Unified QCL + Federal Reserve Discord Bot

This build uses `QCL2K.py` as the primary bot and mounts `FEDERAL_RESERVE_BOT.py`
into the same `discord.py` process.

## Included systems

- `!qclreg`, QCL registration desk, GM/player onboarding
- URG event, lockdown, registration, broadcast, and reporting tools
- Deterministic box-score parser, validation, correction, and Sheets push flow
- Scout, compare, leaderboard, analytics, team reports, and stat search
- Schedule creation, team add/remove, daily reveals, and pushbacks
- Protected end-of-season archive/reset workflow
- `!fed`, `!fed tourney`, nightly Refund automation, and `.fast5`
- Prefix commands work with both `!` and `.`, including `!season_admin create`

## Run

1. Copy `.env.template` to `.env` and set the Discord/server configuration.
2. Install `requirements.txt`.
3. Start with `python run_bot.py` (or `python prefix_only.py` for a single run).

Do **not** launch `QCL2K.py` or `FEDERAL_RESERVE_BOT.py` directly: they are
legacy handler libraries whose old slash-command registrations remain in
their source. The prefix-only launcher converts the handlers into text-command
entry points and removes old global and guild slash registrations on startup.
Existing buttons, dropdowns, and modal forms remain available where a prefix
command opens one. Use `!commands` for the text-command list and subcommands.

Arguments are space-separated; quote multi-word values, mention members or
channels, and attach files to the command message. Optional arguments can be
given as `name=value`. Admin PINs are requested in DMs; **never type a PIN in
a server command**.

The supervisor validates both bot modules before launch and keeps a last-known
good QCL source for crash recovery.

## GitHub safety

Runtime JSON, screenshots, local credentials, generated cards, and backups
should not be committed. The included `.gitignore` excludes them.