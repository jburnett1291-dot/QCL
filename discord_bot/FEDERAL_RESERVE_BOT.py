#!/usr/bin/env python3
# =============================================================================
#  FEDERAL RESERVE BOT — PHASE 1: THE REFUND (nightly losers bracket)
#  ATM PAYDAY  |  "Advance To Money — Paid As You Dominate, All Year"
#
#  APA \u2022 ATM PRO AM BASKETBALL LEAGUE  |  discord.py 2.x  |  py_compile before every ship
#
#  THE WHOLE POINT: users do EVERYTHING through the bot. No manual channel
#  posts, no admin doing payout math by hand, no memorized commands for
#  overrides — that's a dropdown. If it happens in THE REFUND, the bot did it.
#
#  PHASE 1 SCOPE (this file):
#    /fed setup   (admin) ONE command, all dropdowns:
#                 event type -> bracket size (4/8) -> mode -> buy-in -> cashtag
#                 Builds category, 5 channels, 3 roles, posts panels,
#                 and auto-computes the ENTIRE payout ladder.
#    /fed panel   (admin) re-post the dropdown Override Panel anywhere.
#                 Includes a "Reprice Buy-In" option -> pops a modal,
#                 recomputes the whole ladder instantly.
#    /fed enter   captain enters squad (+ optional L-proof screenshot)
#    /fed standby join tonight's standby list
#    /fed board   tonight's machine status (dynamic to bracket size)
#    /fed clockin time clock check-in (slash + persistent button)
#    /fed report  winner reports a GAME result -> admin verify button
#                 (BO1 = 1 game decides it. BO3 = first to 2 games.)
#
#  BRACKET FORMATS (locked in at setup via dropdown):
#    4 TEAMS : Opening Round = Sudden Death (BO1)  ->  Final = Best of 3
#    8 TEAMS : Opening Round = Sudden Death (BO1)  ->  Semis = Best of 3
#              -> Final = Best of 3
#
#  PAYOUT MATH (auto, any buy-in, any bracket size):
#    gross = buy_in x team_count
#    pot   = gross boosted (launch mode) or gross minus rake (standard mode)
#    Championship's cut is locked in FIRST (rounded to the nearest dollar),
#    then each earlier round is filled in working backward from the final,
#    and the OPENING (sudden-death) round absorbs whatever rounding dust
#    is left — so the pot always reconciles exactly and the championship
#    is never the one shortchanged by rounding.
#
#  Nightly auto-loop (America/New_York), fully hands-off once /fed setup runs:
#    23:00 announce -> 23:30 deposits open -> 23:50 lock + time clock
#    00:00 tip (CARD DECLINED auto-forfeits, standby auto-promotes)
#    round-by-round advance -> VAULT EMPTIED -> auto-reset for tomorrow
#
#  PHASE 2 (later, separate module): PAPER ROUTE weekly, THE PAYROLL
#  leaderboard, Google Sheet sync, PIL receipt images, POCKET CHANGE.
# =============================================================================

import asyncio
import hashlib
import json
import os
import random
import re
import subprocess
import sys
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
import discord
from discord import app_commands
from discord.ext import commands, tasks

# Load .env from the SAME FOLDER as this script, regardless of which directory
# you launch it from (e.g. running via a full path from a different cwd).
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# ----------------------------- CONSTANTS ------------------------------------
TOKEN = os.environ.get("FED_RESERVE_TOKEN", "")   # from .env — see .env.template
STATE_FILE = "fed_reserve_state.json"
ET = ZoneInfo("America/New_York")

BOT_OWNER_ID = 1337614287920959609               # Qwik

T_ANNOUNCE = dtime(23, 0)
T_OPEN     = dtime(23, 30)
T_LOCK     = dtime(23, 50)
T_TIP      = dtime(0, 0)

# ── TEST MODE ────────────────────────────────────────────────────────────────
# Toggle from Override Panel or /fed testmode.
# In test mode: auto-loops paused, coach PINs bypassed, pick clock = 10s,
# queues stay open until admin manually locks them.
# Bot ALWAYS boots into live mode (TEST_MODE forced False in on_ready).
TEST_MODE = False

FAST5_PICK_CLOCK_REAL    = 90
FAST5_PICK_CLOCK_TEST    = 10
FAST5_OPEN_BEFORE_TIP    = 60   # minutes (live: queue opens 60 min before tip)
FAST5_LOCK_BEFORE_TIP    = 15   # minutes (live: queue locks 15 min before tip)

NAVY, GOLD, GREEN, RED = 0x0B2A30, 0xC9A227, 0x1E6B32, 0x8E2B2B
TEAL = 0x2FD3DC   # bright cyan-teal accent, matches the APA hype-callout color

CATEGORY_NAME   = "\U0001F3E7 ATM PAYDAY"
CH_ANNOUNCE     = "\U0001F9FE-the-refund"
CH_PAPERTRAIL   = "\U0001F4B0-the-paper-trail"
CH_TIMECLOCK    = "\u23F0-time-clock"
CH_RESULTS      = "\U0001F4E5-results-drop"
CH_HR           = "\u2696\uFE0F-hr"
CH_ROSTER       = "\U0001F4CB-roster-book"
# Pre-emoji channel names from earlier setup runs — ensure_channel() renames
# these in place (preserving history/messages) instead of creating duplicates.
LEGACY_CHANNEL_NAMES = {
    "announce_ch": "the-refund", "papertrail_ch": "the-paper-trail",
    "timeclock_ch": "time-clock", "results_ch": "results-drop",
    "hr_ch": "hr", "roster_ch": "roster-book",
}
ROLE_PAYROLL    = "ON THE PAYROLL"
ROLE_REFUNDED   = "REFUNDED"
ROLE_1099       = "1099"
ROLE_CAPTAIN    = "CAPTAIN"
ROLE_PLAYER     = "PLAYER"

# ----------------------------- FAST 5's --------------------------------------
FAST5_CATEGORY = "\U0001F3C0 FAST 5's"
FAST5_POSITIONS = ["PG", "SG", "SF", "PF", "C"]
FAST5_CHANNELS = {
    "announce_ch": "\U0001F3C0-fast5-announce", "captains_ch": "\U0001F451-fast5-captains",
    "pg_ch": "fast5-pg", "sg_ch": "fast5-sg", "sf_ch": "fast5-sf", "pf_ch": "fast5-pf", "c_ch": "fast5-c",
    "draft_ch": "\U0001F3AF-fast5-draft-room", "papertrail_ch": "\U0001F4B0-fast5-paper-trail",
    "results_ch": "\U0001F4E5-fast5-results", "archive_ch": "\U0001F5C4\uFE0F-fast5-archive",
}
FAST5_DRAFT_ROUNDS = 7
def fast5_pick_clock() -> int:
    return FAST5_PICK_CLOCK_TEST if TEST_MODE else FAST5_PICK_CLOCK_REAL
FAST5_PICK_CLOCK_SECONDS = 90   # kept as fallback reference

# Roster submission format (posted freeform by captains in #roster-book):
#   TEAM: Squad Name
#   GT: ExactGamertag - @DiscordUser
#   GT: ExactGamertag - @DiscordUser
# Gamertag must byte-match the player's FIRST-EVER registered gamertag exactly
# (case-sensitive). A mismatch on rescan logs a fine against the captain who
# posted it, auto-deducted from that captain's next withdrawal.
GT_LINE_RE = re.compile(r"GT:\s*(.+?)\s*(?:-|\|)\s*<@!?(\d+)>", re.IGNORECASE)
TEAM_LINE_RE = re.compile(r"TEAM:\s*(.+)", re.IGNORECASE)

def parse_roster_message(content: str):
    """Pure-text parser, no Discord connection needed — easy to unit test.
    Returns (team_name_or_None, [(gamertag, discord_id), ...])."""
    team_match = TEAM_LINE_RE.search(content)
    team_name = team_match.group(1).strip()[:32] if team_match else None
    players = [(m.group(1).strip(), int(m.group(2))) for m in GT_LINE_RE.finditer(content)]
    return team_name, players

# Bracket definitions: round order is CHRONOLOGICAL (opening round first).
# "weight" = target share of the pot for that round BEFORE rounding.
# Championship math always processes weights final-to-first (see compute_payouts).
TEAM_BRACKETS = {
    2: [
        {"key": "F",  "label": "THE CHAMPIONSHIP",                  "format": "BO3", "matches": 1, "weight": 1.00},
    ],
    4: [
        {"key": "R1", "label": "OPENING ROUND \u2014 SUDDEN DEATH", "format": "BO1", "matches": 2, "weight": 0.40},
        {"key": "F",  "label": "THE CHAMPIONSHIP",                  "format": "BO3", "matches": 1, "weight": 0.60},
    ],
    8: [
        {"key": "QF", "label": "OPENING ROUND \u2014 SUDDEN DEATH", "format": "BO1", "matches": 4, "weight": 0.24},
        {"key": "SF", "label": "SEMIFINALS",                        "format": "BO3", "matches": 2, "weight": 0.24},
        {"key": "F",  "label": "THE CHAMPIONSHIP",                  "format": "BO3", "matches": 1, "weight": 0.52},
    ],
    16: [
        {"key": "R16", "label": "ROUND OF 16 \u2014 SUDDEN DEATH",  "format": "BO1", "matches": 8, "weight": 0.16},
        {"key": "QF",  "label": "QUARTERFINALS",                    "format": "BO3", "matches": 4, "weight": 0.20},
        {"key": "SF",  "label": "SEMIFINALS",                       "format": "BO3", "matches": 2, "weight": 0.24},
        {"key": "F",   "label": "THE CHAMPIONSHIP",                 "format": "BO3", "matches": 1, "weight": 0.40},
    ],
}
# PHASE 2 note: add e.g. "paper_route" as a second /fed setup event_type choice,
# plus its own bracket dict here, once Phase 1 is proven live.

# ----------------------------- PAYOUT ENGINE --------------------------------
def compute_payouts(team_count: int, buy_in: float, mode: str,
                    boost_pct: int = 25, rake_pct: int = 10, winner_takes_all: bool = False):
    """Championship pay is locked in FIRST (rounded to nearest dollar).
    Earlier rounds are filled in working backward from the final; the
    OPENING sudden-death round absorbs whatever rounding remainder is left,
    so the pot always reconciles exactly to the dollar. Returns None if the
    buy-in is too low to pay every round at least $1/winner.

    winner_takes_all=True switches to a totally different payout shape: every
    round except the championship pays $0 (advance only, no withdrawal) and
    the champion takes the ENTIRE pot in one shot."""
    if buy_in <= 0:
        return None
    rounds_def, _byes, _pim = get_rounds_def(team_count)
    if rounds_def is None:
        return None
    gross = round(buy_in * team_count, 2)
    pot = gross * (1 + boost_pct / 100) if mode == "launch" else gross * (1 - rake_pct / 100)
    pot = round(pot)
    if pot <= 0:
        return None

    if winner_takes_all:
        computed = []
        for i, r in enumerate(rounds_def):
            is_final = (i == len(rounds_def) - 1)
            amount = pot if is_final else 0
            computed.append({**r, "per_winner": amount, "round_total": amount})
        return {
            "gross": gross, "pot": pot, "rounds": computed, "team_count": team_count,
            "mode": mode, "boost_pct": boost_pct, "rake_pct": rake_pct, "buy_in": buy_in,
            "winner_takes_all": True,
        }

    reverse_order = list(reversed(rounds_def))   # championship processed first
    remaining = pot
    computed = []
    for i, r in enumerate(reverse_order):
        is_opening_round = (i == len(reverse_order) - 1)   # absorbs the leftover
        if is_opening_round:
            round_total = remaining
        else:
            round_total = round(pot * r["weight"])
        if r["matches"] == 1:
            per_winner = round_total
        else:
            per_winner = round_total // r["matches"]
            round_total = per_winner * r["matches"]        # floor-adjusted actual
        if per_winner < 1 or round_total < 0:
            return None
        remaining -= round_total
        if remaining < 0:
            return None
        computed.append({**r, "per_winner": per_winner, "round_total": round_total})
    # Floor division on multi-winner rounds can leave a dollar or two of dust
    # unallocated (e.g. an odd remainder split across 2 or 4 winners). That
    # dust is real money and must never vanish — fold it into the championship
    # (computed[0], since reverse_order always processes the final round first),
    # which keeps the "championship pay is never shortchanged" rule intact.
    if remaining > 0:
        computed[0]["per_winner"] += remaining
        computed[0]["round_total"] += remaining
    computed.reverse()   # back to chronological order
    return {
        "gross": gross, "pot": pot, "rounds": computed, "team_count": team_count,
        "mode": mode, "boost_pct": boost_pct, "rake_pct": rake_pct, "buy_in": buy_in,
        "winner_takes_all": False,
    }

def find_min_viable_buyin(team_count: int, mode: str, boost_pct: int, rake_pct: int):
    """Search for a helpful 'try at least $X' hint when a buy-in is too low."""
    for cents in range(100, 20000, 100):
        buy_in = cents / 100
        if compute_payouts(team_count, buy_in, mode, boost_pct, rake_pct):
            return buy_in
    return None

def seed_round(order_list, round_key: str, fmt: str) -> dict:
    """Pairs up a list of captain ids into matches for a given round."""
    matches = {}
    n = len(order_list) // 2
    for i in range(n):
        mk = f"{round_key}-{i + 1}"
        matches[mk] = {"round": round_key, "a": order_list[2 * i], "b": order_list[2 * i + 1],
                      "winner": 0, "score_a": 0, "score_b": 0, "format": fmt, "paid": False}
    return matches

def get_rounds_def(n_captains: int):
    """Generalizes bracket construction to ANY captain count from 4 to 16 —
    not just the clean powers of two in TEAM_BRACKETS. Odd/non-power-of-2
    counts get a PLAY-IN round first (sudden death, smallest cut of the pot);
    high seeds who don't play advance on a bye straight into the main bracket.
    After the play-in resolves, the field is exactly a power of two again, so
    the remainder reuses the existing TEAM_BRACKETS structure untouched.

    Returns (rounds_def, byes, play_in_matches). byes=0 and play_in_matches=0
    means n_captains was already a clean power of two — no play-in needed."""
    if n_captains in TEAM_BRACKETS:
        return TEAM_BRACKETS[n_captains], 0, 0
    p = 1
    while p < n_captains:
        p *= 2
    if p // 2 not in TEAM_BRACKETS:
        return None, 0, 0   # out of supported range
    byes = p - n_captains
    play_in_matches = n_captains - p // 2
    remainder = TEAM_BRACKETS[p // 2]
    play_in_round = {"key": "PI", "label": "PLAY-IN \u2014 SUDDEN DEATH", "format": "BO1",
                     "matches": play_in_matches, "weight": 0.10}
    rescaled_remainder = [{**r, "weight": round(r["weight"] * 0.90, 4)} for r in remainder]
    return [play_in_round] + rescaled_remainder, byes, play_in_matches

# ----------------------------- STATE ----------------------------------------
STATE_LOCK = asyncio.Lock()

DEFAULT_STATE = {
    "config": {
        "guild_id": 0,
        "category_id": 0,
        "announce_ch": 0, "papertrail_ch": 0, "timeclock_ch": 0,
        "results_ch": 0, "hr_ch": 0, "roster_ch": 0,
        "admin_role": 0, "payroll_role": 0, "refunded_role": 0, "role_1099": 0,
        "captain_role": 0, "player_role": 0,
        "cashtag": "[$CASHTAG]",
        "event_type": "the_refund",
        "team_count": 4,
        "buy_in": 10.0,
        "mode": "launch",
        "boost_pct": 25,
        "rake_pct": 10,
        "fine_amount": 2,
        "admin_pin_hash": "",
        "archive_ch": 0,
        "tourney_category_id": 0,
        "tourney_counter": 0,
        "payout": None,             # set by compute_payouts() at /fed setup or reprice
        "run_number": 0,
        "auto_nightly": True,
        "setup_complete": False,
    },
    "registry": {
        "players": {},              # discord_id_str -> {"gamertag": str, "first_seen": date}
        "teams": {},                # team_name -> {"captain_id","logo_url","players":[discord_id,...],"created","updated"}
        "rosters": {},              # legacy: team_name -> {...} from /fed scan channel parsing (kept for backward compat)
        "fines_owed": {},           # discord_id_str -> dollars owed, auto-deducted from next withdrawal
        "fines_log": [],            # audit trail of every mismatch found
        "coach_pins": {},           # discord_id_str -> sha256 pin hash; set once at onboarding, required for every coach LOCK action
        "last_scanned_id": 0,       # incremental scan watermark
    },
    "night": {
        "phase": "idle",            # idle|announced|open|locked|live|complete
        "date": "",
        "team_count": 0,
        "payout": None,             # snapshot of config payout at announce time
        "teams": {},
        "standby": [],
        "rounds": [],                # chronological round keys, e.g. ["QF","SF","F"]
        "matches": {},                # mk -> {round,a,b,winner,score_a,score_b,format,paid}
        "current_round_index": 0,
        "announce_msg": 0,
        "clock_msg": 0,
        "live_channel_id": 0,        # per-night "war room" channel, created at lock, deleted on close
        "event_log": [],             # full chronological log -> printed out at close
    },
    "ledger": [],
    "tournaments": {},   # tourney_id -> independent tournament state, any number can run concurrently
    "fast5": {
        "config": {
            "guild_id": 0, "category_id": 0,
            "announce_ch": 0, "captains_ch": 0, "pg_ch": 0, "sg_ch": 0, "sf_ch": 0, "pf_ch": 0, "c_ch": 0,
            "draft_ch": 0, "papertrail_ch": 0, "results_ch": 0, "archive_ch": 0,
            "buy_in": 10.0, "mode": "launch", "boost_pct": 25, "rake_pct": 10, "payout_style": "split",
            "cashtag": "[$CASHTAG]", "min_captains": 4,
            "tip_times": [[17, 0], [19, 0], [21, 0]],   # ET [hour, minute] — 5pm/7pm/9pm
            "run_number": 0, "auto_run": True, "setup_complete": False,
        },
        "session": None,   # one active session at a time; None = idle between tip windows
    },
}


def _fresh_night() -> dict:
    return json.loads(json.dumps(DEFAULT_STATE["night"]))


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                st = json.load(f)
            for k, v in DEFAULT_STATE.items():
                st.setdefault(k, json.loads(json.dumps(v)))
            for k, v in DEFAULT_STATE["config"].items():
                st["config"].setdefault(k, v)
            for k, v in DEFAULT_STATE["night"].items():
                st["night"].setdefault(k, v)
            for k, v in DEFAULT_STATE["registry"].items():
                st["registry"].setdefault(k, json.loads(json.dumps(v)))
            st.setdefault("fast5", json.loads(json.dumps(DEFAULT_STATE["fast5"])))
            for k, v in DEFAULT_STATE["fast5"]["config"].items():
                st["fast5"].setdefault("config", {})
                st["fast5"]["config"].setdefault(k, v)
            st["fast5"].setdefault("session", None)
            return st
        except Exception as e:
            print(f"[state] load failed, starting fresh: {e}")
    return json.loads(json.dumps(DEFAULT_STATE))


STATE = load_state()


async def save_state():
    """Atomic write + read-back verification (assertion-guarded)."""
    async with STATE_LOCK:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(STATE, f, indent=2)
        os.replace(tmp, STATE_FILE)
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            json.load(f)  # raises if corrupt


# ----------------------------- BOT ------------------------------------------
intents = discord.Intents.default()
intents.members = True
intents.message_content = True   # required so /fed scan can read plain roster text in channel history

class FederalReserveBot(commands.Bot):
    """commands.Bot supports BOTH systems at once: the /fed slash commands
    (app_commands, via self.tree, provided automatically by this base class)
    and Fast 5's period-prefix commands (.fast5 ...) — two structurally
    different invocation systems so there's zero naming overlap between them,
    even though some underlying actions (board, report, close) are similarly
    named."""
    def __init__(self):
        super().__init__(command_prefix=".", intents=intents, help_command=None)

    async def setup_hook(self):
        self.add_view(ClockInView())
        self.add_view(DepositView())
        self.add_view(VerifyView())
        self.add_view(OverridePanelView())
        self.add_view(GuideView())
        self.add_view(MainMenuView())
        self.add_view(TourneyDepositView())
        self.add_view(TourneyVerifyView())
        self.add_view(CloseEventView())
        self.add_view(RefundCloseView())
        for pos in FAST5_POSITIONS:
            self.add_view(PositionQueueView(pos))
        self.add_view(CaptainQueueView())
        self.add_view(Fast5VerifyView())
        self.add_view(Fast5CloseView())
        gid = STATE["config"].get("guild_id") or 0
        if gid:
            guild_obj = discord.Object(id=gid)
            self.tree.copy_global_to(guild=guild_obj)
            await self.tree.sync(guild=guild_obj)
            # DE-DUPE: earlier boots (before guild_id existed) synced these
            # commands GLOBALLY. Once guild copies exist too, Discord shows
            # every command TWICE in the picker. Wipe the stale global set.
            self.tree.clear_commands(guild=None)
            await self.tree.sync()
            print("[FEDERAL RESERVE] command sync: guild-scoped only \u2014 stale global duplicates wiped.")
        else:
            await self.tree.sync()
        nightly_loop.start()
        fast5_loop.start()
        draft_clock_loop.start()

bot = FederalReserveBot()
_standalone_bot = bot
fed = app_commands.Group(name="fed", description="Federal Reserve Bot \u2014 THE REFUND machine")
bot.tree.add_command(fed)
tourney = app_commands.Group(name="tourney", description="Instant tournaments \u2014 any captain or admin can fire one up, anytime")
fed.add_command(tourney)

# ----------------------------- HELPERS --------------------------------------
def cfg(key):
    return STATE["config"].get(key, 0)

def is_admin(user) -> bool:
    if user.id == BOT_OWNER_ID:
        return True
    if not isinstance(user, discord.Member):
        return False
    role_id = cfg("admin_role")
    if role_id and any(r.id == role_id for r in user.roles):
        return True
    return bool(user.guild_permissions.administrator)

def team_of(captain_id: int):
    return STATE["night"]["teams"].get(str(captain_id))

def team_name(captain_id: int) -> str:
    t = STATE["night"]["teams"].get(str(captain_id))
    return t["name"] if t else "???"

def locked_count() -> int:
    return sum(1 for t in STATE["night"]["teams"].values() if t["deposit"])

def channel(client: discord.Client, key: str):
    ch_id = cfg(key)
    return client.get_channel(ch_id) if ch_id else None

def refund_live_channel(client):
    """The per-night 'war room' channel — created at lock, deleted at close.
    Falls back to the permanent announce channel if not yet created (e.g.
    a manual /fed force before lock has run)."""
    cid = STATE["night"].get("live_channel_id")
    ch = client.get_channel(cid) if cid else None
    return ch or channel(client, "announce_ch")

def round_def(round_key: str):
    night = STATE["night"]
    payout = night.get("payout")
    if not payout:
        return None
    return next((r for r in payout["rounds"] if r["key"] == round_key), None)

def receipt_block(team: str, match_key: str, label: str, amount: int, nxt: str) -> str:
    now = datetime.now(ET)
    night = STATE["night"]
    payout = night.get("payout") or {}
    pot = payout.get("pot", 0)
    remaining = pot - sum(
        e["amount"] for e in STATE["ledger"]
        if e["run"] == cfg("run_number") and e["date"] == night["date"])
    lines = [
        "        ATM PAYDAY", "     ADVANCE TO MONEY",
        "--------------------------",
        f"TERMINAL : THE REFUND #{cfg('run_number')}",
        f"DATE     : {now.strftime('%m/%d/%y %I:%M %p ET')}",
        f"CARDHOLDER: {team.upper()[:20]}",
        "--------------------------",
        f"TRANSACTION: {label} ({match_key})",
        "STATUS     : *** PAID ***", "",
        f"        ${amount}.00", "",
        "--------------------------",
        f"REMAINING IN VAULT: ${max(remaining, 0)}.00",
        f"NEXT STOP: {nxt}",
        "--------------------------",
        "EVERY ROUND IS A WITHDRAWAL.",
    ]
    return "```\n" + "\n".join(lines) + "\n```"

async def post_receipt(client, team: str, round_key: str, match_key: str, captain_id: int = None):
    night = STATE["night"]
    payout = night["payout"]
    rounds = payout["rounds"]
    idx = next(i for i, r in enumerate(rounds) if r["key"] == round_key)
    r = rounds[idx]
    is_final = (idx == len(rounds) - 1)
    wta = payout.get("winner_takes_all", False)

    if wta and not is_final:
        # Advance-only: no money moves, so no receipt, no fine deduction, and
        # no PAYROLL role yet — that's earned only when a real payout happens.
        ch = channel(client, "papertrail_ch")
        if ch:
            nxt_r = rounds[idx + 1]
            await ch.send(f"\u27A1\uFE0F **{team}** ADVANCES \u2014 {r['label']} ({match_key})\n"
                          f"**WINNER TAKES ALL** \u2014 the pot stays locked until {nxt_r['label']}. No payout yet.")
        return

    amount = r["per_winner"]
    label = "THE REFUND \u2014 CHAMPIONSHIP" if is_final else r["label"]
    if is_final:
        nxt = "EVENT COMPLETE \u2014 RUN IT BACK TOMORROW, 12AM ET"
    else:
        nxt_r = rounds[idx + 1]
        nxt = f"{nxt_r['label']} - ${nxt_r['per_winner']}"

    # Fine auto-deduction: pot accounting (ledger "amount" below) always reflects
    # the full per_winner amount consumed from the pot — fines are a SEPARATE
    # side-ledger against the captain, settled out of their own net payout,
    # never out of the pot itself.
    fine_note = ""
    net_amount = amount
    if captain_id is not None:
        reg = STATE["registry"]
        owed = reg["fines_owed"].get(str(captain_id), 0)
        if owed > 0:
            deduction = min(owed, amount)
            reg["fines_owed"][str(captain_id)] = owed - deduction
            net_amount = amount - deduction
            fine_note = f"\nFINE DEDUCTED: -${deduction} (roster mismatch) \u2014 **NET SEND: ${net_amount}**"

    ch = channel(client, "papertrail_ch")
    if ch:
        emb = discord.Embed(title="\U0001F4B0 WITHDRAWAL CONFIRMED \U0001F4B0",
                            description=receipt_block(team, match_key, label, amount, nxt) + fine_note, color=GOLD)
        emb.set_footer(text="THE PAPER TRAIL \u2022 ATM PAYDAY \u2022 APA ATM PRO AM BASKETBALL LEAGUE")
        await ch.send(embed=emb)
    STATE["ledger"].append({"run": cfg("run_number"), "date": night["date"],
                            "team": team, "round": round_key, "amount": amount,
                            "net_paid": net_amount, "ts": datetime.now(ET).isoformat()})
    log_event(night, f"WITHDRAWAL: {team} paid ${amount} for {label} ({match_key})")
    if captain_id is not None:
        await grant_role(client, captain_id, "payroll_role", "Cashed a withdrawal")
    await save_state()

async def grant_role(client, user_id: int, role_key: str, reason: str):
    gid, rid = cfg("guild_id"), cfg(role_key)
    if not (gid and rid):
        return
    guild = client.get_guild(gid)
    if not guild:
        return
    member = guild.get_member(user_id)
    role = guild.get_role(rid)
    if member and role and role not in member.roles:
        try:
            await member.add_roles(role, reason=reason)
        except discord.HTTPException:
            pass

def format_ladder(payout: dict) -> str:
    if payout.get("winner_takes_all"):
        final = payout["rounds"][-1]
        lines = [f"\U0001F3C6 **WINNER TAKES ALL** \u2014 the entire pot (${payout['pot']}) goes to the "
                f"{final['label']} winner. Every earlier round is advance-only, no payout."]
        for r in payout["rounds"][:-1]:
            lines.append(f"{r['label']} ({r['format']}): advance only \u2014 no payout")
        lines.append(f"{final['label']} ({final['format']}): **${final['per_winner']}** \u2014 winner takes it all")
        return "\n".join(lines)
    lines = []
    for r in payout["rounds"]:
        lines.append(f"{r['label']} ({r['format']}): **${r['per_winner']}**/winner "
                    f"\u2014 {r['matches']} match{'es' if r['matches'] != 1 else ''}, ${r['round_total']} total")
    return "\n".join(lines)

# ----------------------------- INSTANT TOURNAMENTS --------------------------
# Any admin or CAPTAIN can spin one up, any time, in its own dedicated channel.
# Multiple can run concurrently — each is scoped entirely by which channel a
# command is run in, so they never collide with each other or with the
# nightly Refund (STATE["night"]). Non-admin-started tourneys are hard-forced
# to zero rake / zero boost: the pot is exactly what was collected, no more,
# no less, and the money flows to the HOST's own cashtag, not the league's.
def can_start_tourney(user) -> bool:
    if is_admin(user):
        return True
    role_id = cfg("captain_role")
    return bool(role_id and isinstance(user, discord.Member) and any(r.id == role_id for r in user.roles))

def is_tourney_boss(user, t: dict) -> bool:
    """Server admin OR the person who personally started this tournament."""
    return is_admin(user) or user.id == t["creator_id"]

def hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.strip().encode()).hexdigest()

