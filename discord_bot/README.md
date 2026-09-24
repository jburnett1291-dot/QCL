# Unified QCL + Federal Reserve Discord Bot

This build uses `QCL2K.py` as the primary bot and mounts `FEDERAL_RESERVE_BOT.py`
into the same `discord.py` process.

## Included systems

- `!qclreg`, `/qclreg`, QCL registration desk, GM/player onboarding
- URG event, lockdown, registration, broadcast, and reporting tools
- Deterministic box-score parser, validation, correction, and Sheets push flow
- Scout, compare, leaderboard, analytics, team reports, and stat search
- Schedule creation, team add/remove, daily reveals, and pushbacks
- Protected end-of-season archive/reset workflow
- `/fed`, `/fed tourney`, nightly Refund automation, and Fast 5
- Prefix compatibility for both `!` and `.` commands

## Run

1. Copy `.env.template` to `.env` and set the Discord/server configuration.
2. Install `requirements.txt`.
3. Start with `python run_bot.py`.

The supervisor validates both bot modules before launch and keeps a last-known
good QCL source for crash recovery.

## GitHub safety

Runtime JSON, screenshots, local credentials, generated cards, and backups
should not be committed. The included `.gitignore` excludes them.