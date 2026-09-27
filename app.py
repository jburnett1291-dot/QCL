"""
QCL Pull-Server — runs on Railway (or any cloud host), no PC needed.

Real pack pulls for the Discord Activity:
  - verifies the player via the Discord SDK code (real identity, no faking)
  - draws the pack SERVER-SIDE (anti-cheat) using the SAME odds as the bot
  - reads/writes the collection in your GitHub repo (fantasy_save.json), the
    SAME file the bot + Hub use -> everything stays in sync
  - returns the pull so the Activity animates it

ENV VARS (set these in Railway -> Variables):
  DISCORD_CLIENT_ID       = 1498101411894919331
  DISCORD_CLIENT_SECRET   = <Developer Portal -> OAuth2>
  GITHUB_TOKEN            = <same token the bot uses, repo scope>
  GITHUB_REPO            = jburnett1291-dot/SPAM_HUB
  SAVE_PATH             = fantasy_save.json      (optional, this is default)
  POOL_PATH             = fantasy_market.json    (optional; where names+rarity live)
  PORT                  = (Railway sets this automatically)
"""

import os
import json
import time
import base64
import random
import asyncio
import datetime as _datetime
import socket
from pathlib import Path
from urllib.parse import quote, urlsplit
import aiohttp
from aiohttp import web
from draft_operations import create_handlers
from activity_diagnostics import (
    activity_diagnostics,
    request_diagnostics_middleware,
    serve_diagnostics,
)
from spa_shell import SPA_HTML
from discord_sdk_bundle import serve_discord_sdk

CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "")
GH_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GH_REPO = os.environ.get("GITHUB_REPO", "jburnett1291-dot/SPAM_HUB")
BASE_DIR = Path(__file__).resolve().parent

import re as _re


_PSTATS = {"t": 0, "data": {}}


async def _load_player_stats(session):
    import time as _t
    now = _t.time()
    if _PSTATS["data"] and (now - _PSTATS["t"] < 120):
        return _PSTATS["data"]
    try:
        data, _ = await _gh_get(session, "player_stats.json")
        if isinstance(data, dict):
            _PSTATS.update({"t": now, "data": data})
    except Exception as e:
        print(f"[pstats] {e}")
    return _PSTATS["data"]


def _real_stats(name, pstats):
    """Look up a player's real stats, tolerant of l/I/1 variants."""
    if name in pstats:
        return pstats[name]
    want = _slug_loose(name)
    for k, v in pstats.items():
        if _slug_loose(k) == want:
            return v
    return {}

_CARDCAT = {"t": 0, "by_player": {}, "stems": []}


def _slug(s):
    return _re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def _slug_loose(s):
    """Like _slug but treats l/I/1 as the same char, so variant spellings
    (IIHurz vs IIIHurz vs 1lHurz) all match the same card art."""
    base = _re.sub(r"[^a-z0-9]+", "", str(s or "").lower())
    return _re.sub(r"[il1]", "i", base)


async def _card_catalog(session):
    """Mirror the bot: read cards/meta.json + list cards/, map player->raw URL."""
    import time as _t
    now = _t.time()
    if _CARDCAT["by_player"] and (now - _CARDCAT["t"] < 300):
        return _CARDCAT
    branch = os.environ.get("GITHUB_BRANCH", "main")
    hdr = {"User-Agent": "QCL-PullServer", "Accept": "application/vnd.github+json"}
    if GH_TOKEN:
        hdr["Authorization"] = f"Bearer {GH_TOKEN}"
    by_player, stems = {}, []
    try:
        meta = {}
        async with session.get(
                f"https://api.github.com/repos/{GH_REPO}/contents/cards/meta.json",
                params={"ref": branch}, headers=hdr) as r:
            if r.status == 200:
                j = await r.json()
                try:
                    meta = json.loads(_b64.b64decode(j["content"]).decode("utf-8"))
                except Exception:
                    meta = {}
        async with session.get(
                f"https://api.github.com/repos/{GH_REPO}/contents/cards",
                params={"ref": branch}, headers=hdr) as r:
            if r.status == 200:
                for it in await r.json():
                    nm = it.get("name", "")
                    if not nm.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
                        continue
                    stem = nm.rsplit(".", 1)[0]
                    raw = f"https://raw.githubusercontent.com/{GH_REPO}/{branch}/cards/{nm}"
                    player_name = meta.get(stem, {}).get("player", "") if isinstance(meta.get(stem), dict) else ""
                    pslug = _slug(player_name)
                    if pslug:
                        by_player.setdefault(pslug, raw)
                        by_player.setdefault(_slug_loose(player_name), raw)
                    stems.append((_slug(stem), _slug_loose(stem), raw))
    except Exception as ex:
        print(f"[catalog] {ex}")
    _CARDCAT.update({"t": now, "by_player": by_player, "stems": stems})
    return _CARDCAT


def _card_img_from_catalog(name, cat):
    """Resolve a player name to its card art URL. Tries exact match first,
    then a LOOSE match that treats l/I/1 as the same char (variant spellings)."""
    want = _slug(name)
    want_loose = _slug_loose(name)
    if not want:
        return None
    def proxied(raw_url):
        # Card URLs are consumed inside Discord's Activity CSP, where direct
        # raw.githubusercontent.com loads are not reliable.  Catalog URLs are
        # always raw URLs, but keep this idempotent for callers/tests.
        if raw_url.startswith("/api/img?"):
            return raw_url
        return "/api/img?url=" + quote(raw_url, safe="")

    # Metadata is the canonical player alias (award filenames are not player
    # names), so it intentionally wins over an accidental filename match.
    if want in cat["by_player"]:
        return proxied(cat["by_player"][want])
    if want_loose in cat["by_player"]:
        return proxied(cat["by_player"][want_loose])
    # 2) exact filename == player
    for stem_slug, stem_loose, url in cat["stems"]:
        if stem_slug == want:
            return proxied(url)
    # 3) loose filename match (IIHurz ~ IIIHurz)
    for stem_slug, stem_loose, url in cat["stems"]:
        if stem_loose == want_loose:
            return proxied(url)
    # 4) award card containing the name (loose)
    for stem_slug, stem_loose, url in cat["stems"]:
        if want_loose in stem_loose:
            return proxied(url)
    return None


SAVE_PATH = os.environ.get("SAVE_PATH", "fantasy_save.json")  # points to master save
OWNER_ID = os.environ.get("OWNER_ID", "")  # Discord id with unlimited coins / free packs
PACK_COST = int(os.environ.get("PACK_COST", "10"))  # base pack cost in coins
POOL_PATH = os.environ.get("POOL_PATH", "fantasy_market.json")
DRAFT_PATH = os.environ.get("DRAFT_PATH", "qcl_draft_activity.json")
_DRAFT_ADMIN_IDS = {
    item.strip() for item in os.environ.get("DRAFT_ADMIN_IDS", OWNER_ID).split(",")
    if item.strip()
}
PORT = int(os.environ.get("PORT", "8787"))