def admin_pin_matches(pin_attempt: str) -> bool:
    """Lets a trusted non-admin unlock a single sensitive action (like closing
    an event) by entering a PIN, without needing the full Discord admin role."""
    stored = cfg("admin_pin_hash")
    return bool(stored) and hash_pin(pin_attempt) == stored

def find_tourney_by_channel(channel_id: int):
    for t in STATE["tournaments"].values():
        if t["channel_id"] == channel_id:
            return t
    return None

def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9\- ]", "", text.lower()).strip()
    s = re.sub(r"\s+", "-", s)
    return s[:80] or "tourney"

def team_name_t(t: dict, captain_id: int) -> str:
    tm = t["teams"].get(str(captain_id))
    return tm["name"] if tm else "???"

# ----------------------------- SECURITY: ADMIN PIN GATE ---------------------
# EVERY admin action is PIN-locked. Entering the PIN once opens a short
# session so an admin isn't re-typing it for every single verify click.
ADMIN_PIN_SESSION_MINUTES = 15
_admin_pin_sessions: dict = {}   # user_id -> session expiry (datetime, ET)

def unlock_admin(user_id: int):
    _admin_pin_sessions[user_id] = datetime.now(ET) + timedelta(minutes=ADMIN_PIN_SESSION_MINUTES)

def admin_unlocked(user_id: int) -> bool:
    exp = _admin_pin_sessions.get(user_id)
    return bool(exp and datetime.now(ET) < exp)

class AdminPinUnlockModal(discord.ui.Modal, title="\U0001F510 Admin PIN Required"):
    pin = discord.ui.TextInput(label="Enter the admin PIN", max_length=8)

    async def on_submit(self, itx: discord.Interaction):
        if not admin_pin_matches(self.pin.value):
            print(f"[SECURITY] FAILED admin PIN attempt by {itx.user} ({itx.user.id})")
            return await itx.response.send_message("\u274C Wrong PIN. This attempt was logged.", ephemeral=True)
        unlock_admin(itx.user.id)
        print(f"[SECURITY] admin PIN accepted \u2014 {itx.user} ({itx.user.id}) unlocked for {ADMIN_PIN_SESSION_MINUTES} min")
        await itx.response.send_message(
            f"\u2705 Admin actions unlocked for **{ADMIN_PIN_SESSION_MINUTES} minutes** \u2014 "
            f"re-run the action you just tried.", ephemeral=True)

async def ensure_admin_pin(itx: discord.Interaction) -> bool:
    """The single gate in front of EVERYTHING admin-related.
    True  -> caller may proceed (admin role + live PIN session).
    False -> this function already responded (denial, bootstrap notice, or
             the PIN modal) \u2014 caller must just `return`."""
    if not is_admin(itx.user):
        await itx.response.send_message("Admins only.", ephemeral=True)
        return False
    if admin_unlocked(itx.user.id):
        return True
    if not cfg("admin_pin_hash"):
        await itx.response.send_message(
            "\U0001F510 Every admin action is PIN-locked, but no admin PIN exists yet \u2014 "
            "run `/fed pin` once to set it.", ephemeral=True)
        return False
    await itx.response.send_modal(AdminPinUnlockModal())
    return False

async def ensure_boss_pin(itx: discord.Interaction, t: dict) -> bool:
    """Tournament actions: the HOST runs their own event freely; an ADMIN
    stepping in must clear the admin PIN gate like everywhere else."""
    if itx.user.id == t["creator_id"]:
        return True
    if not is_admin(itx.user):
        await itx.response.send_message("Only the tournament host or an admin can do this.", ephemeral=True)
        return False
    return await ensure_admin_pin(itx)

# ----------------------------- SECURITY: COACH PIN ---------------------------
# Every coach sets a personal PIN at onboarding; every LOCK action a coach
# takes (roster lock, entering an event) requires that same PIN.
def coach_pin_set(captain_id: int) -> bool:
    return bool(STATE["registry"].get("coach_pins", {}).get(str(captain_id)))

def set_coach_pin(captain_id: int, pin: str):
    STATE["registry"].setdefault("coach_pins", {})[str(captain_id)] = hash_pin(pin)

def coach_pin_matches(captain_id: int, attempt: str) -> bool:
    stored = STATE["registry"].get("coach_pins", {}).get(str(captain_id), "")
    return bool(stored) and hash_pin(attempt) == stored

class CoachPinSetModal(discord.ui.Modal, title="Onboarding \u2014 Set Your Coach PIN"):
    pin = discord.ui.TextInput(label="New Coach PIN (4-8 digits)", max_length=8)
    confirm = discord.ui.TextInput(label="Confirm PIN", max_length=8)

    def __init__(self, captain_id: int, after):
        super().__init__()
        self.captain_id = captain_id
        self.after = after   # async callable(itx) run once the PIN is set

    async def on_submit(self, itx: discord.Interaction):
        p = self.pin.value.strip()
        if not (p.isdigit() and 4 <= len(p) <= 8):
            return await itx.response.send_message("Coach PIN must be 4-8 digits \u2014 start the action again.",
                                                    ephemeral=True)
        if p != self.confirm.value.strip():
            return await itx.response.send_message("PINs don't match \u2014 start the action again.", ephemeral=True)
        set_coach_pin(self.captain_id, p)
        await save_state()
        print(f"[SECURITY] coach PIN SET for captain {self.captain_id}")
        await self.after(itx)

class CoachPinCheckModal(discord.ui.Modal, title="\U0001F510 Coach PIN Required to Lock"):
    pin = discord.ui.TextInput(label="Enter YOUR Coach PIN", max_length=8)

    def __init__(self, captain_id: int, after):
        super().__init__()
        self.captain_id = captain_id
        self.after = after

    async def on_submit(self, itx: discord.Interaction):
        if not coach_pin_matches(self.captain_id, self.pin.value.strip()):
            print(f"[SECURITY] FAILED coach PIN attempt by {itx.user} ({itx.user.id}) for captain {self.captain_id}")
            return await itx.response.send_message(
                "\u274C Wrong Coach PIN \u2014 lock actions require the PIN you set at onboarding. "
                "Forgot it? An admin can wipe it with `/fed coachpin`.", ephemeral=True)
        await self.after(itx)

async def gate_coach_pin(itx: discord.Interaction, captain_id: int, after):
    """First-ever lock action = onboarding: coach sets his own PIN right there.
    Every lock after that requires the same PIN."""
    if TEST_MODE:
        print(f"[TEST] coach PIN bypassed for {itx.user}")
        return await after(itx)
    if coach_pin_set(captain_id):
        await itx.response.send_modal(CoachPinCheckModal(captain_id, after))
    else:
        await itx.response.send_modal(CoachPinSetModal(captain_id, after))

# ----------------------------- CRASH-RECOVERY CHECKPOINTS -------------------
# After EVERY completed step the bot (1) prints the full state to the console
# and (2) posts a copy/paste-ready plain-text block in Discord, so if it
# crashes mid-selections an admin can scroll up and take over manually.
async def send_copy_block(ch, header: str, block: str, filename: str = "checkpoint.txt"):
    if not ch:
        return
    try:
        if len(block) <= 1850:
            await ch.send(f"{header}\n```txt\n{block}\n```")
        else:
            import io as _io
            await ch.send(content=header,
                          file=discord.File(_io.BytesIO(block.encode("utf-8")), filename=filename))
    except discord.HTTPException as e:
        print(f"[checkpoint] Discord post failed: {e}")

def render_refund_checkpoint(night: dict) -> str:
    ts = datetime.now(ET).strftime("%m/%d %I:%M:%S %p ET")
    lines = [f"THE REFUND Run #{cfg('run_number')} \u2014 CHECKPOINT {ts}",
             f"PHASE: {night['phase'].upper()}   SQUADS: {len(night['teams'])}/{cfg('team_count')}   BUY-IN: ${cfg('buy_in'):g}",
             "", "SQUADS (copy/paste for manual takeover):"]
    for cid, t in night["teams"].items():
        flags = ("LOCKED" if t["deposit"] else "pending")                 + (" +PROOF" if t.get("proof") else "")                 + (" +CLOCKED" if t.get("clocked") else "")                 + (" DECLINED" if t.get("declined") else "")
        lines.append(f"  {t['name']} | capt <@{cid}> | id {cid} | {flags}")
    if night.get("standby"):
        lines.append("STANDBY: " + ", ".join(f"{sb['name']} (<@{sb['captain_id']}>)" for sb in night["standby"]))
    if night.get("matches"):
        lines.append("")
        lines.append("BRACKET:")
        for rk in night.get("rounds", []):
            lines.append(f"  [{rk}]")
            for mk, m in sorted(night["matches"].items()):
                if m["round"] != rk:
                    continue
                res = f" -> WINNER {team_name(m['winner'])}" if m["winner"] else ""
                lines.append(f"    {mk}: {team_name(m['a'])} {m['score_a']}-{m['score_b']} {team_name(m['b'])}"
                             f" ({m['format']}){res}")
    return "\n".join(lines)

async def post_refund_checkpoint(client, step: str):
    night = STATE["night"]
    block = render_refund_checkpoint(night)
    print(f"[CHECKPOINT][REFUND] STEP COMPLETE: {step}\n{block}")
    ch = refund_live_channel(client)
    await send_copy_block(ch, f"\u2705 **STEP COMPLETE \u2014 {step}**", block,
                          f"refund_r{cfg('run_number')}_checkpoint.txt")

def render_tourney_checkpoint(t: dict) -> str:
    ts = datetime.now(ET).strftime("%m/%d %I:%M:%S %p ET")
    lines = [f"INSTANT TOURNEY {t['id']} \u2014 CHECKPOINT {ts}",
             f"PHASE: {t['phase'].upper()}   HOST: <@{t['creator_id']}>   SIZE: {t['team_count']}   BUY-IN: ${t['buy_in']:g}",
             "", "SQUADS (copy/paste for manual takeover):"]
    for cid, tm in t["teams"].items():
        lines.append(f"  {tm['name']} | capt <@{cid}> | id {cid} | {'LOCKED' if tm['deposit'] else 'pending'}")
    if t.get("matches"):
        lines.append("")
        lines.append("BRACKET:")
        for mk, m in sorted(t["matches"].items()):
            res = f" -> WINNER {team_name_t(t, m['winner'])}" if m["winner"] else ""
            lines.append(f"  {mk} [{m['round']}]: {team_name_t(t, m['a'])} {m['score_a']}-{m['score_b']} "
                         f"{team_name_t(t, m['b'])} ({m['format']}){res}")
    return "\n".join(lines)

async def post_tourney_checkpoint(client, t: dict, step: str):
    block = render_tourney_checkpoint(t)
    print(f"[CHECKPOINT][TOURNEY {t['id']}] STEP COMPLETE: {step}\n{block}")
    ch = client.get_channel(t["channel_id"])
    await send_copy_block(ch, f"\u2705 **STEP COMPLETE \u2014 {step}**", block, f"{t['id']}_checkpoint.txt")

# ----------------------------- ONE-COMMAND SETUP ----------------------------
async def build_infrastructure(guild: discord.Guild) -> str:
    """Creates category, channels, and roles if they don't already exist.
    Idempotent: re-running /fed setup will not duplicate anything."""
    c = STATE["config"]
    log = []

    def find_role(name):
        return discord.utils.get(guild.roles, name=name)

    def find_channel(cat, name):
        return discord.utils.get(cat.text_channels, name=name) if cat else None

    admin_role = find_role("APA Admin") or find_role("QCL Admin") or find_role("Admin")
    if not admin_role:
        admin_role = await guild.create_role(name="APA Admin", colour=discord.Colour(GOLD),
                                             hoist=True, reason="Federal Reserve Bot setup")
        log.append("created role @APA Admin")
    elif admin_role.name == "QCL Admin":
        await admin_role.edit(name="APA Admin", reason="Federal Reserve Bot setup \u2014 APA rebrand")
        log.append("renamed @QCL Admin \u2192 @APA Admin")
    c["admin_role"] = admin_role.id

    for key, name in (("payroll_role", ROLE_PAYROLL), ("refunded_role", ROLE_REFUNDED), ("role_1099", ROLE_1099),
                     ("captain_role", ROLE_CAPTAIN), ("player_role", ROLE_PLAYER)):
        existing = find_role(name)
        if not existing:
            existing = await guild.create_role(name=name, colour=discord.Colour(GOLD if name == ROLE_PAYROLL else NAVY),
                                               hoist=(name in (ROLE_CAPTAIN, ROLE_PLAYER)),
                                               reason="Federal Reserve Bot setup")
            log.append(f"created role @{name}")
        c[key] = existing.id

    category = discord.utils.get(guild.categories, name=CATEGORY_NAME)
    if not category:
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(send_messages=False, view_channel=True),
            admin_role: discord.PermissionOverwrite(send_messages=True, view_channel=True),
            guild.me: discord.PermissionOverwrite(send_messages=True, view_channel=True, manage_channels=True),
        }
        category = await guild.create_category(CATEGORY_NAME, overwrites=overwrites,
                                                reason="Federal Reserve Bot setup")
        log.append(f"created category {CATEGORY_NAME}")
    c["category_id"] = category.id

    open_overwrites = {
        guild.default_role: discord.PermissionOverwrite(send_messages=True, view_channel=True),
        admin_role: discord.PermissionOverwrite(send_messages=True, view_channel=True),
    }
    locked_overwrites = {
        guild.default_role: discord.PermissionOverwrite(send_messages=False, view_channel=True),
        admin_role: discord.PermissionOverwrite(send_messages=True, view_channel=True),
    }

    async def ensure_channel(name, key, overwrites, topic, position=None):
        existing = find_channel(category, name)
        if not existing:
            # check for a pre-emoji legacy channel and rename it in place
            # (preserves history/messages) instead of creating a duplicate.
            legacy_name = LEGACY_CHANNEL_NAMES.get(key)
            legacy = find_channel(category, legacy_name) if legacy_name else None
            if legacy:
                existing = legacy
                await existing.edit(name=name, reason="Federal Reserve Bot setup \u2014 emoji + permission refresh")
                log.append(f"renamed #{legacy_name} \u2192 #{name}")
            else:
                existing = await guild.create_text_channel(name, category=category, overwrites=overwrites,
                                                            topic=topic, reason="Federal Reserve Bot setup")
                log.append(f"created #{name}")
        else:
            # re-apply overwrites even on existing channels — catches permission
            # changes like #the-refund moving from open to admin-only.
            await existing.edit(overwrites=overwrites, topic=topic)
        if position is not None:
            try:
                await existing.edit(position=position)
            except discord.HTTPException:
                pass
        c[key] = existing.id

    # Ordered by when they're actually triggered: registration is ongoing/first,
    # then the nightly flow (announce -> time clock -> results -> receipts),
    # HR last since disputes are a last resort.
    await ensure_channel(CH_ROSTER, "roster_ch", open_overwrites,
                        "Post your roster: TEAM: name, then GT: exact-gamertag - @player per line. "
                        "Exact match required or a fine applies.", position=0)
    await ensure_channel(CH_ANNOUNCE, "announce_ch", locked_overwrites,
                        "ADMIN-ONLY. Nightly THE REFUND announcements + setup confirmations post here. Use /fed enter to get in.",
                        position=1)
    await ensure_channel(CH_TIMECLOCK, "timeclock_ch", open_overwrites,
                        "Clock in here before tip-off or it's CARD DECLINED.", position=2)
    await ensure_channel(CH_RESULTS, "results_ch", open_overwrites,
                        "Report your win here with the end-game screenshot.", position=3)
    await ensure_channel(CH_PAPERTRAIL, "papertrail_ch", locked_overwrites,
                        "Every withdrawal, posted the second it's paid.", position=4)
    await ensure_channel(CH_HR, "hr_ch", open_overwrites,
                        "Disputes go here with proof. Commissioner rulings are final.", position=5)

    c["setup_complete"] = True
    await save_state()
    return "\n".join(log) if log else "Everything already existed \u2014 nothing new to create."

# ----------------------------- VIEWS ----------------------------------------
class DepositView(discord.ui.View):
    """Admin confirms a captain's Cash App deposit. Captain id lives in embed footer -> persistent."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Confirm Deposit", style=discord.ButtonStyle.success, custom_id="fed:confirm_deposit")
    async def confirm(self, itx: discord.Interaction, _btn: discord.ui.Button):
        if not await ensure_admin_pin(itx):
            return
        try:
            cid = int(itx.message.embeds[0].footer.text.split(":")[-1])
        except (IndexError, ValueError, AttributeError):
            return await itx.response.send_message("Could not resolve captain from this message.", ephemeral=True)
        t = team_of(cid)
        if not t:
            return await itx.response.send_message("Squad not found for tonight.", ephemeral=True)
        if t["deposit"]:
            return await itx.response.send_message("Already confirmed.", ephemeral=True)
        cap = STATE["night"].get("team_count") or cfg("team_count")
        if locked_count() >= cap:
            return await itx.response.send_message("Machine is full \u2014 move them to standby instead.", ephemeral=True)
        t["deposit"] = True
        await save_state()
        await itx.response.edit_message(
            content=f"\u2705 **DEPOSIT CONFIRMED** \u2014 {t['name']} is LOCKED ({locked_count()}/{cap}).", view=None)
        ch = channel(itx.client, "announce_ch")
        if ch:
            full = locked_count() >= cap
            await ch.send(f"\U0001F3E7 **{t['name']}** locked in. **{locked_count()}/{cap} SQUADS.** "
                          + ("**MACHINE FULL.**" if full else "DM your deposit to claim a slot."))
        await post_refund_checkpoint(itx.client,
                                     f"DEPOSIT CONFIRMED \u2014 {t['name']} locked ({locked_count()}/{cap})")

class ClockInView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="\u23F0 CLOCK IN", style=discord.ButtonStyle.primary, custom_id="fed:clockin")
    async def clockin(self, itx: discord.Interaction, _btn: discord.ui.Button):
        t = team_of(itx.user.id)
        if not (t and t["deposit"]):
            return await itx.response.send_message("You're not a locked captain tonight.", ephemeral=True)
        if t["clocked"]:
            return await itx.response.send_message("Already clocked in. \u2705", ephemeral=True)
        t["clocked"] = True
        await save_state()
        await itx.response.send_message(f"\u2705 **{t['name']}** clocked in. Machine tips at 12:00 ET.")

class VerifyView(discord.ui.View):
    """Admin verifies a reported GAME. Match key + side ride in embed footer -> persistent.
    BO1 = first verified game decides it. BO3 = first side to 2 verified games wins."""
    def __init__(self):
        super().__init__(timeout=None)

    def _resolve(self, itx: discord.Interaction):
        foot = itx.message.embeds[0].footer.text  # "match:{mk}:side:{a|b}"
        parts = foot.split(":")
        return parts[1], parts[3]

    @discord.ui.button(label="\u2705 Verify Game", style=discord.ButtonStyle.success, custom_id="fed:verify")
    async def verify(self, itx: discord.Interaction, _btn: discord.ui.Button):
        if not await ensure_admin_pin(itx):
            return
        try:
            mk, side = self._resolve(itx)
        except (IndexError, AttributeError):
            return await itx.response.send_message("Could not resolve this report.", ephemeral=True)
        night = STATE["night"]
        m = night["matches"].get(mk)
        if not m or m["winner"]:
            return await itx.response.send_message("Match not found or already settled.", ephemeral=True)
        if side not in ("a", "b"):
            return await itx.response.send_message("Malformed report.", ephemeral=True)
        m[f"score_{side}"] += 1
        target = 1 if m["format"] == "BO1" else 2
        cur = m[f"score_{side}"]
        if cur >= target:
            winner_id = m[side]
            m["winner"] = winner_id
            m["paid"] = True
            await save_state()
            await itx.response.edit_message(
                content=f"\u2705 **VERIFIED** \u2014 {team_name(winner_id)} takes {mk} "
                       f"({m['score_a']}-{m['score_b']}).", view=None)
            await post_receipt(itx.client, team_name(winner_id), m["round"], mk, winner_id)
            await advance_bracket(itx.client)
            await post_refund_checkpoint(itx.client,
                                         f"MATCH VERIFIED \u2014 {team_name(winner_id)} takes {mk}")
        else:
            await save_state()
            rd = round_def(m["round"])
            need = target
            await itx.response.edit_message(
                content=f"\u2705 Game recorded. **Series: {team_name(m['a'])} {m['score_a']} \u2013 "
                       f"{m['score_b']} {team_name(m['b'])}.** First to {need} wins "
                       f"{rd['label'] if rd else m['round']}. Waiting on next game report.", view=None)
            await post_refund_checkpoint(itx.client,
                                         f"GAME VERIFIED \u2014 {mk} now {m['score_a']}-{m['score_b']}")

    @discord.ui.button(label="\u274C Reject", style=discord.ButtonStyle.danger, custom_id="fed:reject")
    async def reject(self, itx: discord.Interaction, _btn: discord.ui.Button):
        if not await ensure_admin_pin(itx):
            return
        hr = channel(itx.client, "hr_ch")
        note = f" Take it to {hr.mention}." if hr else ""
        await itx.response.edit_message(content=f"\u274C Report rejected.{note}", view=None)

class TourneyDepositView(discord.ui.View):
    """Same pattern as DepositView, but scoped to an arbitrary tournament via
    tourney_id encoded in the embed footer — one persistent view instance
    handles deposit confirmation for every concurrent instant tournament."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Confirm Deposit", style=discord.ButtonStyle.success, custom_id="itourney:confirm_deposit")
    async def confirm(self, itx: discord.Interaction, _btn: discord.ui.Button):
        try:
            parts = itx.message.embeds[0].footer.text.split(":")  # "tourney:{id}:captain:{cid}"
            tourney_id, cid = parts[1], int(parts[3])
        except (IndexError, ValueError, AttributeError):
            return await itx.response.send_message("Could not resolve this deposit.", ephemeral=True)
        t = STATE["tournaments"].get(tourney_id)
        if not t:
            return await itx.response.send_message("This tournament no longer exists.", ephemeral=True)
        if not await ensure_boss_pin(itx, t):
            return
        tm = t["teams"].get(str(cid))
        if not tm:
            return await itx.response.send_message("Squad not found.", ephemeral=True)
        if tm["deposit"]:
            return await itx.response.send_message("Already confirmed.", ephemeral=True)
        locked = sum(1 for x in t["teams"].values() if x["deposit"])
        if locked >= t["team_count"]:
            return await itx.response.send_message("Tournament is already full.", ephemeral=True)
        tm["deposit"] = True
        await save_state()
        locked = sum(1 for x in t["teams"].values() if x["deposit"])
        full = locked >= t["team_count"]
        await itx.response.edit_message(
            content=f"\u2705 **DEPOSIT CONFIRMED** \u2014 {tm['name']} LOCKED ({locked}/{t['team_count']}).", view=None)
        ch = itx.client.get_channel(t["channel_id"])
        if ch:
            await ch.send(f"\U0001F3E7 **{tm['name']}** locked in. **{locked}/{t['team_count']}.**"
                          + (" **TOURNAMENT FULL \u2014 STARTING NOW.**" if full else ""))
        await post_tourney_checkpoint(itx.client, t,
                                      f"DEPOSIT CONFIRMED \u2014 {tm['name']} locked ({locked}/{t['team_count']})")
        if full:
            await tourney_lock_and_start(itx.client, t)
            await post_tourney_checkpoint(itx.client, t, "TOURNAMENT LOCKED \u2014 bracket seeded")

class TourneyVerifyView(discord.ui.View):
    """Same BO1/BO3 game-by-game scoring as VerifyView, scoped to whichever
    tournament the footer points at."""
    def __init__(self):
        super().__init__(timeout=None)

    def _resolve(self, itx: discord.Interaction):
        parts = itx.message.embeds[0].footer.text.split(":")  # "tourney:{id}:match:{mk}:side:{side}"
        return parts[1], parts[3], parts[5]

    @discord.ui.button(label="\u2705 Verify Game", style=discord.ButtonStyle.success, custom_id="itourney:verify")
    async def verify(self, itx: discord.Interaction, _btn: discord.ui.Button):
        try:
            tourney_id, mk, side = self._resolve(itx)
        except (IndexError, AttributeError):
            return await itx.response.send_message("Could not resolve this report.", ephemeral=True)
        t = STATE["tournaments"].get(tourney_id)
        if not t:
            return await itx.response.send_message("This tournament no longer exists.", ephemeral=True)
        if not await ensure_boss_pin(itx, t):
            return
        m = t["matches"].get(mk)
        if not m or m["winner"]:
            return await itx.response.send_message("Match not found or already settled.", ephemeral=True)
        if side not in ("a", "b"):
            return await itx.response.send_message("Malformed report.", ephemeral=True)
        m[f"score_{side}"] += 1
        target = 1 if m["format"] == "BO1" else 2
        cur = m[f"score_{side}"]
        if cur >= target:
            winner_id = m[side]
            m["winner"] = winner_id
            m["paid"] = True
            await save_state()
            await itx.response.edit_message(
                content=f"\u2705 **VERIFIED** \u2014 {team_name_t(t, winner_id)} takes {mk} "
                       f"({m['score_a']}-{m['score_b']}).", view=None)
            await post_tourney_receipt(itx.client, t, team_name_t(t, winner_id), m["round"], mk, winner_id)
            await advance_tourney_bracket(itx.client, t)
            await post_tourney_checkpoint(itx.client, t,
                                          f"MATCH VERIFIED \u2014 {team_name_t(t, winner_id)} takes {mk}")
        else:
            await save_state()
            rd = next((r for r in t["payout"]["rounds"] if r["key"] == m["round"]), None)
            await itx.response.edit_message(
                content=f"\u2705 Game recorded. **Series: {team_name_t(t, m['a'])} {m['score_a']} \u2013 "
                       f"{m['score_b']} {team_name_t(t, m['b'])}.** First to {target} wins "
                       f"{rd['label'] if rd else m['round']}.", view=None)

    @discord.ui.button(label="\u274C Reject", style=discord.ButtonStyle.danger, custom_id="itourney:reject")
    async def reject(self, itx: discord.Interaction, _btn: discord.ui.Button):
        try:
            tourney_id, _, _ = self._resolve(itx)
        except (IndexError, AttributeError):
            return await itx.response.send_message("Could not resolve this report.", ephemeral=True)
        t = STATE["tournaments"].get(tourney_id)
        if t and not await ensure_boss_pin(itx, t):
            return
        await itx.response.edit_message(content="\u274C Report rejected.", view=None)

class RepriceModal(discord.ui.Modal, title="Reprice THE REFUND"):
    new_buy_in = discord.ui.TextInput(label="New buy-in per squad ($)", placeholder="e.g. 12", max_length=8)

    async def on_submit(self, itx: discord.Interaction):
        if not (is_admin(itx.user) and admin_unlocked(itx.user.id)):
            return await itx.response.send_message(
                "\U0001F510 Admin PIN session expired \u2014 run `/fed unlock`, then try again.", ephemeral=True)
        try:
            val = float(self.new_buy_in.value)
            if val <= 0:
                raise ValueError
        except ValueError:
            return await itx.response.send_message("Enter a positive number.", ephemeral=True)
        wta = cfg("payout") and cfg("payout").get("winner_takes_all", False)
        payout = compute_payouts(cfg("team_count"), val, cfg("mode"), cfg("boost_pct"), cfg("rake_pct"),
                                 winner_takes_all=wta)
        if not payout:
            minv = find_min_viable_buyin(cfg("team_count"), cfg("mode"), cfg("boost_pct"), cfg("rake_pct"))
            hint = f" Try at least ${minv:.0f}." if minv else ""
            return await itx.response.send_message(
                f"\u274C ${val:g} is too low for this bracket size/mode.{hint}", ephemeral=True)
        STATE["config"]["buy_in"] = val
        STATE["config"]["payout"] = payout
        await save_state()
        await itx.response.send_message(
            f"\u2705 Repriced to **${val:g}/squad**. Takes effect the NEXT announced night \u2014 "
            f"tonight's bracket, if already locked, keeps its original price.\n\n{format_ladder(payout)}",
            ephemeral=True)

def guide_admin_embed():
    e = discord.Embed(title="\U0001F6E0\uFE0F ADMIN SETUP GUIDE", color=NAVY,
                      description="First-time setup, start to finish.")
    e.add_field(name="1\uFE0F\u20E3 Build the machine",
               value="`/fed setup` \u2014 pick event type, bracket size (4 or 8), mode, buy-in, cashtag. "
                     "Creates every channel, role, and the payout ladder automatically.", inline=False)
    e.add_field(name="2\uFE0F\u20E3 Confirm the channels",
               value="Check the \U0001F3E7 ATM PAYDAY category: \U0001F9FE-the-refund (admin-only announcements), "
                     "\U0001F4B0-the-paper-trail (receipts), \u23F0-time-clock, \U0001F4E5-results-drop, "
                     "\u2696\uFE0F-hr, \U0001F4CB-roster-book.", inline=False)
    e.add_field(name="3\uFE0F\u20E3 Get players registered",
               value="Tell everyone to run `/fed player register` with their exact gamertag.", inline=False)
    e.add_field(name="4\uFE0F\u20E3 Get captains registered",
               value="Captains run `/fed team register` \u2014 name, logo, then pick their roster from a "
                     "live member list.", inline=False)
    e.add_field(name="5\uFE0F\u20E3 Post the Override Panel",
               value="`/fed panel` \u2014 a dropdown for every manual action (announce, lock, reprice, reboot) "
                     "so you never have to memorize commands.", inline=False)
    e.add_field(name="6\uFE0F\u20E3 Let it run itself",
               value="Nightly loop is hands-off: 11:00 PM announce \u2192 11:30 open \u2192 11:50 lock \u2192 "
                     "12:00 tip.", inline=False)
    e.add_field(name="7\uFE0F\u20E3 Stuck? Reboot",
               value="`/fed reboot` or the Override Panel's \U0001F50C Reboot Bot option \u2014 hard-restarts "
                     "the process, nothing is lost.", inline=False)
    e.add_field(name="8\uFE0F\u20E3 Keep the ledger clean",
               value="`/fed scan` catches manually-posted rosters. `/fed ledger` reviews the full player/team "
                     "registry and outstanding fines.", inline=False)
    return e

def guide_captain_embed():
    e = discord.Embed(title="\U0001F451 CAPTAIN GUIDE", color=GOLD,
                      description="Register once, then run it every night.")
    e.add_field(name="1\uFE0F\u20E3 Get your players registered first",
               value="Every player on your squad (you included) must run `/fed player register` with their "
                     "EXACT gamertag before you can roster them.", inline=False)
    e.add_field(name="2\uFE0F\u20E3 Register your team",
               value="`/fed team register` \u2014 team name + logo upload.", inline=False)
    e.add_field(name="3\uFE0F\u20E3 Pick your roster",
               value="The bot replies with a live member picker \u2014 select up to 8 players. Anyone not yet "
                     "registered gets flagged and blocks the submission.", inline=False)
    e.add_field(name="4\uFE0F\u20E3 Enter each night",
               value=f"After the 11:00 PM announcement drops, run `/fed enter` once deposits open at 11:30 \u2014 "
                     f"tonight's bracket is **{cfg('team_count')} squads**. Attach an elimination screenshot "
                     f"from another bracket for priority seeding.", inline=False)
    e.add_field(name="5\uFE0F\u20E3 Pay the buy-in",
               value=f"Cash App **${cfg('buy_in'):g}** to **{cfg('cashtag')}** with your team name in the note.",
               inline=False)
    e.add_field(name="6\uFE0F\u20E3 Get confirmed",
               value="An admin taps Confirm Deposit on your pending card \u2014 you're locked once that happens. "
                     "Check `/fed board` for live status.", inline=False)
    e.add_field(name="7\uFE0F\u20E3 Clock in",
               value="Before 12:00 AM tip-off, clock in at \u23F0-time-clock (or `/fed clockin`). Miss it and "
                     "it's an automatic forfeit \u2014 CARD DECLINED.", inline=False)
    e.add_field(name="8\uFE0F\u20E3 Report your wins",
               value="Win a game, screenshot the final score, run `/fed report`. Admin verifies \u2192 the "
                     "withdrawal hits the-paper-trail immediately.", inline=False)
    return e

def guide_player_embed():
    e = discord.Embed(title="\U0001F3AE PLAYER GUIDE", color=GREEN,
                      description="Two steps, then you're in the system for good.")
    e.add_field(name="1\uFE0F\u20E3 Register your gamertag",
               value="`/fed player register gamertag:<your exact in-game name>` \u2014 one time only. "
                     "Case-sensitive, locked in forever as your canonical ID.", inline=False)
    e.add_field(name="2\uFE0F\u20E3 Get rostered",
               value="Your captain picks you from a live member list when they run `/fed team register`. "
                     "You get the PLAYER role automatically once you're registered.", inline=False)
    e.add_field(name="3\uFE0F\u20E3 Show up and clock in",
               value="Once your squad is locked for the night, clock in at \u23F0-time-clock before 12:00 AM "
                     "tip-off or your team risks an automatic forfeit.", inline=False)
    return e

def guide_commands_embed():
    e = discord.Embed(title="\U0001F4CB FULL COMMAND REFERENCE", color=NAVY)
    e.add_field(name="\U0001F6E0\uFE0F Admin", value=(
        "`/fed setup` \u2014 build the whole machine\n"
        "`/fed panel` \u2014 repost the Override Panel\n"
        "`/fed reboot` \u2014 hard-restart the bot\n"
        "`/fed scan` \u2014 rebuild the roster ledger from channel history\n"
        "`/fed ledger` \u2014 view players, teams, fines"), inline=False)
    e.add_field(name="\U0001F451 Captain", value=(
        "`/fed team register` \u2014 register team name/logo/roster\n"
        "`/fed enter` \u2014 enter tonight's bracket\n"
        "`/fed standby` \u2014 join the standby list"), inline=False)
    e.add_field(name="\U0001F3AE Player", value=(
        "`/fed player register` \u2014 register your gamertag\n"
        "`/fed roster` \u2014 show the manual roster-post format"), inline=False)
    e.add_field(name="\U0001F30E Everyone", value=(
        "`/fed board` \u2014 tonight's live bracket status\n"
        "`/fed clockin` \u2014 clock in for tonight\n"
        "`/fed report` \u2014 report a game result\n"
        "`/fed guide` \u2014 this guide"), inline=False)
    return e

GUIDE_BUILDERS = {"admin": guide_admin_embed, "captain": guide_captain_embed,
                  "player": guide_player_embed, "commands": guide_commands_embed}

class GuideView(discord.ui.View):
    """Persistent step-by-step guide picker. Post once with /fed guide, reuse forever.
    Embeds are rebuilt fresh on every selection so live config (buy-in, cashtag,
    team count) is always current, never a stale snapshot."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(
        custom_id="fed:guide_picker",
        placeholder="\U0001F4D6 Pick a guide\u2026",
        options=[
            discord.SelectOption(label="Admin Setup Guide", value="admin", emoji="\U0001F6E0\uFE0F",
                                 description="First-time server setup, step by step"),
            discord.SelectOption(label="Captain Guide", value="captain", emoji="\U0001F451",
                                 description="Register your team and run it every night"),
            discord.SelectOption(label="Player Guide", value="player", emoji="\U0001F3AE",
                                 description="Register your gamertag and get rostered"),
            discord.SelectOption(label="Full Command Reference", value="commands", emoji="\U0001F4CB",
                                 description="Every /fed command in one place"),
        ])
    async def pick_guide(self, itx: discord.Interaction, select: discord.ui.Select):
        builder = GUIDE_BUILDERS.get(select.values[0])
        await itx.response.edit_message(embed=builder(), view=self)

# ----------------------------- MENU-DRIVEN MODALS ---------------------------
# Discord modals only accept text fields (no file uploads, no dropdowns) —
# that's a hard platform limit, not a design choice. So: any action needing
# ONLY text goes straight through a modal from the menu. Any action needing
# an attachment (team logo, game screenshot) gets a clear one-line pointer
# to the specific slash command, since that's the only way Discord allows
# a file to reach the bot.
class PlayerRegisterModal(discord.ui.Modal, title="Register as a Player"):
    gamertag = discord.ui.TextInput(label="Your EXACT in-game gamertag", placeholder="e.g. KR4SHOUT23", max_length=32)

    async def on_submit(self, itx: discord.Interaction):
        await _do_player_register(itx, self.gamertag.value)

class StandbyModal(discord.ui.Modal, title="Join Tonight's Standby List"):
    team = discord.ui.TextInput(label="Your squad name", max_length=32)

    async def on_submit(self, itx: discord.Interaction):
        await _do_standby(itx, self.team.value)

class TourneyStartModal(discord.ui.Modal, title="Start an Instant Tournament"):
    buy_in = discord.ui.TextInput(label="Entry price per squad ($)", placeholder="e.g. 10", max_length=8)
    cashtag = discord.ui.TextInput(label="Cash App tag for entries", placeholder="$YourCashtag", max_length=32)
    house_rules = discord.ui.TextInput(label="House rules (optional)", required=False, max_length=200,
                                       style=discord.TextStyle.paragraph)

    async def on_submit(self, itx: discord.Interaction):
        try:
            val = float(self.buy_in.value)
            if val <= 0:
                raise ValueError
        except ValueError:
            return await itx.response.send_message("Enter a positive number for the buy-in.", ephemeral=True)
        await itx.response.send_message(
            "\U0001F3D7\uFE0F Got it \u2014 now pick your bracket size and payout style:",
            view=TourneyOptionsView(val, self.cashtag.value.strip(), self.house_rules.value.strip() or None),
            ephemeral=True)

class TourneyOptionsView(discord.ui.View):
    """Second step of the menu-driven tourney flow: dropdowns for the parts
    that ARE enumerable, following the text-only modal above."""
    def __init__(self, buy_in: float, cashtag: str, house_rules: str):
        super().__init__(timeout=300)
        self.buy_in = buy_in
        self.cashtag = cashtag
        self.house_rules = house_rules
        self.team_count = 4
        self.payout_style = "split"

    @discord.ui.select(placeholder="Bracket size\u2026", options=[
        discord.SelectOption(label="2 Teams \u2014 Straight to the Championship", value="2"),
        discord.SelectOption(label="4 Teams \u2014 Sudden Death opener, BO3 Final", value="4", default=True),
        discord.SelectOption(label="8 Teams \u2014 Sudden Death opener, BO3 the rest", value="8"),
        discord.SelectOption(label="16 Teams \u2014 Sudden Death Round of 16, BO3 the rest", value="16"),
    ])
    async def pick_size(self, itx: discord.Interaction, select: discord.ui.Select):
        self.team_count = int(select.values[0])
        await itx.response.edit_message(content=f"Bracket size: **{self.team_count} teams**. "
                                                f"Payout style: **{self.payout_style.upper()}**. "
                                                f"Hit Fire It Up when ready.", view=self)

    @discord.ui.select(placeholder="Payout style\u2026", options=[
        discord.SelectOption(label="Split Pot \u2014 every round pays", value="split", default=True),
        discord.SelectOption(label="Winner Takes All \u2014 champion gets everything", value="wta"),
    ])
    async def pick_style(self, itx: discord.Interaction, select: discord.ui.Select):
        self.payout_style = select.values[0]
        await itx.response.edit_message(content=f"Bracket size: **{self.team_count} teams**. "
                                                f"Payout style: **{self.payout_style.upper()}**. "
                                                f"Hit Fire It Up when ready.", view=self)

    @discord.ui.button(label="\U0001F3C6 Fire It Up", style=discord.ButtonStyle.success)
    async def confirm(self, itx: discord.Interaction, _btn: discord.ui.Button):
        if not cfg("guild_id"):
            return await itx.response.send_message(
                "Run `/fed setup` at least once first \u2014 the server needs its roles/category before "
                "tournaments can spin up.", ephemeral=True)
        if not can_start_tourney(itx.user):
            return await itx.response.send_message(
                "You need the CAPTAIN role (register a team first) or be an admin to start a tournament.",
                ephemeral=True)
        admin_running = is_admin(itx.user)
        use_mode, use_boost, use_rake = ("launch", 25, 10) if admin_running else ("standard", 0, 0)
        wta = self.payout_style == "wta"
        payout = compute_payouts(self.team_count, self.buy_in, use_mode, use_boost, use_rake, winner_takes_all=wta)
        if not payout:
            minv = find_min_viable_buyin(self.team_count, use_mode, use_boost, use_rake)
            hint = f" Try at least ${minv:.0f}." if minv else ""
            return await itx.response.send_message(
                f"\u274C ${self.buy_in:g} is too low for a {self.team_count}-team bracket.{hint}", ephemeral=True)

        await itx.response.send_message("\U0001F3D7\uFE0F Spinning up your tournament\u2026", ephemeral=True)
        guild = itx.guild
        category_id = cfg("tourney_category_id")
        category = guild.get_channel(category_id) if category_id else None
        if not category:
            admin_role = guild.get_role(cfg("admin_role"))
            overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=True)}
            if admin_role:
                overwrites[admin_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
            category = await guild.create_category("\U0001F3C6 LIVE TOURNEYS", overwrites=overwrites,
                                                    reason="Federal Reserve \u2014 instant tournaments")
            STATE["config"]["tourney_category_id"] = category.id
        STATE["config"]["tourney_counter"] += 1
        tourney_id = f"T{STATE['config']['tourney_counter']}"
        chan_name = f"\U0001F3C6 Tourney {STATE['config']['tourney_counter']:02d}"
        ch = await guild.create_text_channel(chan_name, category=category, reason=f"Instant tournament by {itx.user}")
        t = {
            "id": tourney_id, "creator_id": itx.user.id, "is_admin_run": admin_running,
            "team_count": self.team_count, "buy_in": self.buy_in, "mode": use_mode,
            "boost_pct": use_boost, "rake_pct": use_rake, "cashtag": self.cashtag,
            "house_rules": self.house_rules, "channel_id": ch.id, "payout": payout,
            "phase": "open", "created_at": datetime.now(ET).isoformat(),
            "teams": {}, "standby": [], "rounds": [], "matches": {}, "current_round_index": 0, "event_log": [],
        }
        STATE["tournaments"][tourney_id] = t
        log_event(t, f"Tournament created by {itx.user} \u2014 {t['team_count']} teams, ${t['buy_in']:g} entry")
        await save_state()
        fee_note = ("**NO FEES. NO BOOST.** Every dollar entered is a dollar paid out." if not admin_running
                   else f"League mode: **{use_mode.upper()}**.")
        desc = (f"Started by {itx.user.mention} \u2014 `{tourney_id}`\n"
               f"**{self.team_count} squads \u2014 ${self.buy_in:g} entry \u2014 pot ${payout['pot']}**\n"
               f"{fee_note}\n\n{format_ladder(payout)}\n\n"
               f"Cash App entries to **{self.cashtag}**.\n"
               f"Registered captains: run `/fed tourney enter` right here in this channel.")
        if self.house_rules:
            desc += f"\n\n**HOUSE RULES:** {self.house_rules}"
        await ch.send(embed=discord.Embed(title=f"\U0001F3C6 {tourney_id} \u2014 LIVE NOW", description=desc, color=GOLD))
        await itx.followup.send(f"\u2705 Tournament **{tourney_id}** is live in {ch.mention}.", ephemeral=True)

class SetupModal(discord.ui.Modal, title="Admin Setup \u2014 Text Fields"):
    buy_in = discord.ui.TextInput(label="Entry price per squad ($)", placeholder="e.g. 10", max_length=8)
    cashtag = discord.ui.TextInput(label="Your $cashtag for deposits", max_length=32)
    fine_amount = discord.ui.TextInput(label="Roster mismatch fine ($, default 2)", required=False, max_length=4)

    async def on_submit(self, itx: discord.Interaction):
        if not (is_admin(itx.user) and admin_unlocked(itx.user.id)):
            return await itx.response.send_message(
                "\U0001F510 Admin PIN session expired \u2014 run `/fed unlock`, then try again.", ephemeral=True)
        try:
            val = float(self.buy_in.value)
            if val <= 0:
                raise ValueError
        except ValueError:
            return await itx.response.send_message("Enter a positive number for the buy-in.", ephemeral=True)
        try:
            fine = int(self.fine_amount.value) if self.fine_amount.value.strip() else 2
        except ValueError:
            fine = 2
        await itx.response.send_message(
            "\u2699\uFE0F Got it \u2014 now pick bracket size, mode, and payout style:",
            view=SetupOptionsView(val, self.cashtag.value.strip(), fine), ephemeral=True)

class SetupOptionsView(discord.ui.View):
    def __init__(self, buy_in: float, cashtag: str, fine_amount: int):
        super().__init__(timeout=300)
        self.buy_in, self.cashtag, self.fine_amount = buy_in, cashtag, fine_amount
        self.team_count, self.mode, self.payout_style = 4, "launch", "split"

    @discord.ui.select(placeholder="Bracket size\u2026", options=[
        discord.SelectOption(label="2 Teams", value="2"),
        discord.SelectOption(label="4 Teams", value="4", default=True),
        discord.SelectOption(label="8 Teams", value="8"),
        discord.SelectOption(label="16 Teams", value="16"),
    ])
    async def pick_size(self, itx: discord.Interaction, select: discord.ui.Select):
        self.team_count = int(select.values[0])
        await itx.response.edit_message(content=self._status(), view=self)

    @discord.ui.select(placeholder="Mode\u2026", options=[
        discord.SelectOption(label="Launch (league-boosted pot)", value="launch", default=True),
        discord.SelectOption(label="Standard (self-funded rake)", value="standard"),
    ])
    async def pick_mode(self, itx: discord.Interaction, select: discord.ui.Select):
        self.mode = select.values[0]
        await itx.response.edit_message(content=self._status(), view=self)

    @discord.ui.select(placeholder="Payout style\u2026", options=[
        discord.SelectOption(label="Split Pot \u2014 every round pays", value="split", default=True),
        discord.SelectOption(label="Winner Takes All", value="wta"),
    ])
    async def pick_style(self, itx: discord.Interaction, select: discord.ui.Select):
        self.payout_style = select.values[0]
        await itx.response.edit_message(content=self._status(), view=self)

    def _status(self):
        return (f"Bracket: **{self.team_count} teams** \u2014 Mode: **{self.mode.upper()}** \u2014 "
               f"Style: **{self.payout_style.upper()}**. Hit Build The Machine when ready.")

    @discord.ui.button(label="\u2699\uFE0F Build The Machine", style=discord.ButtonStyle.success)
    async def confirm(self, itx: discord.Interaction, _btn: discord.ui.Button):
        if not (is_admin(itx.user) and admin_unlocked(itx.user.id)):
            return await itx.response.send_message(
                "\U0001F510 Admin PIN session expired \u2014 run `/fed unlock`, then try again.", ephemeral=True)
        wta = self.payout_style == "wta"
        payout = compute_payouts(self.team_count, self.buy_in, self.mode, 25, 10, winner_takes_all=wta)
        if not payout:
            minv = find_min_viable_buyin(self.team_count, self.mode, 25, 10)
            hint = f" Try at least ${minv:.0f}." if minv else ""
            return await itx.response.send_message(
                f"\u274C ${self.buy_in:g} is too low for a {self.team_count}-team bracket in {self.mode} mode."
                f"{hint}", ephemeral=True)
        await itx.response.send_message("\u2699\uFE0F Building the machine \u2014 category, channels, roles, ladder\u2026",
                                        ephemeral=True)
        c = STATE["config"]
        c["guild_id"] = itx.guild_id
        c["event_type"] = "the_refund"
        c["team_count"] = self.team_count
        c["mode"] = self.mode
        c["buy_in"] = self.buy_in
        c["boost_pct"], c["rake_pct"] = 25, 10
        c["fine_amount"] = self.fine_amount
        c["cashtag"] = self.cashtag
        c["payout"] = payout
        await save_state()
        log = await build_infrastructure(itx.guild)
        await bot.tree.sync(guild=discord.Object(id=itx.guild_id))
        tc = channel(bot, "timeclock_ch")
        if tc:
            await tc.send("\u23F0 **TIME CLOCK** \u2014 clock in here every night before tip-off.", view=ClockInView())
        ac = channel(bot, "announce_ch")
        if ac:
            await ac.send(embed=discord.Embed(title="\u2699\uFE0F FEDERAL RESERVE \u2014 MANUAL OVERRIDE PANEL",
                                              description="Admins only. Use the dropdown below.", color=NAVY),
                          view=OverridePanelView())
            await ac.send(embed=discord.Embed(title="\U0001F3E7 APA ATM PRO AM \u2014 MAIN MENU", color=GOLD,
                                              description="Everyday actions, no commands to memorize."),
                          view=MainMenuView())
            total_take = sum(r["per_winner"] for r in payout["rounds"])
            await ac.send(embed=discord.Embed(
                title="\U0001F3E7 THE REFUND IS LIVE",
                description=(f"**{self.team_count} squads \u2014 {self.mode.upper()}**\n"
                            f"Buy-in: ${self.buy_in:g}/squad \u2192 Pot **${payout['pot']}**\n\n"
                            f"{format_ladder(payout)}\n\n"
                            f"Not rostered yet? `/fed player register` then `/fed team register`."),
                color=GOLD))
        await itx.followup.send(
            f"\u2705 **MACHINE BUILT.**\n{log}\n\n{format_ladder(payout)}\n\n"
            f"Public summary + Override Panel posted in {ac.mention if ac else '#the-refund'}.", ephemeral=True)

class MainMenuView(discord.ui.View):
    """The everyday front door. Text-only actions go straight through a modal;
    actions needing a file attachment (team logo, game screenshot) point to
    the one slash command that can actually carry a file — Discord modals
    can't accept uploads, so that's a platform floor, not a shortcut we
    skipped."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(
        custom_id="fed:main_menu",
        placeholder="\U0001F3E7 What do you need\u2026",
        options=[
            discord.SelectOption(label="Register as a Player", value="player_register", emoji="\U0001F3AE",
                                 description="One-time gamertag registration"),
            discord.SelectOption(label="Register My Team", value="team_register", emoji="\U0001F3E2",
                                 description="Needs a logo upload \u2014 opens the command for you"),
            discord.SelectOption(label="Enter Tonight's Refund", value="enter", emoji="\U0001F3AB",
                                 description="Registered captains only"),
            discord.SelectOption(label="Join Standby", value="standby", emoji="\u23F3",
                                 description="First in line if a card declines"),
            discord.SelectOption(label="Check the Board", value="board", emoji="\U0001F4CA",
                                 description="Tonight's live bracket status"),
            discord.SelectOption(label="Clock In", value="clockin", emoji="\u23F0",
                                 description="Required before 12:00 tip-off"),
            discord.SelectOption(label="Report a Win", value="report", emoji="\U0001F3C6",
                                 description="Needs a screenshot \u2014 opens the command for you"),
            discord.SelectOption(label="Start an Instant Tournament", value="tourney_start", emoji="\U0001F3D7\uFE0F",
                                 description="Captains and admins only"),
            discord.SelectOption(label="Admin Setup", value="admin_setup", emoji="\u2699\uFE0F",
                                 description="Admins only \u2014 build the whole machine"),
            discord.SelectOption(label="Open the Guide", value="guide", emoji="\U0001F4D6",
                                 description="Full step-by-step walkthroughs"),
        ])
    async def pick_action(self, itx: discord.Interaction, select: discord.ui.Select):
        action = select.values[0]
        if action == "player_register":
            return await itx.response.send_modal(PlayerRegisterModal())
        if action == "team_register":
            return await itx.response.send_message(
                "Team registration needs a logo image \u2014 Discord menus can't accept file uploads, only "
                "slash commands can. Run `/fed team register team_name:<name> logo:<upload>`.", ephemeral=True)
        if action == "enter":
            return await _do_enter(itx)
        if action == "standby":
            return await itx.response.send_modal(StandbyModal())
        if action == "board":
            return await _do_board(itx)
        if action == "clockin":
            return await _do_clockin(itx)
        if action == "report":
            return await itx.response.send_message(
                "Reporting a win needs your end-game screenshot \u2014 Discord menus can't accept file uploads, "
                "only slash commands can. Run `/fed report screenshot:<upload>`.", ephemeral=True)
        if action == "tourney_start":
            if not can_start_tourney(itx.user):
                return await itx.response.send_message(
                    "You need the CAPTAIN role (register a team first) or be an admin to start a tournament.",
                    ephemeral=True)
            return await itx.response.send_modal(TourneyStartModal())
        if action == "admin_setup":
            if not await ensure_admin_pin(itx):
                return
            return await itx.response.send_modal(SetupModal())
        if action == "guide":
            landing = discord.Embed(title="\U0001F4D6 FEDERAL RESERVE \u2014 STEP BY STEP GUIDE", color=GOLD,
                                    description="Pick your role below for a full walkthrough.")
            return await itx.response.send_message(embed=landing, view=GuideView(), ephemeral=True)