# odds MUST match the bot's TVT_ODDS / TVT_PACK_SIZE
ODDS = [("Common", 0.50), ("Uncommon", 0.30), ("Rare", 0.15),
        ("Epic", 0.04), ("Legendary", 0.01)]
PACK_SIZE = 3

import hmac as _hmac
import hashlib as _hashlib
import base64 as _b64

_SESSION_SECRET = os.environ.get("QCL_SIGNING_SECRET", CLIENT_SECRET or "change-me")


def _make_session(uid, name, avatar):
    payload = _b64.urlsafe_b64encode(
        json.dumps({"id": uid, "name": name, "avatar": avatar,
                    "exp": time.time() + 60*60*6}).encode()).decode().rstrip("=")
    sig = _hmac.new(_SESSION_SECRET.encode(), payload.encode(),
                    _hashlib.sha256).hexdigest()[:16]
    return f"{payload}.{sig}"


def _read_session(tok):
    try:
        payload, sig = tok.split(".", 1)
        good = _hmac.new(_SESSION_SECRET.encode(), payload.encode(),
                         _hashlib.sha256).hexdigest()[:16]
        if not _hmac.compare_digest(sig, good):
            return None
        pad = "=" * (-len(payload) % 4)
        data = json.loads(_b64.urlsafe_b64decode(payload + pad))
        if data.get("exp", 0) < time.time():
            return None
        return data
    except Exception:
        return None


_COOLDOWN = {}
_COOLDOWN_SECS = 2
_GH_API = "https://api.github.com"


def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


# ═══════════════════════════════════════════════════════════════════════════
#  LIVE DRAFT ACTIVITY API
#  The Activity polls /api/draft/state; every mutation increments revision.
#  This keeps all connected clients on the same board without trusting clients.
# ═══════════════════════════════════════════════════════════════════════════

def _draft_default():
    return {
        "schema_version": 2, "revision": 0, "status": "setup", "teams": [], "coaches": {},
        "players": {}, "order": [], "picks": [], "current_pick": 0,
        "pick_seconds": 90, "deadline_at": None, "paused_remaining": None,
        "scheduled_at": None, "started_at": None, "stopped_at": None,
        "lifecycle_updated_at": None, "lifecycle_updated_by": None,
        "protected_picks": [], "trades": [], "audit_log": [],
        "promo": None,
    }


# A two-second process cache lets dozens of Activity clients share one GitHub
# read while keeping the draft board effectively live.
_DRAFT_CACHE = {"t": 0.0, "draft": None}
# This lock intentionally protects only this Railway process. It serializes
# local mutations, but is not a cross-replica locking mechanism.
_DRAFT_MUTATION_LOCK = asyncio.Lock()
_DRAFT_LOCK_SCOPE = "single-process asyncio.Lock; not cross-replica"
_DRAFT_PROCESS_ID = "%s:%s" % (socket.gethostname(), os.getpid())
_GH_LAST_ERROR = None


async def _draft_load_shared(session):
    now = time.time()
    cached = _DRAFT_CACHE.get("draft")
    if isinstance(cached, dict) and now - float(_DRAFT_CACHE.get("t", 0)) < 2:
        return _draft_normalize(cached), None
    draft, sha = await _gh_get(session, DRAFT_PATH)
    draft = _draft_normalize(draft)
    _DRAFT_CACHE.update({"t": now, "draft": draft})
    return draft, sha


def _draft_normalize(data):
    draft = _draft_default()
    if isinstance(data, dict):
        draft.update(data)
    draft["schema_version"] = 2
    if draft.get("status") not in {"setup", "scheduled", "active", "paused", "stopped", "complete"}:
        draft["status"] = "setup"
    for key in ("scheduled_at", "started_at", "stopped_at",
                "lifecycle_updated_at", "lifecycle_updated_by"):
        draft.setdefault(key, None)
    return draft


def _draft_lifecycle(draft, status, uid):
    draft["status"] = status
    draft["lifecycle_updated_at"] = time.time()
    draft["lifecycle_updated_by"] = str(uid)