class OverridePanelView(discord.ui.View):
    """The manual-override dropdown. Post once with /fed panel, reuse forever."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(
        custom_id="fed:override_panel",
        placeholder="\u2699\uFE0F Manual override \u2014 pick an action\u2026",
        options=[
            discord.SelectOption(label="Announce Tonight", value="announce", emoji="\U0001F9FE",
                                 description="Force tonight's REFUND announcement now"),
            discord.SelectOption(label="Open Deposits", value="open", emoji="\U0001F3E7",
                                 description="Open the machine for entries"),
            discord.SelectOption(label="Lock + Start Time Clock", value="lock", emoji="\U0001F512",
                                 description="Seed the bracket, open the time clock"),
            discord.SelectOption(label="Tip Off Now", value="tip", emoji="\U0001F3C0",
                                 description="Start the machine, sweep no-shows"),
            discord.SelectOption(label="Reprice Buy-In", value="reprice", emoji="\U0001F4B2",
                                 description="Change the entry price \u2014 recomputes the whole ladder"),
            discord.SelectOption(label="Reset Tonight's Machine", value="reset", emoji="\U0001F504",
                                 description="Wipe tonight's teams/bracket, back to idle"),
            discord.SelectOption(label="Pause Auto-Loop", value="pause", emoji="\u23F8\uFE0F",
                                 description="Bot stops running the nightly schedule"),
            discord.SelectOption(label="Resume Auto-Loop", value="resume", emoji="\u25B6\uFE0F",
                                 description="Bot resumes the nightly schedule"),
            discord.SelectOption(label="Reboot Bot", value="reboot", emoji="\U0001F50C",
                                 description="Hard-restart the process \u2014 use if it's stuck"),
        ])
    async def select_action(self, itx: discord.Interaction, select: discord.ui.Select):
        if not await ensure_admin_pin(itx):
            return
        action = select.values[0]
        if action == "reprice":
            return await itx.response.send_modal(RepriceModal())
        if action == "reboot":
            await itx.response.send_message(
                "\U0001F50C Rebooting \u2014 back in a few seconds. All state is saved, nothing is lost.",
                ephemeral=True)
            return await perform_reboot(itx.client)
        await itx.response.send_message(f"\u2699\uFE0F Running **{action}**\u2026", ephemeral=True)
        if action == "announce": await do_announce(itx.client)
        elif action == "open": await do_open(itx.client)
        elif action == "lock": await do_lock(itx.client)
        elif action == "tip": await do_tip(itx.client)
        elif action == "test_toggle":
            global TEST_MODE
            TEST_MODE = not TEST_MODE
            pick_s = fast5_pick_clock()
            mode_str = "ON" if TEST_MODE else "OFF"
            status = "\U0001F9EA ON" if TEST_MODE else "\u2705 OFF"
            await itx.response.send_message(
                f"Test Mode {status}.\n" +
                (f"Loops paused | PINs bypassed | pick clock {pick_s}s | manual-lock only"
                 if TEST_MODE else "Live schedule re-armed."), ephemeral=True)
            print(f"[TEST MODE] panel toggle {mode_str} by {itx.user}"); return
        elif action == "test_refund":
            if not TEST_MODE:
                return await itx.response.send_message("Enable Test Mode first.", ephemeral=True)
            await itx.response.send_message("\U0001F9EA Launching Refund dry run\u2026", ephemeral=True)
            await do_test_full_refund_run(itx.client); return
        elif action == "test_fast5":
            if not TEST_MODE:
                return await itx.response.send_message("Enable Test Mode first.", ephemeral=True)
            await itx.response.send_message("\U0001F9EA Opening Fast 5\u2019s queueing\u2026", ephemeral=True)
            await do_test_open_fast5(itx.client); return
        elif action == "test_fast5_lock":
            if not TEST_MODE:
                return await itx.response.send_message("Enable Test Mode first.", ephemeral=True)
            session = STATE["fast5"]["session"]
            if not session or session["phase"] != "queueing":
                return await itx.response.send_message("No queueing session open right now.", ephemeral=True)
            await itx.response.send_message("\U0001F512 Locking draft now\u2026", ephemeral=True)
            await fast5_lock_and_draft(itx.client); return
        elif action == "reset":
            STATE["night"] = _fresh_night()
            await save_state()
        elif action == "pause":
            STATE["config"]["auto_nightly"] = False
            await save_state()
        elif action == "resume":
            STATE["config"]["auto_nightly"] = True
            await save_state()
        await itx.followup.send(f"\u2705 `{action}` complete.", ephemeral=True)

# ----------------------------- BRACKET FLOW ---------------------------------
async def advance_bracket(client):
    night = STATE["night"]
    round_key = night["rounds"][night["current_round_index"]]
    round_matches = {mk: m for mk, m in night["matches"].items() if m["round"] == round_key}
    if not all(m["winner"] for m in round_matches.values()):
        return  # round still in progress
    winners = [m["winner"] for mk, m in sorted(round_matches.items())]

    if night["current_round_index"] == len(night["rounds"]) - 1:
        night["phase"] = "complete"
        champ_id = winners[0]
        champ = team_name(champ_id)
        payout = night["payout"]
        total_take = sum(r["per_winner"] for r in payout["rounds"])
        log_event(night, f"CHAMPION: {champ} \u2014 total takeaway ${total_take}")
        await save_state()
        ch = refund_live_channel(client)
        if ch:
            await ch.send(f"\U0001F3C6 **VAULT EMPTIED** \U0001F3C6\n"
                          f"**{champ}** ran the whole bracket \u2014 **${total_take} total withdrawal** "
                          f"off a ${cfg('buy_in'):g} deposit.\nEvery stub is in the Paper Trail.\n"
                          f"Tap below for the full receipt \u2014 this channel closes after.",
                          view=RefundCloseView())
        return

    next_round_key = night["rounds"][night["current_round_index"] + 1]
    team_count = night["team_count"] or cfg("team_count")
    next_def = next(r for r in TEAM_BRACKETS[team_count] if r["key"] == next_round_key)
    night["matches"].update(seed_round(winners, next_round_key, next_def["format"]))
    night["current_round_index"] += 1
    log_event(night, f"Advanced to {next_def['label']}")
    await save_state()

    ch = refund_live_channel(client)
    if ch:
        next_payout = round_def(next_round_key)
        lines = [f"\U0001F3C0 **{next_def['label']} \u2014 {next_def['format']}** "
                f"(win pays **${next_payout['per_winner'] if next_payout else '?'}**)"]
        for mk, m in sorted(night["matches"].items()):
            if m["round"] == next_round_key:
                lines.append(f"{mk}: **{team_name(m['a'])}** vs **{team_name(m['b'])}**")
        lines.append("Winners run `/fed report` with the end-game screenshot.")
        await ch.send("\n".join(lines))

async def perform_reboot(client):
    """Hard restart: spawns a brand-new process running this same script,
    then hard-exits this one. State is already atomically saved to disk on
    every change, so nothing is lost — the new process just reloads STATE
    from fed_reserve_state.json on boot, exactly where it left off.

    NOTE: this used to use os.execv(), which replaces the CURRENT process
    in place on POSIX. Windows has no true exec() syscall — CPython emulates
    it, and that emulation can silently fail to bring the process back up
    (lost console, orphaned stdio, etc.). subprocess.Popen + os._exit is the
    reliable version on both platforms."""
    ch = channel(client, "announce_ch")
    if ch:
        try:
            await ch.send("\U0001F504 **FEDERAL RESERVE REBOOTING** \u2014 brief downtime, back in a few seconds.")
        except discord.HTTPException:
            pass
    await save_state()
    print("[FEDERAL RESERVE] Reboot requested \u2014 spawning fresh process...")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    popen_kwargs = {"cwd": script_dir, "close_fds": True}
    if sys.platform == "win32":
        # Give the new process its own console so it isn't silently killed
        # when the parent's console/session tears down.
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
    subprocess.Popen([sys.executable] + sys.argv, **popen_kwargs)
    os._exit(0)   # hard exit — skips asyncio/atexit cleanup that can hang

async def tourney_lock_and_start(client, t: dict):
    """No wall-clock schedule, no separate clock-in phase — instant tourneys
    are spontaneous, so filling the last slot immediately seeds and starts
    the bracket."""
    order = [int(cid) for cid, tm in t["teams"].items() if tm["deposit"]]
    random.shuffle(order)
    rounds_def = TEAM_BRACKETS[t["team_count"]]
    first = rounds_def[0]
    t["matches"] = seed_round(order, first["key"], first["format"])
    t["rounds"] = [r["key"] for r in rounds_def]
    t["current_round_index"] = 0
    t["phase"] = "live"
    await save_state()
    ch = client.get_channel(t["channel_id"])
    if ch:
        first_payout = next(r for r in t["payout"]["rounds"] if r["key"] == first["key"])
        lines = [f"\U0001F512 **BRACKET LOCKED \u2014 {first['label']} ({first['format']})**"]
        for mk, m in sorted(t["matches"].items()):
            lines.append(f"{mk}: **{team_name_t(t, m['a'])}** vs **{team_name_t(t, m['b'])}**")
        lines.append(f"Win = **${first_payout['per_winner']}** instant withdrawal. "
                    f"Winners run `/fed tourney report`.")
        await ch.send("\n".join(lines))

async def post_tourney_receipt(client, t: dict, team: str, round_key: str, match_key: str, captain_id: int):
    payout = t["payout"]
    rounds = payout["rounds"]
    idx = next(i for i, r in enumerate(rounds) if r["key"] == round_key)
    r = rounds[idx]
    is_final = (idx == len(rounds) - 1)
    wta = payout.get("winner_takes_all", False)

    if wta and not is_final:
        ch = client.get_channel(t["channel_id"])
        if ch:
            nxt_r = rounds[idx + 1]
            await ch.send(f"\u27A1\uFE0F **{team}** ADVANCES \u2014 {r['label']} ({match_key})\n"
                          f"**WINNER TAKES ALL** \u2014 the pot stays locked until {nxt_r['label']}. No payout yet.")
        return

    amount = r["per_winner"]
    label = f"{t['id']} \u2014 CHAMPIONSHIP" if is_final else r["label"]
    nxt = "EVENT COMPLETE" if is_final else f"{rounds[idx + 1]['label']} - ${rounds[idx + 1]['per_winner']}"

    fine_note = ""
    net_amount = amount
    reg = STATE["registry"]
    owed = reg["fines_owed"].get(str(captain_id), 0)
    if owed > 0:
        deduction = min(owed, amount)
        reg["fines_owed"][str(captain_id)] = owed - deduction
        net_amount = amount - deduction
        fine_note = f"\nFINE DEDUCTED: -${deduction} (roster mismatch) \u2014 **NET SEND: ${net_amount}**"

    ch = client.get_channel(t["channel_id"])
    if ch:
        now = datetime.now(ET)
        block = ("```\n        ATM PAYDAY\n     ADVANCE TO MONEY\n--------------------------\n"
                f"TERMINAL : {t['id']} ({match_key})\n"
                f"DATE     : {now.strftime('%m/%d/%y %I:%M %p ET')}\n"
                f"CARDHOLDER: {team.upper()[:20]}\n--------------------------\n"
                f"TRANSACTION: {label}\nSTATUS     : *** PAID ***\n\n        ${amount}.00\n\n"
                "--------------------------\n"
                f"NEXT STOP: {nxt}\n--------------------------\nEVERY ROUND IS A WITHDRAWAL.\n```")
        emb = discord.Embed(title="\U0001F4B0 WITHDRAWAL CONFIRMED \U0001F4B0", description=block + fine_note,
                            color=GOLD)
        emb.set_footer(text=f"{t['id']} \u2022 INSTANT TOURNAMENT \u2022 APA ATM PRO AM")
        await ch.send(embed=emb)
    STATE["ledger"].append({"run": f"tourney-{t['id']}", "date": t["created_at"][:10],
                            "team": team, "round": round_key, "amount": amount,
                            "net_paid": net_amount, "ts": datetime.now(ET).isoformat()})
    await grant_role(client, captain_id, "payroll_role", "Cashed a withdrawal")
    await save_state()

async def advance_tourney_bracket(client, t: dict):
    round_key = t["rounds"][t["current_round_index"]]
    round_matches = {mk: m for mk, m in t["matches"].items() if m["round"] == round_key}
    if not all(m["winner"] for m in round_matches.values()):
        return
    winners = [m["winner"] for mk, m in sorted(round_matches.items())]
    ch = client.get_channel(t["channel_id"])

    if t["current_round_index"] == len(t["rounds"]) - 1:
        t["phase"] = "complete"
        champ_id = winners[0]
        total_take = sum(r["per_winner"] for r in t["payout"]["rounds"])
        log_event(t, f"CHAMPION: {team_name_t(t, champ_id)} \u2014 total takeaway ${total_take}")
        await save_state()
        if ch:
            await ch.send(f"\U0001F3C6 **{t['id']} \u2014 VAULT EMPTIED** \U0001F3C6\n"
                          f"**{team_name_t(t, champ_id)}** ran the whole bracket \u2014 "
                          f"**${total_take} total withdrawal.**\nGG \u2014 tap below to archive the receipt "
                          f"and close this channel out.", view=CloseEventView())
        return

    next_round_key = t["rounds"][t["current_round_index"] + 1]
    next_def = next(r for r in TEAM_BRACKETS[t["team_count"]] if r["key"] == next_round_key)
    t["matches"].update(seed_round(winners, next_round_key, next_def["format"]))
    t["current_round_index"] += 1
    await save_state()
    if ch:
        next_payout = next(r for r in t["payout"]["rounds"] if r["key"] == next_round_key)
        lines = [f"\U0001F3C0 **{next_def['label']} \u2014 {next_def['format']}** "
                f"(win pays **${next_payout['per_winner']}**)"]
        for mk, m in sorted(t["matches"].items()):
            if m["round"] == next_round_key:
                lines.append(f"{mk}: **{team_name_t(t, m['a'])}** vs **{team_name_t(t, m['b'])}**")
        lines.append("Winners run `/fed tourney report`.")
        await ch.send("\n".join(lines))

async def close_tournament(client, t: dict, closer: discord.abc.User):
    """Archives a full summary receipt permanently, THEN deletes the
    tournament's channel — the receipt must be posted first since the
    channel (and everything in it) is about to disappear."""
    guild_id = cfg("guild_id")
    guild = client.get_guild(guild_id) if guild_id else None

    entries = [e for e in STATE["ledger"] if e["run"] == f"tourney-{t['id']}"]
    total_paid = sum(e["net_paid"] for e in entries)
    champ_id = None
    if t["rounds"]:
        final_key = t["rounds"][-1]
        final_matches = [m for m in t["matches"].values() if m["round"] == final_key and m["winner"]]
        if final_matches:
            champ_id = final_matches[0]["winner"]
    squads = [tm["name"] for tm in t["teams"].values() if tm["deposit"]]
    closed = datetime.now(ET).isoformat()

    lines = [
        f"**{t['id']}** \u2014 hosted by <@{t['creator_id']}>, closed by {closer.mention}",
        f"Format: {t['team_count']} squads, ${t['buy_in']:g} entry, "
        f"{'league mode ' + t['mode'].upper() if t['is_admin_run'] else 'NO FEE / NO BOOST'}",
        f"Pot: ${t['payout']['pot']}  \u2022  Total paid out: ${total_paid}",
        f"Squads: {', '.join(squads) if squads else 'none locked in'}",
        f"Champion: {team_name_t(t, champ_id) if champ_id else 'not completed'}",
        f"Started: {t['created_at'][:16].replace('T', ' ')} ET  \u2192  Closed: {closed[:16].replace('T', ' ')} ET",
    ]
    if t["house_rules"]:
        lines.append(f"House rules: {t['house_rules']}")

    archive_ch = None
    if guild:
        archive_ch = guild.get_channel(cfg("archive_ch"))
        if not archive_ch:
            admin_role = guild.get_role(cfg("admin_role"))
            overwrites = {guild.default_role: discord.PermissionOverwrite(send_messages=False, view_channel=True)}
            if admin_role:
                overwrites[admin_role] = discord.PermissionOverwrite(send_messages=True, view_channel=True)
            category = guild.get_channel(cfg("category_id"))
            archive_ch = await guild.create_text_channel(
                "\U0001F5C4\uFE0F-event-archive", category=category, overwrites=overwrites,
                reason="Federal Reserve \u2014 event archive")
            STATE["config"]["archive_ch"] = archive_ch.id
            await save_state()
    if archive_ch:
        await archive_ch.send(embed=discord.Embed(title=f"\U0001F4DC EVENT CLOSED \u2014 {t['id']}",
                                                   description="\n".join(lines), color=NAVY))
        printout_lines = ["=" * 60, f"  {t['id']} \u2014 INSTANT TOURNAMENT", "  APA ATM PRO AM BASKETBALL LEAGUE",
                          "=" * 60, *lines, "", "--- EVENT LOG ---"]
        for e in t.get("event_log", []):
            printout_lines.append(f"[{e['ts']}] {e['msg']}")
        printout_lines.append("")
        printout_lines.append("--- LEDGER ---")
        for e in entries:
            printout_lines.append(f"{e['team']}: ${e['amount']} ({e['round']}) \u2014 net ${e.get('net_paid', e['amount'])}")
        printout_lines.append(f"TOTAL PAID OUT: ${total_paid}")
        printout_lines.append("=" * 60)
        import io
        buf = io.BytesIO("\n".join(printout_lines).encode("utf-8"))
        await archive_ch.send(file=discord.File(buf, filename=f"{t['id']}_receipt.txt"))

    if guild:
        ch = guild.get_channel(t["channel_id"])
        if ch:
            try:
                await ch.delete(reason=f"Federal Reserve \u2014 event {t['id']} closed by {closer}")
            except discord.HTTPException:
                pass

    STATE["tournaments"].pop(t["id"], None)
    await save_state()

def render_refund_printout(night: dict) -> str:
    lines = [
        "=" * 60, f"  THE REFUND \u2014 Run #{cfg('run_number')}",
        "  APA ATM PRO AM BASKETBALL LEAGUE", "=" * 60,
        f"Date: {night['date']}   Squads: {night.get('team_count', 0)}   Buy-in: ${cfg('buy_in'):g}",
        "", "--- EVENT LOG ---",
    ]
    for e in night.get("event_log", []):
        lines.append(f"[{e['ts']}] {e['msg']}")
    lines.append("")
    lines.append("--- SQUADS ---")
    for cid, t in night["teams"].items():
        lines.append(f"{t['name']} (capt {cid})")
    lines.append("")
    lines.append("--- LEDGER ---")
    entries = [e for e in STATE["ledger"] if e["run"] == cfg("run_number") and e["date"] == night["date"]]
    total = 0
    for e in entries:
        lines.append(f"{e['team']}: ${e['amount']} ({e['round']}) \u2014 net ${e.get('net_paid', e['amount'])}")
        total += e["amount"]
    lines.append(f"TOTAL PAID OUT: ${total}")
    lines.append("=" * 60)
    return "\n".join(lines)

async def close_refund_night(client, closer, silent: bool = False):
    """Archives tonight's full receipt permanently, THEN deletes the
    per-night live channel — same pattern as Instant Tournaments and
    Fast 5's. Shares the same event-archive channel as Instant Tournaments."""
    night = STATE["night"]
    if not night.get("live_channel_id"):
        return
    guild_id = cfg("guild_id")
    guild = client.get_guild(guild_id) if guild_id else None
    printout = render_refund_printout(night)

    archive_ch = None
    if guild:
        archive_ch = guild.get_channel(cfg("archive_ch"))
        if not archive_ch:
            admin_role = guild.get_role(cfg("admin_role"))
            overwrites = {guild.default_role: discord.PermissionOverwrite(send_messages=False, view_channel=True)}
            if admin_role:
                overwrites[admin_role] = discord.PermissionOverwrite(send_messages=True, view_channel=True)
            category = guild.get_channel(cfg("category_id"))
            archive_ch = await guild.create_text_channel(
                "\U0001F5C4\uFE0F-event-archive", category=category, overwrites=overwrites,
                reason="Federal Reserve \u2014 event archive")
            STATE["config"]["archive_ch"] = archive_ch.id
            await save_state()
    if archive_ch:
        import io
        buf = io.BytesIO(printout.encode("utf-8"))
        f = discord.File(buf, filename=f"refund_run{cfg('run_number')}_receipt.txt")
        closer_note = "" if silent else f", closed by {getattr(closer, 'mention', closer)}"
        await archive_ch.send(content=f"\U0001F4DC Full receipt for **THE REFUND Run #{cfg('run_number')}**"
                                      f"{closer_note}.", file=f)

    if guild:
        ch = guild.get_channel(night["live_channel_id"])
        if ch:
            try:
                await ch.delete(reason=f"Federal Reserve \u2014 night closed by {closer}")
            except discord.HTTPException:
                pass

    night["live_channel_id"] = 0
    night["phase"] = "idle"
    await save_state()

class RefundClosePinModal(discord.ui.Modal, title="Admin PIN Required to Close"):
    pin = discord.ui.TextInput(label="Enter admin PIN", max_length=8)

    async def on_submit(self, itx: discord.Interaction):
        if not cfg("admin_pin_hash"):
            return await itx.response.send_message(
                "No admin PIN has been set yet \u2014 ask an admin to run `/fed pin`, or have an admin close "
                "directly.", ephemeral=True)
        if not admin_pin_matches(self.pin.value):
            return await itx.response.send_message("\u274C Incorrect PIN.", ephemeral=True)
        if not STATE["night"].get("live_channel_id"):
            return await itx.response.send_message("Already closed.", ephemeral=True)
        await itx.response.send_message("\u2705 PIN accepted \u2014 closing tonight's night\u2026", ephemeral=True)
        await close_refund_night(itx.client, itx.user)

class RefundCloseView(discord.ui.View):
    """Posted on the VAULT EMPTIED message. Admin closes instantly; anyone
    else gets a PIN prompt as a fallback authorization path."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="\U0001F512 Close Night & Archive", style=discord.ButtonStyle.danger,
                      custom_id="refund:close_night")
    async def close(self, itx: discord.Interaction, _btn: discord.ui.Button):
        if not STATE["night"].get("live_channel_id"):
            return await itx.response.send_message("Already closed.", ephemeral=True)
        if is_admin(itx.user):
            if not admin_unlocked(itx.user.id):
                return await itx.response.send_modal(AdminPinUnlockModal())
            await itx.response.send_message("\u2705 Closing tonight's night\u2026", ephemeral=True)
            return await close_refund_night(itx.client, itx.user)
        await itx.response.send_modal(RefundClosePinModal())

class ClosePinModal(discord.ui.Modal, title="Admin PIN Required to Close"):
    pin = discord.ui.TextInput(label="Enter admin PIN", max_length=8)

    def __init__(self, tourney_id: str):
        super().__init__()
        self.tourney_id = tourney_id

    async def on_submit(self, itx: discord.Interaction):
        if not cfg("admin_pin_hash"):
            return await itx.response.send_message(
                "No admin PIN has been set yet \u2014 ask an admin to run `/fed pin`, or have the host/an "
                "admin close this event directly.", ephemeral=True)
        if not admin_pin_matches(self.pin.value):
            return await itx.response.send_message("\u274C Incorrect PIN.", ephemeral=True)
        t = STATE["tournaments"].get(self.tourney_id)
        if not t:
            return await itx.response.send_message("This event is already closed.", ephemeral=True)
        await itx.response.send_message("\u2705 PIN accepted \u2014 closing event\u2026", ephemeral=True)
        await close_tournament(itx.client, t, itx.user)

class CloseEventView(discord.ui.View):
    """Posted on the VAULT EMPTIED completion message. Host/admin closes
    instantly; anyone else gets a PIN prompt as a fallback authorization path."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="\U0001F512 Close Event", style=discord.ButtonStyle.danger,
                      custom_id="itourney:close_event")
    async def close_event(self, itx: discord.Interaction, _btn: discord.ui.Button):
        t = find_tourney_by_channel(itx.channel_id)
        if not t:
            return await itx.response.send_message("This event is already closed.", ephemeral=True)
        if itx.user.id == t["creator_id"] or (is_admin(itx.user) and admin_unlocked(itx.user.id)):
            await itx.response.send_message("\u2705 Closing event\u2026", ephemeral=True)
            return await close_tournament(itx.client, t, itx.user)
        if is_admin(itx.user):
            return await itx.response.send_modal(AdminPinUnlockModal())
        await itx.response.send_modal(ClosePinModal(t["id"]))

async def card_declined(client, captain_id: int, match_key: str):
    night = STATE["night"]
    m = night["matches"][match_key]
    opp = m["b"] if m["a"] == captain_id else m["a"]
    t = team_of(captain_id)
    if t:
        t["declined"] = True
    m["winner"] = opp
    m["paid"] = True
    log_event(night, f"CARD DECLINED: {team_name(captain_id)} no-showed {match_key}")
    await save_state()
    ch = refund_live_channel(client)
    if ch:
        await ch.send(f"\U0001F6AB **CARD DECLINED** \u2014 {team_name(captain_id)} no-showed {match_key}. "
                      f"**{team_name(opp)}** advances automatically.")
    await post_receipt(client, team_name(opp), m["round"], match_key, opp)
    await advance_bracket(client)
    await post_refund_checkpoint(client, f"CARD DECLINED \u2014 {team_name(captain_id)} out of {match_key}")

# ----------------------------- NIGHTLY LOOP ---------------------------------
def _between(now: dtime, a: dtime, b: dtime) -> bool:
    return a <= now < b

@tasks.loop(seconds=60)
async def nightly_loop():
    if TEST_MODE:
        return
    if not STATE["config"].get("auto_nightly", True):
        return
    if not (cfg("announce_ch") and cfg("guild_id")):
        return
    now_dt = datetime.now(ET)
    now = dtime(now_dt.hour, now_dt.minute)
    phase = STATE["night"]["phase"]
    try:
        if phase in ("idle", "complete") and _between(now, T_ANNOUNCE, T_OPEN):
            await do_announce(bot)
        elif phase == "announced" and _between(now, T_OPEN, T_LOCK):
            await do_open(bot)
        elif phase == "open" and now == dtime(23, 50):
            await do_lock(bot)
        elif phase == "locked" and now == T_TIP:
            await do_tip(bot)
    except Exception as e:
        print(f"[nightly] {type(e).__name__}: {e}")

@nightly_loop.before_loop
async def _wait_ready():
    await bot.wait_until_ready()

async def do_announce(client):
    if not cfg("payout"):
        print("[nightly] no payout configured yet \u2014 run /fed setup first. Skipping announce.")
        return
    prior = STATE["night"]
    if prior.get("live_channel_id") and prior.get("phase") != "idle":
        # admin forgot to close last night — archive + clean up before starting fresh
        try:
            await close_refund_night(client, client.user, silent=True)
        except Exception as e:
            print(f"[do_announce] auto-close of orphaned night failed: {e}")
    STATE["night"] = _fresh_night()
    night = STATE["night"]
    night["phase"] = "announced"
    night["date"] = datetime.now(ET).strftime("%Y-%m-%d")
    night["payout"] = STATE["config"]["payout"]
    night["team_count"] = cfg("team_count")
    STATE["config"]["run_number"] += 1
    log_event(night, f"Run #{cfg('run_number')} announced")
    await save_state()
    total_take = sum(r["per_winner"] for r in night["payout"]["rounds"])
    ch = channel(client, "announce_ch")
    if ch:
        test_tag = "\U0001F9EA **[TEST RUN]** " if TEST_MODE else ""
        msg = await ch.send(
            f"{test_tag}\U0001F9FE **THE REFUND \u2014 TONIGHT, 12:00 AM ET** \U0001F9FE\n"
            f"Took an L in somebody else's bracket? File a chargeback in ours.\n"
            f"**{night['team_count']} squads. ${cfg('buy_in'):g} in. Run the whole bracket, take ${total_take} "
            f"\u2014 GET UR MONEY BACK.**\nDeposits open **11:30**. `/fed enter` to claim your slot.")
        night["announce_msg"] = msg.id
        await save_state()

async def do_open(client):
    STATE["night"]["phase"] = "open"
    await save_state()
    ch = channel(client, "announce_ch")
    if ch:
        await ch.send(f"\U0001F3E7 **MACHINE OPEN.** `/fed enter` then Cash App **${cfg('buy_in'):g}** "
                      f"to **{cfg('cashtag')}**. Slots lock at 11:50. **0/{cfg('team_count')}.**")

async def do_lock(client):
    night = STATE["night"]
    team_count = cfg("team_count")
    while locked_count() < team_count and night["standby"]:
        sb = night["standby"].pop(0)
        night["teams"][str(sb["captain_id"])] = {
            "name": sb["name"], "members": [], "deposit": True,
            "proof": False, "clocked": False, "declined": False}
    locked = [int(cid) for cid, t in night["teams"].items() if t["deposit"]]
    ch = channel(client, "announce_ch")
    if len(locked) < team_count:
        night["phase"] = "idle"
        night["teams"] = {}
        log_event(night, f"Cancelled \u2014 only {len(locked)} squads, needed {team_count}")
        await save_state()
        if ch:
            await ch.send(f"\u26A0\uFE0F Under {team_count} squads tonight \u2014 machine stays closed. "
                          "We never run a half-machine. Back tomorrow, 11PM.")
        return
    pri = [c for c in locked if night["teams"][str(c)]["proof"]]
    rest = [c for c in locked if not night["teams"][str(c)]["proof"]]
    random.shuffle(pri); random.shuffle(rest)
    order = (pri + rest)[:team_count]
    random.shuffle(order)

    rounds_def = TEAM_BRACKETS[team_count]
    first = rounds_def[0]
    night["matches"] = seed_round(order, first["key"], first["format"])
    night["rounds"] = [r["key"] for r in rounds_def]
    night["current_round_index"] = 0
    night["team_count"] = team_count
    night["phase"] = "locked"

    # per-night live channel — this is what gets deleted on close
    guild = client.get_guild(cfg("guild_id"))
    live_ch = None
    if guild:
        category = guild.get_channel(cfg("category_id"))
        admin_role = guild.get_role(cfg("admin_role"))
        overwrites = {guild.default_role: discord.PermissionOverwrite(send_messages=False, view_channel=True)}
        if admin_role:
            overwrites[admin_role] = discord.PermissionOverwrite(send_messages=True, view_channel=True)
        live_ch = await guild.create_text_channel(
            f"\U0001F9FE Refund {cfg('run_number'):02d}", category=category, overwrites=overwrites,
            topic=f"Tonight's live bracket \u2014 Run #{cfg('run_number')}. Closes and archives after the chip.",
            reason="Federal Reserve \u2014 nightly live channel")
        night["live_channel_id"] = live_ch.id
    log_event(night, f"Locked \u2014 {team_count} squads, live channel created")
    await save_state()

    tc = channel(client, "timeclock_ch")
    if tc:
        msg = await tc.send("\u23F0 **TIME CLOCK OPEN** \u2014 captains, clock in NOW. Machine tips 12:00 sharp.\n"
                            "No clock-in at tip = **CARD DECLINED.**", view=ClockInView())
        night["clock_msg"] = msg.id
        await save_state()
    if ch and live_ch:
        await ch.send(f"\U0001F512 **MACHINE LOCKED.** Tonight's bracket is live in {live_ch.mention}.")
    if live_ch:
        first_payout = round_def(first["key"])
        lines = [f"\U0001F512 **MACHINE LOCKED \u2014 TONIGHT'S BRACKET ({team_count} SQUADS)**",
                f"**{first['label']} \u2014 {first['format']}**"]
        for mk, m in sorted(night["matches"].items()):
            lines.append(f"{mk}: **{team_name(m['a'])}** vs **{team_name(m['b'])}**")
        lines.append(f"Win = **${first_payout['per_winner']}** instant withdrawal.")
        await live_ch.send("\n".join(lines))
    await post_refund_checkpoint(client, f"MACHINE LOCKED \u2014 {team_count}-squad bracket seeded")

async def do_tip(client):
    night = STATE["night"]
    night["phase"] = "live"
    log_event(night, "Tip-off")
    await save_state()
    ch = refund_live_channel(client)
    if ch:
        await ch.send("\U0001F3C0 **12:00 \u2014 THE MACHINE IS LIVE.** Winners run `/fed report` with the end screen.")
    await post_refund_checkpoint(client, "TIP-OFF \u2014 machine live, clock-ins swept below")
    first_round_key = night["rounds"][0]
    for mk, m in list(night["matches"].items()):
        if m["round"] != first_round_key or m["winner"]:
            continue
        ta, tb = team_of(m["a"]), team_of(m["b"])
        a_ok = bool(ta and ta["clocked"])
        b_ok = bool(tb and tb["clocked"])
        if not a_ok and b_ok:
            await card_declined(client, m["a"], mk)
        elif not b_ok and a_ok:
            await card_declined(client, m["b"], mk)
        elif not a_ok and not b_ok and ch:
            await ch.send(f"\u26A0\uFE0F Both squads no-showed {mk} \u2014 admin resolve manually via Override Panel/HR.")

# ----------------------------- COMMANDS -------------------------------------
@fed.command(name="setup", description="(Admin) Pick event, size, format & price \u2014 builds the whole machine")
@app_commands.describe(
    event_type="Which event to configure",
    team_count="Bracket size \u2014 sets the format automatically",
    mode="launch = league-boosted pot | standard = self-funded rake",
    payout_style="Split Pot (every round pays) or Winner Takes All (champion gets everything)",
    buy_in="Entry price per squad in dollars (e.g. 10)",
    cashtag="Your $cashtag for deposits",
    boost_pct="Launch mode boost % on top of entries (default 25)",
    rake_pct="Standard mode rake % kept by league (default 10)",
    fine_amount="Roster mismatch fine in dollars, auto-deducted from next withdrawal (default 2)")
@app_commands.choices(
    event_type=[app_commands.Choice(name="THE REFUND (nightly losers bracket)", value="the_refund")],
    team_count=[
        app_commands.Choice(name="2 Teams \u2014 Straight to the Championship (BO3)", value=2),
        app_commands.Choice(name="4 Teams \u2014 Sudden Death opener, then BO3 Final", value=4),
        app_commands.Choice(name="8 Teams \u2014 Sudden Death opener, then BO3 the rest", value=8),
        app_commands.Choice(name="16 Teams \u2014 Sudden Death Round of 16, then BO3 the rest", value=16)],
    mode=[
        app_commands.Choice(name="Launch (league-boosted pot)", value="launch"),
        app_commands.Choice(name="Standard (self-funded, league keeps rake)", value="standard")],
    payout_style=[
        app_commands.Choice(name="Split Pot \u2014 every round pays (default)", value="split"),
        app_commands.Choice(name="Winner Takes All \u2014 champion gets the entire pot", value="wta")])
async def setup_cmd(itx: discord.Interaction, event_type: app_commands.Choice[str],
                    team_count: app_commands.Choice[int], mode: app_commands.Choice[str],
                    buy_in: float, cashtag: str, payout_style: app_commands.Choice[str] = None,
                    boost_pct: int = 25, rake_pct: int = 10, fine_amount: int = 2):
    if not await ensure_admin_pin(itx):
        return
    await itx.response.send_message("\u2699\uFE0F Building the machine \u2014 category, channels, roles, ladder\u2026",
                                    ephemeral=True)
    wta = bool(payout_style and payout_style.value == "wta")
    payout = compute_payouts(team_count.value, buy_in, mode.value, boost_pct, rake_pct, winner_takes_all=wta)
    if not payout:
        minv = find_min_viable_buyin(team_count.value, mode.value, boost_pct, rake_pct)
        hint = f" Try at least ${minv:.0f}." if minv else ""
        return await itx.followup.send(
            f"\u274C ${buy_in:g} is too low for a {team_count.value}-team bracket in {mode.value} mode "
            f"\u2014 a round would pay $0/winner.{hint}", ephemeral=True)

    c = STATE["config"]
    c["guild_id"] = itx.guild_id
    c["event_type"] = event_type.value
    c["team_count"] = team_count.value
    c["mode"] = mode.value
    c["buy_in"] = buy_in
    c["boost_pct"] = boost_pct
    c["rake_pct"] = rake_pct
    c["fine_amount"] = fine_amount
    c["cashtag"] = cashtag
    c["payout"] = payout
    await save_state()

    log = await build_infrastructure(itx.guild)
    guild_obj = discord.Object(id=itx.guild_id)
    bot.tree.copy_global_to(guild=guild_obj)
    await bot.tree.sync(guild=guild_obj)
    bot.tree.clear_commands(guild=None)
    await bot.tree.sync()   # push empty GLOBAL set so nothing ever shows doubled

    tc = channel(bot, "timeclock_ch")
    if tc:
        await tc.send("\u23F0 **TIME CLOCK** \u2014 clock in here every night before tip-off.", view=ClockInView())
    ac = channel(bot, "announce_ch")
    if ac:
        await ac.send(embed=discord.Embed(
            title="\u2699\uFE0F FEDERAL RESERVE \u2014 MANUAL OVERRIDE PANEL",
            description="Admins only. Use the dropdown below instead of memorizing commands.",
            color=NAVY), view=OverridePanelView())
        await ac.send(embed=discord.Embed(title="\U0001F3E7 APA ATM PRO AM \u2014 MAIN MENU", color=GOLD,
                                          description="Everyday actions, no commands to memorize."),
                      view=MainMenuView())
        # #the-refund is admin-only-post but visible to everyone — this is the
        # public record of what the machine is currently configured to run.
        await ac.send(embed=discord.Embed(
            title=f"\U0001F3E7 THE REFUND IS LIVE \u2014 {event_type.name}",
            description=(f"**{team_count.value} squads \u2014 {mode.name}**\n"
                        f"Buy-in: ${buy_in:g}/squad \u2192 Pot **${payout['pot']}**\n\n"
                        f"{format_ladder(payout)}\n\n"
                        f"Nightly: 11:00 announce \u2192 11:30 open \u2192 11:50 lock \u2192 12:00 tip.\n"
                        f"Not rostered yet? `/fed player register` then `/fed team register`."),
            color=GOLD))

    await itx.followup.send(
        f"\u2705 **MACHINE BUILT.**\n{log}\n\n"
        f"**{event_type.name}** \u2014 {team_count.value} squads \u2014 **{mode.name}**\n"
        f"Buy-in: ${buy_in:g}/squad \u2192 Gross ${payout['gross']:g} \u2192 Pot **${payout['pot']}**\n\n"
        f"{format_ladder(payout)}\n\n"
        f"Cashtag: {cashtag}\n"
        + ("\U0001F9EA TEST MODE ON \u2014 loops paused, PINs bypassed, pick clock 10s\n"
           if TEST_MODE else
           "Nightly loop LIVE: 11:00 announce \u2192 11:30 open \u2192 11:50 lock \u2192 12:00 tip.\n")
        + f"Public summary + Override Panel posted in {ac.mention if ac else '#the-refund'}.",
        ephemeral=True)

@fed.command(name="panel", description="(Admin) Re-post the manual Override Panel dropdown here")
async def panel_cmd(itx: discord.Interaction):
    if not await ensure_admin_pin(itx):
        return
    await itx.response.send_message(embed=discord.Embed(
        title="\u2699\uFE0F FEDERAL RESERVE \u2014 MANUAL OVERRIDE PANEL",
        description="Admins only. Use the dropdown below instead of memorizing commands.",
        color=NAVY), view=OverridePanelView())

@fed.command(name="reboot", description="(Admin) Hard-restart the bot process \u2014 use if it's stuck")
async def reboot_cmd(itx: discord.Interaction):
    if not await ensure_admin_pin(itx):
        return
    await itx.response.send_message(
        "\U0001F50C Rebooting \u2014 back in a few seconds. All state is saved, nothing is lost.", ephemeral=True)
    await perform_reboot(itx.client)

@fed.command(name="pin", description="(Admin) Set the admin PIN \u2014 lets a trusted non-admin unlock sensitive actions")
@app_commands.describe(new_pin="4-8 digit PIN")
async def pin_cmd(itx: discord.Interaction, new_pin: str):
    if not is_admin(itx.user):
        return await itx.response.send_message("Admins only.", ephemeral=True)
    if cfg("admin_pin_hash") and not admin_unlocked(itx.user.id):
        # changing the PIN is itself an admin action \u2014 prove you know the current one first
        return await itx.response.send_modal(AdminPinUnlockModal())
    if not (new_pin.isdigit() and 4 <= len(new_pin) <= 8):
        return await itx.response.send_message("PIN must be 4-8 digits.", ephemeral=True)
    STATE["config"]["admin_pin_hash"] = hash_pin(new_pin)
    unlock_admin(itx.user.id)   # you just set it \u2014 obviously you know it
    await save_state()
    print(f"[SECURITY] admin PIN changed by {itx.user} ({itx.user.id})")
    await itx.response.send_message(
        "\u2705 Admin PIN updated. Anyone who enters it correctly can close out an event even without "
        "the admin role or being the host.", ephemeral=True)


# ── TEST MODE COMMANDS ───────────────────────────────────────────────────────
async def _test_banner(ch, label: str):
    if not ch:
        return
    await ch.send(f"\U0001F9EA **[TEST MODE] {label}**\nTest run only \u2014 no real money. "
                  "Turn off with `/fed testmode` when done.")

def _seed_test_squads():
    """Inject `team_count` fake deposit-confirmed, clocked-in squads into tonight's
    night so do_lock() can actually seed a bracket during a dry run."""
    night = STATE["night"]
    team_count = cfg("team_count") or 4
    for i in range(team_count):
        fake_id = str(9_000_000_000_000_000_000 + i)   # impossible real Discord ID range
        night["teams"][fake_id] = {
            "name": f"TEST SQUAD {i + 1:02d}",
            "members": [], "deposit": True, "proof": False,
            "clocked": True, "declined": False,
        }
    log_event(night, f"[TEST] seeded {team_count} dummy squads")

# ---- shared accessors so ONE panel can drive all three event shapes --------
def _test_bracket_ctx(kind: str, tid: str = None):
    """Return (container, matches, rounds, current_index, name_fn, advance_fn,
    receipt_fn, round_fmt_fn, close_view_fn) for the given event kind so the
    panel can stay event-agnostic. `container` is the dict holding phase/matches."""
    if kind == "refund":
        night = STATE["night"]
        return {
            "obj": night,
            "name": lambda cid: team_name(cid),
            "advance": lambda client: advance_bracket(client),
            "receipt": lambda client, cid, rk, mk: post_receipt(client, team_name(cid), rk, mk, cid),
            "round_fmt": lambda rk: (round_def(rk) or {}).get("format", "BO1"),
            "label": "THE REFUND",
        }
    if kind == "fast5":
        session = STATE["fast5"]["session"]
        return {
            "obj": session,
            "name": lambda cid: fast5_team_name(session, cid),
            "advance": lambda client: advance_fast5_bracket(client),
            "receipt": lambda client, cid, rk, mk: post_fast5_receipt(
                client, session, fast5_team_name(session, cid), rk, mk, cid),
            "round_fmt": lambda rk: next((r["format"] for r in session["payout"]["rounds"]
                                          if r["key"] == rk), "BO1"),
            "label": f"FAST 5's {session['session_id']}" if session else "FAST 5's",
        }
    # tourney
    t = STATE["tournaments"].get(tid)
    return {
        "obj": t,
        "name": lambda cid: team_name_t(t, cid),
        "advance": lambda client: advance_tourney_bracket(client, t),
        "receipt": lambda client, cid, rk, mk: post_tourney_receipt(
            client, t, team_name_t(t, cid), rk, mk, cid),
        "round_fmt": lambda rk: next((r["format"] for r in t["payout"]["rounds"]
                                      if r["key"] == rk), "BO1"),
        "label": f"TOURNEY {tid}",
    }

class TestAdvanceView(discord.ui.View):
    """A button per open match in the current round. Click the winner."""
    def __init__(self, kind: str, tid: str = None):
        super().__init__(timeout=1800)
        self.kind = kind
        self.tid = tid
        ctx = _test_bracket_ctx(kind, tid)
        obj = ctx["obj"]
        if not obj:
            return
        cur_key = obj["rounds"][obj["current_round_index"]]
        for mk, m in sorted(obj["matches"].items()):
            if m["round"] != cur_key or m["winner"] or not (m["a"] and m["b"]):
                continue
            # one row = one match: two team buttons
            self.add_item(_TeamWinButton(kind, tid, mk, "a", ctx["name"](m["a"])))
            self.add_item(_TeamWinButton(kind, tid, mk, "b", ctx["name"](m["b"])))

class _TeamWinButton(discord.ui.Button):
    def __init__(self, kind, tid, match_key, side, team_label):
        super().__init__(
            label=f"{match_key}: {team_label[:60]}",
            style=discord.ButtonStyle.success if side == "a" else discord.ButtonStyle.primary)
        self.kind, self.tid, self.match_key, self.side = kind, tid, match_key, side

    async def callback(self, itx: discord.Interaction):
        if not (is_admin(itx.user) and admin_unlocked(itx.user.id)):
            return await itx.response.send_message(
                "\U0001F510 Admin PIN session required \u2014 run `/fed unlock` first.", ephemeral=True)
        await advance_test_match(itx, self.kind, self.tid, self.match_key, self.side)

async def advance_test_match(itx, kind, tid, match_key, side):
    ctx = _test_bracket_ctx(kind, tid)
    obj = ctx["obj"]
    if not obj:
        return await itx.response.send_message("That event is no longer active.", ephemeral=True)
    m = obj["matches"].get(match_key)
    if not m or m["winner"]:
        return await itx.response.send_message("That match is already decided \u2014 refreshing.", ephemeral=True)
    winner_id = m[side]
    loser_id = m["b"] if side == "a" else m["a"]
    need = 2 if ctx["round_fmt"](m["round"]) == "BO3" else 1
    if side == "a":
        m["score_a"] = need
    else:
        m["score_b"] = need
    m["winner"] = winner_id
    wname, lname = ctx["name"](winner_id), ctx["name"](loser_id)
    await ctx["receipt"](itx.client, winner_id, m["round"], match_key)
    await ctx["advance"](itx.client)
    await save_state()

    # figure out what phase we are in now
    obj = ctx["obj"]   # re-fetch (advance may mutate)
    done = (obj is None) or obj.get("phase") == "complete"
    await itx.response.edit_message(
        content=f"\U0001F9EA **[TEST] {wname} beat {lname}** \u2014 {lname} knocked out.",
        view=None)
    if done:
        champ_line = ""
        if obj:
            champ_line = "\n\U0001F3C6 **Champion crowned \u2014 bracket complete.** "
            champ_line += ("Run `.fast5 close`" if kind == "fast5" else "Run `/fed close`" if kind == "refund"
                           else "Run `/fed tourney close`") + " to archive."
        await itx.followup.send(f"\U0001F9EA **[TEST] {ctx['label']} finished.**{champ_line}")
    else:
        # more matches / next round — post a fresh panel
        await post_test_advance_panel(itx.client, kind, tid, via=itx)

async def post_test_advance_panel(client, kind, tid=None, via=None):
    """Post (or re-post) the clickable advance panel for the current round."""
    ctx = _test_bracket_ctx(kind, tid)
    obj = ctx["obj"]
    if not obj or obj.get("phase") == "complete":
        return
    cur_key = obj["rounds"][obj["current_round_index"]]
    open_matches = [(mk, m) for mk, m in sorted(obj["matches"].items())
                    if m["round"] == cur_key and not m["winner"] and m["a"] and m["b"]]
    # resolve target channel
    if kind == "refund":
        ch = refund_live_channel(client)
    elif kind == "fast5":
        ch = fast5_live_channel(client)
    else:
        ch = client.get_channel(obj["channel_id"])
    if not open_matches:
        return
    lines = [f"\U0001F9EA **[TEST] {ctx['label']} \u2014 CLICK THE WINNER OF EACH MATCH**",
             f"Round: **{cur_key}**  ({len(open_matches)} match(es) open)"]
    for mk, m in open_matches:
        lines.append(f"\u2022 {mk}: **{ctx['name'](m['a'])}**  vs  **{ctx['name'](m['b'])}**")
    body = "\n".join(lines)
    view = TestAdvanceView(kind, tid)
    if via is not None:
        await via.followup.send(content=body, view=view)
    elif ch:
        await ch.send(content=body, view=view)


async def _auto_play_refund_bracket(client):
    """Walk the whole bracket to a champion by awarding wins to the first team in
    each open match, so a dry run exercises advance_bracket + payouts end-to-end."""
    import asyncio
    guard = 0
    while STATE["night"]["phase"] == "live" and guard < 64:
        guard += 1
        night = STATE["night"]
        cur_key = night["rounds"][night["current_round_index"]]
        open_match = None
        for mk, m in sorted(night["matches"].items()):
            if m["round"] == cur_key and not m["winner"] and m["a"] and m["b"]:
                open_match = (mk, m); break
        if not open_match:
            break
        mk, m = open_match
        winner_id = m["a"]
        rd = round_def(cur_key)
        need = 2 if rd and rd["format"] == "BO3" else 1
        if m["a"] == winner_id:
            m["score_a"] = need
        else:
            m["score_b"] = need
        m["winner"] = winner_id
        await post_receipt(client, team_name(winner_id), m["round"], mk, winner_id)
        await advance_bracket(client)
        await asyncio.sleep(1)

async def do_test_full_refund_run(client):
    import asyncio
    ch = channel(client, "announce_ch")
    await _test_banner(ch, "FULL REFUND DRY RUN STARTING")
    await do_announce(client); await asyncio.sleep(2)
    await do_open(client);     await asyncio.sleep(2)
    _seed_test_squads()                       # <-- makes lock succeed
    if ch:
        await ch.send("\U0001F9EA **[TEST]** Seeded dummy squads \u2014 locking the machine\u2026")
    await save_state()
    await do_lock(client);     await asyncio.sleep(2)
    await do_tip(client);      await asyncio.sleep(2)
    if ch:
        await ch.send("\U0001F9EA **[TEST]** Bracket seeded \u2014 use the CLICK-THE-WINNER panel below "
                      "to advance each round manually.")
    await post_test_advance_panel(client, "refund")

async def _seed_fast5_test_field(n_captains: int = 4, players_per_pos: int = 4):
    """Fill the current queueing session with dummy captains and a dummy player
    pool so a dry run can lock, draft, and play out without real humans."""
    session = STATE["fast5"]["session"]
    reg = STATE["registry"]
    # dummy captains
    for i in range(n_captains):
        cid = str(9_100_000_000_000_000_000 + i)
        session["captains"][cid] = {"team_name": f"TEST CAPT {i + 1:02d}", "roster": []}
        reg["players"].setdefault(cid, {"gamertag": f"TestCapt{i + 1:02d}", "onboarded": False})
    # dummy players spread across all five positions
    pidx = 0
    for pos in FAST5_POSITIONS:
        for _ in range(players_per_pos):
            pid = 9_200_000_000_000_000_000 + pidx
            session["pools"][pos].append(pid)
            reg["players"].setdefault(str(pid), {"gamertag": f"TestPlyr{pidx + 1:02d}", "onboarded": False})
            pidx += 1
    log_event(session, f"[TEST] seeded {n_captains} captains + {pidx} players")

async def _auto_run_fast5_draft(client):
    """Advance every pick with auto_skip_pick until the draft finishes."""
    import asyncio
    guard = 0
    while STATE["fast5"]["session"] and STATE["fast5"]["session"]["phase"] == "drafting" and guard < 200:
        guard += 1
        await auto_skip_pick(client)
        await asyncio.sleep(0.3)

async def _auto_play_fast5_bracket(client):
    """Award each open match to its first team until a champion is crowned."""
    import asyncio
    guard = 0
    while STATE["fast5"]["session"] and STATE["fast5"]["session"]["phase"] == "live" and guard < 64:
        guard += 1
        session = STATE["fast5"]["session"]
        cur_key = session["rounds"][session["current_round_index"]]
        open_match = None
        for mk, m in sorted(session["matches"].items()):
            if m["round"] == cur_key and not m["winner"] and m["a"] and m["b"]:
                open_match = (mk, m); break
        if not open_match:
            break
        mk, m = open_match
        winner_id = m["a"]
        rd = next((r for r in session["payout"]["rounds"] if r["key"] == cur_key), None)
        need = 2 if rd and rd["format"] == "BO3" else 1
        m["score_a"] = need
        m["winner"] = winner_id
        await post_fast5_receipt(client, session, fast5_team_name(session, winner_id),
                                 m["round"], mk, winner_id)
        await advance_fast5_bracket(client)
        await asyncio.sleep(1)

async def do_test_tourney(client, itx):
    """Build a fully-seeded 4-team test tournament, then show the click panel."""
    guild = itx.guild
    if not guild or not cfg("guild_id"):
        return await itx.followup.send("Run `/fed setup` first \u2014 need the server category/roles.")
    category_id = cfg("tourney_category_id")
    category = guild.get_channel(category_id) if category_id else None
    if not category:
        admin_role = guild.get_role(cfg("admin_role"))
        overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=True)}
        if admin_role:
            overwrites[admin_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        category = await guild.create_category("\U0001F3C6 LIVE TOURNEYS", overwrites=overwrites,
                                                reason="Federal Reserve \u2014 instant tournaments")
        STATE["config"]["tourney_category_id"] = category.id
    STATE["config"]["tourney_counter"] += 1
    tourney_id = f"T{STATE['config']['tourney_counter']}"
    chan_name = f"\U0001F3C6 Tourney {STATE['config']['tourney_counter']:02d}"
    ch = await guild.create_text_channel(chan_name, category=category, reason="Federal Reserve — TEST tournament")
    team_count = 4
    payout = compute_payouts(team_count, 10, "launch", 25, 10, winner_takes_all=False)
    t = {
        "id": tourney_id, "creator_id": itx.user.id, "is_admin_run": True,
        "team_count": team_count, "buy_in": 10, "mode": "launch",
        "boost_pct": 25, "rake_pct": 10, "cashtag": "$TEST",
        "house_rules": None, "channel_id": ch.id, "payout": payout,
        "phase": "open", "created_at": datetime.now(ET).isoformat(),
        "teams": {}, "standby": [], "rounds": [], "matches": {}, "current_round_index": 0, "event_log": [],
    }
    # seed dummy squads
    for i in range(team_count):
        cid = str(9_300_000_000_000_000_000 + i)
        t["teams"][cid] = {"name": f"TEST SQUAD {i + 1:02d}", "members": [],
                           "deposit": True, "proof": False, "clocked": True, "declined": False}
    STATE["tournaments"][tourney_id] = t
    log_event(t, "[TEST] tournament seeded with 4 dummy squads")
    await save_state()
    await tourney_lock_and_start(client, t)     # seeds the bracket, phase -> live
    await ch.send("\U0001F9EA **[TEST]** Tournament seeded \u2014 use the CLICK-THE-WINNER panel to advance rounds.")
    await post_test_advance_panel(client, "tourney", tourney_id)
    await itx.followup.send(f"\U0001F9EA Test tournament live in {ch.mention}.", ephemeral=True)


async def do_test_open_fast5(client):
    import asyncio
    tip_times = cfg5("tip_times")
    if not tip_times:
        print("[TEST] no tip_times \u2014 run .fast5 setup first")
        return
    hh, mm = tip_times[0]
    await fast5_open_session(client, hh, mm)
    ch = fast5_channel(client, "announce_ch")
    await _seed_fast5_test_field(n_captains=4, players_per_pos=4)
    await save_state()
    if ch:
        await ch.send("\U0001F9EA **[TEST]** Seeded 4 dummy captains + player pool \u2014 "
                      "locking + auto-running the draft\u2026")
    await fast5_lock_and_draft(client)          # builds the live channel, starts the draft
    await asyncio.sleep(2)
    await _auto_run_fast5_draft(client)         # fills every roster via auto-pick
    await asyncio.sleep(2)
    if ch:
        await ch.send("\U0001F9EA **[TEST]** Draft done \u2014 use the CLICK-THE-WINNER panel below "
                      "to advance each round manually.")
    await post_test_advance_panel(client, "fast5")

@fed.command(name="testmode", description="(Admin) Toggle test mode ON/OFF")
async def testmode_cmd(itx: discord.Interaction):
    global TEST_MODE
    if not await ensure_admin_pin(itx):
        return
    TEST_MODE = not TEST_MODE
    pick_s = fast5_pick_clock()
    mode_str = "ON" if TEST_MODE else "OFF"
    if TEST_MODE:
        msg = (f"\U0001F9EA **TEST MODE ON**\n"
               f"\u2022 Auto-loops paused\n\u2022 Coach PIN gates bypassed\n"
               f"\u2022 Draft pick clock: **{pick_s}s**\n"
               f"\u2022 Queues stay open until you manually lock them\n"
               f"\u2022 Run `/fed test` to fire a dry run")
    else:
        msg = "\u2705 **TEST MODE OFF** \u2014 back on live schedule."
    print(f"[TEST MODE] {mode_str} by {itx.user} ({itx.user.id})")
    await itx.response.send_message(msg, ephemeral=True)

@fed.command(name="test", description="(Admin) Fire a full dry run (test mode required)")
@app_commands.describe(event="Which event to dry-run")
@app_commands.choices(event=[
    app_commands.Choice(name="Refund: full announce to tip",  value="refund"),
    app_commands.Choice(name="Tourney: seed a 4-team test bracket", value="tourney"),
    app_commands.Choice(name="Fast 5s: open queueing now",   value="fast5"),
])
async def test_cmd(itx: discord.Interaction, event: app_commands.Choice[str]):
    if not await ensure_admin_pin(itx):
        return
    if not TEST_MODE:
        return await itx.response.send_message(
            "\u26A0\uFE0F Test mode is OFF. Run `/fed testmode` first.", ephemeral=True)
    await itx.response.send_message(f"\U0001F9EA Launching **{event.name}** dry run\u2026", ephemeral=True)
    if event.value == "refund":
        await do_test_full_refund_run(itx.client)
    elif event.value == "tourney":
        await do_test_tourney(itx.client, itx)
    else:
        await do_test_open_fast5(itx.client)

@fed.command(name="unlock", description="(Admin) Enter the admin PIN \u2014 unlocks every admin action for 15 minutes")
async def unlock_cmd(itx: discord.Interaction):
    if not is_admin(itx.user):
        return await itx.response.send_message("Admins only.", ephemeral=True)
    if admin_unlocked(itx.user.id):
        return await itx.response.send_message("\u2705 Already unlocked \u2014 you're good.", ephemeral=True)
    if not cfg("admin_pin_hash"):
        return await itx.response.send_message("No admin PIN set yet \u2014 run `/fed pin` first.", ephemeral=True)
    await itx.response.send_modal(AdminPinUnlockModal())

@fed.command(name="coachpin", description="(Admin) Wipe a coach's PIN \u2014 their next lock action sets a fresh one")
@app_commands.describe(coach="The coach whose PIN should be reset")
async def coachpin_cmd(itx: discord.Interaction, coach: discord.Member):
    if not await ensure_admin_pin(itx):
        return
    STATE["registry"].setdefault("coach_pins", {}).pop(str(coach.id), None)
    await save_state()
    print(f"[SECURITY] coach PIN wiped for {coach.id} by {itx.user} ({itx.user.id})")
    await itx.response.send_message(
        f"\u2705 Coach PIN wiped for {coach.mention} \u2014 their next lock action will set a new one.",
        ephemeral=True)

@fed.command(name="close", description="(Admin) Close tonight's REFUND \u2014 archives the full receipt, deletes the live channel")
async def refund_close_cmd(itx: discord.Interaction):
    if not await ensure_admin_pin(itx):
        return
    if not STATE["night"].get("live_channel_id"):
        return await itx.response.send_message("No active night to close right now.", ephemeral=True)
    await itx.response.send_message("\U0001F4DC Archiving full receipt\u2026", ephemeral=True)
    await close_refund_night(itx.client, itx.user)

@fed.command(name="guide", description="Step-by-step walkthrough \u2014 pick admin, captain, or player")
async def guide_cmd(itx: discord.Interaction):
    landing = discord.Embed(title="\U0001F4D6 FEDERAL RESERVE \u2014 STEP BY STEP GUIDE", color=GOLD,
                            description="Pick your role below for a full walkthrough.")
    await itx.response.send_message(embed=landing, view=GuideView())

@fed.command(name="menu", description="The main menu \u2014 every everyday action as a dropdown, no commands to memorize")
async def menu_cmd(itx: discord.Interaction):
    emb = discord.Embed(title="\U0001F3E7 APA ATM PRO AM \u2014 MAIN MENU", color=GOLD,
                        description="Pick what you need from the dropdown below. Two actions still need a "
                                    "one-off slash command (team logo, game screenshot) since Discord menus "
                                    "can't accept file uploads \u2014 the menu will tell you exactly which one.")
    await itx.response.send_message(embed=emb, view=MainMenuView())

@fed.command(name="roster", description="Show the exact format to post your roster in the roster book")
async def roster_template_cmd(itx: discord.Interaction):
    ch = channel(itx.client, "roster_ch")
    fine_amt = cfg("fine_amount")
    template = ("```\n"
               "TEAM: YourSquadName\n"
               "GT: ExactGamertag1 - @Player1\n"
               "GT: ExactGamertag2 - @Player2\n"
               "GT: ExactGamertag3 - @Player3\n"
               "```")
    await itx.response.send_message(
        f"\U0001F4CB **Post this in {ch.mention if ch else '#roster-book'}** (edit the values, keep the format):\n"
        f"{template}\n"
        f"Gamertag must match your **exact, first-ever registered spelling** \u2014 case-sensitive. "
        f"A mismatch found on `/fed scan` = a **${fine_amt} fine**, auto-deducted from your squad's next withdrawal.",
        ephemeral=True)

@fed.command(name="scan", description="(Admin) Scan the roster book and build the player/roster ledger")
async def scan_cmd(itx: discord.Interaction):
    if not await ensure_admin_pin(itx):
        return
    ch = channel(itx.client, "roster_ch")
    if not ch:
        return await itx.response.send_message("Roster channel isn't set up yet \u2014 run `/fed setup` first.",
                                                ephemeral=True)
    await itx.response.send_message("\U0001F50D Scanning the roster book\u2026", ephemeral=True)

    reg = STATE["registry"]
    fine_amt = cfg("fine_amount")
    last_id = reg.get("last_scanned_id", 0)
    after_obj = discord.Object(id=last_id) if last_id else None
    messages = [m async for m in ch.history(limit=500, after=after_obj, oldest_first=True)]

    new_count = mismatch_count = rosters_updated = 0
    fresh_fines = []
    max_id_seen = last_id
    for msg in messages:
        max_id_seen = max(max_id_seen, msg.id)
        team_name, players = parse_roster_message(msg.content)
        if not players:
            continue
        captain_id = msg.author.id
        roster_entry = {"captain_id": captain_id, "players": [],
                        "updated": datetime.now(ET).strftime("%Y-%m-%d"), "message_id": msg.id}
        for gamertag, discord_id in players:
            existing = reg["players"].get(str(discord_id))
            if not existing:
                reg["players"][str(discord_id)] = {"gamertag": gamertag,
                                                    "first_seen": datetime.now(ET).strftime("%Y-%m-%d")}
                new_count += 1
                status = "NEW"
            elif existing["gamertag"] == gamertag:
                status = "OK"
            else:
                mismatch_count += 1
                reg["fines_owed"][str(captain_id)] = reg["fines_owed"].get(str(captain_id), 0) + fine_amt
                fine_entry = {"captain_id": captain_id, "discord_id": discord_id,
                             "expected": existing["gamertag"], "submitted": gamertag,
                             "amount": fine_amt, "message_id": msg.id,
                             "date": datetime.now(ET).strftime("%Y-%m-%d")}
                reg["fines_log"].append(fine_entry)
                fresh_fines.append(fine_entry)
                status = "MISMATCH"
            roster_entry["players"].append({"discord_id": discord_id, "gamertag": gamertag, "status": status})
        if team_name:
            reg["rosters"][team_name] = roster_entry
            rosters_updated += 1
    reg["last_scanned_id"] = max_id_seen
    await save_state()

    await itx.followup.send(
        f"\u2705 **SCAN COMPLETE** \u2014 {len(messages)} new messages checked.\n"
        f"New players registered: **{new_count}**\n"
        f"Rosters updated: **{rosters_updated}**\n"
        f"Mismatches found: **{mismatch_count}**"
        + (f" (${fine_amt} fine each, auto-deducted from next withdrawal)" if mismatch_count else ""),
        ephemeral=True)

    if fresh_fines:
        hr = channel(itx.client, "hr_ch")
        if hr:
            lines = [f"<@{f['captain_id']}> \u2014 expected `{f['expected']}`, got `{f['submitted']}` "
                    f"(+${f['amount']} fine)" for f in fresh_fines]
            await hr.send("\u26A0\uFE0F **ROSTER SCAN \u2014 MISMATCHES FOUND**\n" + "\n".join(lines))

@fed.command(name="ledger", description="(Admin) View the player registry, rosters, and outstanding fines")
async def ledger_cmd(itx: discord.Interaction):
    if not await ensure_admin_pin(itx):
        return
    reg = STATE["registry"]
    lines = [f"**PLAYER REGISTRY** \u2014 {len(reg['players'])} on file"]
    for did, p in list(reg["players"].items())[:25]:
        lines.append(f"- <@{did}>: `{p['gamertag']}` (since {p['first_seen']})")
    if len(reg["players"]) > 25:
        lines.append(f"...and {len(reg['players']) - 25} more")
    lines.append(f"\n**ROSTERS ON FILE** \u2014 {len(reg['rosters'])}")
    for team, r in reg["rosters"].items():
        lines.append(f"- {team} (capt <@{r['captain_id']}>): {len(r['players'])} players, updated {r['updated']}")
    outstanding = {k: v for k, v in reg.get("fines_owed", {}).items() if v > 0}
    if outstanding:
        lines.append("\n**OUTSTANDING FINES** (auto-deducted from next withdrawal)")
        for did, amt in outstanding.items():
            lines.append(f"- <@{did}>: ${amt} owed")
    await itx.response.send_message("\n".join(lines), ephemeral=True)

class RosterSelectView(discord.ui.View):
    """UserSelect always reflects LIVE server membership — no manual refresh
    needed, unlike a bot-populated dropdown. Short-lived (not persistent
    across restarts); only needs to survive one registration interaction."""
    def __init__(self, team_name: str, captain_id: int, logo_url: str):
        super().__init__(timeout=300)
        self.team_name = team_name
        self.captain_id = captain_id
        self.logo_url = logo_url

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="Select your roster (players must be PLAYER-registered first)",
                      min_values=1, max_values=8)
    async def pick_roster(self, itx: discord.Interaction, select: discord.ui.UserSelect):
        if itx.user.id != self.captain_id:
            return await itx.response.send_message("Only the registering captain can submit this roster.", ephemeral=True)
        reg = STATE["registry"]
        missing = [m for m in select.values if str(m.id) not in reg["players"]]
        if missing:
            names = ", ".join(m.mention for m in missing)
            return await itx.response.send_message(
                f"\u274C These players haven't registered a gamertag yet: {names}\n"
                f"Have them run `/fed player register` first, then submit the roster again.", ephemeral=True)
        picked = [(m.id, m.mention, reg["players"][str(m.id)]["gamertag"]) for m in select.values]

        async def _lock_roster(mitx: discord.Interaction):
            # Runs only AFTER the coach's own PIN clears \u2014 this is the roster LOCK.
            reg2 = STATE["registry"]
            now = datetime.now(ET).strftime("%Y-%m-%d")
            existing_team = reg2["teams"].get(self.team_name)
            reg2["teams"][self.team_name] = {
                "captain_id": self.captain_id, "logo_url": self.logo_url,
                "players": [pid for pid, _m, _g in picked],
                "created": existing_team["created"] if existing_team else now,
                "updated": now,
            }
            await save_state()
            roster_lines = [f"\u2022 {mention} \u2014 `{gt}`" for _pid, mention, gt in picked]
            emb = discord.Embed(title=f"\U0001F510 TEAM REGISTERED & PIN-LOCKED \u2014 {self.team_name}",
                                description="\n".join(roster_lines), color=GOLD)
            if self.logo_url:
                emb.set_thumbnail(url=self.logo_url)
            copy_block = "\n".join(
                [f"TEAM: {self.team_name} | capt <@{self.captain_id}> | id {self.captain_id}"]
                + [f"  <@{pid}> | id {pid} | gt {gt}" for pid, _m, gt in picked])
            print(f"[CHECKPOINT][ROSTER] STEP COMPLETE \u2014 roster locked:\n{copy_block}")
            await mitx.response.send_message(
                content=f"\u2705 **STEP COMPLETE \u2014 roster locked with Coach PIN.** "
                        f"Copy/paste record:\n```txt\n{copy_block}\n```", embed=emb)

        await gate_coach_pin(itx, self.captain_id, _lock_roster)