def _draft_schedule_timestamp(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("schedule requires an ISO timestamp")
    try:
        parsed = _datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timestamp must include timezone")
        stamp = parsed.timestamp()
    except (TypeError, ValueError):
        raise ValueError("schedule requires a valid ISO timestamp with timezone")
    if stamp <= time.time():
        raise ValueError("scheduled time must be in the future")
    return stamp


def _draft_session(request, body=None):
    token = request.query.get("session")
    if isinstance(body, dict):
        token = body.get("session") or token
    return _read_session(token) if token else None


def _draft_is_admin(uid):
    return str(uid) in _DRAFT_ADMIN_IDS


def _draft_team_for_user(draft, uid):
    uid = str(uid)
    return next(
        (team for team, coach in draft.get("coaches", {}).items()
         if str(coach) == uid),
        None,
    )


def _draft_turn(draft):
    index = int(draft.get("current_pick", 0))
    order = draft.get("order", [])
    return order[index] if 0 <= index < len(order) else None


def _draft_audit(draft, action, uid, **details):
    draft.setdefault("audit_log", []).append({
        "at": time.time(), "action": action, "actor_id": str(uid) if uid else None,
        "details": details,
    })
    draft["audit_log"] = draft["audit_log"][-2000:]


def _draft_public(draft, uid):
    """Return role-aware state; strategy lists remain private to their team."""
    out = dict(draft)
    out["server_time"] = time.time()
    out["access"] = (
        "admin" if _draft_is_admin(uid)
        else "coach" if _draft_team_for_user(draft, uid)
        else "player"
    )
    out["my_team"] = _draft_team_for_user(draft, uid)
    strategies = draft.get("strategies", {})
    out["strategies"] = (
        strategies if _draft_is_admin(uid)
        else ({out["my_team"]: strategies.get(out["my_team"], {})}
              if out["my_team"] else {})
    )
    return out


def _draft_available(draft):
    return [
        player for player in draft.get("players", {}).values()
        if not player.get("drafted_by")
    ]


def _draft_rank(player):
    for key in ("ovr", "overall", "rating", "rank_score"):
        try:
            return float(player.get(key, 0) or 0)
        except (TypeError, ValueError):
            continue
    return 1.0 if player.get("eligible", True) else 0.0


def _draft_auto_player(draft, team):
    available = [p for p in _draft_available(draft) if p.get("eligible", True)]
    if not available:
        available = _draft_available(draft)
    roster_positions = {
        str(p.get("position", "")).lower() for p in draft.get("players", {}).values()
        if p.get("drafted_by") == team
    }
    return max(
        available,
        key=lambda p: (
            str(p.get("position", "")).lower() not in roster_positions,
            _draft_rank(p),
        ),
        default=None,
    )


def _draft_record_pick(draft, player, uid, source):
    turn = _draft_turn(draft)
    if not turn or not player or player.get("drafted_by"):
        return None
    player["drafted_by"] = turn["team"]
    player["pick_number"] = turn["pick"]
    player["drafted_at"] = time.time()
    pick = {
        **turn, "player": player.get("gamertag", "Unknown"),
        "player_id": str(player.get("discord_id", "")),
        "timestamp": time.time(), "source": source,
    }
    draft.setdefault("picks", []).append(pick)
    draft["current_pick"] = int(draft.get("current_pick", 0)) + 1
    next_turn = _draft_turn(draft)
    if next_turn:
        draft["deadline_at"] = time.time() + int(draft.get("pick_seconds", 90))
    else:
        draft["status"], draft["deadline_at"] = "complete", None
    draft["promo"] = {
        "event_id": f"pick-{pick['pick']}-{int(time.time() * 1000)}",
        "kind": "pick", "player": pick["player"], "team": pick["team"],
        "pick": pick["pick"], "media_url": player.get("hype_video_url"),
        "entrance_audio_url": player.get("entrance_audio_url"),
        "duck_background_audio": True, "started_at": time.time(),
    }
    _draft_audit(draft, "pick_recorded", uid, **pick)
    return pick


async def draft_players(request):
    """Export or merge the prospect pool without replacing draft history."""
    if request.method == "OPTIONS":
        return _cors(web.Response())

    body = {}
    if request.method == "POST":
        try:
            body = await request.json()
        except Exception:
            return _cors(web.json_response({"error": "bad request"}, status=400))

    sess = _draft_session(request, body)
    if not sess:
        return _cors(web.json_response({"error": "not logged in"}, status=401))
    uid = str(sess["id"])

    async with aiohttp.ClientSession() as session:
        draft_raw, sha = await _gh_get(session, DRAFT_PATH)
        draft = _draft_normalize(draft_raw)

        if request.method == "GET":
            payload = {
                "schema_version": 1,
                "revision": int(draft.get("revision", 0)),
                "players": list(draft.get("players", {}).values()),
                "teams": list(draft.get("teams", [])),
                "coaches": dict(draft.get("coaches", {})),
                "exported_at": time.time(),
            }
            return _cors(web.json_response(payload, headers={
                "Content-Disposition": 'attachment; filename="qspn_draft_players.json"',
            }))

        if not _draft_is_admin(uid):
            return _cors(web.json_response(
                {"error": "commissioner access required"}, status=403
            ))

        incoming = body.get("players", body)
        if isinstance(incoming, list):
            incoming = {
                str(player.get("discord_id") or player.get("id") or
                    player.get("gamertag") or index): player
                for index, player in enumerate(incoming)
                if isinstance(player, dict)
            }
        if not isinstance(incoming, dict):
            return _cors(web.json_response(
                {"error": "players must be an object or array"}, status=400
            ))

        players = draft.setdefault("players", {})
        synced = 0
        for player_id, raw_player in incoming.items():
            if not isinstance(raw_player, dict):
                continue
            stable_id = str(
                raw_player.get("discord_id") or raw_player.get("id") or player_id
            )
            existing = players.get(stable_id, {})
            protected = {
                key: existing[key]
                for key in ("drafted_by", "pick_number", "drafted_at")
                if key in existing
            }
            players[stable_id] = {
                **existing,
                **raw_player,
                "discord_id": stable_id,
                **protected,
            }
            synced += 1

        if isinstance(body.get("teams"), list):
            draft["teams"] = [str(team) for team in body["teams"] if str(team)]
        if isinstance(body.get("coaches"), dict):
            draft["coaches"] = {
                str(team): str(coach)
                for team, coach in body["coaches"].items()
            }

        _draft_audit(draft, "players_synced", uid, count=synced)
        draft["revision"] = int(draft.get("revision", 0)) + 1
        ok = await _gh_put(
            session, DRAFT_PATH, draft, sha,
            f"draft activity: synced {synced} players",
        )
        if not ok:
            return _cors(web.json_response(
                {"error": "draft changed; refresh and retry"}, status=409
            ))
        _DRAFT_CACHE.update({"t": time.time(), "draft": draft})
        return _cors(web.json_response({
            "ok": True,
            "synced": synced,
            "revision": draft["revision"],
            "draft": _draft_public(draft, uid),
        }))


async def resource_keep_cut(request):
    """Save one authenticated user's current Keep 4 / Cut 4 decision."""
    if request.method == "OPTIONS":
        return _cors(web.Response())
    try:
        body = await request.json()
    except Exception:
        return _cors(web.json_response({"error": "bad request"}, status=400))
    token = body.get("session") or request.query.get("session")
    sess = _read_session(token) if token else None
    if not sess:
        return _cors(web.json_response({"error": "not logged in"}, status=401))

    players = body.get("players")
    keeps = body.get("keeps")
    if (not isinstance(players, list) or not isinstance(keeps, list)
            or len(players) != 8 or len(keeps) != 4):
        return _cors(web.json_response(
            {"error": "submit exactly 8 players with exactly 4 keeps"}, status=400
        ))
    player_ids = [str(player).strip() for player in players]
    keep_ids = [str(player).strip() for player in keeps]
    if (not all(player_ids) or len(set(player_ids)) != 8
            or len(set(keep_ids)) != 4 or not set(keep_ids).issubset(player_ids)):
        return _cors(web.json_response(
            {"error": "players and keeps must be unique valid selections"}, status=400
        ))

    uid = str(sess["id"])
    cuts = [player for player in player_ids if player not in set(keep_ids)]
    async with aiohttp.ClientSession() as session:
        raw, sha = await _gh_get(session, DRAFT_PATH)
        draft = _draft_normalize(raw)
        decisions = draft.setdefault("resource_decisions", {})
        keep_cut = decisions.setdefault("keep_cut", {})
        keep_cut[uid] = {
            "players": player_ids,
            "keeps": keep_ids,
            "cuts": cuts,
            "updated_at": time.time(),
        }
        _draft_audit(draft, "resource_keep_cut_saved", uid,
                     keep_count=len(keep_ids), cut_count=len(cuts))
        draft["revision"] = int(draft.get("revision", 0)) + 1
        ok = await _gh_put(
            session, DRAFT_PATH, draft, sha,
            f"resources: keep/cut saved for {uid}",
        )
        if not ok:
            return _cors(web.json_response(
                {"error": "draft changed; refresh and retry"}, status=409
            ))
        _DRAFT_CACHE.update({"t": time.time(), "draft": draft})
    return _cors(web.json_response({
        "ok": True, "keeps": keep_ids, "cuts": cuts,
        "updated_at": keep_cut[uid]["updated_at"],
    }))


# ── GitHub as the shared save (read + write fantasy_save.json) ──────────────
async def _gh_get(session, path):
    """Return (json_obj, sha) or ({}, None)."""
    url = f"{_GH_API}/repos/{GH_REPO}/contents/{path}"
    headers = {"Authorization": f"token {GH_TOKEN}",
               "Accept": "application/vnd.github+json"}
    async with session.get(url, headers=headers) as r:
        if r.status == 200:
            data = await r.json()
            content = base64.b64decode(data["content"]).decode("utf-8")
            try:
                return json.loads(content), data["sha"]
            except Exception:
                return {}, data["sha"]
        return {}, None


async def _gh_put(session, path, obj, sha, msg):
    global _GH_LAST_ERROR
    url = f"{_GH_API}/repos/{GH_REPO}/contents/{path}"
    headers = {"Authorization": f"token {GH_TOKEN}",
               "Accept": "application/vnd.github+json"}
    body = {"message": msg,
            "content": base64.b64encode(
                json.dumps(obj, indent=2, ensure_ascii=False).encode()).decode()}
    if sha:
        body["sha"] = sha
    async with session.put(url, headers=headers, json=body) as r:
        if r.status in (200, 201):
            _GH_LAST_ERROR = None
            return True
        # Never let a failed persistence write appear successful or update a
        # caller cache.  409/422 are normal SHA/content conflicts.
        detail = await r.text()
        _GH_LAST_ERROR = {"status": r.status, "path": path, "detail": detail[:300],
                          "at": time.time()}
        print("[github put failed] %s %s: %s" % (r.status, path, detail[:300]))
        return False


# ── pool + draw (server-side, anti-cheat) ──────────────────────────────────
def _draw(names, rarity):
    buckets = {}
    for n in names:
        buckets.setdefault(rarity.get(n, "Common"), []).append(n)
    out = []
    for _ in range(PACK_SIZE):
        roll, cum, chosen = random.random(), 0.0, "Common"
        for tier, odds in ODDS:
            cum += odds
            if roll <= cum:
                chosen = tier
                break
        bucket = buckets.get(chosen) or names
        if bucket:
            out.append(random.choice(bucket))
    return out


async def _verify_user(session, code):
    data = {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": "https://127.0.0.1"}
    try:
        async with session.post("https://discord.com/api/oauth2/token", data=data) as r:
            if r.status != 200:
                return None
            tok = (await r.json()).get("access_token")
        async with session.get("https://discord.com/api/users/@me",
                               headers={"Authorization": f"Bearer {tok}"}) as r2:
            if r2.status != 200:
                return None
            return await r2.json()
    except Exception as e:
        print(f"[verify] {e}")
        return None



async def login(request):
    if request.method == "OPTIONS":
        return _cors(web.Response())
    try:
        body = await request.json()
    except Exception:
        return _cors(web.json_response({"error": "bad request"}, status=400))
    code = body.get("code")
    if not code:
        return _cors(web.json_response({"error": "no code"}, status=400))
    async with aiohttp.ClientSession() as session:
        user = await _verify_user(session, code)
        if not user:
            return _cors(web.json_response({"error": "auth failed"}, status=401))
        uid = str(user.get("id"))
        sess = _make_session(uid, user.get("global_name") or user.get("username"),
                             user.get("avatar"))
    return _cors(web.json_response({
        "user": user.get("global_name") or user.get("username"),
        "avatar": user.get("avatar"), "user_id": uid, "session": sess}))



# ═══════════════════════════════════════════════════════════════════════════
#  STARTER PACKS — onboarding: pick a strategy, get curated players + 500 coins
# ═══════════════════════════════════════════════════════════════════════════

STARTER_PACKS = {
    "glass_cleaner": {"name": "The Glass Cleaner Pack",
        "desc": "2 Rebounding Bigs + 1 Scoring Guard — high floor.",
        "recipe": [("rebounder", 2), ("scorer", 1)]},
    "floor_general": {"name": "The Floor General Pack",
        "desc": "2 Playmaking Guards + 1 Finishing Big — efficiency & multipliers.",
        "recipe": [("playmaker", 2), ("finisher", 1)]},
    "pure_scorer": {"name": "The Pure Scorer Pack",
        "desc": "2 Shot-Creating Wings + 1 Defensive Anchor — high ceiling, high risk.",
        "recipe": [("scorer", 2), ("defender", 1)]},
    "lockdown": {"name": "The Lockdown Pack",
        "desc": "2 Defensive Specialists + 1 Two-Way Wing — the disruptor.",
        "recipe": [("defender", 2), ("scorer", 1)]},
}


def _classify_players(pstats):
    """Bucket players into roles from their real stats. Returns {role: [names]}."""
    buckets = {"rebounder": [], "playmaker": [], "scorer": [],
               "defender": [], "finisher": []}
    for name, s in pstats.items():
        if not isinstance(s, dict):
            continue
        ppg = float(s.get("ppg") or 0); rpg = float(s.get("rpg") or 0)
        apg = float(s.get("apg") or 0); spg = float(s.get("spg") or 0)
        bpg = float(s.get("bpg") or 0)
        # score each role, assign to the strongest (with sensible thresholds)
        scores = {
            "rebounder": rpg * 2.0 + bpg,
            "playmaker": apg * 2.0 + spg,
            "scorer": ppg,
            "defender": (spg + bpg) * 3.0,
            "finisher": ppg * 0.6 + rpg,
        }
        # a player can qualify for multiple pools they're strong in
        for role, val in scores.items():
            buckets[role].append((name, val))
    # sort each bucket by strength desc
    for role in buckets:
        buckets[role].sort(key=lambda x: x[1], reverse=True)
        buckets[role] = [n for n, _ in buckets[role]]
    return buckets


def _build_starter(pack_key, pstats):
    """Return a list of player names for the chosen starter pack."""
    import random as _r
    pack = STARTER_PACKS.get(pack_key)
    if not pack:
        return None
    buckets = _classify_players(pstats)
    chosen, used = [], set()
    for role, count in pack["recipe"]:
        pool_r = [n for n in buckets.get(role, []) if n not in used]
        # take from the TOP third for quality, randomized within it
        top = pool_r[:max(count * 4, 8)] or pool_r
        _r.shuffle(top)
        for n in top[:count]:
            chosen.append(n); used.add(n)
    return chosen


async def claim_starter(request):
    if request.method == "OPTIONS":
        return _cors(web.Response())
    try:
        body = await request.json()
    except Exception:
        return _cors(web.json_response({"error": "bad request"}, status=400))
    session_tok = body.get("session")
    pack_key = body.get("pack")
    sess = _read_session(session_tok) if session_tok else None
    if not sess:
        return _cors(web.json_response({"error": "not logged in"}, status=401))
    if pack_key not in STARTER_PACKS:
        return _cors(web.json_response({"error": "unknown pack"}, status=400))
    uid = str(sess["id"])

    async with aiohttp.ClientSession() as session:
        save_data, sha = await _gh_get(session, SAVE_PATH)
        if not isinstance(save_data, dict):
            save_data = {}
        users_dict = save_data.setdefault("users", {})
        entry = users_dict.setdefault(uid, {
            "name": sess.get("name"), "coins": 0, "cards": [],
            "roster": {"G": None, "F": None, "C": None, "B1": None, "B2": None},
            "serials": {}, "cumulative_fp": 0.0, "history": []})

        # CLAIM-ONCE guard
        if entry.get("starter_claimed"):
            return _cors(web.json_response({"error": "starter already claimed",
                                            "already": True}, status=409))

        pstats = await _load_player_stats(session)
        if not pstats:
            return _cors(web.json_response({"error": "stats not published yet"}, status=503))
        picks = _build_starter(pack_key, pstats)
        if not picks:
            return _cors(web.json_response({"error": "could not build pack"}, status=500))

        entry["cards"].extend(picks)
        entry["coins"] = int(entry.get("coins", 0)) + 500
        entry["starter_claimed"] = pack_key
        entry["name"] = sess.get("name")

        ok = await _gh_put(session, SAVE_PATH, save_data, sha,
                           f"starter [{pack_key}]: {entry['name']} +{len(picks)} +500c")
        if not ok:
            return _cors(web.json_response({"error": "save failed (retry)"}, status=500))

        # decorate for the reveal
        _cat = await _card_catalog(session)
        cards = []
        for n in picks:
            rs = _real_stats(n, pstats)
            cards.append({"name": n, "tier": rarity_lookup(n) if 'rarity_lookup' in globals() else "Common",
                          "img": _card_img_from_catalog(n, _cat),
                          "stats": {"ppg": rs.get("ppg"), "rpg": rs.get("rpg"),
                                    "apg": rs.get("apg"), "spg": rs.get("spg"),
                                    "bpg": rs.get("bpg"), "fp": rs.get("fp"),
                                    "gp": rs.get("gp")}})
        return _cors(web.json_response({
            "pack": STARTER_PACKS[pack_key]["name"], "cards": cards,
            "coins": entry["coins"], "user": entry["name"]}))


async def starter_status(request):
    """Check if a user has claimed a starter (for onboarding gate)."""
    session_tok = request.query.get("session")
    sess = _read_session(session_tok) if session_tok else None
    if not sess:
        return _cors(web.json_response({"claimed": False, "error": "not logged in"}))
    uid = str(sess["id"])
    async with aiohttp.ClientSession() as session:
        save_data, _ = await _gh_get(session, SAVE_PATH)
        entry = (save_data.get("users", {}) or {}).get(uid, {}) if isinstance(save_data, dict) else {}
        return _cors(web.json_response({
            "claimed": bool(entry.get("starter_claimed")),
            "pack": entry.get("starter_claimed"),
            "packs": {k: {"name": v["name"], "desc": v["desc"]}
                      for k, v in STARTER_PACKS.items()}}))



def _is_owner(uid):
    return OWNER_ID and str(uid) == str(OWNER_ID)


def _staked_odds(stake):
    """Return tier odds shifted by how many coins were staked.
    Base (PACK_COST) = normal. More stake = more weight to rare tiers,
    re-normalized to a valid distribution."""
    # normal weights (must mirror the base _draw odds)
    base = {"Common": 0.50, "Uncommon": 0.30, "Rare": 0.15, "Epic": 0.04, "Legendary": 0.01}
    extra = max(0, stake - PACK_COST)
    # boost factor scales with extra coins: +10 coins ~ +0.5x rare weight
    boost = extra / 20.0   # 20 extra coins = +1.0 boost unit
    if boost <= 0:
        return base
    mult = {"Common": 1.0, "Uncommon": 1.0 + boost * 0.2,
            "Rare": 1.0 + boost * 0.8, "Epic": 1.0 + boost * 1.5,
            "Legendary": 1.0 + boost * 3.0}
    weighted = {k: base[k] * mult[k] for k in base}
    total = sum(weighted.values())
    return {k: v / total for k, v in weighted.items()}


def _draw_weighted(names, rarity, odds, pack_size=3):
    import random as _r
    buckets = {}
    for n in names:
        buckets.setdefault(rarity.get(n, "Common"), []).append(n)
    tiers = list(odds.keys()); weights = list(odds.values())
    out = []
    for _ in range(pack_size):
        tier = _r.choices(tiers, weights=weights, k=1)[0]
        bucket = buckets.get(tier) or names
        if bucket:
            out.append(_r.choice(bucket))
    return out



async def get_balance(request):
    session_tok = request.query.get("session")
    sess = _read_session(session_tok) if session_tok else None
    if not sess:
        return _cors(web.json_response({"error": "not logged in"}, status=401))
    uid = str(sess["id"])
    async with aiohttp.ClientSession() as session:
        save_data, _ = await _gh_get(session, SAVE_PATH)
        entry = (save_data.get("users", {}) or {}).get(uid, {}) if isinstance(save_data, dict) else {}
        owner = _is_owner(uid)
        return _cors(web.json_response({
            "coins": "unlimited" if owner else int(entry.get("coins", 0)),
            "owner": owner, "pack_cost": PACK_COST}))



# ═══════════════════════════════════════════════════════════════════════════
#  ROSTER LOCK — set 5 (3 starters + 2 bench), locked 48h, only those 5 count
# ═══════════════════════════════════════════════════════════════════════════

LOCK_HOURS = 48
ROSTER_SLOTS = ["G", "F", "C", "B1", "B2"]   # 3 starters + 2 bench


async def _sheet_max_game(session):
    """Current max game number, so scoring later counts games AFTER the lock.
    Reads player_stats.json's 'gp' as a proxy if a game index isn't available."""
    # placeholder: we stamp the lock with the highest 'gp' seen (game count).
    # when real per-game scoring lands, replace with the sheet's max game #.
    pstats = await _load_player_stats(session)
    try:
        return max((int(v.get("gp", 0)) for v in pstats.values() if isinstance(v, dict)), default=0)
    except Exception:
        return 0


async def get_lineup(request):
    session_tok = request.query.get("session")
    sess = _read_session(session_tok) if session_tok else None
    if not sess:
        return _cors(web.json_response({"error": "not logged in"}, status=401))
    uid = str(sess["id"])
    async with aiohttp.ClientSession() as session:
        save_data, _ = await _gh_get(session, SAVE_PATH)
        entry = (save_data.get("users", {}) or {}).get(uid, {}) if isinstance(save_data, dict) else {}
        roster = entry.get("roster", {s: None for s in ROSTER_SLOTS})
        locked_at = entry.get("roster_locked_at", 0)
        now = time.time()
        remaining = max(0, (locked_at + LOCK_HOURS * 3600) - now) if locked_at else 0
        # decorate roster with card art + stats
        _cat = await _card_catalog(session)
        pstats = await _load_player_stats(session)
        decorated = {}
        for slot in ROSTER_SLOTS:
            nm = roster.get(slot)
            if nm:
                rs = _real_stats(nm, pstats)
                decorated[slot] = {"name": nm, "img": _card_img_from_catalog(nm, _cat),
                                   "stats": rs}
            else:
                decorated[slot] = None
        return _cors(web.json_response({
            "roster": decorated,
            "locked": remaining > 0,
            "remaining_seconds": int(remaining),
            "owned": entry.get("cards", []),
            "slots": ROSTER_SLOTS}))


async def set_lineup(request):
    if request.method == "OPTIONS":
        return _cors(web.Response())
    try:
        body = await request.json()
    except Exception:
        return _cors(web.json_response({"error": "bad request"}, status=400))
    session_tok = body.get("session")
    new_roster = body.get("roster", {})
    sess = _read_session(session_tok) if session_tok else None
    if not sess:
        return _cors(web.json_response({"error": "not logged in"}, status=401))
    uid = str(sess["id"])

    async with aiohttp.ClientSession() as session:
        save_data, sha = await _gh_get(session, SAVE_PATH)
        if not isinstance(save_data, dict):
            save_data = {}
        users_dict = save_data.setdefault("users", {})
        entry = users_dict.setdefault(uid, {
            "name": sess.get("name"), "coins": 0, "cards": [],
            "roster": {s: None for s in ROSTER_SLOTS}, "serials": {},
            "cumulative_fp": 0.0, "history": []})

        # LOCK ENFORCEMENT: can't change until 48h since last lock
        now = time.time()
        locked_at = entry.get("roster_locked_at", 0)
        remaining = (locked_at + LOCK_HOURS * 3600) - now if locked_at else 0
        if remaining > 0:
            hrs = remaining / 3600
            return _cors(web.json_response(
                {"error": f"Lineup locked for {hrs:.1f} more hours.",
                 "remaining_seconds": int(remaining), "locked": True}, status=423))

        # validate: only slots we know, only cards the user OWNS, no duplicates
        owned = set(entry.get("cards", []))
        chosen = {}
        seen = set()
        for slot in ROSTER_SLOTS:
            nm = new_roster.get(slot)
            if nm is None or nm == "":
                chosen[slot] = None
                continue
            if nm not in owned:
                return _cors(web.json_response(
                    {"error": f"You don't own '{nm}'."}, status=400))
            if nm in seen:
                return _cors(web.json_response(
                    {"error": f"'{nm}' is in two slots."}, status=400))
            seen.add(nm); chosen[slot] = nm

        # require all 5 filled to lock (a full lineup)
        if any(chosen[s] is None for s in ROSTER_SLOTS):
            return _cors(web.json_response(
                {"error": "Fill all 5 slots (3 starters + 2 bench) to lock."}, status=400))

        entry["roster"] = chosen
        entry["roster_locked_at"] = now
        entry["roster_lock_game"] = await _sheet_max_game(session)  # for scoring later
        entry["name"] = sess.get("name")

        ok = await _gh_put(session, SAVE_PATH, save_data, sha,
                           f"lineup lock: {entry['name']}")
        if not ok:
            return _cors(web.json_response({"error": "save failed (retry)"}, status=500))
        return _cors(web.json_response({
            "ok": True, "roster": chosen,
            "locked": True, "remaining_seconds": LOCK_HOURS * 3600}))


async def open_pack(request):
    if request.method == "OPTIONS":
        return _cors(web.Response())
    try:
        body = await request.json()
    except Exception:
        return _cors(web.json_response({"error": "bad request"}, status=400))
    code = body.get("code")
    session_tok = body.get("session")
    stake = int(body.get("stake", PACK_COST) or PACK_COST)
    if stake < PACK_COST:
        stake = PACK_COST

    async with aiohttp.ClientSession() as session:
        sess = _read_session(session_tok) if session_tok else None
        if sess:
            uid = str(sess["id"])
            user = {"id": uid, "global_name": sess.get("name"),
                    "username": sess.get("name"), "avatar": sess.get("avatar")}
            new_session = None
        elif code:
            user = await _verify_user(session, code)
            if not user:
                return _cors(web.json_response({"error": "auth failed"}, status=401))
            uid = str(user.get("id"))
            new_session = _make_session(uid, user.get("global_name") or user.get("username"),
                                        user.get("avatar"))
        else:
            return _cors(web.json_response({"error": "no code or session"}, status=400))

        now = time.time()
        if now - _COOLDOWN.get(uid, 0) < _COOLDOWN_SECS:
            return _cors(web.json_response({"error": "slow down"}, status=429))
        _COOLDOWN[uid] = now

        pool, _ = await _gh_get(session, POOL_PATH)
        names, rarity = [], {}
        if isinstance(pool, dict) and pool.get("names"):
            names = pool["names"]
            rarity = pool.get("rarity", {})
        elif isinstance(pool, dict) and pool.get("cards"):
            names = list(pool["cards"].keys())
            rarity = {n: (v.get("tier", "Common") if isinstance(v, dict) else "Common")
                      for n, v in pool["cards"].items()}
        elif isinstance(pool, dict):
            for n, v in pool.items():
                if isinstance(v, dict) and ("tier" in v or "cls" in v or "legend" in v):
                    names.append(n)
                    rarity[n] = v.get("tier", "Common")
        if not names:
            return _cors(web.json_response({"error": "pool not published yet"}, status=503))

        # Read master save (fantasy_save.json)
        save_data, sha = await _gh_get(session, SAVE_PATH)
        if not isinstance(save_data, dict):
            save_data = {}

        users_dict = save_data.setdefault("users", {})
        user_entry = users_dict.setdefault(uid, {
            "name": user.get("global_name") or user.get("username"),
            "coins": 500,
            "cards": [],
            "roster": {"G": None, "F": None, "C": None, "B1": None, "B2": None},
            "serials": {},
            "cumulative_fp": 0.0,
            "history": []
        })
        user_entry["name"] = user.get("global_name") or user.get("username")

        owner = _is_owner(uid)
        bal = int(user_entry.get("coins", 0))
        # charge the stake (owner opens free + unlimited)
        if not owner:
            if bal < stake:
                return _cors(web.json_response(
                    {"error": f"Not enough coins. Need {stake}, have {bal}.",
                     "coins": bal, "need": stake}, status=402))
            user_entry["coins"] = bal - stake

        # staked odds: base cost = normal, extra coins boost rare chances
        odds = _staked_odds(stake)
        pulled = _draw_weighted(names, rarity, odds)
        user_entry["cards"].extend(pulled)

        coins_after = "unlimited" if owner else user_entry["coins"]
        ok = await _gh_put(session, SAVE_PATH, save_data, sha,
                           f"pack (stake {stake}): {user_entry['name']} +{len(pulled)}")
        if not ok:
            return _cors(web.json_response({"error": "save failed (retry)"}, status=500))

        _cat = await _card_catalog(session)
        _pstats = await _load_player_stats(session)
        def _stats_for(n):
            rs = _real_stats(n, _pstats)
            e = pool.get(n, {}) if isinstance(pool, dict) else {}
            if not isinstance(e, dict):
                return {}
            return {
                "archetype": e.get("arch", ""),
                "gp": rs.get("gp", e.get("gp", 0)),
                "ppg": rs.get("ppg"), "rpg": rs.get("rpg"), "apg": rs.get("apg"),
                "spg": rs.get("spg"), "bpg": rs.get("bpg"), "fp": rs.get("fp"),
                "legend": e.get("legend", 0), "perf": e.get("perf", 0),
            }
        cards = [{"name": n, "tier": rarity.get(n, "Common"),
                  "img": _card_img_from_catalog(n, _cat),
                  "stats": _stats_for(n)} for n in pulled]
        resp = {"user": user.get("global_name") or user.get("username"),
                "avatar": user.get("avatar"), "user_id": uid, "cards": cards,
                "coins": coins_after, "staked": stake}
        if new_session:
            resp["session"] = new_session
        return _cors(web.json_response(resp))

async def get_binder(request):
    if request.method == "OPTIONS":
        return _cors(web.Response())
    try:
        body = await request.json()
    except Exception:
        return _cors(web.json_response({"error": "bad request"}, status=400))

    session_tok = body.get("session")
    if not session_tok:
        return _cors(web.json_response({"error": "Not authenticated. Let auto-login finish or open a pack first!"}, status=401))

    sess = _read_session(session_tok)
    if not sess:
        return _cors(web.json_response({"error": "Session expired."}, status=401))

    uid = str(sess["id"])
    
    try:
        async with aiohttp.ClientSession() as session:
            save_data, _ = await _gh_get(session, SAVE_PATH)
            if not isinstance(save_data, dict):
                save_data = {}
            
            users_dict = save_data.get("users", {})
            user_data = users_dict.get(uid, {})
            owned_cards = user_data.get("cards", [])

            pool, _ = await _gh_get(session, POOL_PATH)
            rarity_map = {}
            if isinstance(pool, dict) and pool.get("rarity"):
                rarity_map = pool["rarity"]
            elif isinstance(pool, dict) and pool.get("cards"):
                rarity_map = {n: (v.get("tier", "Common") if isinstance(v, dict) else "Common")
                              for n, v in pool["cards"].items()}
            elif isinstance(pool, dict):
                for n, v in pool.items():
                    if isinstance(v, dict) and "tier" in v:
                        rarity_map[n] = v["tier"]

            card_counts = {}
            if isinstance(owned_cards, dict):
                for k, v in owned_cards.items():
                    card_counts[k] = int(v) if str(v).isdigit() else 1
            elif isinstance(owned_cards, list):
                for card in owned_cards:
                    if isinstance(card, str):
                        card_counts[card] = card_counts.get(card, 0) + 1
                    elif isinstance(card, dict) and "name" in card:
                        cname = card["name"]
                        card_counts[cname] = card_counts.get(cname, 0) + 1
                
            _bcat = await _card_catalog(session)
            _bpstats = await _load_player_stats(session)
            formatted_cards = []
            def _bstats(nm):
                e = pool.get(nm, {}) if isinstance(pool, dict) else {}
                if not isinstance(e, dict):
                    e = {}
                rs = _real_stats(nm, _bpstats)
                return {"archetype": e.get("arch",""),
                        "gp": rs.get("gp", e.get("gp",0)),
                        "ppg": rs.get("ppg"), "rpg": rs.get("rpg"), "apg": rs.get("apg"),
                        "spg": rs.get("spg"), "bpg": rs.get("bpg"), "fp": rs.get("fp"),
                        "legend": e.get("legend",0), "perf": e.get("perf",0)}
            for name, count in card_counts.items():
                tier_val = rarity_map.get(name, "Common")
                tier = str(tier_val).lower() if tier_val else "common"
                formatted_cards.append({"name": str(name), "tier": tier, "count": count,
                                        "img": _card_img_from_catalog(name, _bcat),
                                        "stats": _bstats(name)})

            tier_order = {"legendary": 0, "epic": 1, "rare": 2, "uncommon": 3, "common": 4}
            formatted_cards.sort(key=lambda x: (tier_order.get(x["tier"], 5), x["name"]))

        return _cors(web.json_response({"cards": formatted_cards}))
        
    except Exception as e:
        print(f"[Binder Error] {e}")
        return _cors(web.json_response({"error": f"Server Error: {str(e)}"}, status=500))



async def cards_debug(request):
    """Diagnostic: what card files does the server see, and how do names map?"""
    out = {}
    async with aiohttp.ClientSession() as session:
        cat = await _card_catalog(session)
        out["stem_count"] = len(cat.get("stems", []))
        out["sample_files"] = [s[0] for s in cat.get("stems", [])[:15]]
        out["meta_player_count"] = len(cat.get("by_player", {}))
        out["meta_sample"] = list(cat.get("by_player", {}).keys())[:15]
        # test-resolve some names
        tests = ["Kyglo", "iBoola", "DynastyOnTop", "ThaGap", "YPFRAS",
                 "IIHurz", "Trifecta"]
        out["resolutions"] = {}
        for t in tests:
            url = _card_img_from_catalog(t, cat)
            out["resolutions"][t] = url.split("/")[-1] if url else "NO MATCH"
    return _cors(web.json_response(out))


async def health(request):
    return _cors(web.json_response({"ok": True, "service": "qcl-pull-server",
                                     "repo": GH_REPO, "process_id": _DRAFT_PROCESS_ID,
                                     "lock_scope": _DRAFT_LOCK_SCOPE}))





async def diag(request):
    out = {"env": {}, "steps": {}}
    out["env"]["has_client_id"] = bool(CLIENT_ID)
    out["env"]["has_client_secret"] = bool(CLIENT_SECRET)
    out["env"]["has_github_token"] = bool(GH_TOKEN)
    out["env"]["repo"] = GH_REPO
    out["env"]["pool_path"] = POOL_PATH
    out["env"]["save_path"] = SAVE_PATH
    out["draft_lock"] = {"process_id": _DRAFT_PROCESS_ID, "scope": _DRAFT_LOCK_SCOPE,
                         "last_github_write_error": _GH_LAST_ERROR}
    async with aiohttp.ClientSession() as session:
        try:
            pool, _ = await _gh_get(session, POOL_PATH)
            names = []
            if isinstance(pool, dict) and pool.get("names"):
                names = pool["names"]
            elif isinstance(pool, dict) and pool.get("cards"):
                names = list(pool["cards"].keys())
            elif isinstance(pool, dict):
                for n, v in pool.items():
                    if isinstance(v, dict) and ("tier" in v or "cls" in v or "legend" in v):
                        names.append(n)
            out["steps"]["read_pool"] = {"ok": bool(names), "player_count": len(names),
                                         "sample": names[:5]}
        except Exception as e:
            out["steps"]["read_pool"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        try:
            save, sha = await _gh_get(session, SAVE_PATH)
            out["steps"]["read_save"] = {"ok": True, "exists": sha is not None,
                                         "users": len(save.get("users", {}))}
        except Exception as e:
            out["steps"]["read_save"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        try:
            probe, sha = await _gh_get(session, "pull_server_probe.json")
            ok = await _gh_put(session, "pull_server_probe.json",
                               {"last_diag": time.time()}, sha, "pull-server write test")
            out["steps"]["write_repo"] = {"ok": ok,
                "note": "if false, the GITHUB_TOKEN can't WRITE (needs repo scope + push access)"}
        except Exception as e:
            out["steps"]["write_repo"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    out["verdict"] = ("ALL GOOD — pulls should save"
                      if all(s.get("ok") for s in out["steps"].values())
                      else "SOMETHING FAILED — see which step ok=false above")
    return _cors(web.json_response(out, status=200))

async def proxy_image(request):
    if request.method == "OPTIONS":
        return _cors(web.Response())
    url = request.query.get("url")
    parsed = urlsplit(url or "")
    if (parsed.scheme != "https" or parsed.netloc != "raw.githubusercontent.com"
            or parsed.username or parsed.password or parsed.port):
        return _cors(web.json_response({"error": "Bad URL"}, status=400))
    
    headers = {}
    if GH_TOKEN:
        headers["Authorization"] = f"token {GH_TOKEN}"
        
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=headers, allow_redirects=False) as r:
                if r.status != 200:
                    return _cors(web.Response(status=r.status))
                data = await r.read()
                # aiohttp's content_type argument rejects values with
                # parameters; preserve valid upstream image types exactly.
                ctype = r.headers.get("Content-Type", "").split(";", 1)[0].strip()
                if not ctype.lower().startswith("image/"):
                    ctype = "image/png"
                resp = web.Response(body=data, content_type=ctype, headers={
                    "Cache-Control": "public, max-age=86400",
                })
                return _cors(resp)
        except Exception as e:
            return _cors(web.json_response({"error": str(e)}, status=500))

async def serve_qtcg(request):
    """The sole Activity document; aliases normalize in the client."""
    return web.Response(text=SPA_HTML, content_type="text/html",
                        headers={"Cache-Control": "no-store, max-age=0"})


async def serve_resources(request):
    return await serve_qtcg(request)


draft_state, draft_action = create_handlers({
    "cors": _cors,
    "session": _draft_session,
    "load": lambda session: _draft_load_shared(session),
    "get": lambda session, path: _gh_get(session, path),
    "put": lambda session, path, obj, sha, msg: _gh_put(
        session, path, obj, sha, msg
    ),
    "normalize": _draft_normalize,
    "public": _draft_public,
    "turn": _draft_turn,
    "auto": _draft_auto_player,
    "record": _draft_record_pick,
    "admin": _draft_is_admin,
    "team": _draft_team_for_user,
    "available": _draft_available,
    "audit": _draft_audit,
    "lifecycle": _draft_lifecycle,
    "schedule": _draft_schedule_timestamp,
    "path": DRAFT_PATH,
    "cache": _DRAFT_CACHE,
    "lock": _DRAFT_MUTATION_LOCK,
})


@web.middleware
async def _draft_non_action_mutation_lock(request, handler):
    """Serialize the remaining draft-file POST read/modify/write handlers."""
    if request.method == "POST" and request.path in {
            "/api/draft/players", "/api/resources/keep-cut"}:
        async with _DRAFT_MUTATION_LOCK:
            return await handler(request)
    return await handler(request)


app = web.Application(middlewares=[
    request_diagnostics_middleware, _draft_non_action_mutation_lock,
])

# --- 1. API ROUTES (Must be registered first) ---
app.router.add_post("/api/login", login)
app.router.add_post("/api/starter", claim_starter)
app.router.add_options("/api/starter", claim_starter)
app.router.add_get("/api/starter_status", starter_status)
app.router.add_get("/api/balance", get_balance)
app.router.add_get("/api/lineup", get_lineup)
app.router.add_post("/api/lineup", set_lineup)
app.router.add_options("/api/lineup", set_lineup)
app.router.add_options("/api/login", login)
app.router.add_post("/api/openpack", open_packs := open_pack)
app.router.add_options("/api/openpack", open_pack)
app.router.add_post("/api/binder", get_binder)
app.router.add_options("/api/binder", get_binder)
app.router.add_get("/api/health", health)
app.router.add_get("/api/activity-diagnostics", activity_diagnostics)
app.router.add_get("/api/diag", diag)
app.router.add_get("/api/cards", cards_debug)
app.router.add_get("/api/draft/state", draft_state)
app.router.add_post("/api/draft/action", draft_action)
app.router.add_options("/api/draft/action", draft_action)
app.router.add_get("/api/draft/players", draft_players)
app.router.add_post("/api/draft/players", draft_players)
app.router.add_options("/api/draft/players", draft_players)
app.router.add_post("/api/resources/keep-cut", resource_keep_cut)
app.router.add_options("/api/resources/keep-cut", resource_keep_cut)
app.router.add_get("/api/img", proxy_image)
app.router.add_options("/api/img", proxy_image)

# --- 2. FRONTEND & STATIC ROUTES ---
# Canonical API routes above must remain ahead of this static catch-all.
# Every Activity entry point serves the exact same SPA document.
app.router.add_get("/", serve_qtcg)
app.router.add_get("/qtcg", serve_qtcg)
app.router.add_get("/draft", serve_qtcg)
app.router.add_get("/war-room", serve_qtcg)
app.router.add_get("/warroom", serve_qtcg)
app.router.add_get("/warroom/war-room", serve_qtcg)
app.router.add_get("/resources", serve_resources)
app.router.add_get("/diagnostics", serve_diagnostics)
app.router.add_get("/coach", serve_qtcg)
app.router.add_get("/director", serve_qtcg)
app.router.add_get("/assets/discord-embedded-sdk.js", serve_discord_sdk)

app.router.add_static('/', path=str(BASE_DIR), name='static', show_index=False)
app.router.add_static('/warroom/', path=str(BASE_DIR), name='warroom_static', show_index=False)


if __name__ == "__main__":
    print(f"[qcl-pull-server] starting on :{PORT}, repo={GH_REPO}")
    web.run_app(app, host="0.0.0.0", port=PORT)