@fed.command(name="player", description="Register yourself as a player \u2014 required before any captain can roster you")
@app_commands.describe(gamertag="Your EXACT in-game gamertag \u2014 case-sensitive, locked in on first registration")
async def player_register_cmd(itx: discord.Interaction, gamertag: str):
    await _do_player_register(itx, gamertag)

@fed.command(name="team", description="Register your team \u2014 name, logo, then pick your roster")
@app_commands.describe(team_name="Your squad name", logo="Team logo image")
async def team_register_cmd(itx: discord.Interaction, team_name: str, logo: discord.Attachment):
    team_name = team_name.strip()[:32]
    reg = STATE["registry"]
    existing = reg["teams"].get(team_name)
    if existing and existing["captain_id"] != itx.user.id:
        return await itx.response.send_message(
            f"**{team_name}** is already registered by <@{existing['captain_id']}>. Pick a different name.",
            ephemeral=True)
    await grant_role(itx.client, itx.user.id, "captain_role", "Registered a team")
    emb = discord.Embed(title=f"\U0001F3E7 {team_name}", description=f"Captain: {itx.user.mention}", color=GOLD)
    emb.set_thumbnail(url=logo.url)
    await itx.response.send_message(
        content="Now pick your roster \u2014 selections are LIVE server members, always current:",
        embed=emb, view=RosterSelectView(team_name, itx.user.id, logo.url))

async def _do_enter(itx: discord.Interaction, proof: discord.Attachment = None):
    if not cfg("payout"):
        return await itx.response.send_message("Machine hasn't been set up yet \u2014 ask an admin to run `/fed setup`.",
                                                ephemeral=True)
    reg = STATE["registry"]
    my_team = next((name for name, t in reg["teams"].items() if t["captain_id"] == itx.user.id), None)
    if not my_team:
        return await itx.response.send_message(
            "You're not a registered captain yet \u2014 run `/fed team register` first (name, logo, then pick your roster).",
            ephemeral=True)
    night = STATE["night"]
    if night["phase"] not in ("announced", "open"):
        return await itx.response.send_message(
            "Machine isn't taking entries right now. Deposits open **11:30 PM ET** nightly.", ephemeral=True)
    if str(itx.user.id) in night["teams"]:
        return await itx.response.send_message("You already have a slot pending tonight.", ephemeral=True)
    cap = cfg("team_count")
    if locked_count() >= cap:
        return await itx.response.send_message(
            "Machine is full \u2014 use `/fed standby` to be first in line for a declined card.", ephemeral=True)
    team_data = reg["teams"][my_team]

    async def _lock_entry(mitx: discord.Interaction):
        # Runs only AFTER the coach's own PIN clears \u2014 entering = locking a slot request.
        night2 = STATE["night"]
        if str(mitx.user.id) in night2["teams"]:
            return await mitx.response.send_message("You already have a slot pending tonight.", ephemeral=True)
        night2["teams"][str(mitx.user.id)] = {
            "name": my_team, "members": team_data["players"], "deposit": False,
            "proof": bool(proof), "clocked": False, "declined": False}
        log_event(night2, f"ENTRY: {my_team} (capt {mitx.user.id}) PIN-locked an entry request")
        await save_state()
        emb = discord.Embed(
            title="\U0001F3E7 SLOT PENDING \u2014 MAKE YOUR DEPOSIT",
            description=(f"**{my_team}** (capt. {mitx.user.mention})\n"
                         f"Cash App **${cfg('buy_in'):g}** to **{cfg('cashtag')}** with your squad name in the note.\n"
                         f"Slot locks when an admin confirms the deposit."
                         + ("\n\U0001F9FE **L-proof attached \u2014 priority seeding.**" if proof else "")),
            color=GOLD)
        emb.set_footer(text=f"captain:{mitx.user.id}")
        if team_data.get("logo_url"):
            emb.set_thumbnail(url=team_data["logo_url"])
        if proof:
            emb.set_image(url=proof.url)
            await grant_role(mitx.client, mitx.user.id, "refunded_role", "Same-night L proof")
        await mitx.response.send_message(embed=emb, view=DepositView())

    await gate_coach_pin(itx, itx.user.id, _lock_entry)

async def _do_standby(itx: discord.Interaction, team: str):
    night = STATE["night"]
    if any(sb["captain_id"] == itx.user.id for sb in night["standby"]):
        return await itx.response.send_message("Already on standby.", ephemeral=True)
    night["standby"].append({"captain_id": itx.user.id, "name": team.strip()[:32]})
    await save_state()
    await itx.response.send_message(
        f"\U0001F4CB **{team}** on standby (#{len(night['standby'])}). "
        f"If a card declines, you're in \u2014 have your deposit ready.")

async def _do_board(itx: discord.Interaction):
    night = STATE["night"]
    lines = [f"**THE REFUND #{cfg('run_number')}** \u2014 phase: `{night['phase'].upper()}` \u2014 "
             f"size: `{cfg('team_count')}` \u2014 mode: `{cfg('mode').upper()}` \u2014 buy-in: ${cfg('buy_in'):g}"]
    if cfg("payout"):
        lines.append(format_ladder(cfg("payout")))
    lines.append("")
    if night["teams"]:
        lines.append("**SQUADS:**")
        for cid, t in night["teams"].items():
            flags = "".join(["\U0001F512" if t["deposit"] else "\u23F3",
                             " \U0001F9FE" if t["proof"] else "",
                             " \u23F0" if t["clocked"] else "",
                             " \U0001F6AB" if t["declined"] else ""])
            lines.append(f"- {t['name']} <@{cid}> {flags}")
    if night["standby"]:
        lines.append("**STANDBY:** " + ", ".join(sb["name"] for sb in night["standby"]))
    if night["matches"]:
        lines.append("")
        current_key = night["rounds"][night["current_round_index"]] if night["rounds"] else None
        for rk in night["rounds"]:
            rd = round_def(rk)
            label = rd["label"] if rd else rk
            marker = " \u25C0 CURRENT" if rk == current_key else ""
            lines.append(f"**{label}**{marker}")
            for mk, m in sorted(night["matches"].items()):
                if m["round"] != rk:
                    continue
                if m["winner"]:
                    lines.append(f"  {mk}: {team_name(m['a'])} vs {team_name(m['b'])} \u2192 **{team_name(m['winner'])}**")
                elif m["format"] == "BO3" and (m["score_a"] or m["score_b"]):
                    lines.append(f"  {mk}: {team_name(m['a'])} {m['score_a']}\u2013{m['score_b']} {team_name(m['b'])}")
                elif m["a"]:
                    lines.append(f"  {mk}: {team_name(m['a'])} vs {team_name(m['b'])}")
    await itx.response.send_message("\n".join(lines))

async def _do_clockin(itx: discord.Interaction):
    t = team_of(itx.user.id)
    if not (t and t["deposit"]):
        return await itx.response.send_message("You're not a locked captain tonight.", ephemeral=True)
    if t["clocked"]:
        return await itx.response.send_message("Already clocked in. \u2705", ephemeral=True)
    t["clocked"] = True
    await save_state()
    await itx.response.send_message(f"\u2705 **{t['name']}** clocked in.")

async def _do_player_register(itx: discord.Interaction, gamertag: str):
    reg = STATE["registry"]
    gamertag = gamertag.strip()[:32]
    existing = reg["players"].get(str(itx.user.id))
    if existing:
        if existing["gamertag"] == gamertag:
            return await itx.response.send_message(f"You're already registered as `{gamertag}`. \u2705", ephemeral=True)
        hr = channel(itx.client, "hr_ch")
        return await itx.response.send_message(
            f"You're already registered as `{existing['gamertag']}`. Gamertag changes aren't self-service \u2014 "
            f"post in {hr.mention if hr else '#hr'} if this needs correcting.", ephemeral=True)
    reg["players"][str(itx.user.id)] = {"gamertag": gamertag, "first_seen": datetime.now(ET).strftime("%Y-%m-%d")}
    await save_state()
    await grant_role(itx.client, itx.user.id, "player_role", "Self-registered gamertag")
    await itx.response.send_message(
        f"\u2705 Registered as **{gamertag}**. This is now your permanent, exact-match gamertag \u2014 "
        f"captains can roster you starting now.", ephemeral=True)

@fed.command(name="enter", description="Enter tonight's REFUND \u2014 registered captains only")
@app_commands.describe(proof="Screenshot of tonight's elimination elsewhere (priority seeding)")
async def enter_cmd(itx: discord.Interaction, proof: discord.Attachment = None):
    await _do_enter(itx, proof)

@fed.command(name="standby", description="Join tonight's standby list (fills declined cards)")
@app_commands.describe(team="Your squad name")
async def standby_cmd(itx: discord.Interaction, team: str):
    await _do_standby(itx, team)

@fed.command(name="board", description="Tonight's machine status")
async def board_cmd(itx: discord.Interaction):
    await _do_board(itx)

@fed.command(name="clockin", description="Clock in for tonight (required before 12:00)")
async def clockin_cmd(itx: discord.Interaction):
    await _do_clockin(itx)

@fed.command(name="report", description="Report your GAME W \u2014 attach the end-game screenshot")
@app_commands.describe(screenshot="End-game screenshot showing the final score")
async def report_cmd(itx: discord.Interaction, screenshot: discord.Attachment):
    night = STATE["night"]
    if night["phase"] not in ("locked", "live"):
        return await itx.response.send_message("Machine isn't live.", ephemeral=True)
    mk, side, m = None, None, None
    for k, mm in night["matches"].items():
        if not mm["winner"] and itx.user.id in (mm["a"], mm["b"]):
            mk, m = k, mm
            side = "a" if mm["a"] == itx.user.id else "b"
            break
    if not mk:
        return await itx.response.send_message("No open match found for you.", ephemeral=True)
    opp_id = m["b"] if side == "a" else m["a"]
    rd = round_def(m["round"])
    series = f" (Series: {m['score_a']}\u2013{m['score_b']})" if m["format"] == "BO3" else ""
    ch = channel(itx.client, "results_ch") or itx.channel
    emb = discord.Embed(
        title=f"\U0001F4E5 GAME RESULT REPORTED \u2014 {mk}",
        description=f"**{team_name(itx.user.id)}** claims this game over **{team_name(opp_id)}**.{series}\n"
                    f"{rd['label'] if rd else m['round']} \u2014 {m['format']}\n"
                    f"Admin: verify to log the game (or trigger the withdrawal if this clinches it).",
        color=NAVY)
    emb.set_image(url=screenshot.url)
    emb.set_footer(text=f"match:{mk}:side:{side}")
    await ch.send(embed=emb, view=VerifyView())
    await itx.response.send_message("\u2705 Reported. Verification logs the game / fires the withdrawal.", ephemeral=True)

# ----------------------------- INSTANT TOURNAMENT COMMANDS -----------------
@tourney.command(name="start", description="Fire up a new tournament right now \u2014 any captain or admin can do this")
@app_commands.describe(
    team_count="Bracket size", buy_in="Entry price per squad in dollars",
    cashtag="Cash App tag entries get sent to \u2014 YOURS if you're not an admin",
    payout_style="Split Pot (every round pays) or Winner Takes All (champion gets everything)",
    house_rules="Optional: any custom rules for this tournament",
    mode="Admins only \u2014 non-admins are always fee-free, zero boost",
    boost_pct="Admins only, launch mode boost % (default 25)",
    rake_pct="Admins only, standard mode rake % (default 10)")
@app_commands.choices(
    team_count=[
        app_commands.Choice(name="2 Teams \u2014 Straight to the Championship (BO3)", value=2),
        app_commands.Choice(name="4 Teams \u2014 Sudden Death opener, then BO3 Final", value=4),
        app_commands.Choice(name="8 Teams \u2014 Sudden Death opener, then BO3 the rest", value=8),
        app_commands.Choice(name="16 Teams \u2014 Sudden Death Round of 16, then BO3 the rest", value=16)],
    mode=[app_commands.Choice(name="Launch (league-boosted pot)", value="launch"),
         app_commands.Choice(name="Standard (self-funded, house rake)", value="standard")],
    payout_style=[
        app_commands.Choice(name="Split Pot \u2014 every round pays (default)", value="split"),
        app_commands.Choice(name="Winner Takes All \u2014 champion gets the entire pot", value="wta")])
async def tourney_start_cmd(itx: discord.Interaction, team_count: app_commands.Choice[int], buy_in: float,
                           cashtag: str, house_rules: str = None, mode: app_commands.Choice[str] = None,
                           payout_style: app_commands.Choice[str] = None, boost_pct: int = 25, rake_pct: int = 10):
    if not cfg("guild_id"):
        return await itx.response.send_message(
            "Run `/fed setup` at least once first \u2014 the server needs its roles/category before "
            "tournaments can spin up.", ephemeral=True)
    if not can_start_tourney(itx.user):
        return await itx.response.send_message(
            "You need the CAPTAIN role (register a team with `/fed team register`) or be an admin to "
            "start a tournament.", ephemeral=True)

    admin_running = is_admin(itx.user)
    if admin_running and mode:
        use_mode, use_boost, use_rake = mode.value, boost_pct, rake_pct
    else:
        use_mode, use_boost, use_rake = "standard", 0, 0   # NO FEES. NO BOOST. for user-run tourneys
    wta = bool(payout_style and payout_style.value == "wta")

    payout = compute_payouts(team_count.value, buy_in, use_mode, use_boost, use_rake, winner_takes_all=wta)
    if not payout:
        minv = find_min_viable_buyin(team_count.value, use_mode, use_boost, use_rake)
        hint = f" Try at least ${minv:.0f}." if minv else ""
        return await itx.response.send_message(
            f"\u274C ${buy_in:g} is too low for a {team_count.value}-team bracket.{hint}", ephemeral=True)

    await itx.response.send_message("\U0001F3D7\uFE0F Spinning up your tournament\u2026", ephemeral=True)
    guild = itx.guild
    category_id = cfg("tourney_category_id")
    category = guild.get_channel(category_id) if category_id else None
    if not category:
        admin_role = guild.get_role(cfg("admin_role"))
        overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=True)}
        if admin_role:
            overwrites[admin_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        category = await guild.create_category("\U0001F3C6 LIVE TOURNEYS", overwrites=overwrites,
                                                reason="Federal Reserve \u2014 instant tournaments")
        STATE["config"]["tourney_category_id"] = category.id

    STATE["config"]["tourney_counter"] += 1
    tourney_id = f"T{STATE['config']['tourney_counter']}"
    chan_name = f"\U0001F3C6 Tourney {STATE['config']['tourney_counter']:02d}"
    ch = await guild.create_text_channel(chan_name, category=category, reason=f"Instant tournament by {itx.user}")

    t = {
        "id": tourney_id, "creator_id": itx.user.id, "is_admin_run": admin_running,
        "team_count": team_count.value, "buy_in": buy_in, "mode": use_mode,
        "boost_pct": use_boost, "rake_pct": use_rake, "cashtag": cashtag,
        "house_rules": house_rules, "channel_id": ch.id, "payout": payout,
        "phase": "open", "created_at": datetime.now(ET).isoformat(),
        "teams": {}, "standby": [], "rounds": [], "matches": {}, "current_round_index": 0, "event_log": [],
    }
    STATE["tournaments"][tourney_id] = t
    log_event(t, f"Tournament created by {itx.user} — {t['team_count']} teams, ${t['buy_in']:g} entry")
    await save_state()

    fee_note = ("**NO FEES. NO BOOST.** Every dollar entered is a dollar paid out." if not admin_running
               else f"League mode: **{use_mode.upper()}**.")
    desc = (f"Started by {itx.user.mention} \u2014 `{tourney_id}`\n"
           f"**{team_count.value} squads \u2014 ${buy_in:g} entry \u2014 pot ${payout['pot']}**\n"
           f"{fee_note}\n\n{format_ladder(payout)}\n\n"
           f"Cash App entries to **{cashtag}**.\n"
           f"Registered captains: run `/fed tourney enter` right here in this channel.")
    if house_rules:
        desc += f"\n\n**HOUSE RULES:** {house_rules}"
    await ch.send(embed=discord.Embed(title=f"\U0001F3C6 {tourney_id} \u2014 LIVE NOW", description=desc, color=GOLD))
    await itx.followup.send(f"\u2705 Tournament **{tourney_id}** is live in {ch.mention}.", ephemeral=True)

@tourney.command(name="enter", description="Enter the tournament running in THIS channel")
@app_commands.describe(proof="Optional elimination screenshot for priority seeding")
async def tourney_enter_cmd(itx: discord.Interaction, proof: discord.Attachment = None):
    t = find_tourney_by_channel(itx.channel_id)
    if not t:
        return await itx.response.send_message("No tournament is running in this channel.", ephemeral=True)
    if t["phase"] != "open":
        return await itx.response.send_message(
            f"This tournament isn't taking entries right now (phase: `{t['phase']}`).", ephemeral=True)
    reg = STATE["registry"]
    my_team = next((name for name, tm in reg["teams"].items() if tm["captain_id"] == itx.user.id), None)
    if not my_team:
        return await itx.response.send_message("Register your team first with `/fed team register`.",
                                                ephemeral=True)
    if str(itx.user.id) in t["teams"]:
        return await itx.response.send_message("You already have a slot pending in this tournament.",
                                                ephemeral=True)
    locked = sum(1 for tm in t["teams"].values() if tm["deposit"])
    if locked >= t["team_count"]:
        return await itx.response.send_message("This tournament is already full.", ephemeral=True)
    team_data = reg["teams"][my_team]

    async def _lock_tourney_entry(mitx: discord.Interaction):
        # Runs only AFTER the coach's own PIN clears.
        t2 = STATE["tournaments"].get(t["id"])
        if not t2:
            return await mitx.response.send_message("This tournament no longer exists.", ephemeral=True)
        if str(mitx.user.id) in t2["teams"]:
            return await mitx.response.send_message("You already have a slot pending in this tournament.",
                                                     ephemeral=True)
        t2["teams"][str(mitx.user.id)] = {"name": my_team, "members": team_data["players"], "deposit": False,
                                          "proof": bool(proof), "clocked": True, "declined": False}
        await save_state()
        emb = discord.Embed(
            title="\U0001F3E7 SLOT PENDING \u2014 MAKE YOUR DEPOSIT",
            description=(f"**{my_team}** (capt. {mitx.user.mention})\n"
                         f"Cash App **${t2['buy_in']:g}** to **{t2['cashtag']}**.\n"
                         f"Slot locks when the tournament host confirms the deposit."),
            color=GOLD)
        emb.set_footer(text=f"tourney:{t2['id']}:captain:{mitx.user.id}")
        if team_data.get("logo_url"):
            emb.set_thumbnail(url=team_data["logo_url"])
        if proof:
            emb.set_image(url=proof.url)
        await mitx.response.send_message(embed=emb, view=TourneyDepositView())

    await gate_coach_pin(itx, itx.user.id, _lock_tourney_entry)

@tourney.command(name="board", description="Status of the tournament running in this channel")
async def tourney_board_cmd(itx: discord.Interaction):
    t = find_tourney_by_channel(itx.channel_id)
    if not t:
        return await itx.response.send_message("No tournament is running in this channel.", ephemeral=True)
    lines = [f"**{t['id']}** \u2014 phase: `{t['phase'].upper()}` \u2014 host: <@{t['creator_id']}> \u2014 "
             f"{t['team_count']} squads \u2014 ${t['buy_in']:g} entry", format_ladder(t["payout"])]
    if t["house_rules"]:
        lines.append(f"**House rules:** {t['house_rules']}")
    if t["teams"]:
        lines.append("\n**SQUADS:**")
        for cid, tm in t["teams"].items():
            flag = "\U0001F512" if tm["deposit"] else "\u23F3"
            lines.append(f"- {tm['name']} <@{cid}> {flag}")
    if t["matches"]:
        lines.append("")
        current_key = t["rounds"][t["current_round_index"]] if t["rounds"] else None
        for rk in t["rounds"]:
            rd = next((r for r in t["payout"]["rounds"] if r["key"] == rk), None)
            marker = " \u25C0 CURRENT" if rk == current_key else ""
            lines.append(f"**{rd['label'] if rd else rk}**{marker}")
            for mk, m in sorted(t["matches"].items()):
                if m["round"] != rk:
                    continue
                if m["winner"]:
                    lines.append(f"  {mk}: {team_name_t(t, m['a'])} vs {team_name_t(t, m['b'])} \u2192 "
                                f"**{team_name_t(t, m['winner'])}**")
                elif m["format"] == "BO3" and (m["score_a"] or m["score_b"]):
                    lines.append(f"  {mk}: {team_name_t(t, m['a'])} {m['score_a']}\u2013{m['score_b']} "
                                f"{team_name_t(t, m['b'])}")
                elif m["a"]:
                    lines.append(f"  {mk}: {team_name_t(t, m['a'])} vs {team_name_t(t, m['b'])}")
    await itx.response.send_message("\n".join(lines))

@tourney.command(name="report", description="Report your GAME W in this tournament's channel")
@app_commands.describe(screenshot="End-game screenshot showing the final score")
async def tourney_report_cmd(itx: discord.Interaction, screenshot: discord.Attachment):
    t = find_tourney_by_channel(itx.channel_id)
    if not t:
        return await itx.response.send_message("No tournament is running in this channel.", ephemeral=True)
    if t["phase"] != "live":
        return await itx.response.send_message("This tournament isn't live yet.", ephemeral=True)
    mk, side, m = None, None, None
    for k, mm in t["matches"].items():
        if not mm["winner"] and itx.user.id in (mm["a"], mm["b"]):
            mk, m = k, mm
            side = "a" if mm["a"] == itx.user.id else "b"
            break
    if not mk:
        return await itx.response.send_message("No open match found for you.", ephemeral=True)
    opp_id = m["b"] if side == "a" else m["a"]
    series = f" (Series: {m['score_a']}\u2013{m['score_b']})" if m["format"] == "BO3" else ""
    emb = discord.Embed(
        title=f"\U0001F4E5 GAME RESULT \u2014 {mk}",
        description=f"**{team_name_t(t, itx.user.id)}** claims this game over **{team_name_t(t, opp_id)}**.{series}\n"
                    f"Host/admin: verify to log the game (or trigger the withdrawal if this clinches it).",
        color=NAVY)
    emb.set_image(url=screenshot.url)
    emb.set_footer(text=f"tourney:{t['id']}:match:{mk}:side:{side}")
    ch = itx.client.get_channel(t["channel_id"]) or itx.channel
    await ch.send(embed=emb, view=TourneyVerifyView())
    await itx.response.send_message("\u2705 Reported. Verification logs the game / fires the withdrawal.",
                                    ephemeral=True)

@tourney.command(name="forfeit", description="(Host/admin) Force a forfeit \u2014 use for no-shows")
@app_commands.describe(loser="The captain who's no-showing")
async def tourney_forfeit_cmd(itx: discord.Interaction, loser: discord.Member):
    t = find_tourney_by_channel(itx.channel_id)
    if not t:
        return await itx.response.send_message("No tournament is running in this channel.", ephemeral=True)
    if not await ensure_boss_pin(itx, t):
        return
    mk = None
    for k, m in t["matches"].items():
        if not m["winner"] and loser.id in (m["a"], m["b"]):
            mk = k
            break
    if not mk:
        return await itx.response.send_message("No open match found for that player.", ephemeral=True)
    m = t["matches"][mk]
    opp = m["b"] if m["a"] == loser.id else m["a"]
    m["winner"] = opp
    m["paid"] = True
    await save_state()
    ch = itx.client.get_channel(t["channel_id"])
    if ch:
        await ch.send(f"\U0001F6AB **FORFEIT** \u2014 {team_name_t(t, loser.id)} is out of {mk}. "
                      f"**{team_name_t(t, opp)}** advances.")
    await post_tourney_receipt(itx.client, t, team_name_t(t, opp), m["round"], mk, opp)
    await advance_tourney_bracket(itx.client, t)
    await post_tourney_checkpoint(itx.client, t, f"FORFEIT \u2014 {team_name_t(t, loser.id)} out of {mk}")
    await itx.response.send_message("\u2705 Forfeit processed.", ephemeral=True)

@tourney.command(name="close", description="Close the tournament in this channel \u2014 archives a receipt, deletes the channel")
async def tourney_close_cmd(itx: discord.Interaction):
    t = find_tourney_by_channel(itx.channel_id)
    if not t:
        return await itx.response.send_message("No tournament is running in this channel.", ephemeral=True)
    if itx.user.id == t["creator_id"] or (is_admin(itx.user) and admin_unlocked(itx.user.id)):
        await itx.response.send_message("\u2705 Closing event\u2026", ephemeral=True)
        return await close_tournament(itx.client, t, itx.user)
    if is_admin(itx.user):
        return await itx.response.send_modal(AdminPinUnlockModal())
    await itx.response.send_modal(ClosePinModal(t["id"]))

@tourney.command(name="cancel", description="Cancel the tournament running in this channel (host or admin only)")
async def tourney_cancel_cmd(itx: discord.Interaction):
    t = find_tourney_by_channel(itx.channel_id)
    if not t:
        return await itx.response.send_message("No tournament is running in this channel.", ephemeral=True)
    if not await ensure_boss_pin(itx, t):
        return
    deposited = [tm["name"] for tm in t["teams"].values() if tm["deposit"]]
    del STATE["tournaments"][t["id"]]
    await save_state()
    note = ""
    if deposited:
        note = f"\n\u26A0\uFE0F These squads already paid in and need a MANUAL refund from the host: {', '.join(deposited)}"
    await itx.response.send_message(f"\U0001F6D1 **{t['id']} CANCELLED.**{note}")

@tourney.command(name="list", description="Show every currently-active instant tournament")
async def tourney_list_cmd(itx: discord.Interaction):
    active = STATE["tournaments"]
    if not active:
        return await itx.response.send_message("No instant tournaments running right now.", ephemeral=True)
    lines = ["**ACTIVE INSTANT TOURNAMENTS:**"]
    for tid, t in active.items():
        locked = sum(1 for tm in t["teams"].values() if tm["deposit"])
        lines.append(f"- **{tid}** <#{t['channel_id']}> \u2014 host <@{t['creator_id']}> \u2014 "
                    f"{locked}/{t['team_count']} squads \u2014 phase `{t['phase']}`")
    await itx.response.send_message("\n".join(lines))

# =============================================================================
#  FAST 5's — daily 5v5 DRAFT-STYLE tournament, three tip windows a day
#  (5pm / 7pm / 9pm ET by default). Players queue by position, captains queue
#  separately, the bot runs a 7-round snake draft, then the resulting rosters
#  play out a bracket exactly like the other event types — same payout
#  engine, same BO1/BO3 verify flow, same instant-withdrawal receipts, plus
#  a FULL start-to-finish event log printed out as a text file at close.
#
#  Only one Fast 5's session is active at a time; it cycles through the
#  three daily windows automatically once .fast5 setup has run.
# =============================================================================
def cfg5(key):
    return STATE["fast5"]["config"].get(key, 0)

def fast5_channel(client, key):
    ch_id = cfg5(key)
    return client.get_channel(ch_id) if ch_id else None

def fast5_live_channel(client):
    """The per-session 'live' channel — created at lock, deleted at close.
    Draft picks and match results happen here; the position/captain queue
    channels and paper-trail/archive stay permanent across every session."""
    session = STATE["fast5"]["session"]
    cid = session.get("live_channel_id") if session else 0
    ch = client.get_channel(cid) if cid else None
    return ch or fast5_channel(client, "draft_ch")

def log_event(session: dict, msg: str):
    session.setdefault("event_log", []).append(
        {"ts": datetime.now(ET).strftime("%I:%M:%S %p ET"), "msg": msg})

def fast5_team_name(session: dict, captain_id: int) -> str:
    c = session["captains"].get(str(captain_id))
    return c["team_name"] if c else "???"

def render_fast5_printout(session: dict) -> str:
    """The full start-to-finish receipt: registration, every draft pick in
    order, every match, every withdrawal. This is the literal 'printout'."""
    lines = [
        "=" * 60,
        f"  FAST 5's \u2014 {session['session_id']}",
        f"  APA ATM PRO AM BASKETBALL LEAGUE",
        "=" * 60,
        f"Tip time: {session['tip_hour']:02d}:{session['tip_minute']:02d} ET   Date: {session['date']}",
        f"Captains: {len(session['captains'])}   Buy-in: ${session.get('buy_in', 0):g}",
        "",
        "--- EVENT LOG ---",
    ]
    for e in session.get("event_log", []):
        lines.append(f"[{e['ts']}] {e['msg']}")
    lines.append("")
    lines.append("--- FINAL ROSTERS ---")
    for cid, c in session["captains"].items():
        roster_names = ", ".join(str(pid) for pid in c["roster"]) or "(none drafted)"
        lines.append(f"{c['team_name']} (capt {cid}): {roster_names}")
    lines.append("")
    lines.append("--- LEDGER ---")
    entries = [e for e in STATE["ledger"] if e["run"] == f"fast5-{session['session_id']}"]
    total = 0
    for e in entries:
        lines.append(f"{e['team']}: ${e['amount']} ({e['round']}) \u2014 net ${e.get('net_paid', e['amount'])}")
        total += e["amount"]
    lines.append(f"TOTAL PAID OUT: ${total}")
    lines.append("=" * 60)
    return "\n".join(lines)

def render_fast5_checkpoint(session: dict) -> str:
    """Copy/paste-ready snapshot of the ENTIRE session \u2014 posted after every
    completed step so a crash mid-draft never loses the picks."""
    reg = STATE["registry"]

    def gt(pid):
        return reg["players"].get(str(pid), {}).get("gamertag", str(pid))

    ts = datetime.now(ET).strftime("%m/%d %I:%M:%S %p ET")
    lines = [f"FAST 5's {session['session_id']} \u2014 CHECKPOINT {ts}",
             f"PHASE: {session['phase'].upper()}   TIP: {session['tip_hour']:02d}:{session['tip_minute']:02d} ET   "
             f"CAPTAINS: {len(session['captains'])}"]
    if session["phase"] == "drafting" and session["draft_order"]:
        done = session["draft_pick_num"]
        total = len(session["draft_order"])
        lines.append(f"DRAFT: pick {min(done + 1, total)}/{total}")
        if done < total:
            rnd, cap = session["draft_order"][done]
            lines.append(f"NEXT ON CLOCK: {fast5_team_name(session, cap)} <@{cap}> (round {rnd})")
    lines.append("")
    lines.append("CAPTAINS / ROSTERS (copy/paste for manual takeover):")
    for cid, c in session["captains"].items():
        roster = ", ".join(f"{gt(pid)} (<@{pid}>)" for pid in c["roster"]) or "(none drafted yet)"
        lines.append(f"  {c['team_name']} | capt <@{cid}> | id {cid}")
        lines.append(f"    roster: {roster}")
    pools_left = {p: session["pools"][p] for p in FAST5_POSITIONS if session["pools"][p]}
    if pools_left:
        lines.append("")
        lines.append("REMAINING PLAYER POOLS:")
        for pos, pool in pools_left.items():
            lines.append(f"  {pos}: " + ", ".join(f"{gt(pid)} (<@{pid}>)" for pid in pool))
    if session.get("matches"):
        lines.append("")
        lines.append("BRACKET:")
        for mk, m in sorted(session["matches"].items()):
            res = f" -> WINNER {fast5_team_name(session, m['winner'])}" if m["winner"] else ""
            lines.append(f"  {mk} [{m['round']}]: {fast5_team_name(session, m['a'])} {m['score_a']}-{m['score_b']} "
                         f"{fast5_team_name(session, m['b'])} ({m['format']}){res}")
        if session.get("byes"):
            lines.append("  BYES: " + ", ".join(fast5_team_name(session, b) for b in session["byes"]))
    return "\n".join(lines)

async def post_fast5_checkpoint(client, step: str):
    session = STATE["fast5"]["session"]
    if not session:
        return
    block = render_fast5_checkpoint(session)
    print(f"[CHECKPOINT][FAST5 {session['session_id']}] STEP COMPLETE: {step}\n{block}")
    ch = fast5_channel(client, "papertrail_ch") or fast5_live_channel(client)
    await send_copy_block(ch, f"\u2705 **STEP COMPLETE \u2014 {step}**", block,
                          f"{session['session_id']}_checkpoint.txt")

# ----------------------------- INFRASTRUCTURE -------------------------------
async def build_fast5_infrastructure(guild: discord.Guild) -> str:
    c = STATE["fast5"]["config"]
    log = []

    def find_role(name):
        return discord.utils.get(guild.roles, name=name)

    def find_channel(cat, name):
        return discord.utils.get(cat.text_channels, name=name) if cat else None

    admin_role = find_role("APA Admin") or find_role("QCL Admin") or find_role("Admin")
    category = discord.utils.get(guild.categories, name=FAST5_CATEGORY)
    if not category:
        overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=True)}
        if admin_role:
            overwrites[admin_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        category = await guild.create_category(FAST5_CATEGORY, overwrites=overwrites,
                                                reason="Federal Reserve \u2014 Fast 5's setup")
        log.append(f"created category {FAST5_CATEGORY}")
    c["category_id"] = category.id

    locked_overwrites = {
        guild.default_role: discord.PermissionOverwrite(send_messages=False, view_channel=True),
    }
    if admin_role:
        locked_overwrites[admin_role] = discord.PermissionOverwrite(send_messages=True, view_channel=True)
    open_overwrites = {guild.default_role: discord.PermissionOverwrite(send_messages=True, view_channel=True)}
    if admin_role:
        open_overwrites[admin_role] = discord.PermissionOverwrite(send_messages=True, view_channel=True)

    topics = {
        "announce_ch": ("Nightly Fast 5's session announcements.", locked_overwrites),
        "captains_ch": ("Tap the button to register as captain for the next session.", open_overwrites),
        "pg_ch": ("Point Guards \u2014 tap to queue for the next session.", open_overwrites),
        "sg_ch": ("Shooting Guards \u2014 tap to queue for the next session.", open_overwrites),
        "sf_ch": ("Small Forwards \u2014 tap to queue for the next session.", open_overwrites),
        "pf_ch": ("Power Forwards \u2014 tap to queue for the next session.", open_overwrites),
        "c_ch": ("Centers \u2014 tap to queue for the next session.", open_overwrites),
        "draft_ch": ("The live snake draft happens here.", open_overwrites),
        "papertrail_ch": ("Every Fast 5's withdrawal, posted the second it's paid.", locked_overwrites),
        "results_ch": ("Report game results here.", open_overwrites),
        "archive_ch": ("Full start-to-finish receipts for every closed session.", locked_overwrites),
    }
    for i, (key, name) in enumerate(FAST5_CHANNELS.items()):
        topic, ow = topics[key]
        existing = find_channel(category, name)
        if not existing:
            existing = await guild.create_text_channel(name, category=category, overwrites=ow, topic=topic,
                                                        reason="Federal Reserve \u2014 Fast 5's setup")
            log.append(f"created #{name}")
        else:
            await existing.edit(overwrites=ow, topic=topic)
        try:
            await existing.edit(position=i)
        except discord.HTTPException:
            pass
        c[key] = existing.id

    # Persistent queue buttons — one message per position channel + captains channel
    for pos in FAST5_POSITIONS:
        ch = guild.get_channel(c[f"{pos.lower()}_ch"])
        if ch:
            await ch.send(f"\U0001F3C0 **QUEUE UP FOR THE NEXT FAST 5's SESSION \u2014 {pos}**\n"
                         f"Must be PLAYER-registered first (`/fed player register`).",
                         view=PositionQueueView(pos))
    cap_ch = guild.get_channel(c["captains_ch"])
    if cap_ch:
        await cap_ch.send("\U0001F451 **REGISTER AS CAPTAIN FOR THE NEXT FAST 5's SESSION**\n"
                          "You'll pick a team name, then draft your roster live when the window opens.",
                          view=CaptainQueueView())

    c["setup_complete"] = True
    await save_state()
    return "\n".join(log) if log else "Everything already existed \u2014 nothing new to create."

# ----------------------------- QUEUEING VIEWS -------------------------------
class PositionQueueView(discord.ui.View):
    """One instance per position; custom_id encodes which position so a
    single generic callback handles all five, persistent across restarts."""
    def __init__(self, position: str):
        super().__init__(timeout=None)
        self.position = position
        btn = discord.ui.Button(label=f"Queue for {position}", style=discord.ButtonStyle.primary,
                                emoji="\U0001F3C0", custom_id=f"fast5:queue:{position}")
        btn.callback = self._callback
        self.add_item(btn)

    async def _callback(self, itx: discord.Interaction):
        session = STATE["fast5"]["session"]
        if not session or session["phase"] != "queueing":
            return await itx.response.send_message(
                "Queueing isn't open right now \u2014 check #\U0001F3C0-fast5-announce for the next window.",
                ephemeral=True)
        reg = STATE["registry"]
        if str(itx.user.id) not in reg["players"]:
            return await itx.response.send_message(
                "Register your gamertag first: `/fed player register`.", ephemeral=True)
        already = any(itx.user.id in pool for pool in session["pools"].values())
        if already:
            return await itx.response.send_message("You're already queued for this session.", ephemeral=True)
        session["pools"][self.position].append(itx.user.id)
        log_event(session, f"{reg['players'][str(itx.user.id)]['gamertag']} queued as {self.position}")
        await save_state()
        await itx.response.send_message(f"\u2705 Queued as **{self.position}** for {session['session_id']}.",
                                        ephemeral=True)

class CaptainTeamNameModal(discord.ui.Modal, title="Register as Fast 5's Captain"):
    team_name = discord.ui.TextInput(label="Your team name", max_length=32)
    pin = discord.ui.TextInput(label="Coach PIN (4-8 digits)", max_length=8,
                               placeholder="First registration SETS your PIN \u2014 after that it must match")

    async def on_submit(self, itx: discord.Interaction):
        session = STATE["fast5"]["session"]
        if not session or session["phase"] != "queueing":
            return await itx.response.send_message(
                "Queueing isn't open right now \u2014 check the announce channel for the next window.",
                ephemeral=True)
        if str(itx.user.id) in session["captains"]:
            return await itx.response.send_message("You're already registered as a captain this session.",
                                                    ephemeral=True)
        attempt = self.pin.value.strip()
        if coach_pin_set(itx.user.id):
            if not coach_pin_matches(itx.user.id, attempt):
                print(f"[SECURITY] FAILED coach PIN attempt (fast5 register) by {itx.user} ({itx.user.id})")
                return await itx.response.send_message(
                    "\u274C Wrong Coach PIN \u2014 locking in as captain requires the PIN you set at "
                    "onboarding. Forgot it? An admin can wipe it with `/fed coachpin`.", ephemeral=True)
        else:
            if not (attempt.isdigit() and 4 <= len(attempt) <= 8):
                return await itx.response.send_message("Coach PIN must be 4-8 digits.", ephemeral=True)
            set_coach_pin(itx.user.id, attempt)
            print(f"[SECURITY] coach PIN SET (fast5 onboarding) for {itx.user} ({itx.user.id})")
        session["captains"][str(itx.user.id)] = {"team_name": self.team_name.value.strip()[:32], "roster": []}
        log_event(session, f"{self.team_name.value.strip()} (capt <@{itx.user.id}>) registered")
        await save_state()
        await itx.response.send_message(
            f"\u2705 **{self.team_name.value.strip()}** registered for {session['session_id']}. "
            f"Draft starts when the window locks.", ephemeral=True)
        await post_fast5_checkpoint(itx.client,
                                    f"CAPTAIN REGISTERED \u2014 {self.team_name.value.strip()} (<@{itx.user.id}>)")

class CaptainQueueView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Register as Captain", style=discord.ButtonStyle.success, emoji="\U0001F451",
                      custom_id="fast5:captain_register")
    async def register(self, itx: discord.Interaction, _btn: discord.ui.Button):
        await itx.response.send_modal(CaptainTeamNameModal())

# ----------------------------- MOCK DRAFT -----------------------------------
# Captains can pre-rank preferred players before the live draft even starts.
# Two purposes: (1) a viewable "draft board" of everyone's plan, and (2) the
# AFK safety net — auto_skip_pick() checks this list FIRST before falling
# back to a random pick, so an absent captain still gets players they
# actually wanted instead of whatever the bot rolls.
class MockDraftPositionSelectView(discord.ui.View):
    def __init__(self, captain_id: int):
        super().__init__(timeout=300)
        self.captain_id = captain_id
        session = STATE["fast5"]["session"]
        options = [discord.SelectOption(label=f"{pos} ({len(session['pools'][pos])} queued)", value=pos)
                  for pos in FAST5_POSITIONS if session["pools"][pos]]
        if not options:
            options = [discord.SelectOption(label="No players queued yet", value="none")]
        select = discord.ui.Select(placeholder="Pick a position for your next mock preference\u2026", options=options)
        select.callback = self._callback
        self.add_item(select)

    async def _callback(self, itx: discord.Interaction):
        if itx.user.id != self.captain_id:
            return await itx.response.send_message("This isn't your mock draft.", ephemeral=True)
        position = itx.data["values"][0]
        if position == "none":
            return await itx.response.send_message("No players queued yet.", ephemeral=True)
        await itx.response.edit_message(content=f"Mock-drafting from **{position}**\u2026",
                                        view=MockDraftPlayerSelectView(self.captain_id, position))

class MockDraftPlayerSelectView(discord.ui.View):
    def __init__(self, captain_id: int, position: str):
        super().__init__(timeout=300)
        self.captain_id, self.position = captain_id, position
        session = STATE["fast5"]["session"]
        reg = STATE["registry"]
        mock_list = session.setdefault("mock_draft", {}).get(str(captain_id), [])
        already = {pid for pid, _pos in mock_list}
        pool = [pid for pid in session["pools"][position] if pid not in already][:25]
        options = [discord.SelectOption(label=reg["players"].get(str(pid), {}).get("gamertag", str(pid))[:100],
                                        value=str(pid)) for pid in pool]
        if not options:
            options = [discord.SelectOption(label="None available", value="none")]
        select = discord.ui.Select(placeholder=f"Pick your {position}\u2026", options=options)
        select.callback = self._callback
        self.add_item(select)

    async def _callback(self, itx: discord.Interaction):
        if itx.user.id != self.captain_id:
            return await itx.response.send_message("This isn't your mock draft.", ephemeral=True)
        if itx.data["values"][0] == "none":
            return await itx.response.send_message("No one available there.", ephemeral=True)
        picked_id = int(itx.data["values"][0])
        session = STATE["fast5"]["session"]
        mock = session.setdefault("mock_draft", {}).setdefault(str(self.captain_id), [])
        mock.append([picked_id, self.position])
        reg = STATE["registry"]
        gt = reg["players"].get(str(picked_id), {}).get("gamertag", str(picked_id))
        await save_state()
        rank = len(mock)
        if rank >= FAST5_DRAFT_ROUNDS:
            return await itx.response.edit_message(
                content=f"\u2705 Mock pick #{rank}: **{gt}** ({self.position}). List is full "
                       f"({FAST5_DRAFT_ROUNDS}/{FAST5_DRAFT_ROUNDS}).", view=None)
        await itx.response.edit_message(content=f"\u2705 Mock pick #{rank}: **{gt}** ({self.position}).",
                                        view=MockDraftContinueView(self.captain_id))

class MockDraftContinueView(discord.ui.View):
    def __init__(self, captain_id: int):
        super().__init__(timeout=300)
        self.captain_id = captain_id

    @discord.ui.button(label="Add Another Pick", style=discord.ButtonStyle.primary)
    async def add_more(self, itx: discord.Interaction, _btn: discord.ui.Button):
        if itx.user.id != self.captain_id:
            return await itx.response.send_message("This isn't your mock draft.", ephemeral=True)
        await itx.response.edit_message(content="Pick your next mock preference:",
                                        view=MockDraftPositionSelectView(self.captain_id))

    @discord.ui.button(label="Done", style=discord.ButtonStyle.secondary)
    async def done(self, itx: discord.Interaction, _btn: discord.ui.Button):
        if itx.user.id != self.captain_id:
            return await itx.response.send_message("This isn't your mock draft.", ephemeral=True)
        await itx.response.edit_message(content="\u2705 Mock draft saved \u2014 that's your AFK safety net set.",
                                        view=None)

# ----------------------------- DRAFT ENGINE ---------------------------------
def build_snake_order(captain_ids: list, rounds: int) -> list:
    order = []
    for r in range(rounds):
        seq = captain_ids if r % 2 == 0 else list(reversed(captain_ids))
        order.extend((r + 1, cap) for cap in seq)
    return order

async def start_fast5_draft(client):
    session = STATE["fast5"]["session"]
    session["phase"] = "drafting"
    captain_ids = [int(cid) for cid in session["captains"].keys()]
    session["draft_order"] = build_snake_order(captain_ids, FAST5_DRAFT_ROUNDS)
    session["draft_pick_num"] = 0
    log_event(session, f"Draft started \u2014 {len(captain_ids)} captains, {FAST5_DRAFT_ROUNDS} rounds, snake order")
    await save_state()
    await post_draft_prompt(client)

async def post_draft_prompt(client):
    session = STATE["fast5"]["session"]
    ch = fast5_live_channel(client)
    total_players = sum(len(p) for p in session["pools"].values())
    if session["draft_pick_num"] >= len(session["draft_order"]) or total_players == 0:
        return await finish_fast5_draft(client)
    rnd, captain_id = session["draft_order"][session["draft_pick_num"]]
    deadline_dt = datetime.now(ET) + timedelta(seconds=fast5_pick_clock())
    session["pick_deadline"] = deadline_dt.isoformat()
    session["pick_deadline_pick_num"] = session["draft_pick_num"]
    await save_state()
    if ch:
        unix_ts = int(deadline_dt.timestamp())
        counts = ", ".join(f"{pos}:{len(session['pools'][pos])}" for pos in FAST5_POSITIONS)
        await ch.send(f"\U0001F3AF **ROUND {rnd}/{FAST5_DRAFT_ROUNDS} \u2014 ON THE CLOCK: "
                      f"<@{captain_id}> ({fast5_team_name(session, captain_id)})**\nAvailable: {counts}\n"
                      f"\u23F1\uFE0F Auto-skip <t:{unix_ts}:R> if no pick is made.",
                      view=DraftPositionSelectView(captain_id))

async def auto_skip_pick(client):
    """Fires when a captain lets the 90-second clock expire. Checks their
    pre-set mock draft FIRST (highest-ranked entry still available), only
    falling back to a random pick if they never set one or it's exhausted.
    Guarded against racing a real, in-flight manual pick via
    pick_deadline_pick_num."""
    session = STATE["fast5"]["session"]
    if not session or session["phase"] != "drafting":
        return
    idx = session["draft_pick_num"]
    if idx >= len(session["draft_order"]):
        return
    rnd, captain_id = session["draft_order"][idx]

    picked_id, position, used_mock = None, None, False
    for pid, pos in session.get("mock_draft", {}).get(str(captain_id), []):
        if pid in session["pools"].get(pos, []):
            picked_id, position, used_mock = pid, pos, True
            break

    if picked_id is None:
        available_positions = [p for p in FAST5_POSITIONS if session["pools"][p]]
        if not available_positions:
            return await finish_fast5_draft(client)
        position = random.choice(available_positions)
        picked_id = random.choice(session["pools"][position])

    session["pools"][position].remove(picked_id)
    session["captains"][str(captain_id)]["roster"].append(picked_id)
    reg = STATE["registry"]
    gt = reg["players"].get(str(picked_id), {}).get("gamertag", str(picked_id))
    source = "used their pre-set mock pick" if used_mock else "bot randomly drafted"
    log_event(session, f"Round {rnd}: {fast5_team_name(session, captain_id)} AUTO-SKIPPED \u2014 "
                       f"{source} {gt} ({position}) after {fast5_pick_clock()}s")
    session["draft_pick_num"] += 1
    await save_state()
    ch = fast5_live_channel(client)
    if ch:
        verb = "drafted their planned pick" if used_mock else "randomly drafted"
        await ch.send(f"\u23F1\uFE0F **TIME'S UP** \u2014 {fast5_team_name(session, captain_id)} didn't pick in "
                      f"{fast5_pick_clock()} seconds. Bot {verb}: **{gt}** ({position}).")
    await post_fast5_checkpoint(client,
                                f"AUTO-PICK \u2014 {fast5_team_name(session, captain_id)} gets {gt} ({position})")
    await post_draft_prompt(client)

@tasks.loop(seconds=5)
async def draft_clock_loop():
    session = STATE["fast5"]["session"]
    if not session or session["phase"] != "drafting":
        return
    deadline_str = session.get("pick_deadline")
    deadline_pick = session.get("pick_deadline_pick_num")
    if deadline_str is None or deadline_pick != session["draft_pick_num"]:
        return   # deadline is stale (a real pick already advanced the draft) — nothing to do
    try:
        deadline = datetime.fromisoformat(deadline_str)
        if datetime.now(ET) >= deadline:
            await auto_skip_pick(bot)
    except Exception as e:
        print(f"[draft_clock_loop] {type(e).__name__}: {e}")

@draft_clock_loop.before_loop
async def _draft_clock_wait_ready():
    await bot.wait_until_ready()

class DraftPositionSelectView(discord.ui.View):
    def __init__(self, captain_id: int):
        super().__init__(timeout=fast5_pick_clock() + 15)
        self.captain_id = captain_id
        session = STATE["fast5"]["session"]
        options = [discord.SelectOption(label=f"{pos} ({len(session['pools'][pos])} available)", value=pos)
                  for pos in FAST5_POSITIONS if session["pools"][pos]]
        if not options:
            options = [discord.SelectOption(label="No players left", value="none")]
        select = discord.ui.Select(placeholder="Pick a position to draft from\u2026", options=options)
        select.callback = self._callback
        self.add_item(select)

    async def _callback(self, itx: discord.Interaction):
        if itx.user.id != self.captain_id:
            return await itx.response.send_message("It's not your pick.", ephemeral=True)
        position = itx.data["values"][0]
        if position == "none":
            return await itx.response.send_message("No players left to draft.", ephemeral=True)
        await itx.response.edit_message(content=f"Drafting from **{position}**\u2026",
                                        view=DraftPlayerSelectView(self.captain_id, position))

class DraftPlayerSelectView(discord.ui.View):
    def __init__(self, captain_id: int, position: str):
        super().__init__(timeout=fast5_pick_clock() + 15)
        self.captain_id = captain_id
        session = STATE["fast5"]["session"]
        reg = STATE["registry"]
        pool = session["pools"][position][:25]
        options = [discord.SelectOption(
            label=reg["players"].get(str(pid), {}).get("gamertag", str(pid))[:100], value=str(pid))
            for pid in pool]
        select = discord.ui.Select(placeholder=f"Pick your {position}\u2026", options=options)
        select.callback = self._callback
        self.add_item(select)
        self.position = position

    async def _callback(self, itx: discord.Interaction):
        if itx.user.id != self.captain_id:
            return await itx.response.send_message("It's not your pick.", ephemeral=True)
        picked_id = int(itx.data["values"][0])
        session = STATE["fast5"]["session"]
        session["pools"][self.position].remove(picked_id)
        session["captains"][str(self.captain_id)]["roster"].append(picked_id)
        reg = STATE["registry"]
        gt = reg["players"].get(str(picked_id), {}).get("gamertag", str(picked_id))
        rnd, _ = session["draft_order"][session["draft_pick_num"]]
        log_event(session, f"Round {rnd}: {fast5_team_name(session, self.captain_id)} selects {gt} ({self.position})")
        session["draft_pick_num"] += 1
        await save_state()
        await itx.response.edit_message(
            content=f"\u2705 **{fast5_team_name(session, self.captain_id)}** drafted **{gt}** ({self.position}).",
            view=None)
        await post_fast5_checkpoint(itx.client,
                                    f"DRAFT PICK \u2014 {fast5_team_name(session, self.captain_id)} selects {gt} ({self.position})")
        await post_draft_prompt(itx.client)

async def finish_fast5_draft(client):
    session = STATE["fast5"]["session"]
    captain_ids = [int(cid) for cid in session["captains"].keys()]
    n = len(captain_ids)
    log_event(session, f"Draft complete \u2014 {n} rosters set")
    rounds_def, byes, play_in_matches = get_rounds_def(n)
    if rounds_def is None:
        ch = fast5_channel(client, "announce_ch")
        if ch:
            await ch.send(f"\u26A0\uFE0F {n} captains is outside supported range (4\u201316) \u2014 admin needs to "
                          f"resolve this session manually.")
        return
    payout = compute_payouts(n, cfg5("buy_in"), cfg5("mode"), cfg5("boost_pct"), cfg5("rake_pct"),
                             winner_takes_all=(cfg5("payout_style") == "wta"))
    session["payout"] = payout
    session["rounds"] = [r["key"] for r in rounds_def]
    session["current_round_index"] = 0
    random.shuffle(captain_ids)
    if byes:
        session["byes"] = captain_ids[:byes]
        playing = captain_ids[byes:]
        session["matches"] = seed_round(playing, "PI", "BO1")
        log_event(session, f"Play-in seeded: {play_in_matches} match(es), {byes} bye(s)")
    else:
        first = rounds_def[0]
        session["byes"] = []
        session["matches"] = seed_round(captain_ids, first["key"], first["format"])
    session["phase"] = "live"
    await save_state()
    ac = fast5_channel(client, "announce_ch")
    live = fast5_live_channel(client)
    if ac and live:
        await ac.send(f"\U0001F3C0 **DRAFT COMPLETE.** Bracket is live in {live.mention}.")
    if live:
        lines = [f"\U0001F3C0 **DRAFT COMPLETE \u2014 BRACKET IS LIVE ({n} TEAMS)**"]
        current_key = session["rounds"][session["current_round_index"]]
        for mk, m in sorted(session["matches"].items()):
            lines.append(f"{mk}: **{fast5_team_name(session, m['a'])}** vs **{fast5_team_name(session, m['b'])}**")
        if session["byes"]:
            lines.append("BYE: " + ", ".join(fast5_team_name(session, b) for b in session["byes"]))
        lines.append("Winners run `.fast5 report` with the end-game screenshot.")
        await live.send("\n".join(lines))
    await post_fast5_checkpoint(client, "DRAFT COMPLETE \u2014 final rosters locked, bracket seeded")

# ----------------------------- FAST5 BRACKET FLOW ---------------------------
async def post_fast5_receipt(client, session: dict, team: str, round_key: str, match_key: str, captain_id: int):
    payout = session["payout"]
    rounds = payout["rounds"]
    idx = next(i for i, r in enumerate(rounds) if r["key"] == round_key)
    r = rounds[idx]
    is_final = (idx == len(rounds) - 1)
    wta = payout.get("winner_takes_all", False)

    if wta and not is_final:
        ch = fast5_channel(client, "papertrail_ch")
        if ch:
            nxt_r = rounds[idx + 1]
            await ch.send(f"\u27A1\uFE0F **{team}** ADVANCES \u2014 {r['label']} ({match_key})\n"
                          f"**WINNER TAKES ALL** \u2014 pot stays locked until {nxt_r['label']}.")
        log_event(session, f"{team} advances {match_key} (no payout, WTA)")
        return

    amount = r["per_winner"]
    label = f"{session['session_id']} \u2014 CHAMPIONSHIP" if is_final else r["label"]
    nxt = "EVENT COMPLETE" if is_final else f"{rounds[idx + 1]['label']} - ${rounds[idx + 1]['per_winner']}"
    reg = STATE["registry"]
    owed = reg["fines_owed"].get(str(captain_id), 0)
    net_amount = amount
    fine_note = ""
    if owed > 0:
        deduction = min(owed, amount)
        reg["fines_owed"][str(captain_id)] = owed - deduction
        net_amount = amount - deduction
        fine_note = f"\nFINE DEDUCTED: -${deduction} \u2014 **NET SEND: ${net_amount}**"

    ch = fast5_channel(client, "papertrail_ch")
    if ch:
        now = datetime.now(ET)
        block = ("```\n        ATM PAYDAY\n     ADVANCE TO MONEY\n--------------------------\n"
                f"TERMINAL : {session['session_id']} ({match_key})\n"
                f"DATE     : {now.strftime('%m/%d/%y %I:%M %p ET')}\n"
                f"CARDHOLDER: {team.upper()[:20]}\n--------------------------\n"
                f"TRANSACTION: {label}\nSTATUS     : *** PAID ***\n\n        ${amount}.00\n\n"
                "--------------------------\n"
                f"NEXT STOP: {nxt}\n--------------------------\nEVERY ROUND IS A WITHDRAWAL.\n```")
        emb = discord.Embed(title="\U0001F4B0 WITHDRAWAL CONFIRMED \U0001F4B0", description=block + fine_note,
                            color=GOLD)
        emb.set_footer(text=f"{session['session_id']} \u2022 FAST 5's \u2022 APA ATM PRO AM")
        await ch.send(embed=emb)
    STATE["ledger"].append({"run": f"fast5-{session['session_id']}", "date": session["date"],
                            "team": team, "round": round_key, "amount": amount,
                            "net_paid": net_amount, "ts": datetime.now(ET).isoformat()})
    log_event(session, f"WITHDRAWAL: {team} paid ${amount} for {label} ({match_key})")
    await grant_role(client, captain_id, "payroll_role", "Cashed a Fast 5's withdrawal")
    await save_state()

async def advance_fast5_bracket(client):
    session = STATE["fast5"]["session"]
    round_key = session["rounds"][session["current_round_index"]]
    round_matches = {mk: m for mk, m in session["matches"].items() if m["round"] == round_key}
    if not all(m["winner"] for m in round_matches.values()):
        return
    winners = [m["winner"] for mk, m in sorted(round_matches.items())]
    ch = fast5_live_channel(client)

    if round_key == "PI":
        # play-in winners + byes combine to fill the next round's capacity
        combined = winners + session["byes"]
        random.shuffle(combined)
        next_round_key = session["rounds"][1]
        next_def = next(r for r in session["payout"]["rounds"] if r["key"] == next_round_key)
        session["matches"].update(seed_round(combined, next_round_key, next_def["format"]))
        session["current_round_index"] = 1
        await save_state()
        log_event(session, f"Play-in resolved \u2014 {next_def['label']} seeded")
        if ch:
            lines = [f"\U0001F3C0 **{next_def['label']} \u2014 {next_def['format']}**"]
            for mk, m in sorted(session["matches"].items()):
                if m["round"] == next_round_key:
                    lines.append(f"{mk}: **{fast5_team_name(session, m['a'])}** vs **{fast5_team_name(session, m['b'])}**")
            await ch.send("\n".join(lines))
        return

    if session["current_round_index"] == len(session["rounds"]) - 1:
        session["phase"] = "complete"
        champ_id = winners[0]
        total_take = sum(r["per_winner"] for r in session["payout"]["rounds"])
        log_event(session, f"CHAMPION: {fast5_team_name(session, champ_id)} \u2014 total takeaway ${total_take}")
        await save_state()
        if ch:
            await ch.send(f"\U0001F3C6 **{session['session_id']} \u2014 VAULT EMPTIED** \U0001F3C6\n"
                          f"**{fast5_team_name(session, champ_id)}** takes it all \u2014 "
                          f"**${total_take} total withdrawal.**\nTap below for the full start-to-finish receipt.",
                          view=Fast5CloseView())
        await post_fast5_checkpoint(client, f"CHAMPION \u2014 {fast5_team_name(session, champ_id)} "
                                            f"(total ${total_take})")
        return

    next_round_key = session["rounds"][session["current_round_index"] + 1]
    n = len(session["captains"])
    rounds_def, _, _ = get_rounds_def(n)
    next_def = next(r for r in rounds_def if r["key"] == next_round_key)
    session["matches"].update(seed_round(winners, next_round_key, next_def["format"]))
    session["current_round_index"] += 1
    await save_state()
    if ch:
        next_payout = next(r for r in session["payout"]["rounds"] if r["key"] == next_round_key)
        lines = [f"\U0001F3C0 **{next_def['label']} \u2014 {next_def['format']}** "
                f"(win pays **${next_payout['per_winner']}**)"]
        for mk, m in sorted(session["matches"].items()):
            if m["round"] == next_round_key:
                lines.append(f"{mk}: **{fast5_team_name(session, m['a'])}** vs **{fast5_team_name(session, m['b'])}**")
        lines.append("Winners run `.fast5 report`.")
        await ch.send("\n".join(lines))

class Fast5VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    def _resolve(self, itx):
        parts = itx.message.embeds[0].footer.text.split(":")  # "match:{mk}:side:{a|b}"
        return parts[1], parts[3]

    @discord.ui.button(label="\u2705 Verify Game", style=discord.ButtonStyle.success, custom_id="fast5:verify")
    async def verify(self, itx: discord.Interaction, _btn: discord.ui.Button):
        if not await ensure_admin_pin(itx):
            return
        session = STATE["fast5"]["session"]
        if not session:
            return await itx.response.send_message("No active Fast 5's session.", ephemeral=True)
        try:
            mk, side = self._resolve(itx)
        except (IndexError, AttributeError):
            return await itx.response.send_message("Could not resolve this report.", ephemeral=True)
        m = session["matches"].get(mk)
        if not m or m["winner"]:
            return await itx.response.send_message("Match not found or already settled.", ephemeral=True)
        m[f"score_{side}"] += 1
        target = 1 if m["format"] == "BO1" else 2
        cur = m[f"score_{side}"]
        if cur >= target:
            winner_id = m[side]
            m["winner"] = winner_id
            m["paid"] = True
            await save_state()
            await itx.response.edit_message(
                content=f"\u2705 **VERIFIED** \u2014 {fast5_team_name(session, winner_id)} takes {mk} "
                       f"({m['score_a']}-{m['score_b']}).", view=None)
            await post_fast5_receipt(itx.client, session, fast5_team_name(session, winner_id), m["round"], mk, winner_id)
            await advance_fast5_bracket(itx.client)
            await post_fast5_checkpoint(itx.client,
                                        f"MATCH VERIFIED \u2014 {fast5_team_name(session, winner_id)} takes {mk}")
        else:
            await save_state()
            await itx.response.edit_message(
                content=f"\u2705 Game recorded. Series: {fast5_team_name(session, m['a'])} {m['score_a']}\u2013"
                       f"{m['score_b']} {fast5_team_name(session, m['b'])}. First to {target} wins.", view=None)
            await post_fast5_checkpoint(itx.client,
                                        f"GAME VERIFIED \u2014 {mk} now {m['score_a']}-{m['score_b']}")

    @discord.ui.button(label="\u274C Reject", style=discord.ButtonStyle.danger, custom_id="fast5:reject")
    async def reject(self, itx: discord.Interaction, _btn: discord.ui.Button):
        if not await ensure_admin_pin(itx):
            return
        await itx.response.edit_message(content="\u274C Report rejected.", view=None)

class Fast5CloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="\U0001F4DC Get Full Receipt & Close", style=discord.ButtonStyle.danger,
                      custom_id="fast5:close")
    async def close(self, itx: discord.Interaction, _btn: discord.ui.Button):
        if not await ensure_admin_pin(itx):
            return
        await close_fast5_session(itx.client, itx.user)
        await itx.response.send_message("\u2705 Session closed, full receipt archived.", ephemeral=True)

async def close_fast5_session(client, closer):
    session = STATE["fast5"]["session"]
    if not session:
        return
    printout = render_fast5_printout(session)
    archive_ch = fast5_channel(client, "archive_ch")
    if archive_ch:
        import io
        buf = io.BytesIO(printout.encode("utf-8"))
        f = discord.File(buf, filename=f"{session['session_id']}_receipt.txt")
        closer_mention = getattr(closer, "mention", str(closer))
        await archive_ch.send(content=f"\U0001F4DC Full receipt for **{session['session_id']}**, "
                                      f"closed by {closer_mention}.", file=f)
    guild = client.get_guild(cfg5("guild_id"))
    if guild and session.get("live_channel_id"):
        live_ch = guild.get_channel(session["live_channel_id"])
        if live_ch:
            try:
                await live_ch.delete(reason=f"Federal Reserve \u2014 {session['session_id']} closed by {closer}")
            except discord.HTTPException:
                pass
    STATE["fast5"]["session"] = None
    await save_state()

# ----------------------------- FAST5 AUTO-LOOP ------------------------------
@tasks.loop(seconds=60)
async def fast5_loop():
    if TEST_MODE:
        return
    if not cfg5("auto_run") or not cfg5("guild_id"):
        return
    now_dt = datetime.now(ET)
    now = dtime(now_dt.hour, now_dt.minute)
    session = STATE["fast5"]["session"]
    try:
        if session is None:
            for hh, mm in cfg5("tip_times"):
                open_at = (hh * 60 + mm - FAST5_OPEN_BEFORE_TIP) % 1440
                open_time = dtime(open_at // 60, open_at % 60)
                if now == open_time:
                    await fast5_open_session(bot, hh, mm)
                    return
        elif session["phase"] == "queueing":
            lock_at_total = (session["tip_hour"] * 60 + session["tip_minute"] - FAST5_LOCK_BEFORE_TIP) % 1440
            lock_time = dtime(lock_at_total // 60, lock_at_total % 60)
            if now == lock_time:
                await fast5_lock_and_draft(bot)
    except Exception as e:
        print(f"[fast5_loop] {type(e).__name__}: {e}")

@fast5_loop.before_loop
async def _fast5_wait_ready():
    await bot.wait_until_ready()

async def fast5_open_session(client, hh, mm):
    c = STATE["fast5"]["config"]
    c["run_number"] += 1
    session = {
        "session_id": f"F5-{c['run_number']}", "tip_hour": hh, "tip_minute": mm,
        "date": datetime.now(ET).strftime("%Y-%m-%d"), "phase": "queueing",
        "captains": {}, "pools": {p: [] for p in FAST5_POSITIONS},
        "draft_order": [], "draft_pick_num": 0, "byes": [], "mock_draft": {},
        "buy_in": cfg5("buy_in"), "payout": None, "rounds": [], "matches": {},
        "current_round_index": 0, "event_log": [], "live_channel_id": 0,
    }
    STATE["fast5"]["session"] = session
    log_event(session, f"Session opened for {hh:02d}:{mm:02d} ET tip")
    await save_state()
    ch = fast5_channel(client, "announce_ch")
    if ch:
        test_tag = "\U0001F9EA [TEST] " if TEST_MODE else ""
        lock_note = ("No auto-lock \u2014 Override Panel \u2192 Lock Draft when ready."
                     if TEST_MODE else
                     f"Locks in {FAST5_OPEN_BEFORE_TIP - FAST5_LOCK_BEFORE_TIP} minutes.")
        await ch.send(f"{test_tag}\U0001F3C0 **FAST 5\u2019s \u2014 {hh:02d}:{mm:02d} ET TIP** \U0001F3C0\n"
                      f"Queueing is OPEN. Players: queue in your position channel. Captains: register in "
                      f"#\U0001F451-fast5-captains.\nMinimum **{cfg5('min_captains')} captains** needed. "
                      f"{lock_note}")

async def fast5_lock_and_draft(client):
    session = STATE["fast5"]["session"]
    session["phase"] = "locked"
    n = len(session["captains"])
    ch = fast5_channel(client, "announce_ch")
    if n < cfg5("min_captains"):
        log_event(session, f"Session cancelled \u2014 only {n} captain(s), needed {cfg5('min_captains')}")
        if ch:
            await ch.send(f"\u26A0\uFE0F Only {n} captain(s) registered \u2014 needed {cfg5('min_captains')}. "
                          f"Session cancelled.")
        STATE["fast5"]["session"] = None
        await save_state()
        return

    # per-session live channel — draft + matches happen here, deleted on close.
    # Position/captain queue channels and paper-trail/archive stay permanent.
    guild = client.get_guild(cfg5("guild_id"))
    live_ch = None
    if guild:
        category = guild.get_channel(cfg5("category_id"))
        admin_role = guild.get_role(cfg("admin_role"))
        overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=True)}
        if admin_role:
            overwrites[admin_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        live_ch = await guild.create_text_channel(
            f"\U0001F3AF Fast5s {cfg5('run_number'):02d}", category=category, overwrites=overwrites,
            topic=f"{session['session_id']} draft + live bracket. Closes and archives after the chip.",
            reason="Federal Reserve \u2014 Fast 5's session live channel")
        session["live_channel_id"] = live_ch.id
    log_event(session, f"Locked \u2014 {n} captains, live channel created")
    await save_state()

    if ch and live_ch:
        await ch.send(f"\U0001F512 **QUEUEING LOCKED \u2014 {n} CAPTAINS.** Draft starting now in "
                      f"{live_ch.mention}.")
    await post_fast5_checkpoint(client, f"QUEUE LOCKED \u2014 {n} captains, player pools frozen")
    await start_fast5_draft(client)

# ----------------------------- FAST5 COMMANDS (PERIOD-PREFIX, not slash) ---
# Fast 5's fires completely differently on purpose: ".fast5 ..." instead of
# ".fast5 ...". Two reasons: it removes the naming overlap with the main
# /fed commands (board/report/forfeit/close/setup existed in both), and it's
# genuinely faster to type mid-draft than opening Discord's slash picker.
@bot.group(name="fast5", invoke_without_command=True)
async def fast5_group(ctx: commands.Context):
    await ctx.send("Fast 5's commands: `.fast5 setup` `.fast5 mock` `.fast5 draftboard` "
                   "`.fast5 board` `.fast5 report` `.fast5 forfeit` `.fast5 close`")

@fast5_group.command(name="setup")
async def fast5_setup_cmd(ctx: commands.Context, buy_in: float, cashtag: str, mode: str,
                          payout_style: str, min_captains: int = 4, boost_pct: int = 25, rake_pct: int = 10):
    """.fast5 setup <buy_in> <cashtag> <launch|standard> <split|wta> [min_captains] [boost_pct] [rake_pct]"""
    if not is_admin(ctx.author):
        return await ctx.send("Admins only.")
    if not admin_unlocked(ctx.author.id):
        return await ctx.send("\U0001F510 Admin PIN required \u2014 run `/fed unlock` first "
                              "(opens a 15-minute admin session), then re-run this.")
    mode = mode.lower()
    payout_style = payout_style.lower()
    if mode not in ("launch", "standard"):
        return await ctx.send("Mode must be `launch` or `standard`.")
    if payout_style not in ("split", "wta"):
        return await ctx.send("Payout style must be `split` or `wta`.")
    await ctx.send("\U0001F3C0 Building Fast 5's \u2014 channels, queue buttons, ladder\u2026")
    c = STATE["fast5"]["config"]
    c["guild_id"] = ctx.guild.id
    c["buy_in"] = buy_in
    c["cashtag"] = cashtag
    c["mode"] = mode
    c["payout_style"] = payout_style
    c["min_captains"] = max(4, min_captains)
    c["boost_pct"] = boost_pct
    c["rake_pct"] = rake_pct
    await save_state()
    log = await build_fast5_infrastructure(ctx.guild)
    tips = ", ".join(f"{h:02d}:{m:02d} ET" for h, m in c["tip_times"])
    await ctx.send(
        f"\u2705 **FAST 5's BUILT.**\n{log}\n\n"
        f"Buy-in: ${buy_in:g}/captain \u2014 Mode: {mode.upper()} \u2014 Style: {payout_style.upper()}\n"
        f"Min captains: {c['min_captains']}\nDaily tip windows: {tips}\n"
        f"Queueing opens automatically 60 min before each tip \u2014 fully hands-off from here.")

@fast5_group.command(name="mock")
async def fast5_mock_cmd(ctx: commands.Context):
    """.fast5 mock — pre-rank your draft preferences (AFK safety net)"""
    session = STATE["fast5"]["session"]
    if not session or session["phase"] != "queueing":
        return await ctx.send("Mock drafting is only open during the queueing window.")
    if str(ctx.author.id) not in session["captains"]:
        return await ctx.send("Register as a captain for this session first.")
    await ctx.send(
        f"\U0001F4CB **Mock Draft \u2014 {fast5_team_name(session, ctx.author.id)}**\n"
        f"Rank up to {FAST5_DRAFT_ROUNDS} players. If you go AFK during the live draft, the bot picks the "
        f"highest-ranked player from this list who's still available, instead of a random pick.",
        view=MockDraftPositionSelectView(ctx.author.id))

@fast5_group.command(name="draftboard")
async def fast5_draftboard_cmd(ctx: commands.Context):
    """.fast5 draftboard — view every captain's mock draft board"""
    session = STATE["fast5"]["session"]
    if not session:
        return await ctx.send("No active Fast 5's session.")
    mock = session.get("mock_draft", {})
    if not mock:
        return await ctx.send("No mock draft picks submitted yet.")
    reg = STATE["registry"]
    lines = [f"\U0001F4CB **{session['session_id']} \u2014 DRAFT BOARD**"]
    for cid, picks in mock.items():
        lines.append(f"\n**{fast5_team_name(session, int(cid))}**")
        for i, (pid, pos) in enumerate(picks, 1):
            gt = reg["players"].get(str(pid), {}).get("gamertag", str(pid))
            lines.append(f"  {i}. {gt} ({pos})")
    await ctx.send("\n".join(lines))

@fast5_group.command(name="board")
async def fast5_board_cmd(ctx: commands.Context):
    """.fast5 board — current session status"""
    session = STATE["fast5"]["session"]
    if not session:
        return await ctx.send("No active Fast 5's session right now.")
    lines = [f"**{session['session_id']}** \u2014 phase: `{session['phase'].upper()}` \u2014 "
             f"tip: {session['tip_hour']:02d}:{session['tip_minute']:02d} ET"]
    lines.append(f"Captains: {len(session['captains'])}")
    for cid, c in session["captains"].items():
        lines.append(f"- {c['team_name']} <@{cid}> \u2014 {len(c['roster'])} drafted")
    if session["phase"] == "queueing":
        for pos in FAST5_POSITIONS:
            lines.append(f"{pos}: {len(session['pools'][pos])} queued")
    if session.get("payout"):
        lines.append("")
        lines.append(format_ladder(session["payout"]))
    if session["matches"]:
        lines.append("")
        current_key = session["rounds"][session["current_round_index"]] if session["rounds"] else None
        for rk in session["rounds"]:
            rd = next((r for r in session["payout"]["rounds"] if r["key"] == rk), None) if session.get("payout") else None
            label = rd["label"] if rd else rk
            marker = " \u25C0 CURRENT" if rk == current_key else ""
            lines.append(f"**{label}**{marker}")
            for mk, m in sorted(session["matches"].items()):
                if m["round"] != rk:
                    continue
                if m["winner"]:
                    lines.append(f"  {mk}: {fast5_team_name(session, m['a'])} vs {fast5_team_name(session, m['b'])} "
                                f"\u2192 **{fast5_team_name(session, m['winner'])}**")
                elif m["a"]:
                    lines.append(f"  {mk}: {fast5_team_name(session, m['a'])} vs {fast5_team_name(session, m['b'])}")
    await ctx.send("\n".join(lines))

@fast5_group.command(name="report")
async def fast5_report_cmd(ctx: commands.Context):
    """.fast5 report — attach your end-game screenshot to the message"""
    session = STATE["fast5"]["session"]
    if not session or session["phase"] != "live":
        return await ctx.send("No live Fast 5's bracket right now.")
    if not ctx.message.attachments:
        return await ctx.send("Attach your end-game screenshot to the message with this command.")
    screenshot = ctx.message.attachments[0]
    mk, side, m = None, None, None
    for k, mm in session["matches"].items():
        if not mm["winner"] and ctx.author.id in (mm["a"], mm["b"]):
            mk, m = k, mm
            side = "a" if mm["a"] == ctx.author.id else "b"
            break
    if not mk:
        return await ctx.send("No open match found for you.")
    opp_id = m["b"] if side == "a" else m["a"]
    ch = fast5_live_channel(ctx.bot)
    emb = discord.Embed(
        title=f"\U0001F4E5 GAME RESULT \u2014 {mk}",
        description=f"**{fast5_team_name(session, ctx.author.id)}** claims this game over "
                    f"**{fast5_team_name(session, opp_id)}**.\nAdmin: verify to log the game.",
        color=NAVY)
    emb.set_image(url=screenshot.url)
    emb.set_footer(text=f"match:{mk}:side:{side}")
    await ch.send(embed=emb, view=Fast5VerifyView())
    await ctx.send("\u2705 Reported.")

@fast5_group.command(name="forfeit")
async def fast5_forfeit_cmd(ctx: commands.Context, loser: discord.Member):
    """.fast5 forfeit @captain — (admin) force a forfeit for a no-show"""
    if not is_admin(ctx.author):
        return await ctx.send("Admins only.")
    if not admin_unlocked(ctx.author.id):
        return await ctx.send("\U0001F510 Admin PIN required \u2014 run `/fed unlock` first "
                              "(opens a 15-minute admin session), then re-run this.")
    session = STATE["fast5"]["session"]
    if not session:
        return await ctx.send("No active Fast 5's session.")
    mk = None
    for k, m in session["matches"].items():
        if not m["winner"] and loser.id in (m["a"], m["b"]):
            mk = k
            break
    if not mk:
        return await ctx.send("No open match found for that player.")
    m = session["matches"][mk]
    opp = m["b"] if m["a"] == loser.id else m["a"]
    m["winner"] = opp
    m["paid"] = True
    log_event(session, f"FORFEIT: {fast5_team_name(session, loser.id)} out of {mk}")
    await save_state()
    ch = fast5_live_channel(ctx.bot)
    if ch:
        await ch.send(f"\U0001F6AB **FORFEIT** \u2014 {fast5_team_name(session, loser.id)} is out of {mk}. "
                      f"**{fast5_team_name(session, opp)}** advances.")
    await post_fast5_receipt(ctx.bot, session, fast5_team_name(session, opp), m["round"], mk, opp)
    await advance_fast5_bracket(ctx.bot)
    await post_fast5_checkpoint(ctx.bot, f"FORFEIT \u2014 {fast5_team_name(session, loser.id)} out of {mk}")
    await ctx.send("\u2705 Forfeit processed.")

@fast5_group.command(name="close")
async def fast5_close_cmd(ctx: commands.Context):
    """.fast5 close — (admin) close the session, archives the full receipt"""
    if not is_admin(ctx.author):
        return await ctx.send("Admins only.")
    if not admin_unlocked(ctx.author.id):
        return await ctx.send("\U0001F510 Admin PIN required \u2014 run `/fed unlock` first "
                              "(opens a 15-minute admin session), then re-run this.")
    session = STATE["fast5"]["session"]
    if not session:
        return await ctx.send("No active Fast 5's session.")
    await ctx.send("\U0001F4DC Archiving full receipt\u2026")
    await close_fast5_session(ctx.bot, ctx.author)

# ----------------------------- RUN ------------------------------------------
@bot.event
async def on_ready():
    print(f"[FEDERAL RESERVE] logged in as {bot.user} \u2014 THE REFUND machine armed.")
    print(f"[FEDERAL RESERVE] security: admin PIN {'SET' if cfg('admin_pin_hash') else 'NOT SET \u2014 run /fed pin'} "
          f"| coach PINs on file: {len(STATE['registry'].get('coach_pins', {}))} "
          f"| every admin action PIN-gated, {ADMIN_PIN_SESSION_MINUTES}-min unlock sessions")
    print("[FEDERAL RESERVE] crash recovery: step-by-step checkpoints post to Discord + console after every completed step")
    print(f"[FEDERAL RESERVE] prefix commands loaded: {[c.name for c in bot.commands]}")
    fast5_cmd = bot.get_command("fast5")
    if fast5_cmd:
        print(f"[FEDERAL RESERVE] .fast5 subcommands: {[c.name for c in fast5_cmd.commands]}")
    else:
        print("[FEDERAL RESERVE] WARNING: 'fast5' prefix command not found \u2014 file may be incomplete.")

@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    """Without this, a failed .fast5 command fails SILENTLY in Discord —
    the error only ever showed up in the console, which is easy to miss.
    Now it also gets posted back where you typed the command."""
    if isinstance(error, commands.CommandNotFound):
        return   # someone typed a "." message that wasn't meant as a command
    print(f"[FEDERAL RESERVE] command error in '{ctx.command}': {type(error).__name__}: {error}")
    try:
        await ctx.send(f"\u26A0\uFE0F `{ctx.command}` failed: {error}")
    except discord.HTTPException:
        pass

_installed_target = None
_services_started = False


def install_into(target_bot: commands.Bot):
    """Mount Federal Reserve commands on an existing discord.py bot."""
    global bot, _installed_target
    if _installed_target is target_bot:
        return
    if _installed_target is not None:
        raise RuntimeError("Federal Reserve is already installed on another bot instance")

    bot = target_bot
    target_bot.tree.add_command(fed)

    fast5_command = _standalone_bot.remove_command("fast5")
    if fast5_command is not None and target_bot.get_command("fast5") is None:
        target_bot.add_command(fast5_command)

    target_bot.add_listener(on_ready, "on_ready")
    target_bot.add_listener(on_command_error, "on_command_error")
    _installed_target = target_bot


async def start_services(target_bot: commands.Bot):
    """Register persistent views and start Federal Reserve background loops."""
    global _services_started
    if _installed_target is not target_bot:
        raise RuntimeError("Call install_into(bot) before start_services(bot)")
    if _services_started:
        return

    target_bot.add_view(ClockInView())
    target_bot.add_view(DepositView())
    target_bot.add_view(VerifyView())
    target_bot.add_view(OverridePanelView())
    target_bot.add_view(GuideView())
    target_bot.add_view(MainMenuView())
    target_bot.add_view(TourneyDepositView())
    target_bot.add_view(TourneyVerifyView())
    target_bot.add_view(CloseEventView())
    target_bot.add_view(RefundCloseView())
    for pos in FAST5_POSITIONS:
        target_bot.add_view(PositionQueueView(pos))
    target_bot.add_view(CaptainQueueView())
    target_bot.add_view(Fast5VerifyView())
    target_bot.add_view(Fast5CloseView())

    for loop in (nightly_loop, fast5_loop, draft_clock_loop):
        if not loop.is_running():
            loop.start()
    _services_started = True


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Set FED_RESERVE_TOKEN in your .env first \u2014 see .env.template")
    bot.run(TOKEN)