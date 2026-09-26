"""
QCL LEAGUE HUB — Streamlit Analytics Dashboard
================================================
Qwik's Cup League (QCL) / NBA 2K Pro-Am analytics front end.


Data source : Google Sheet (CSV export) — Type='Total' rows are source of truth.
Run         : streamlit run qcl_hub.py


v3.2 CHANGELOG
--------------
* PLAYOFFS VIEW   : dedicated postseason section (Game_ID 9001-9999). Same stat
                    engine as the regular season — leaders, full player table,
                    team board, single-game highs — scoped to playoff games.
* STAT ENGINE     : Section 8 refactored into compute_stats() so the main app and
                    the Playoffs view share identical math (no drift).


v3.1 CHANGELOG
--------------
* ORACLE REBUILT  : true Monte Carlo (vectorized, N sims) over a FIVE-MAN ROTATION
                    only. Rotation = top 5 by Games Played (PIE tiebreak). Scratches
                    scale team output. Outputs win prob, spread, total, moneyline,
                    margin distribution, MVP odds.
* LINEUP LAB      : build any 5-man unit, compare head-to-head vs another unit.
* PLAYER SPOTLIGHT: game logs, rolling form, W/L splits, opponent splits.
* INTERACTIVITY   : global filters, search, watchlist, tunable award thresholds,
                    adjustable power-ranking weights, CSV downloads everywhere.
* FIXES           : real TS% (uses FTA), real eFG% (uses 3PM).
* HARDENING       : works on older Streamlit (no max_selections / ProgressColumn /
                    st.rerun dependencies), guards for empty teams/rotations,
                    guaranteed Opp_PTS column, NaN-safe formatting throughout.
"""


import os
import re
import html as _html
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import requests as _hub_requests
import base64
import json
import streamlit.components.v1 as components


# =============================================================================
# 1. CONFIG
# =============================================================================
st.set_page_config(page_title="QCL LEAGUE CENTRAL", page_icon="🏀", layout="wide")

st.markdown("""
    <link rel="apple-touch-icon" href="https://cdn-icons-png.flaticon.com/512/1055/1055687.png">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-title" content="QCL Hub">
    <meta name="theme-color" content="#5865F2">
    <meta name="mobile-web-app-capable" content="yes">
    <style>@media (max-width:640px){header[data-testid="stHeader"]{height:0;}.block-container{padding-top:1rem;}}</style>
""", unsafe_allow_html=True)


SHEET_ID = "1rksLYUcXQJ03uTacfIBD6SRsvtH-IE6djqT-LINwcH4"
URL = os.environ.get("QCL_CSV_URL",
                     f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv")


GOLD, SILVER, BRONZE = "#d4af37", "#a0a0a0", "#cd7f32"
GREEN, RED, BLUE = "#00ff88", "#ff5555", "#00bfff"
ROTATION_SIZE = 5  # 2K Pro-Am: only five bodies on the floor




def _rerun():
    """st.rerun on new Streamlit, experimental_rerun on old."""
    if hasattr(st, "rerun"):
        st.rerun()
    else:
        st.experimental_rerun()


# ==================================================================
#  QCL login + packs + merged cards (inlined)
# ==================================================================
import streamlit.components.v1 as components
# ═══════════════════════════════════════════════════════════════════════════
#  OPTIONAL DISCORD LINKING — required only for the GM / Players desks.
#
#  A signed 30-day token is kept in an encrypted browser cookie only on
#  compatible Streamlit versions. Existing ?qcl= signed links still
#  restore a session, and are used as a fallback without cookie support.
#
#  Replaces hub_discord_login.py. Same setup (Discord app + secrets), plus one
#  more secret for signing:
#     .streamlit/secrets.toml
#        DISCORD_CLIENT_ID = "..."
#        DISCORD_CLIENT_SECRET = "..."
#        DISCORD_REDIRECT_URI = "https://your-hub.streamlit.app"
#        QCL_SIGNING_SECRET = "a-unique-random-secret-of-at-least-32-characters"
#
#  The league pages remain public without Discord setup. Cookie persistence
#  is optional; the old manager is incompatible with current Streamlit.
# ═══════════════════════════════════════════════════════════════════════════

import requests
import os
import json
import hmac
import hashlib
import base64
import time
import urllib.parse
from datetime import datetime, timedelta

_AUTH = "https://discord.com/api/oauth2/authorize"
_TOKEN = "https://discord.com/api/oauth2/token"
_ME = "https://discord.com/api/users/@me"
_TOKEN_TTL = 60 * 60 * 24 * 30      # 30 days


def _cfg(key, default=""):
    try:
        return st.secrets.get(key, default)
    except Exception:
        return os.environ.get(key, default)


# ── signed token: {id, name, avatar, exp} base64 + hmac ────────────────────
def _sign(payload: dict) -> str:
    secret = _cfg("QCL_SIGNING_SECRET").encode()
    if len(secret) < 32:
        raise RuntimeError("QCL_SIGNING_SECRET must be configured with at least 32 characters.")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    sig = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def _verify(token: str):
    try:
        body, sig = token.split(".", 1)
        secret = _cfg("QCL_SIGNING_SECRET").encode()
        if len(secret) < 32:
            return None
        good = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, good):
            return None
        pad = "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(body + pad))
        if payload.get("exp", 0) < time.time() or not str(payload.get("id", "")).isdigit():
            return None
        return payload
    except Exception:
        return None


def _login_cookies():
    """Browser-persistent login is optional and never blocks public browsing."""
    if len(_cfg("QCL_SIGNING_SECRET")) < 32:
        return None
    try:
        import extra_streamlit_components as stx
    except ImportError:
        return None
    return stx.CookieManager(key="qcl_session_cookie_manager")


def _cookie_get(name):
    if _cookies is None:
        return None
    try:
        return _cookies.get(name)
    except Exception:
        return None


def _cookie_set(name, value):
    if _cookies is None:
        return False
    try:
        _cookies.set(
            name,
            value,
            expires_at=datetime.now() + timedelta(seconds=_TOKEN_TTL),
            key=f"set_{name}_{hashlib.sha256(value.encode()).hexdigest()[:12]}",
        )
        return True
    except Exception:
        return False


def _cookie_delete(name):
    if _cookies is None:
        return
    try:
        _cookies.delete(name, key=f"delete_{name}_{int(time.time())}")
    except Exception:
        pass


def _exchange_code(code):
    data = {"client_id": _cfg("DISCORD_CLIENT_ID"),
            "client_secret": _cfg("DISCORD_CLIENT_SECRET"),
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": _cfg("DISCORD_REDIRECT_URI")}
    try:
        r = requests.post(_TOKEN, data=data,
                          headers={"Content-Type": "application/x-www-form-urlencoded"},
                          timeout=8)
        
        # --- NEW DEBUG CODE ---
        if r.status_code != 200:
            st.error(f"🚨 Discord rejected the trade: {r.text}")
            return None
        # ----------------------
        
        tok = r.json().get("access_token")
        me = requests.get(_ME, headers={"Authorization": f"Bearer {tok}"}, timeout=8)
        return me.json() if me.status_code == 200 else None
    except Exception as e:
        st.error(f"🚨 Network Crash: {e}")
        return None


def restore_session():
    """Restore a 30-day login from a browser cookie or an older signed URL."""
    params = st.query_params
    if st.session_state.get("discord_user") and st.session_state.get("auth_expires_at", 0) <= time.time():
        st.session_state.pop("discord_user", None)
        st.session_state.pop("auth_expires_at", None)
    if st.session_state.get("discord_user") and not params.get("code"):
        return

    # 1. Returning from Discord with ?code=...
    code = params.get("code")
    if code:
        code_str = code if isinstance(code, str) else code[0]
        
        # CLEAR THE CODE IMMEDIATELY so it never loops or tries to reuse a dead code
        st.query_params.clear()
        
        user = _exchange_code(code_str)
        if user:
            u = {"id": user.get("id"), "username": user.get("username"),
                 "global_name": user.get("global_name") or user.get("username"),
                 "avatar": user.get("avatar")}
            st.session_state["discord_user"] = u
            st.session_state["auth_expires_at"] = time.time() + _TOKEN_TTL
            
            # Persist in the browser; never expose a new login token in the URL
            # when cookie support is installed.
            token = _sign({"id": u["id"], "name": u["global_name"],
                           "avatar": u["avatar"], "exp": time.time() + _TOKEN_TTL})
            if not _cookie_set("qcl", token):
                st.query_params["qcl"] = token
            _rerun()
        else:
            st.error("🚨 Discord Login Failed! Check that your Client ID, Client Secret, and Redirect URI match perfectly in Streamlit Cloud Secrets.")
            st.stop()
        return

    # 2. Saved cookie (preferred) or existing signed URL link (legacy).
    tok = _cookie_get("qcl")
    tok = tok or params.get("qcl")
    if tok:
        payload = _verify(tok if isinstance(tok, str) else tok[0])
        if payload:
            st.session_state["discord_user"] = {
                "id": payload["id"], "username": payload["name"],
                "global_name": payload["name"], "avatar": payload.get("avatar")}
            st.session_state["auth_expires_at"] = payload["exp"]
            if params.get("qcl") and _cookie_set("qcl", tok):
                st.query_params.clear()


def current_user():
    return st.session_state.get("discord_user")


def _login_url():
    return _AUTH + "?" + urllib.parse.urlencode({
        "client_id": _cfg("DISCORD_CLIENT_ID"),
        "redirect_uri": _cfg("DISCORD_REDIRECT_URI"),
        "response_type": "code", "scope": "identify"})


def login_widget(key="sidebar"):
    """Login button, or a 'logged in as' chip with logout."""
    if not all(_cfg(k) for k in ("DISCORD_CLIENT_ID", "DISCORD_CLIENT_SECRET",
                                  "DISCORD_REDIRECT_URI")) or len(_cfg("QCL_SIGNING_SECRET")) < 32:
        st.caption("Optional Discord linking is unavailable until its OAuth settings and signing secret are configured.")
        return
    u = current_user()
    if u:
        c1, c2 = st.columns([3, 1])
        av = (f"https://cdn.discordapp.com/avatars/{u['id']}/{u['avatar']}.png"
             if u.get("avatar") else "https://cdn.discordapp.com/embed/avatars/0.png")
        c1.markdown(f"<div style='display:flex;align-items:center;gap:8px;'>"
                    f"<img src='{av}' width='28' style='border-radius:50%;'>"
                     f"<span style='color:#fff;font-weight:700;'>{_html.escape(str(u['global_name']))}</span>"
                    f"<span style='color:#3ba55d;font-size:12px;'>✓ verified</span>"
                    f"</div>", unsafe_allow_html=True)
        if c2.button("Log out", key=f"logout_{key}"):
            st.session_state.pop("discord_user", None)
            st.session_state.pop("auth_expires_at", None)
            if _cookie_get("qcl"):
                _cookie_delete("qcl")
            st.query_params.clear()
            _rerun()
    else:
        st.markdown(
            f"<a href='{_login_url()}' style='display:inline-block;"
            f"background:#5865F2;color:#fff;font-weight:800;padding:10px 20px;"
            f"border-radius:10px;text-decoration:none;'>🔗 Login with Discord</a>",
            unsafe_allow_html=True)


# =============================================================================
# ACCESS CONTROL / REGISTRATION DESK
# =============================================================================
# qcl_state.json is the bot's authoritative registration state. A separate
# Streamlit host can read a sanitized export from the public QCL repository.
# Never grant
# GM access from a client-supplied role, a gamertag, or the old player cards.
# registrations.json (the bot's /qtcg join feed) grants player access only.
def _hub_path(setting, default):
    root = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(_cfg(setting, os.path.join(root, default)))


def _hub_json(path):
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        return data
    except (OSError, ValueError) as exc:
        st.error(f"Registration data unavailable: {type(exc).__name__}. Access remains closed.")
        return {}


@st.cache_data(ttl=60, show_spinner=False)
def _public_registration_export(url):
    try:
        response = requests.get(url, timeout=6)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}
    except (requests.RequestException, ValueError):
        return {}


def _people(record):
    people = record.get("people") or []
    if isinstance(people, dict):
        people = list(people.values())
    return [person for person in people if isinstance(person, dict)] if isinstance(people, list) else []


def _official_records():
    path = _hub_path("QCL_REGISTRY_PATH", "qcl_state.json")
    state = (_hub_json(path) if os.path.isfile(path) else
             _public_registration_export(_cfg(
                 "QCL_REGISTRY_URL",
                 "https://raw.githubusercontent.com/jburnett1291-dot/QCL/main/qcl_registrations.json"
             )))
    records = state.get("qcl_registrations", state)
    if not isinstance(records, dict):
        return []
    # Draft/submitted/needs-info registrations must not reveal rosters or grant roles.
    return [r for r in records.values() if isinstance(r, dict)
            and r.get("status") == "approved"
            and r.get("role") in {"byot_gm", "draft_gm", "draft_player"}]


def _access(user):
    if not user or not str(user.get("id", "")).isdigit():
        return {"role": "guest", "team_records": []}
    uid = str(user["id"])
    records = _official_records()
    owned = [r for r in records if r.get("role") in {"byot_gm", "draft_gm"}
             and str(r.get("owner_id", "")) == uid]
    if owned:
        return {"role": "gm", "team_records": owned}
    if any(str(p.get("discord_id", "")) == uid for r in records for p in _people(r)):
        return {"role": "player", "team_records": []}
    legacy = _hub_json(_hub_path("QCL_PLAYER_REGISTRATIONS_PATH", "registrations.json"))
    if isinstance(legacy.get(uid), dict) and str(legacy[uid].get("discord_id", uid)) == uid:
        return {"role": "player", "team_records": []}
    return {"role": "unregistered", "team_records": []}


def _branding():
    return _hub_json(_hub_path("QCL_BRANDING_PATH", "qcl_team_branding.json"))


def _team_label(record, brand):
    rid = str(record.get("registration_id", ""))
    return str(brand.get(rid, {}).get("display_name") or record.get("team_name") or "Unnamed team")


def _brand_for_team(team):
    # Keep the statistical team key untouched; only change the displayed brand.
    for record in _official_records():
        if record.get("role") in {"byot_gm", "draft_gm"} and str(record.get("team_name", "")).casefold() == str(team).casefold():
            return _branding().get(str(record.get("registration_id", "")), {})
    return {}


def _team_brand_preview(record, brand):
    saved = brand.get(str(record.get("registration_id", "")), {})
    logo = saved.get("logo_url", "")
    if isinstance(logo, str) and logo.startswith("https://"):
        st.image(logo, width=100)
    if saved.get("tagline"):
        st.caption(str(saved["tagline"]))


def _contact(discord_id, label):
    if str(discord_id).isdigit():
        st.link_button(label, f"https://discord.com/users/{discord_id}")


def _gm_desk(access):
    """Server-side role check, not just a hidden navigation item."""
    if access["role"] not in {"gm", "player"}:
        st.error("Register with the league and link your Discord account to access a desk.")
        return
    st.header("🏢 GM Desk" if access["role"] == "gm" else "👥 Players Desk")
    records = _official_records()
    teams = [r for r in records if r.get("role") in {"byot_gm", "draft_gm"}]
    legacy = _hub_json(_hub_path("QCL_PLAYER_REGISTRATIONS_PATH", "registrations.json"))
    brand = _branding()
    st.caption("Only approved teams and registrations appear here. Contact opens a Discord profile; it never sends a message for you.")
    labels = ["GMs", "Rosters", "Stats & seasons"]
    if access["role"] == "gm":
        labels += ["Free agents", "Manage my team"]
    tabs = st.tabs(labels)
    with tabs[0]:
        if not teams:
            st.info("No approved GM teams are available yet. Ask the league to sync its approved bot registration file.")
        for record in teams:
            name = _team_label(record, brand)
            st.subheader(name)
            _team_brand_preview(record, brand)
            st.caption(f"{record.get('role', '').replace('_', ' ').upper()} · {len(_people(record))} registered people")
            _contact(record.get("owner_id"), "Reach out to GM on Discord")
    with tabs[1]:
        if not teams:
            st.info("No approved team rosters have been published.")
        for record in teams:
            with st.expander(_team_label(record, brand)):
                _team_brand_preview(record, brand)
                members = _people(record)
                if members:
                    st.dataframe([{"Player": p.get("display_name") or p.get("discord_tag") or "Player",
                                   "Gamertag": p.get("gamertag_or_psn", ""),
                                   "Position": p.get("position", ""),
                                   "Availability": p.get("availability", ""),
                                   "Socials": ", ".join(p.get("socials") or p.get("proof_platforms") or [])}
                                  for p in members],
                                 hide_index=True, use_container_width=True)
                else:
                    st.info("No registered roster members yet.")
    with tabs[2]:
        st.info("Use the Data Scope selector in the sidebar to browse prior QCL and SPAM seasons. The current season stays pending until games reach the sheet.")
        st.metric("Approved teams", len(teams))
        st.metric("Approved player registrations", sum(r.get("role") == "draft_player" for r in records))
    if access["role"] == "gm":
        with tabs[3]:
            agents = {}
            for record in records:
                if record.get("role") == "draft_player":
                    for person in _people(record):
                        uid = str(person.get("discord_id", ""))
                        if uid.isdigit():
                            agents[uid] = person
            for uid, person in legacy.items():
                if isinstance(person, dict) and str(uid).isdigit() and not person.get("team"):
                    agents.setdefault(str(uid), person)
            if not agents:
                st.info("No registered free agents have been published yet.")
            for uid, person in agents.items():
                st.write(f"**{person.get('display_name') or person.get('discord_tag') or uid}** · "
                         f"{person.get('position') or 'Position not listed'} · "
                         f"{person.get('gamertag_or_psn') or person.get('gamertag') or 'Gamertag not listed'}")
                st.caption(f"Availability: {person.get('availability') or 'Not listed'} · "
                           f"Socials: {', '.join(person.get('socials') or person.get('proof_platforms') or []) or 'Not listed'}")
                _contact(uid, f"Reach out to {person.get('display_name') or uid} on Discord")
        with tabs[4]:
            owned = access["team_records"]
            record = st.selectbox("Your approved team", owned,
                                  format_func=lambda r: _team_label(r, brand))
            rid = str(record.get("registration_id", ""))
            if not rid:
                st.error("This team has no registration ID; changes are disabled.")
            else:
                saved = brand.get(rid, {})
                with st.form(f"brand_{rid}"):
                    name = st.text_input("Hub team display name", value=saved.get("display_name") or record.get("team_name", ""), max_chars=48)
                    tagline = st.text_input("Tagline", value=saved.get("tagline", ""), max_chars=90)
                    logo = st.text_input("HTTPS logo URL", value=saved.get("logo_url", ""), max_chars=500)
                    submitted = st.form_submit_button("Save team branding")
                if submitted:
                    from urllib.parse import urlparse
                    parsed = urlparse(logo.strip())
                    if (not name.strip() or not re.fullmatch(r"[\w .&'-]+", name.strip()) or
                            (logo.strip() and (parsed.scheme != "https" or not parsed.netloc or
                                               not re.fullmatch(r"[A-Za-z0-9._~:/?#@!$&()*+,;=%-]+", logo.strip())))):
                        st.error("Enter a team name using letters, numbers and basic punctuation, and use an HTTPS logo URL.")
                    elif rid not in {str(r.get("registration_id")) for r in _access(current_user())["team_records"]}:
                        st.error("Team ownership changed. No changes were saved.")
                    else:
                        path = _hub_path("QCL_BRANDING_PATH", "qcl_team_branding.json")
                        latest = _hub_json(path)
                        latest[rid] = {"display_name": name.strip(), "tagline": tagline.strip(),
                                       "logo_url": logo.strip()}
                        try:
                            tmp = f"{path}.{os.getpid()}.tmp"
                            with open(tmp, "w", encoding="utf-8") as fh:
                                json.dump(latest, fh, indent=2, ensure_ascii=False)
                            os.replace(tmp, path)
                            st.success("Hub branding saved. Official bot and sheet team names are unchanged.")
                            _rerun()
                        except OSError:
                            st.error("Could not save branding. Configure a writable, persistent QCL_BRANDING_PATH.")
                if saved.get("logo_url", "").startswith("https://"):
                    st.image(saved["logo_url"], width=120)
                if saved.get("tagline"):
                    st.caption(saved["tagline"])


def _closed_season_desk(reason):
    st.warning(reason)
    st.info("Season analytics are paused. Registered members can still use their desk.")
    access = _access(current_user())
    if access["role"] in {"gm", "player"}:
        _gm_desk(access)
    else:
        st.caption("League data is public. Link an approved Discord account for desk access.")
        login_widget(key="empty_season")


def _registration_attachment(upload, prefix, files):
    if upload is None:
        return None
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", upload.name or "proof.png")
    field = f"files[{len(files)}]"
    files.append(
        (field, (f"{prefix}-{safe_name}", upload.getvalue(),
                 upload.type or "application/octet-stream"))
    )
    return field


def _submit_streamlit_registration(payload, uploads):
    """Send a registration to the bot's private Discord intake channel."""
    webhook = str(_cfg("QCL_REGISTRATION_WEBHOOK_URL") or "").strip()
    if not webhook.startswith("https://discord.com/api/webhooks/"):
        raise RuntimeError(
            "Registration intake is not configured. Add "
            "QCL_REGISTRATION_WEBHOOK_URL to Streamlit secrets."
        )
    files = []
    payload["attachments"] = {}
    for label, prefix, upload in uploads:
        field = _registration_attachment(upload, prefix, files)
        if field:
            payload["attachments"].setdefault(label, []).append(field)
    if len(files) > 10:
        raise ValueError("Discord accepts at most 10 uploaded files per registration.")
    message = "QCL_STREAMLIT_REGISTRATION\n```json\n" + json.dumps(
        payload, ensure_ascii=True, separators=(",", ":")
    ) + "\n```"
    if len(message) > 1950:
        raise ValueError("The registration is too large. Shorten roster notes and try again.")
    request_files = {
        field: file_tuple for field, file_tuple in files
    }
    response = requests.post(
        webhook,
        data={"payload_json": json.dumps({"content": message})},
        files=request_files or None,
        timeout=30,
    )
    if response.status_code not in (200, 204):
        raise RuntimeError(f"Discord intake returned HTTP {response.status_code}.")


def _render_registration():
    st.title("📝 League Registration")
    st.caption(
        "Register as a Draft Player or BYOT GM. Submissions go to the same "
        "private Discord review queue used by the bot."
    )
    user = current_user()
    if not user:
        st.info("Link Discord first so the registration is tied to the correct account.")
        login_widget(key="registration")
        return
    st.success(f"Discord linked as {user.get('global_name') or user.get('username')}")
    role_label = st.radio(
        "Registration type",
        ["Draft Player", "BYOT GM"],
        horizontal=True,
        key="registration_type",
    )
    role = "draft_player" if role_label == "Draft Player" else "byot_gm"
    with st.form("qcl_streamlit_registration", clear_on_submit=False):
        if role == "byot_gm":
            team_name = st.text_input("Team name *", max_chars=80)
            team_logo = st.file_uploader(
                "Team logo *", type=["png", "jpg", "jpeg", "webp"],
                key="reg_team_logo",
            )
        else:
            team_name, team_logo = "", None
        gamertag = st.text_input("Your gamertag or PSN *", max_chars=80)
        platform = st.selectbox("Platform *", ["PlayStation", "Xbox", "PC", "Other"])
        position = st.selectbox("Position *", ["PG", "SG", "SF", "PF", "C", "Utility"])
        availability = st.text_area(
            "Availability *", max_chars=500,
            placeholder="Days, times, and time zone",
        )
        socials = st.multiselect(
            "Social platforms shown in your proof",
            ["Instagram", "TikTok", "YouTube", "X/Twitter"],
        )
        owner_proof = st.file_uploader(
            "Your social proof screenshot(s) *",
            type=["png", "jpg", "jpeg", "webp"],
            accept_multiple_files=True,
            key="reg_owner_proof",
        )
        roster = []
        roster_uploads = []
        if role == "byot_gm":
            st.markdown("#### BYOT roster")
            st.caption(
                "Add at least one player besides yourself. Each player needs "
                "their Discord ID and social proof."
            )
            for index in range(1, 6):
                with st.expander(f"Roster player {index}", expanded=index == 1):
                    c1, c2 = st.columns(2)
                    discord_id = c1.text_input(
                        "Discord numeric ID", key=f"reg_p{index}_discord", max_chars=25
                    )
                    player_gamertag = c2.text_input(
                        "Gamertag / PSN", key=f"reg_p{index}_tag", max_chars=80
                    )
                    c3, c4 = st.columns(2)
                    player_platform = c3.selectbox(
                        "Platform", ["PlayStation", "Xbox", "PC", "Other"],
                        key=f"reg_p{index}_platform",
                    )
                    player_position = c4.selectbox(
                        "Position", ["PG", "SG", "SF", "PF", "C", "Utility"],
                        key=f"reg_p{index}_position",
                    )
                    player_proof = st.file_uploader(
                        "Social proof screenshot(s)",
                        type=["png", "jpg", "jpeg", "webp"],
                        accept_multiple_files=True,
                        key=f"reg_p{index}_proof",
                    )
                    if discord_id or player_gamertag or player_proof:
                        roster.append({
                            "discord_id": discord_id.strip(),
                            "gamertag": player_gamertag.strip(),
                            "platform": player_platform,
                            "position": player_position,
                            "proof_label": f"player_{index}",
                        })
                        roster_uploads.extend(
                            (f"player_{index}", f"player{index}-proof", item)
                            for item in player_proof
                        )
        confirm = st.checkbox(
            "I confirm this information is accurate and the proof belongs to the listed accounts."
        )
        submitted = st.form_submit_button(
            "Submit for Discord review", type="primary", use_container_width=True
        )
    if not submitted:
        return
    errors = []
    if not gamertag.strip():
        errors.append("your gamertag")
    if not availability.strip():
        errors.append("availability")
    if not owner_proof:
        errors.append("your social proof")
    if role == "byot_gm":
        if not team_name.strip():
            errors.append("team name")
        if team_logo is None:
            errors.append("team logo")
        if not roster:
            errors.append("at least one roster player")
        for number, player in enumerate(roster, 1):
            if not player["discord_id"].isdigit() or not player["gamertag"]:
                errors.append(f"valid Discord ID and gamertag for roster entry {number}")
            if not any(label == player["proof_label"] for label, _, _ in roster_uploads):
                errors.append(f"social proof for roster entry {number}")
    if not confirm:
        errors.append("confirmation")
    if errors:
        st.error("Please provide: " + "; ".join(errors) + ".")
        return
    payload = {
        "version": 1,
        "role": role,
        "owner_id": str(user["id"]),
        "owner_name": str(user.get("global_name") or user.get("username") or ""),
        "gamertag": gamertag.strip(),
        "platform": platform,
        "position": position,
        "availability": availability.strip(),
        "socials": socials,
        "team_name": team_name.strip(),
        "roster": roster,
        "submitted_at": int(time.time()),
    }
    uploads = [
        ("owner", "owner-proof", item) for item in owner_proof
    ] + roster_uploads
    if team_logo is not None:
        uploads.append(("team_logo", "team-logo", team_logo))
    try:
        _submit_streamlit_registration(payload, uploads)
    except (RuntimeError, ValueError, requests.RequestException) as exc:
        st.error(f"Registration was not submitted: {exc}")
        return
    st.success(
        "Registration submitted to the Discord review queue. Status and approval "
        "will sync back to the Hub after the locally running bot processes it."
    )


# Discord linking is optional for browsing the public league pages.
_cookies = _login_cookies()
restore_session()
_viewer = current_user()
_viewer_access = _access(_viewer)



# =============================================================================
# 2. STYLE
# =============================================================================
st.markdown("""
<style>
    .stApp { background: radial-gradient(circle at top, #121212 0%, #000000 100%); color: #e0e0e0; font-family: 'Helvetica Neue', sans-serif; }
    .header-banner {
        padding: 20px; text-align: center;
        background: linear-gradient(90deg, #d4af37 0%, #f7e08a 50%, #d4af37 100%);
        color: #000; font-family: 'Arial Black'; font-size: 26px; border-radius: 5px;
        margin-bottom: 20px; text-transform: uppercase; letter-spacing: 2px;
    }
    .metric-box { background: #1e1e1e; border-left: 4px solid #d4af37; padding: 15px; border-radius: 4px; text-align: center; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }
    .metric-title { font-size: 12px; color: #888; text-transform: uppercase; letter-spacing: 1px; }
    .metric-value { font-size: 22px; font-weight: 900; color: #fff; margin-top: 5px; }
    .metric-sub { font-size: 11px; color: #666; margin-top: 3px; }


    .sleek-table { width: 100%; border-collapse: collapse; margin: 15px 0; font-size: 13px; text-align: center; background: #161616; border-radius: 8px; overflow: hidden; }
    .sleek-table th { background: #222; color: #d4af37; padding: 12px; font-weight: bold; text-transform: uppercase; border-bottom: 2px solid #333; }
    .sleek-table td { padding: 10px; border-bottom: 1px solid #222; color: #ddd; }
    .sleek-table tr:hover { background: #1f1f1f; }
    .sleek-table td.player-name { text-align: left; font-weight: bold; color: #fff; }


    .podium-container { display: flex; justify-content: center; align-items: flex-end; margin: 30px 0; height: 200px; gap: 10px; }
    .podium { display: flex; flex-direction: column; align-items: center; justify-content: flex-end; text-align: center; width: 120px; border-radius: 8px 8px 0 0; }
    .podium-1 { background: linear-gradient(to top, #d4af37, #ffd700); height: 160px; color: #000; box-shadow: 0 0 20px rgba(212,175,55,0.4); z-index: 3; }
    .podium-2 { background: linear-gradient(to top, #a0a0a0, #e0e0e0); height: 120px; color: #000; z-index: 2; }
    .podium-3 { background: linear-gradient(to top, #cd7f32, #d2691e); height: 90px; color: #000; z-index: 1; }
    .podium-name { font-weight: bold; font-size: 14px; margin-bottom: 5px; padding: 0 5px; }
    .podium-stat { font-size: 20px; font-weight: 900; margin-bottom: 10px; }


    .shot-bar-container { background: #222; height: 20px; border-radius: 10px; width: 100%; position: relative; margin-top: 5px; overflow: hidden; }
    .shot-bar-fill { height: 100%; position: absolute; left: 0; top: 0; border-radius: 10px; }


    .award-card { background: #161b22; border: 1px solid #d4af37; padding: 20px; border-radius: 8px; text-align: center; height: 100%; box-shadow: 0 5px 15px rgba(0,0,0,0.5); }


    .flip-card { background-color: transparent; width: 100%; perspective: 1000px; margin-bottom: 25px; }
    .flip-card-inner { position: relative; width: 100%; height: 100%; text-align: center; transition: transform 0.6s; transform-style: preserve-3d; }
    .flip-card:hover .flip-card-inner { transform: rotateY(180deg); }
    .flip-card-front, .flip-card-back { position: absolute; width: 100%; height: 100%; -webkit-backface-visibility: hidden; backface-visibility: hidden; border-radius: 12px; border: 3px solid #d4af37; box-shadow: 0 10px 30px rgba(0,0,0,0.5); }
    .flip-card-front { background: linear-gradient(145deg, #1c2128, #2a2d35); display: flex; flex-direction: column; justify-content: center; align-items: center; padding: 20px; }
    .flip-card-back { background-color: #161b22; color: white; transform: rotateY(180deg); padding: 15px; overflow-y: auto; text-align: left; }
    .stat-row { display: flex; justify-content: space-between; border-bottom: 1px dashed #333; padding: 6px 0; font-size: 13px; }
    .stat-val { font-weight: bold; color: #d4af37; }
    .stat-label { color: #8b949e; }
    .sim-box { background: #161b22; padding: 20px; border-radius: 10px; border: 2px solid #d4af37; text-align: center; box-shadow: 0 5px 15px rgba(0,0,0,0.5); margin-bottom: 20px; }


    .chip { display:inline-block; background:#d4af37; color:#000; font-size:11px; font-weight:bold; padding:4px 9px; border-radius:12px; margin:2px; }
    .line-box { background:#0d1117; border:1px solid #30363d; border-radius:8px; padding:14px; text-align:center; }
    .line-label { color:#8b949e; font-size:11px; text-transform:uppercase; letter-spacing:1px; }
    .line-value { color:#fff; font-size:20px; font-weight:900; margin-top:4px; }
    .block-container {
        animation: slideUpFade 0.8s cubic-bezier(0.2, 0.8, 0.2, 1) forwards;
    }
    [data-testid="stMetric"], .award-card, .metric-box {
        animation: popIn 0.6s cubic-bezier(0.2, 0.8, 0.2, 1) both;
        animation-delay: 0.2s;
    }
    @keyframes slideUpFade {
        from { opacity: 0; transform: translateY(30px); }
        to { opacity: 1; transform: translateY(0); }
    }
    @keyframes popIn {
        from { opacity: 0; transform: scale(0.95); }
        to { opacity: 1; transform: scale(1); }
    }
    .galaxy-stat-card {
        display: grid;
        grid-template-columns: 2.5rem minmax(0, 1fr) minmax(10rem, 13rem);
        align-items: center;
        gap: 0.8rem;
        margin: 0.55rem 0;
        padding: 0.8rem 1rem;
        border: 1px solid var(--qcl-border, rgba(235,231,220,0.13));
        border-radius: 0.8rem;
        background:
            linear-gradient(105deg, rgba(138,43,226,0.08), transparent 42%),
            var(--qcl-surface, #15171a);
        transition: transform 180ms ease, border-color 180ms ease,
                    box-shadow 180ms ease;
    }
    .galaxy-stat-card:hover {
        transform: translateX(4px);
        border-color: rgba(0,191,255,0.45);
        box-shadow: 0 8px 26px rgba(0,0,0,0.22);
    }
    .galaxy-rank {
        font-size: 0.9rem;
        font-weight: 950;
        text-align: center;
    }
    .galaxy-player-name {
        overflow: hidden;
        color: #fff;
        font-size: 0.98rem;
        font-weight: 850;
        text-overflow: ellipsis;
        white-space: nowrap;
    }
    .galaxy-player-meta {
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: 0.32rem;
        margin-top: 0.18rem;
        color: #92979d;
        font-size: 0.68rem;
    }
    .galaxy-tier-badge, .galaxy-archetype {
        display: inline-block;
        padding: 0.13rem 0.34rem;
        border: 1px solid;
        border-radius: 99px;
        font-size: 0.58rem;
        font-weight: 850;
        letter-spacing: 0.03em;
        text-transform: uppercase;
    }
    .galaxy-archetype {
        color: #cbd5e1;
        border-color: rgba(203,213,225,0.2);
        background: rgba(203,213,225,0.06);
    }
    .galaxy-stat-chips {
        display: flex;
        flex-wrap: wrap;
        gap: 0.3rem;
        margin-top: 0.42rem;
    }
    .galaxy-stat-chip {
        padding: 0.2rem 0.38rem;
        border-radius: 0.3rem;
        color: #aeb3b7;
        background: rgba(255,255,255,0.055);
        font-size: 0.64rem;
    }
    .galaxy-stat-chip b {
        color: #e6bf55;
        font-weight: 850;
    }
    .galaxy-card-value {
        min-width: 0;
        text-align: right;
    }
    .galaxy-stat-label {
        color: #92979d;
        font-size: 0.6rem;
        font-weight: 850;
        letter-spacing: 0.12em;
        text-transform: uppercase;
    }
    .galaxy-stat-value {
        margin-top: 0.1rem;
        color: #fff;
        font-size: 1.35rem;
        font-weight: 950;
        letter-spacing: -0.04em;
    }
    .galaxy-percentile-track {
        height: 0.24rem;
        margin-top: 0.28rem;
        overflow: hidden;
        border-radius: 99px;
        background: rgba(255,255,255,0.08);
    }
    .galaxy-percentile-track span {
        display: block;
        height: 100%;
        border-radius: inherit;
        animation: slideIn 0.8s ease-out both;
    }
    @keyframes slideIn {
        from { width: 0%; opacity: 0.3; }
        to { opacity: 1; }
    }
    @media (max-width: 700px) {
        .galaxy-stat-card {
            grid-template-columns: 2rem minmax(0, 1fr);
        }
        .galaxy-card-value {
            grid-column: 2;
            text-align: left;
        }
    }
</style>
""", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# QCL visual system
#
# The dashboard's calculations and page renderers intentionally stay below this
# block.  These rules are presentation-only so the existing navigation,
# filters, downloads, login, cards, and analytics keep their current behavior.
# -----------------------------------------------------------------------------
st.markdown("""
<style>
    :root {
        --qcl-bg: #0d0e10;
        --qcl-surface: #15171a;
        --qcl-surface-2: #1b1e22;
        --qcl-border: rgba(235, 231, 220, 0.13);
        --qcl-border-strong: rgba(235, 231, 220, 0.24);
        --qcl-ink: #f1eee7;
        --qcl-muted: #92979d;
        --qcl-faint: #5e646b;
        --qcl-accent: #e6bf55;
        --qcl-accent-soft: #f3da8b;
        --qcl-positive: #58d39a;
        --qcl-negative: #ef7277;
    }

    html, body, [class*="css"] {
        font-family: Inter, ui-sans-serif, system-ui, -apple-system,
            BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    .stApp {
        position: relative;
        isolation: isolate;
        background:
            radial-gradient(circle at 82% -10%, rgba(230, 191, 85, 0.08), transparent 29rem),
            radial-gradient(circle at 12% 78%, rgba(0, 191, 255, 0.08), transparent 26rem),
            radial-gradient(circle at 8% 30%, rgba(255, 255, 255, 0.025), transparent 24rem),
            var(--qcl-bg) !important;
        color: var(--qcl-ink) !important;
    }

    /* Ambient motion layer: animated, low-contrast, and always behind the UI. */
    .stApp::before,
    .stApp::after {
        content: "";
        position: fixed;
        inset: -18%;
        pointer-events: none;
        z-index: 0;
    }

    .stApp::before {
        background:
            radial-gradient(circle at 18% 18%, rgba(230, 191, 85, 0.12), transparent 18rem),
            radial-gradient(circle at 84% 20%, rgba(0, 191, 255, 0.09), transparent 22rem),
            radial-gradient(circle at 52% 92%, rgba(47, 128, 237, 0.08), transparent 20rem);
        filter: blur(12px);
        opacity: 0.8;
        animation: qcl-ambient-drift 22s ease-in-out infinite alternate;
    }

    .stApp::after {
        inset: 0;
        opacity: 0.17;
        background-image:
            linear-gradient(rgba(230, 191, 85, 0.13) 1px, transparent 1px),
            linear-gradient(90deg, rgba(0, 191, 255, 0.12) 1px, transparent 1px),
            radial-gradient(circle at 50% 0%, rgba(255, 255, 255, 0.10), transparent 38%);
        background-size: 54px 54px, 54px 54px, 100% 100%;
        mask-image: linear-gradient(to bottom, black, transparent 82%);
        animation: qcl-grid-drift 32s linear infinite;
    }

    .stApp > *,
    [data-testid="stAppViewContainer"],
    [data-testid="stHeader"],
    [data-testid="stSidebar"] {
        position: relative;
        z-index: 1;
    }

    @keyframes qcl-ambient-drift {
        0% { transform: translate3d(-2%, -1%, 0) scale(1); }
        50% { transform: translate3d(2%, 1.5%, 0) scale(1.05); }
        100% { transform: translate3d(-1%, 3%, 0) scale(1.02); }
    }

    @keyframes qcl-grid-drift {
        0% { background-position: 0 0, 0 0, 0 0; }
        100% { background-position: 54px 54px, 54px 54px, 0 0; }
    }

    header[data-testid="stHeader"] {
        background: rgba(13, 14, 16, 0.7) !important;
    }

    #MainMenu, footer { visibility: hidden; }

    .block-container {
        max-width: 1560px !important;
        padding: 2.4rem 3.4rem 4.5rem !important;
    }

    [data-testid="stSidebar"] {
        background: #111316 !important;
        border-right: 1px solid var(--qcl-border) !important;
    }

    [data-testid="stSidebar"] > div:first-child {
        padding: 1.4rem 1rem 1.5rem !important;
    }

    [data-testid="stSidebar"] .block-container {
        padding: 0 !important;
    }

    .sidebar-brand {
        padding: 0.35rem 0.55rem 1.25rem;
        border-bottom: 1px solid var(--qcl-border);
        margin-bottom: 1.1rem;
    }

    .sidebar-brand__mark {
        color: var(--qcl-accent);
        font-size: 0.68rem;
        letter-spacing: 0.18em;
        text-transform: uppercase;
        font-weight: 800;
        margin-bottom: 0.42rem;
    }

    .sidebar-brand__title {
        color: var(--qcl-ink);
        font-size: 1.22rem;
        line-height: 1;
        letter-spacing: -0.04em;
        font-weight: 800;
    }

    .sidebar-brand__sub {
        color: var(--qcl-muted);
        font-size: 0.7rem;
        margin-top: 0.45rem;
    }

    [data-testid="stSidebar"] .stRadio > label,
    [data-testid="stSidebar"] .stSelectbox > label,
    [data-testid="stSidebar"] .stSlider > label,
    [data-testid="stSidebar"] .stTextInput > label {
        color: var(--qcl-muted) !important;
        font-size: 0.64rem !important;
        font-weight: 800 !important;
        letter-spacing: 0.12em !important;
        text-transform: uppercase !important;
    }

    [data-testid="stSidebar"] .stRadio > div {
        gap: 0.22rem !important;
    }

    [data-testid="stSidebar"] .stRadio [role="radiogroup"] > label {
        border: 1px solid transparent;
        border-radius: 0.6rem;
        color: #aeb3b7 !important;
        margin: 0 !important;
        min-height: 2.05rem;
        padding: 0.38rem 0.62rem !important;
        transition: background 160ms ease, border-color 160ms ease, color 160ms ease;
    }

    [data-testid="stSidebar"] .stRadio [role="radiogroup"] > label:hover {
        background: rgba(230, 191, 85, 0.08);
        border-color: rgba(230, 191, 85, 0.18);
        color: var(--qcl-ink) !important;
    }

    [data-testid="stSidebar"] .stRadio [role="radiogroup"] > label:has(input:checked) {
        background: rgba(230, 191, 85, 0.13);
        border-color: rgba(230, 191, 85, 0.42);
        color: var(--qcl-accent-soft) !important;
        box-shadow: inset 3px 0 0 var(--qcl-accent);
    }

    [data-testid="stSidebar"] .stRadio [role="radiogroup"] > label > div:first-child {
        display: none;
    }

    [data-testid="stSidebar"] [data-testid="stExpander"] {
        background: rgba(255, 255, 255, 0.025);
        border-color: var(--qcl-border);
        border-radius: 0.65rem;
    }

    [data-testid="stSidebar"] .stButton > button {
        min-height: 2.25rem;
    }

    .header-banner {
        background:
            linear-gradient(115deg, rgba(230, 191, 85, 0.13), transparent 58%),
            var(--qcl-surface) !important;
        border: 1px solid var(--qcl-border) !important;
        border-left: 3px solid var(--qcl-accent) !important;
        border-radius: 0.9rem !important;
        color: var(--qcl-ink) !important;
        font-family: Inter, ui-sans-serif, system-ui, sans-serif !important;
        font-size: clamp(1.15rem, 2vw, 1.75rem) !important;
        font-weight: 800 !important;
        letter-spacing: -0.035em !important;
        line-height: 1.2 !important;
        margin: 0 0 1.3rem !important;
        padding: 1.15rem 1.4rem !important;
        text-align: left !important;
        text-transform: none !important;
    }

    .stMarkdown h1, .stMarkdown h2, .stMarkdown h3,
    [data-testid="stHeader"] h1, [data-testid="stHeader"] h2 {
        color: var(--qcl-ink) !important;
        letter-spacing: -0.045em !important;
    }

    .stMarkdown h2, .stMarkdown h3 {
        margin-top: 1.15rem !important;
    }

    [data-testid="stMetric"] {
        background: var(--qcl-surface) !important;
        border: 1px solid var(--qcl-border) !important;
        border-radius: 0.8rem !important;
        padding: 0.95rem 1.05rem !important;
    }

    [data-testid="stMetricLabel"] {
        color: var(--qcl-muted) !important;
        font-size: 0.64rem !important;
        font-weight: 800 !important;
        letter-spacing: 0.1em !important;
        text-transform: uppercase !important;
    }

    [data-testid="stMetricValue"] {
        color: var(--qcl-ink) !important;
        font-weight: 800 !important;
        letter-spacing: -0.04em !important;
    }

    .metric-box, .line-box, .sim-box, .award-card {
        background: var(--qcl-surface) !important;
        border: 1px solid var(--qcl-border) !important;
        border-radius: 0.8rem !important;
        box-shadow: none !important;
    }

    .metric-box { border-left: 3px solid var(--qcl-accent) !important; }
    .metric-title, .line-label { color: var(--qcl-muted) !important; }
    .metric-value, .line-value { color: var(--qcl-ink) !important; }

    .stButton > button, .stDownloadButton > button {
        background: transparent !important;
        border: 1px solid var(--qcl-border-strong) !important;
        border-radius: 0.55rem !important;
        color: var(--qcl-ink) !important;
        font-weight: 700 !important;
        min-height: 2.45rem;
        transition: background 160ms ease, border-color 160ms ease, transform 160ms ease;
    }

    .stButton > button:hover, .stDownloadButton > button:hover {
        background: rgba(230, 191, 85, 0.11) !important;
        border-color: var(--qcl-accent) !important;
        color: var(--qcl-accent-soft) !important;
        transform: translateY(-1px);
    }

    .stButton > button[kind="primary"] {
        background: var(--qcl-accent) !important;
        border-color: var(--qcl-accent) !important;
        color: #17130b !important;
    }

    .stTextInput input, .stNumberInput input, .stSelectbox div[data-baseweb="select"],
    .stMultiSelect div[data-baseweb="select"], .stDateInput input {
        background: var(--qcl-surface) !important;
        border-color: var(--qcl-border-strong) !important;
        border-radius: 0.55rem !important;
        color: var(--qcl-ink) !important;
    }

    [data-baseweb="popover"], [data-baseweb="menu"] {
        background: #1c1f23 !important;
    }

    [data-baseweb="tab-list"] {
        border-bottom: 1px solid var(--qcl-border) !important;
        gap: 1.2rem !important;
    }

    [data-baseweb="tab"] {
        color: var(--qcl-muted) !important;
        font-weight: 700 !important;
        padding: 0.7rem 0.1rem !important;
    }

    [aria-selected="true"][data-baseweb="tab"] {
        color: var(--qcl-accent-soft) !important;
        border-bottom-color: var(--qcl-accent) !important;
    }

    .sleek-table {
        background: var(--qcl-surface) !important;
        border: 1px solid var(--qcl-border);
        border-radius: 0.8rem !important;
    }

    .sleek-table th {
        background: var(--qcl-surface-2) !important;
        border-bottom: 1px solid var(--qcl-border) !important;
        color: var(--qcl-accent-soft) !important;
        font-size: 0.65rem !important;
        letter-spacing: 0.08em;
    }

    .sleek-table td { border-bottom-color: rgba(235, 231, 220, 0.08) !important; }
    .sleek-table tr:hover { background: rgba(230, 191, 85, 0.05) !important; }

    .qcl-tools-hero {
        position: relative;
        overflow: hidden;
        padding: 1.35rem 1.5rem;
        margin: 0.2rem 0 1.2rem;
        border: 1px solid rgba(230, 191, 85, 0.25);
        border-radius: 1rem;
        background:
            linear-gradient(120deg, rgba(230, 191, 85, 0.14), transparent 45%),
            linear-gradient(300deg, rgba(0, 191, 255, 0.10), transparent 50%),
            var(--qcl-surface);
        box-shadow: 0 18px 55px rgba(0, 0, 0, 0.2);
    }

    .qcl-tools-hero::after {
        content: "";
        position: absolute;
        width: 14rem;
        height: 14rem;
        right: -4rem;
        top: -7rem;
        border: 1px solid rgba(230, 191, 85, 0.35);
        border-radius: 50%;
        box-shadow: 0 0 0 18px rgba(230, 191, 85, 0.04),
                    0 0 0 36px rgba(0, 191, 255, 0.06);
        animation: qcl-orbit 10s linear infinite;
    }

    .qcl-tools-kicker {
        color: var(--qcl-accent);
        font-size: 0.67rem;
        font-weight: 900;
        letter-spacing: 0.16em;
        text-transform: uppercase;
    }

    .qcl-tools-title {
        margin: 0.35rem 0 0.35rem;
        color: var(--qcl-ink);
        font-size: clamp(1.6rem, 3vw, 2.5rem);
        font-weight: 900;
        letter-spacing: -0.06em;
    }

    .qcl-tools-copy {
        max-width: 48rem;
        margin: 0;
        color: var(--qcl-muted);
        line-height: 1.55;
    }

    .qcl-tool-card {
        position: relative;
        overflow: hidden;
        min-height: 7.5rem;
        padding: 1rem 1.1rem;
        border: 1px solid var(--qcl-border);
        border-radius: 0.85rem;
        background: linear-gradient(145deg, rgba(255,255,255,0.045), rgba(255,255,255,0.015));
        transition: transform 180ms ease, border-color 180ms ease, background 180ms ease;
    }

    .qcl-tool-card:hover {
        transform: translateY(-3px);
        border-color: rgba(230, 191, 85, 0.5);
        background: linear-gradient(145deg, rgba(230,191,85,0.10), rgba(0,191,255,0.04));
    }

    .qcl-tool-card::before {
        content: "";
        position: absolute;
        left: -35%;
        top: 0;
        width: 30%;
        height: 2px;
        background: linear-gradient(90deg, var(--qcl-accent), #00bfff);
        box-shadow: 0 0 18px rgba(230, 191, 85, 0.8);
        animation: qcl-scan 4.5s ease-in-out infinite;
    }

    .qcl-tool-label {
        color: var(--qcl-muted);
        font-size: 0.65rem;
        font-weight: 800;
        letter-spacing: 0.1em;
        text-transform: uppercase;
    }

    .qcl-tool-value {
        margin-top: 0.3rem;
        color: var(--qcl-ink);
        font-size: 1.55rem;
        font-weight: 900;
        letter-spacing: -0.04em;
    }

    .qcl-tool-meta {
        color: var(--qcl-faint);
        font-size: 0.75rem;
        margin-top: 0.25rem;
    }

    .qcl-compare-row {
        display: grid;
        grid-template-columns: 1fr 5rem 1fr;
        align-items: center;
        gap: 0.75rem;
        margin: 0.75rem 0;
    }

    .qcl-compare-row .bar {
        height: 0.4rem;
        overflow: hidden;
        border-radius: 99px;
        background: rgba(255,255,255,0.08);
    }

    .qcl-compare-row .bar > span {
        display: block;
        height: 100%;
        border-radius: inherit;
        background: linear-gradient(90deg, var(--qcl-accent), var(--qcl-accent-soft));
        animation: qcl-bar-in 700ms cubic-bezier(.2,.8,.2,1) both;
        transform-origin: left center;
    }

    .qcl-compare-row .right .bar > span {
        margin-left: auto;
        background: linear-gradient(90deg, #00bfff, #2f80ed);
        transform-origin: right center;
    }

    .qcl-compare-name {
        overflow: hidden;
        color: var(--qcl-ink);
        font-size: 0.75rem;
        font-weight: 800;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    .qcl-compare-name.right { text-align: right; }

    .qcl-compare-stat {
        color: var(--qcl-muted);
        font-size: 0.64rem;
        font-weight: 800;
        letter-spacing: 0.08em;
        text-align: center;
        text-transform: uppercase;
    }

    @keyframes qcl-orbit {
        from { transform: rotate(0deg) translateX(0); }
        to { transform: rotate(360deg) translateX(0); }
    }

    @keyframes qcl-scan {
        0%, 15% { left: -35%; opacity: 0; }
        30% { opacity: 1; }
        70%, 100% { left: 110%; opacity: 0; }
    }

    @keyframes qcl-bar-in {
        from { transform: scaleX(0); opacity: 0.2; }
        to { transform: scaleX(1); opacity: 1; }
    }

    @media (prefers-reduced-motion: reduce) {
        .stApp::before, .stApp::after, .qcl-tools-hero::after,
        .qcl-tool-card::before, .qcl-compare-row .bar > span {
            animation: none !important;
        }
        .qcl-tool-card { transition: none; }
    }

    [data-testid="stDataFrame"] {
        border: 1px solid var(--qcl-border);
        border-radius: 0.8rem;
        overflow: hidden;
    }

    [data-testid="stAlert"] {
        border-radius: 0.7rem !important;
        border-color: var(--qcl-border) !important;
    }

    @media (max-width: 900px) {
        .block-container { padding: 1.25rem 1rem 3rem !important; }
        .header-banner { margin-top: 0.4rem !important; }
    }
</style>
""", unsafe_allow_html=True)




# =============================================================================
def _render_news():
    """Public landing page; works even when the new season has no stats."""
    st.title("📰 News & Updates")
    st.caption("QCL announcements, registration updates, and league notices.")
    news = _hub_json(_hub_path("QCL_NEWS_PATH", "qcl_news.json"))
    posts = news.get("posts", [])
    if not isinstance(posts, list):
        posts = []
    posts = [p for p in posts if isinstance(p, dict) and p.get("title") and p.get("body")]
    posts.sort(key=lambda p: str(p.get("date", "")), reverse=True)
    if posts:
        for post in posts:
            with st.container(border=True):
                st.subheader(str(post["title"]))
                if post.get("date"):
                    st.caption(str(post["date"]))
                st.markdown(str(post["body"]))
                link = str(post.get("url") or "")
                if link.startswith("https://"):
                    st.link_button("Read more", link)
    else:
        st.info("No announcements have been posted yet. Check back for league and season updates.")

    if st.checkbox("Show registration snapshot", value=False):
        approved = _official_records()
        if approved:
            st.divider()
            st.subheader("Registration snapshot")
            gm_count = sum(r.get("role") in {"byot_gm", "draft_gm"} for r in approved)
            player_count = sum(r.get("role") == "draft_player" for r in approved)
            c1, c2 = st.columns(2)
            c1.metric("Approved GM teams", gm_count)
            c2.metric("Approved draft players", player_count)
        else:
            st.caption("No approved registrations are available yet.")
    st.caption("Use Quick access or the sidebar to browse. Discord linking is optional; approved members can access their desk.")


st.sidebar.markdown("""
<div class="sidebar-brand">
    <div class="sidebar-brand__mark">QSPN / ANALYTICS</div>
    <div class="sidebar-brand__title">QCL League Hub</div>
    <div class="sidebar-brand__sub">The league, at a glance.</div>
</div>
""", unsafe_allow_html=True)
VIEWS = [
    "📰 News & Updates",
    "📱 Mobile Hub",
    "📝 Register",
    "🏠 League Home & Awards",
    "Film Terminal",
    "🌌 Player Galaxy",
    "🏅 Awards & Rewards",
    "🏆 Power Rankings & SOS",
    "🏢 Franchise Hub",
    "🛡️ League Teams",
    "🔦 Player Spotlight",
    "🗃️ Full Player Database",
    "⚔️ Head-to-Head Radar",
    "🧪 Lineup Lab",
    "🥊 Rivalry Corner",
    "🏆 Playoffs",
    "🔮 Oracle Predictor",
    "🔬 Advanced Analytics Lab",
    "🏦 The Vault",
    "📈 Card Market",
    "🎴 Qwiks TCG",
    "👤 My Profile",
    "🎁 Open Packs",
    "🃏 Player Cards",
    "💬 Discord",
    "📖 Record Book & Milestones",
]
if _viewer_access["role"] == "gm":
    VIEWS.insert(3, "🏢 GM Desk")
elif _viewer_access["role"] == "player":
    VIEWS.insert(3, "👥 Players Desk")
st.sidebar.caption("EXPLORE")
view_mode = st.sidebar.radio("Navigation", VIEWS, label_visibility="collapsed", key="qcl_nav")
try:
    login_widget(key="sidebar")
except Exception:
    pass
st.sidebar.divider()
if os.path.exists("Logo.png"):
    st.sidebar.image("Logo.png", width=140)


def _open_quick_page():
    selected = st.session_state["qcl_quick_page"]
    if selected in VIEWS:
        st.session_state["qcl_nav"] = selected


def _go_to_page(page):
    st.session_state["qcl_nav"] = page


st.selectbox(
    "📱 Quick access — tap to open a page",
    ["Choose a page…"] + VIEWS,
    key="qcl_quick_page",
    on_change=_open_quick_page,
)
if view_mode == "📱 Mobile Hub":
    st.title("📱 QCL Mobile Hub")
    st.caption("Tap a page below. This menu and the News and Film pages open without loading league statistics.")
    for label in [
        "📰 News & Updates", "📝 Register", "Film Terminal", "🏠 League Home & Awards",
        "🛡️ League Teams", "🗃️ Full Player Database",
    ]:
        st.button(label, key=f"mobile_{label}", on_click=_go_to_page,
                  args=(label,), use_container_width=True)
    if _viewer_access["role"] == "gm":
        st.button("🏢 GM Desk", on_click=_go_to_page, args=("🏢 GM Desk",),
                  use_container_width=True)
    elif _viewer_access["role"] == "player":
        st.button("👥 Players Desk", on_click=_go_to_page, args=("👥 Players Desk",),
                  use_container_width=True)
    st.stop()
if view_mode == "📰 News & Updates":
    st.button("📱 Open Mobile Hub", on_click=_go_to_page,
              args=("📱 Mobile Hub",), use_container_width=True)
    _render_news()
    st.stop()
if view_mode == "📝 Register":
    _render_registration()
    st.stop()
if view_mode == "Film Terminal":
    from qcl_film import render as render_film_room
    render_film_room()
    st.stop()


# 3. DATA ENGINE
# =============================================================================
def basic_name_clean(raw_name):
    """Remove pure OCR junk only: edge pipes/whitespace and [bracket]/(paren) tags.
    Never alters casing or strips letters from the gamertag itself."""
    if pd.isna(raw_name) or not isinstance(raw_name, str):
        return raw_name
    n = re.sub(r'^[|\s]+|[|\s]+$', '', raw_name)
    n = re.sub(r'^\[.*?\]\s*|^\(.*?\)\s*', '', n)
    return n.strip()




def name_match_key(name):
    """OCR-tolerant identity key: strips leading I/l/| prefix runs (2K clan-tag
    misreads), folds l<->i confusion, case-insensitive. Grouping only — display
    names are never modified by this."""
    k = re.sub(r'^[|Il\s]+', '', str(name))
    return k.strip().lower().replace('l', 'i').replace(' ', '')




@st.cache_data(ttl=60, show_spinner="Pulling the league sheet...")
def load_data():
    try:
        from io import StringIO
        frames = []
        health = {}
        try:
            response = requests.get(URL, timeout=12)
            response.raise_for_status()
            live = pd.read_csv(StringIO(response.text))
            live.columns = live.columns.str.strip()
            if {'Player/Team', 'Team Name', 'Season', 'Game_ID', 'Type'}.issubset(live.columns):
                frames.append(live)
                health['Google Sheet'] = 'Available'
            else:
                health['Google Sheet'] = 'No usable game rows'
        except (requests.RequestException, pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeError):
            health['Google Sheet'] = 'Offline / season pending'

        # --- merge SPAM history (Seasons 1-6) if the CSV is in the repo ---
        try:
            _root = os.path.dirname(os.path.abspath(__file__))
        except NameError:
            _root = os.getcwd()
        _history_path = _cfg("QCL_HISTORY_CSV_PATH")
        for _spam_name in ([_history_path] if _history_path else []) + ["SPAM_Raw_Data_v2.csv", "spam_history.csv"]:
            if not _spam_name:
                continue
            _sp_path = _spam_name if os.path.isabs(_spam_name) else os.path.join(_root, _spam_name)
            if os.path.exists(_sp_path):
                try:
                    _sp = pd.read_csv(_sp_path)
                    _sp.columns = _sp.columns.str.strip()
                    if {'Player/Team', 'Team Name', 'Season', 'Game_ID', 'Type'}.issubset(_sp.columns):
                        # The named SPAM files use 1..6; an explicit history file
                        # may already contain QCL and namespaced SPAM seasons.
                        if not _history_path or _spam_name != _history_path:
                            _sp["Season"] = pd.to_numeric(_sp["Season"], errors="coerce") + 100
                        _sp = _sp[_sp["Season"].notna()]
                        if not _sp.empty:
                            frames.append(_sp)
                            health['Historical CSV'] = os.path.basename(_sp_path)
                            break
                except (OSError, ValueError, pd.errors.ParserError, pd.errors.EmptyDataError):
                    health['Historical CSV'] = 'Unreadable'
        if not frames:
            return {'df': pd.DataFrame(), 'health': health}
        df = pd.concat(frames, ignore_index=True)
        if df.empty:
            return {'df': df, 'health': health}
        df = df[df['Player/Team'] != 'Player/Team']
        df = df[df['Team Name'].notna()
                & (df['Team Name'].astype(str).str.strip() != '')
                & (df['Team Name'].astype(str) != '0')]
        health['Rows loaded'] = len(df)


        # --- TYPE NORMALIZATION ---
        raw_type = df['Type'].astype(str).str.strip().str.lower()
        total_name = df['Player/Team'].astype(str).str.strip().str.upper().isin(['TOTAL', 'TOTALS', 'TEAM TOTAL'])
        is_team_row = raw_type.isin(['team', 'total', 'team total', 'totals']) | total_name
        health['Players recovered (bad Type)'] = int((~is_team_row & (raw_type != 'player')).sum())
        df['Type'] = np.where(is_team_row, 'Team', 'Player')


        # --- CANONICAL NAMES ---
        df['Player/Team'] = df['Player/Team'].apply(basic_name_clean)
        p_names = df.loc[df['Type'] == 'Player', 'Player/Team'].dropna()
        counts = p_names.value_counts()
        canon = {}
        for name, cnt in counts.items():
            k = name_match_key(name)
            if k not in canon or cnt > canon[k][1]:
                canon[k] = (name, cnt)


        def to_canonical(n):
            if not isinstance(n, str):
                return n
            hit = canon.get(name_match_key(n))
            return hit[0] if hit else n


        df.loc[df['Type'] == 'Player', 'Player/Team'] = df.loc[df['Type'] == 'Player', 'Player/Team'].map(to_canonical)
        health['Name variants unified'] = int(sum(1 for n in counts.index if canon[name_match_key(n)][0] != n))


        req_cols = ['PTS', 'REB', 'AST', 'STL', 'BLK', 'FOULS', 'TO', 'FGA', 'FGM', '3PM', '3PA',
                    'FTA', 'FTM', 'OREB', 'DREB', 'MIN', 'Q1', 'Q2', 'Q3', 'Q4',
                    'Game_ID', 'Win', 'Season', 'Type', 'Team Name']
        for c in req_cols:
            if c not in df.columns:
                df[c] = 0
            if c not in ['Type', 'Team Name', 'Player/Team', 'Win']:
                df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)


        df['Win'] = pd.to_numeric(df['Win'], errors='coerce')   # keep NaN; derived from score later
        df['Game_ID'] = pd.to_numeric(df['Game_ID'], errors='coerce')


        # --- DROP rows with no game/season identity; cross-season-safe game key ---
        pre = len(df)
        df = df[df['Game_ID'].notna() & (df['Season'] > 0)]
        health['Rows dropped (no Game_ID/Season)'] = pre - len(df)
        df['GKey'] = df['Season'].astype(int).astype(str) + '-' + df['Game_ID'].astype(int).astype(str)
        df['Era'] = np.where(df['Season'] >= 100, 'SPAM', 'QCL')


        # --- DEDUPE double-entered rows (keep the fuller stat line) ---
        pre = len(df)
        df['_bulk'] = df['PTS'] + df['FGA'] + df['REB']
        df = (df.sort_values('_bulk')
                .drop_duplicates(subset=['Season', 'Game_ID', 'Team Name', 'Player/Team', 'Type'], keep='last')
                .drop(columns='_bulk')
                .sort_index())
        health['Duplicate rows removed'] = pre - len(df)


        df['PIE_Raw'] = (df['PTS'] + df['REB'] + df['AST'] + df['STL'] + df['BLK']) - (df['FGA'] * 0.5) - df['TO']
        df['Poss_Raw'] = df['FGA'] + 0.44 * df['FTA'] + df['TO']


        # --- HOLLINGER GAME SCORE (0.42*REB fallback when OREB/DREB not tracked) ---
        reb_term = np.where((df['OREB'] + df['DREB']) > 0,
                            0.7 * df['OREB'] + 0.3 * df['DREB'],
                            0.42 * df['REB'])
        df['Game_Score'] = (df['PTS'] + 0.4 * df['FGM'] - 0.7 * df['FGA'] - 0.4 * (df['FTA'] - df['FTM'])
                            + reb_term + df['STL'] + 0.7 * df['AST'] + 0.7 * df['BLK']
                            - 0.4 * df['FOULS'] - df['TO'])


        # --- TEAM TOTALS: recorded preferred; rebuilt from players as fallback ---
        key = ['Season', 'Game_ID', 'Team Name']
        players = df[df['Type'] == 'Player'].copy()
        recorded = df[df['Type'] == 'Team'].copy()
        recorded = recorded[recorded['PTS'] > 0].drop_duplicates(subset=key, keep='last')


        sum_cols = ['PTS', 'REB', 'AST', 'STL', 'BLK', 'FOULS', 'TO', 'FGA', 'FGM', '3PM', '3PA',
                    'FTA', 'FTM', 'OREB', 'DREB', 'Q1', 'Q2', 'Q3', 'Q4',
                    'Poss_Raw', 'PIE_Raw', 'Game_Score']
        rebuilt = players.groupby(key).agg({**{c: 'sum' for c in sum_cols},
                                            'Win': 'max', 'GKey': 'first'}).reset_index()
        rebuilt = rebuilt.merge(recorded[key].assign(_rec=1), on=key, how='left')
        rebuilt = rebuilt[rebuilt['_rec'].isna()].drop(columns=['_rec'])


        team_rows = pd.concat([recorded, rebuilt], ignore_index=True)
        team_rows['Type'] = 'Team'
        team_rows['Player/Team'] = team_rows['Team Name'].astype(str) + " TOTALS"
        health['Team totals (recorded / rebuilt)'] = f"{len(recorded)} / {len(rebuilt)}"


        # --- COVERAGE & CONSISTENCY ---
        q_sum = team_rows[['Q1', 'Q2', 'Q3', 'Q4']].sum(axis=1)
        has_q = q_sum > 0
        health['Quarter data coverage'] = f"{int(has_q.sum())}/{len(team_rows)} team-games"
        q_mismatch = int((has_q & (q_sum != team_rows['PTS'])).sum())
        if q_mismatch:
            health['⚠️ Quarters ≠ PTS'] = q_mismatch
        reb_split = df[df['Type'] == 'Player'][['OREB', 'DREB']].sum(axis=1) > 0
        health['OREB/DREB coverage'] = f"{int(reb_split.sum())}/{int((df['Type'] == 'Player').sum())} player rows"
        ft_cov = int((df.loc[df['Type'] == 'Player', 'FTA'] > 0).sum())
        health['FTA coverage'] = f"{ft_cov}/{int((df['Type'] == 'Player').sum())} player rows"


        # --- WIN RECONCILIATION: fill missing Win from head-to-head score ---
        n_teams = team_rows.groupby(['Season', 'Game_ID'])['Team Name'].transform('nunique')
        max_pts = team_rows.groupby(['Season', 'Game_ID'])['PTS'].transform('max')
        min_pts = team_rows.groupby(['Season', 'Game_ID'])['PTS'].transform('min')
        derived = pd.Series(np.where((n_teams == 2) & (max_pts != min_pts),
                                     (team_rows['PTS'] == max_pts).astype(float), np.nan),
                            index=team_rows.index)
        health['Wins derived from score'] = int((team_rows['Win'].isna() & derived.notna()).sum())
        team_rows['Win'] = team_rows['Win'].fillna(derived)


        win_map = team_rows.set_index(key)['Win']
        mapped = players.set_index(key).index.map(win_map)
        players['Win'] = players['Win'].fillna(pd.Series(mapped, index=players.index))
        df = pd.concat([players, team_rows], ignore_index=True)
        df['Win'] = pd.to_numeric(df['Win'], errors='coerce').fillna(0).apply(lambda x: 1 if x > 0 else 0)


        # --- ADVANCED RATINGS (PER GAME) ---
        p_mask = df['Type'].astype(str).str.lower() == 'player'
        team_poss = df[p_mask].groupby(['Season', 'Game_ID', 'Team Name'])['Poss_Raw'].transform('sum')
        df.loc[p_mask, 'USG_Game'] = np.where(team_poss > 0, df.loc[p_mask, 'Poss_Raw'] / team_poss * 100, 0)
        df['USG_Game'] = pd.to_numeric(df.get('USG_Game'), errors='coerce').fillna(0)
        df['ORtg_Game'] = np.where(df['Poss_Raw'] > 0, df['PTS'] / df['Poss_Raw'] * 100, 0)


        # --- PROXY STATS ---
        df['Game_Type'] = np.where(df['Game_ID'] >= 9000, 'Playoffs',
                                   np.where(df['Game_ID'] >= 8000, 'Tournament', 'Regular Season'))
        players_df = df[df['Type'].astype(str).str.lower() == 'player'].copy()
        players_df = players_df.sort_values(by=['Season', 'Game_ID', 'Team Name'])
        players_df['Position_Num'] = players_df.groupby(['Season', 'Game_ID', 'Team Name']).cumcount() + 1


        players_df['Tipped_Passes'] = np.where(players_df['Position_Num'] <= 2,
                                               (players_df['STL'] * 2.2) + (players_df['FOULS'] * 0.4),
                                               (players_df['STL'] * 1.2) + (players_df['BLK'] * 0.2)
                                               ).round().astype(int)
        players_df['Shots_Affected'] = np.where(players_df['Position_Num'] >= 3,
                                                (players_df['BLK'] * 3.0) + (players_df['REB'] * 0.4) + (players_df['FOULS'] * 0.5),
                                                (players_df['BLK'] * 1.5) + (players_df['STL'] * 0.4)
                                                ).round().astype(int)
        players_df['FB_Points'] = np.where(players_df['Position_Num'] <= 2,
                                           (players_df['STL'] * 2.0) + (players_df['FGM'] * 0.4),
                                           (players_df['STL'] * 1.0) + (players_df['FGM'] * 0.1)
                                           ).round().astype(int)
        players_df['FB_Points'] = players_df[['FB_Points', 'PTS']].min(axis=1)


        df = df.merge(players_df[['Season', 'Game_ID', 'Team Name', 'Player/Team',
                                  'Tipped_Passes', 'Shots_Affected', 'FB_Points', 'Position_Num']],
                      on=['Season', 'Game_ID', 'Team Name', 'Player/Team'], how='left')
        for c in ['Tipped_Passes', 'Shots_Affected', 'FB_Points', 'Position_Num']:
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)


        t_proxy = (df[df['Type'] == 'Player']
                   .groupby(['Season', 'Game_ID', 'Team Name'])[['Tipped_Passes', 'Shots_Affected', 'FB_Points']]
                   .sum().reset_index())
        for col in ['Tipped_Passes', 'Shots_Affected', 'FB_Points']:
            df.loc[df['Type'] == 'Team', col] = (df.loc[df['Type'] == 'Team']
                                                 .set_index(['Season', 'Game_ID', 'Team Name']).index
                                                 .map(t_proxy.set_index(['Season', 'Game_ID', 'Team Name'])[col])
                                                 ).fillna(0)


        # --- MATCHUP LOGIC & SOS ---
        t_logs = df[df['Type'] == 'Team'][['Game_ID', 'Team Name', 'PTS', 'FGM', 'FGA', '3PM', '3PA',
                                           'TO', 'FTA', 'Win', 'Season']].copy()
        t_logs['Team_Win_Pct'] = t_logs.groupby(['Season', 'Team Name'])['Win'].transform('mean')


        n_in_game = t_logs.groupby(['Season', 'Game_ID'])['Team Name'].transform('nunique')
        pairable = t_logs[n_in_game == 2]
        opps = pd.merge(pairable, pairable, on=['Season', 'Game_ID'], suffixes=('', '_Opp'))
        opps = opps[opps['Team Name'] != opps['Team Name_Opp']]
        opps = opps.drop_duplicates(subset=['Season', 'Game_ID', 'Team Name'])


        if not opps.empty:
            opps['Point_Diff'] = opps['PTS'] - opps['PTS_Opp']
            opps['Opp_Possessions'] = opps['FGA_Opp'] + (0.44 * opps['FTA_Opp']) + opps['TO_Opp']
            opps['Opp_PPP'] = np.where(opps['Opp_Possessions'] > 0, opps['PTS_Opp'] / opps['Opp_Possessions'], 0)
            opps['Opp_FG%'] = np.where(opps['FGA_Opp'] > 0, (opps['FGM_Opp'] / opps['FGA_Opp']) * 100, 0)
            df = pd.merge(df, opps[['Season', 'Game_ID', 'Team Name', 'Point_Diff', 'Opp_PPP', 'Opp_FG%',
                                    'Team Name_Opp', 'Team_Win_Pct_Opp', 'PTS_Opp']],
                          on=['Season', 'Game_ID', 'Team Name'], how='left')
            for src, dst in [('Point_Diff', 'Point_Diff'), ('Opp_PPP', 'Opp_PPP'),
                             ('Team_Win_Pct_Opp', 'SOS_Game'), ('Team Name_Opp', 'Opp_Name'),
                             ('PTS_Opp', 'Opp_PTS')]:
                df[dst] = df.groupby(['Season', 'Game_ID', 'Team Name'])[src].transform('first')
        else:
            # No pairable head-to-head games at all — still guarantee columns exist.
            df['Point_Diff'] = 0.0
            df['Opp_PPP'] = np.nan
            df['Opp_FG%'] = np.nan
            df['SOS_Game'] = np.nan
            df['Opp_Name'] = None
            df['Opp_PTS'] = np.nan


        return {'df': df, 'health': health}
    except Exception as e:
        return f"{type(e).__name__}: {e}"




_loaded = load_data()
if isinstance(_loaded, str):
    st.error(f"⚠️ DATA ERROR: {_loaded}")
    st.stop()


full_df = _loaded['df']
DATA_HEALTH = _loaded['health']


if full_df is None or full_df.empty:
    _closed_season_desk("No games are available in Google Sheets or the historical CSV yet. Season analytics are paused.")
    st.stop()




# =============================================================================
# 4. GLOBAL MILESTONE / CLUB TRACKER
# =============================================================================
@st.cache_data(ttl=60)
def build_clubs(df):
    gp = df[df['Type'].astype(str).str.lower() == 'player'].copy()
    season_totals = gp.groupby(['Player/Team', 'Season']).sum(numeric_only=True).reset_index()


    def calc_clubs(row):
        clubs = []
        if row['REB'] >= 40 and row['STL'] >= 40 and row['AST'] >= 40:
            clubs.append("40/40/40 Club")
        elif row['REB'] >= 30 and row['STL'] >= 30 and row['AST'] >= 30:
            clubs.append("30/30/30 Club")
        if row['PTS'] >= 300 and row['3PM'] >= 100:
            clubs.append("300 Pts / 100 3s")
        if row['PTS'] >= 100 and row['REB'] >= 100:
            clubs.append("100 Pts / 100 Reb")
        return clubs


    season_totals['Clubs'] = season_totals.apply(calc_clubs, axis=1)
    pc = season_totals.groupby('Player/Team')['Clubs'].agg(
        lambda x: [i for sub in x for i in sub if i]).reset_index()
    pc['Clubs'] = pc['Clubs'].apply(lambda x: sorted(set(x)))
    return pc




player_clubs = build_clubs(full_df)




# =============================================================================
# 5. RENDER HELPERS
# =============================================================================
def dl(df, label, fname, key):
    """Download button for any table."""
    st.download_button(label, df.to_csv(index=False).encode('utf-8'),
                       file_name=fname, mime='text/csv', key=key, use_container_width=True)




def norm(val, mx):
    if pd.isna(val) or pd.isna(mx):
        return 0
    return min(100, (max(0, val) / mx) * 100) if mx > 0 else 0




def fnum(v, default=0.0):
    """NaN-safe float for f-strings."""
    try:
        v = float(v)
        if np.isnan(v):
            v = default
    except (TypeError, ValueError):
        v = default
    return v




def draw_shot_profile(fgm, fga, tpm, tpa):
    twopm, twopa = fgm - tpm, fga - tpa
    three_pct = (tpm / tpa * 100) if tpa > 0 else 0
    two_pct = (twopm / twopa * 100) if twopa > 0 else 0
    return f"""
    <div style="display:flex; justify-content:space-between; margin-bottom:2px; font-size:11px; color:#aaa;"><span>Interior (2PT)</span><span>{two_pct:.1f}% ({int(twopm)}/{int(twopa)})</span></div>
    <div class="shot-bar-container"><div class="shot-bar-fill" style="width:{two_pct}%; background:#d4af37;"></div></div>
    <div style="display:flex; justify-content:space-between; margin-top:10px; margin-bottom:2px; font-size:11px; color:#aaa;"><span>Perimeter (3PT)</span><span>{three_pct:.1f}% ({int(tpm)}/{int(tpa)})</span></div>
    <div class="shot-bar-container"><div class="shot-bar-fill" style="width:{three_pct}%; background:#00bfff;"></div></div>
    """




def generate_sleek_box_score(df_game):
    df_game = df_game.sort_values(by='PTS', ascending=False)
    html = ("<table class='sleek-table'><tr><th>Player</th><th>PTS</th><th>REB</th><th>AST</th>"
            "<th>STL</th><th>BLK</th><th>FG</th><th>3PT</th><th>PIE</th></tr>")
    for _, r in df_game.iterrows():
        html += (f"<tr><td class='player-name'>{r['Player/Team']}</td><td>{int(r['PTS'])}</td>"
                 f"<td>{int(r['REB'])}</td><td>{int(r['AST'])}</td><td>{int(r['STL'])}</td>"
                 f"<td>{int(r['BLK'])}</td><td>{int(r['FGM'])}/{int(r['FGA'])}</td>"
                 f"<td>{int(r['3PM'])}/{int(r['3PA'])}</td><td>{r['PIE_Raw']:.1f}</td></tr>")
    return html + "</table>"




def render_podium(title, top3_df, stat_col):
    if len(top3_df) < 3:
        return f"<p style='color:#888;'>{title}: not enough players yet.</p>"
    p1, p2, p3 = top3_df.iloc[0], top3_df.iloc[1], top3_df.iloc[2]
    html = f"<div style='text-align:center; margin-bottom:10px;'><h3 style='color:#fff; text-transform:uppercase;'>{title}</h3></div>"
    html += "<div class='podium-container'>"
    html += f"<div class='podium podium-3'><div class='podium-name'>{p3['Player/Team']}</div><div class='podium-stat'>{p3[stat_col]:.0f}</div></div>"
    html += f"<div class='podium podium-1'><div class='podium-name'>{p1['Player/Team']}</div><div class='podium-stat'>{p1[stat_col]:.0f}</div></div>"
    html += f"<div class='podium podium-2'><div class='podium-name'>{p2['Player/Team']}</div><div class='podium-stat'>{p2[stat_col]:.0f}</div></div>"
    return html + "</div>"




def generate_2k_player_card(player_name, stats, rank=""):
    rank_badge = (f'<div style="position:absolute; top:-10px; right:-10px; background:#d4af37; color:#000; '
                  f'font-weight:bold; padding:8px; border-radius:50%; border:2px solid #fff; z-index:10;">#{rank}</div>'
                  if rank else "")
    clubs_html = ""
    if isinstance(stats.get('Clubs'), list) and stats['Clubs']:
        badges = "".join([f"<span class='chip'>{c}</span>" for c in stats['Clubs']])
        clubs_html = f"<div style='margin-top:10px; padding-top:8px; border-top:1px dashed #444; width:100%;'>{badges}</div>"


    def g(k):
        return fnum(stats.get(k, 0))


    try:
        _art = player_card_uri(player_name)
    except Exception:
        _art = ""
    try:
        _col = team_color(stats.get('Team', ''))
    except Exception:
        _col = GOLD
    _acc = player_accolades(player_name)
    _acc_html = ("<div style='margin-top:8px;'>"
                 + "".join(f"<span class='chip'>\U0001f3c5 {x}</span>" for x in _acc)
                 + "</div>") if _acc else ""
    _rar_name, _rar_col = card_rarity(player_name)
    _rar_badge = (f'<div style="position:absolute; top:-10px; left:-10px; background:{_rar_col}; '
                  f'color:#000; font-weight:900; font-size:10px; letter-spacing:1px; padding:5px 9px; '
                  f'border-radius:10px; border:2px solid #fff; z-index:10;">{_rar_name.upper()}</div>')
    if _art:
        _front = (f'<img src="{_art}" style="max-width:100%; max-height:340px; '
                  f'object-fit:contain; border-radius:8px;">')
    else:
        _hs = find_player_headshot_uri(player_name)
        _face_img = (f'<img src="{_hs}" style="width:110px; height:110px; border-radius:50%; '
                     f'object-fit:cover; border:2px solid #d4af37; margin-bottom:10px;">' if _hs else
                     '<img src="https://upload.wikimedia.org/wikipedia/commons/7/7c/Profile_avatar_placeholder_large.png" '
                     'style="width: 90px; border-radius: 50%; border: 2px solid #d4af37; margin-bottom: 10px;">')
        _front = (
            _face_img
            + f'<h3 style="margin: 0; color: white; font-size: 18px;">{player_name}</h3>'
            + f'<h2 style="color: #d4af37; margin-top: 5px; margin-bottom: 5px;">{g("PIE"):.1f} PIE</h2>'
            + f'{clubs_html}')


    return f'''<div class="flip-card" style="height: 380px;">
{rank_badge}
{_rar_badge}
<div class="flip-card-inner">
<div class="flip-card-front" style="border-color:{_col};">
{_front}
</div>
<div class="flip-card-back" style="border-color:{_col};">
<h4 style="color: #d4af37; border-bottom: 1px solid #333; padding-bottom: 3px; margin-top: 0; font-size: 14px;">Season Averages & Highs</h4>
<div class="stat-row"><span class="stat-label">GP</span> <span class="stat-val">{int(g('GP'))}</span></div>
<div class="stat-row"><span class="stat-label">PPG | RPG | APG</span> <span class="stat-val">{g('PTS'):.1f} | {g('REB'):.1f} | {g('AST'):.1f}</span></div>
<div class="stat-row"><span class="stat-label">SEASON HIGHS</span> <span class="stat-val" style="color:#fff;">{int(g('High_PTS'))}P | {int(g('High_REB'))}R | {int(g('High_AST'))}A</span></div>
<div class="stat-row"><span class="stat-label">CAREER HIGHS</span> <span class="stat-val" style="color:#d4af37;">{int(g('AT_High_PTS'))}P | {int(g('AT_High_REB'))}R | {int(g('AT_High_AST'))}A</span></div>
<div class="stat-row"><span class="stat-label">DEF HIGHS (SZN)</span> <span class="stat-val">{int(g('High_STL'))}S | {int(g('High_BLK'))}B</span></div>
<div class="stat-row"><span class="stat-label">FG% | 3P% | TS%</span> <span class="stat-val">{g('FG%'):.1f}% | {g('3P%'):.1f}% | {g('TS%'):.1f}%</span></div>
<div class="stat-row"><span class="stat-label">USG | NetRtg</span> <span class="stat-val">{g('USG'):.1f}% | {g('NetRtg'):+.1f}</span></div>
{_acc_html}
</div>
</div>
</div>'''




def generate_mini_leaderboard(title, df, stat_col, color=GOLD, top_n=5, name_col=None):
    html = (f"<div style='background:#1c2128; padding:15px; border-radius:8px; border-left:4px solid {color}; "
            f"margin-bottom:15px; box-shadow: 0 4px 6px rgba(0,0,0,0.3);'>")
    html += (f"<h3 style='margin-top:0; color:#fff; font-size:16px; border-bottom:1px dashed #444; "
             f"padding-bottom:8px; text-transform:uppercase;'>{title}</h3>")
    sorted_df = df.sort_values(by=stat_col, ascending=False).head(top_n)
    for i, (_, row) in enumerate(sorted_df.iterrows()):
        val = row[stat_col]
        if pd.isna(val):
            continue
        val_str = f"{int(val)}" if float(val) == int(val) else f"{val:.1f}"
        rank_color = "#ffd700" if i == 0 else "#888"
        name = row.get(name_col, 'Unknown') if name_col else row.get('Player/Team')
        if pd.isna(name) or str(name).strip() == '0':
            name = row.get('Team Name', 'Unknown')
        html += ("<div style='display:flex; justify-content:space-between; align-items:center; margin-bottom:6px; font-size:14px;'>"
                 f"<div><span style='color:{rank_color}; font-weight:bold; margin-right:8px;'>{i+1}.</span>"
                 f"<span style='color:#ddd; font-weight:bold;'>{name}</span></div>"
                 f"<span style='color:{color}; font-weight:bold;'>{val_str}</span></div>")
    return html + "</div>"




def draw_dynamic_radar(p1_name, r1_vals, p2_name, r2_vals, categories, title):
    r1_vals = list(r1_vals) + [r1_vals[0]]
    r2_vals = list(r2_vals) + [r2_vals[0]]
    cats = list(categories) + [categories[0]]
    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(r=r1_vals, theta=cats, fill='toself', name=p1_name,
                                  fillcolor='rgba(212, 175, 55, 0.4)', line=dict(color=GOLD, width=2)))
    fig.add_trace(go.Scatterpolar(r=r2_vals, theta=cats, fill='toself', name=p2_name,
                                  fillcolor='rgba(204, 0, 0, 0.4)', line=dict(color='#cc0000', width=2)))
    fig.update_layout(title=dict(text=title, font=dict(color='white', size=16)),
                      polar=dict(radialaxis=dict(visible=True, range=[0, 100], showticklabels=False, gridcolor='#444')),
                      showlegend=True, template="plotly_dark", height=380,
                      paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                      margin=dict(l=40, r=40, t=60, b=40))
    return fig




def american_odds(p):
    p = min(max(p, 0.01), 0.99)
    return f"-{int(round(100 * p / (1 - p)))}" if p >= 0.5 else f"+{int(round(100 * (1 - p) / p))}"




# =============================================================================
# 5.5 ASSETS + ROTATING SEASON CARD
# =============================================================================
try:
    _ASSET_BASE = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _ASSET_BASE = os.getcwd()
CARDS_DIR = os.path.join(_ASSET_BASE, "cards")
LOGOS_DIR = os.path.join(_ASSET_BASE, "logos")
_ASSET_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif")




def _asset_slug(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())




def _data_uri(path):
    try:
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext or 'png'}"
        with open(path, "rb") as fh:
            return f"data:{mime};base64," + base64.b64encode(fh.read()).decode("utf-8")
    except Exception:
        return ""




@st.cache_data(ttl=60)
def _load_card_meta():
    mf = os.path.join(CARDS_DIR, "meta.json")
    if os.path.exists(mf):
        try:
            with open(mf, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}




def _list_card_files():
    try:
        return [f for f in os.listdir(CARDS_DIR) if f.lower().endswith(_ASSET_EXT)]
    except Exception:
        return []




def find_player_card_uris(player):
    """Every custom card image tied to a player (via meta 'player' or filename)."""
    want = _asset_slug(player)
    if not want:
        return []
    meta = _load_card_meta()
    files = _list_card_files()
    stem_map = {os.path.splitext(f)[0]: f for f in files}
    uris, seen = [], set()
    for stem, info in meta.items():
        if _asset_slug(info.get("player", "")) == want and stem in stem_map:
            u = _data_uri(os.path.join(CARDS_DIR, stem_map[stem]))
            if u and u not in seen:
                seen.add(u); uris.append(u)
    for stem, f in stem_map.items():
        if want in _asset_slug(stem):
            u = _data_uri(os.path.join(CARDS_DIR, f))
            if u and u not in seen:
                seen.add(u); uris.append(u)
    return uris




def _logo_path(team):
    want = _asset_slug(team)
    if not want:
        return ""
    try:
        for lf in os.listdir(LOGOS_DIR):
            if lf.lower().endswith(_ASSET_EXT) and _asset_slug(os.path.splitext(lf)[0]) == want:
                return os.path.join(LOGOS_DIR, lf)
    except Exception:
        pass
    return ""




def find_team_logo_uri(team):
    brand = _brand_for_team(team)
    if brand.get("logo_url"):
        return brand["logo_url"]
    p = _logo_path(team)
    return _data_uri(p) if p else ""




@st.cache_data(ttl=300)
def _logo_accent(team):
    """Pull a representative accent colour straight from the team's logo."""
    p = _logo_path(team)
    if not p:
        return ""
    try:
        from PIL import Image
        from collections import Counter
        img = Image.open(p).convert("RGBA")
        img.thumbnail((72, 72))
        px = [q for q in img.getdata() if q[3] > 200]  # opaque pixels only
        if not px:
            return ""


        def vivid(q):
            r, g, b = q[0], q[1], q[2]
            mx, mn = max(r, g, b), min(r, g, b)
            return mx >= 40 and mn <= 220 and (mx - mn) >= 25   # skip black/white/gray


        pool = [q for q in px if vivid(q)] or px
        counts = Counter((q[0] // 24 * 24, q[1] // 24 * 24, q[2] // 24 * 24) for q in pool)
        r, g, b = counts.most_common(1)[0][0]
        return f"#{min(r+12,255):02x}{min(g+12,255):02x}{min(b+12,255):02x}"
    except Exception:
        return ""




HEADSHOTS_DIR = os.path.join(_ASSET_BASE, "headshots")




def find_player_headshot_uri(player):
    """A player's onboarding photo (headshots/<name>.png), or ''."""
    want = _asset_slug(player)
    if not want:
        return ""
    try:
        for f in os.listdir(HEADSHOTS_DIR):
            if f.lower().endswith(_ASSET_EXT) and _asset_slug(os.path.splitext(f)[0]) == want:
                return _data_uri(os.path.join(HEADSHOTS_DIR, f))
    except Exception:
        pass
    return ""




def player_season_lines(player):
    """Career + per-season average stat lines for a player, pulled from full_df."""
    d = full_df[(full_df['Type'].astype(str).str.lower() == 'player')
                & (full_df['Player/Team'] == player)]
    if d.empty:
        return []


    def line(frame, label):
        def m(c):
            return float(pd.to_numeric(frame[c], errors='coerce').mean()) if c in frame.columns else 0.0
        gp = int(frame['GKey'].nunique()) if 'GKey' in frame.columns else len(frame)
        team = ""
        if 'Team Name' in frame.columns and frame['Team Name'].notna().any():
            team = str(frame['Team Name'].dropna().iloc[-1])
        return {"label": label, "gp": gp, "team": team,
                "pts": m('PTS'), "reb": m('REB'), "ast": m('AST'),
                "stl": m('STL'), "blk": m('BLK'), "pie": m('PIE_Raw'), "tpm": m('3PM')}


    out = [line(d, "CAREER")]
    for s in sorted({int(x) for x in pd.to_numeric(d['Season'], errors='coerce').dropna().unique()},
                    reverse=True):
        out.append(line(d[pd.to_numeric(d['Season'], errors='coerce') == s], f"SEASON {s}"))
    return out




_ROT_TPL = r"""
<div id="rc">
  <div class="face"></div>
  <div class="panel">
    <div class="pl"></div>
    <div class="szn"></div>
    <div class="grid"></div>
    <div class="dots"></div>
  </div>
</div>
<style>
  #rc { display:flex; width:100%; height:__H__px; background:#0c0c0c; border:2px solid __ACC__;
        border-radius:16px; overflow:hidden; box-shadow:0 10px 30px rgba(0,0,0,.6);
        font-family:'Helvetica Neue',sans-serif; }
  #rc .face { flex:1; display:flex; align-items:center; justify-content:center;
              background:radial-gradient(circle at 30% 20%,#1a1a1a,#000); padding:12px; position:relative; }
  #rc .face img { max-width:100%; max-height:100%; object-fit:contain; border-radius:10px;
                  box-shadow:0 6px 20px rgba(0,0,0,.7); }
  #rc .face.gen { flex-direction:column; background:linear-gradient(145deg,#1c2128,#0a0a0a); }
  #rc .face.gen .lg { width:70px; height:70px; object-fit:contain; margin-bottom:10px; border-radius:8px; background:#111; }
  #rc .face.gen .hs { width:110px; height:110px; border-radius:50%; object-fit:cover; border:2px solid __ACC__; margin-bottom:10px; }
  #rc .face.gen .nm { color:#fff; font-size:26px; font-weight:900; text-align:center; padding:0 10px; }
  #rc .face.gen .pie { color:__ACC__; font-size:20px; font-weight:800; margin-top:6px; }
  #rc .panel { flex:1; display:flex; flex-direction:column; justify-content:center; padding:20px 24px;
               background:linear-gradient(145deg,#141414,#0a0a0a); border-left:1px solid #222; }
  #rc .pl { color:#fff; font-size:24px; font-weight:900; }
  #rc .szn { color:__ACC__; font-size:14px; font-weight:800; letter-spacing:2px; text-transform:uppercase; margin:2px 0 14px; }
  #rc .grid { display:grid; grid-template-columns:repeat(4,1fr); gap:10px; }
  #rc .grid.fade { animation:rcf .5s ease; }
  @keyframes rcf { from{opacity:0; transform:translateY(6px);} to{opacity:1; transform:none;} }
  #rc .c { background:#0d1117; border:1px solid #262b33; border-radius:8px; padding:8px 4px; text-align:center; }
  #rc .c .v { color:#fff; font-size:19px; font-weight:900; }
  #rc .c .l { color:#8b949e; font-size:10px; text-transform:uppercase; letter-spacing:1px; margin-top:2px; }
  #rc .dots { margin-top:14px; }
  #rc .dot { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:6px;
             background:#444; cursor:pointer; }
  #rc .dot.on { background:#d4af37; }
  #rc .acc { margin-top:10px; }
  #rc .acc .chip{display:inline-block;background:__ACC__;color:#000;font-size:10px;font-weight:800;padding:3px 8px;border-radius:10px;margin:2px 4px 0 0;}
  @media (max-width:640px){ #rc{flex-direction:column;} #rc .grid{grid-template-columns:repeat(4,1fr);} }
</style>
<script>
  const D = __DATA__;
  const SPEED = __SPEED__;
  const face = document.querySelector('#rc .face');
  if (D.imgs && D.imgs.length) {
    face.innerHTML = '<img src="' + D.imgs[0] + '">';
  } else {
    face.classList.add('gen');
    const s0 = D.seasons[0] || {pie:0};
    face.innerHTML = (D.headshot ? '<img class="hs" src="' + D.headshot + '">' : '')
      + (D.logo ? '<img class="lg" src="' + D.logo + '">' : '')
      + '<div class="nm">' + D.player + '</div>'
      + '<div class="pie">' + (s0.pie).toFixed(1) + ' PIE</div>';
  }
  if (D.rarity) { var _rb=document.createElement('div'); _rb.textContent=(D.rarity.name||'').toUpperCase(); _rb.style.cssText='position:absolute;top:10px;left:10px;background:'+D.rarity.color+';color:#000;font-weight:900;font-size:11px;letter-spacing:1px;padding:3px 9px;border-radius:10px;box-shadow:0 2px 6px rgba(0,0,0,.5);'; face.appendChild(_rb); }
  document.querySelector('#rc .pl').textContent = D.player;
  (function(){ var p=document.querySelector('#rc .panel'); var el=document.createElement('div'); el.className='acc'; el.innerHTML=(D.accolades||[]).map(function(x){return '<span class="chip">\ud83c\udfc5 '+x+'</span>';}).join(''); p.appendChild(el); })();
  const szn = document.querySelector('#rc .szn');
  const grid = document.querySelector('#rc .grid');
  const dots = document.querySelector('#rc .dots');
  let i = 0, t = null;
  function cell(l, v){ return '<div class="c"><div class="v">' + v + '</div><div class="l">' + l + '</div></div>'; }
  function render(){
    const s = D.seasons[i];
    szn.textContent = s.label + (s.team ? '  \u2022  ' + s.team : '');
    grid.innerHTML = cell('PPG', s.pts.toFixed(1)) + cell('RPG', s.reb.toFixed(1))
      + cell('APG', s.ast.toFixed(1)) + cell('STK', (s.stl + s.blk).toFixed(1))
      + cell('3PM', s.tpm.toFixed(1)) + cell('PIE', s.pie.toFixed(1)) + cell('GP', s.gp);
    grid.classList.remove('fade'); void grid.offsetWidth; grid.classList.add('fade');
    dots.innerHTML = D.seasons.map(function(_, k){
      return '<span class="dot ' + (k === i ? 'on' : '') + '" data-i="' + k + '"></span>'; }).join('');
    dots.querySelectorAll('.dot').forEach(function(d){
      d.onclick = function(){ i = +d.dataset.i; render(); reset(); }; });
  }
  function reset(){ clearInterval(t); if (D.seasons.length > 1){ t = setInterval(function(){ i = (i + 1) % D.seasons.length; render(); }, SPEED); } }
  render(); reset();
</script>
"""




def render_rotating_card(player, key="rc", team=None, height=440, speed_ms=4000):
    """Rotating card: art (if any) + a season-by-season stat panel that cycles."""
    seasons = player_season_lines(player)
    if not seasons:
        st.info("No season data for this player yet.")
        return
    _rn, _rc = card_rarity(player)
    data = {"player": player,
            "logo": find_team_logo_uri(team) if team else "",
            "imgs": find_player_card_uris(player),
            "headshot": find_player_headshot_uri(player),
            "seasons": seasons,
            "accolades": player_accolades(player),
            "rarity": {"name": _rn, "color": _rc}}
    accent = team_color(team) if team else GOLD
    html = (_ROT_TPL.replace("__DATA__", json.dumps(data))
                    .replace("__SPEED__", str(int(speed_ms)))
                    .replace("__H__", str(int(height)))
                    .replace("__ACC__", accent))
    components.html(html, height=int(height) + 30, scrolling=False)




def _cached_logo_uri(team):
    return find_team_logo_uri(team)




def team_logo_html(team, px=22, radius=4, ml=0, mr=6):
    """Inline <img> badge for a team wherever its name is shown ('' if no logo)."""
    u = _cached_logo_uri(team)
    if not u:
        return ""
    return (f"<img src='{u}' style='height:{px}px;width:{px}px;object-fit:contain;"
            f"vertical-align:middle;border-radius:{radius}px;margin-left:{ml}px;margin-right:{mr}px;'>")




@st.cache_data(ttl=120)
def player_card_uri(player):
    """First custom card image for a player, or '' — used on flip cards."""
    uris = find_player_card_uris(player)
    return uris[0] if uris else ""




@st.cache_data(ttl=60)
def _load_team_meta():
    tf = os.path.join(_ASSET_BASE, "teams.json")
    if os.path.exists(tf):
        try:
            with open(tf, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}




def _team_entry(team):
    m = _load_team_meta()
    if team in m:
        return m[team]
    s = _asset_slug(team)
    for k, v in m.items():
        if _asset_slug(k) == s:
            return v
    return {}




def team_color(team, default=GOLD):
    # explicit override (rare) wins; otherwise pull the accent from the logo.
    c = _team_entry(team).get("color")
    if isinstance(c, str) and c.strip():
        return c
    a = _logo_accent(team)
    return a if a else default




def team_full(team):
    return _brand_for_team(team).get("display_name") or _team_entry(team).get("full") or team




@st.cache_data(ttl=60)
def _load_allleague():
    f = os.path.join(_ASSET_BASE, "allleague.json")
    if os.path.exists(f):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}
    return {}




def player_accolades(player):
    data = _load_allleague()
    want, out = _asset_slug(player), []
    for season, teams in data.items():
        if not isinstance(teams, dict):
            continue
        for tier, players in teams.items():
            if isinstance(players, list) and any(_asset_slug(p) == want for p in players):
                out.append(f"{tier} (S{season})")
    return out




RARITY_TIERS = [(0.95, "Legendary", "#f1c40f"), (0.85, "Epic", "#9b59b6"),
                (0.65, "Rare", "#3498db"), (0.35, "Uncommon", "#2ecc71"),
                (0.0, "Common", "#8a929c")]




@st.cache_data(ttl=60)
def _career_ratings():
    """Percentile rank of each player's career impact (PIE) across the league."""
    d = full_df[full_df['Type'].astype(str).str.lower() == 'player']
    if d.empty:
        return {}
    g = d.groupby('Player/Team').agg(PIE=('PIE_Raw', 'mean')).reset_index()
    g['pct'] = g['PIE'].rank(pct=True)
    return dict(zip(g['Player/Team'], g['pct']))




def card_rarity(player):
    """(tier_name, hex_color) from career percentile — stat-based rarity."""
    pct = _career_ratings().get(player)
    if pct is None:
        return ("Common", "#8a929c")
    for thresh, name, color in RARITY_TIERS:
        if pct >= thresh:
            return (name, color)
    return ("Common", "#8a929c")




RARITY_SUPPLY = {"Legendary": 3, "Epic": 10, "Rare": 25, "Uncommon": 60, "Common": 0}  # 0 = unlimited
RARITY_COLOR_BY_NAME = {name: color for _thr, name, color in RARITY_TIERS}




@st.cache_data(ttl=45)
def _load_market():
    """Official serialized-card counts published by the bot (fantasy_market.json)."""
    f = os.path.join(_ASSET_BASE, "fantasy_market.json")
    if os.path.exists(f):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            return d.get("cards", {}) if isinstance(d, dict) else {}
        except Exception:
            return {}
    return {}




@st.cache_data(ttl=60)
def _load_popularity():
    f = os.path.join(_ASSET_BASE, "popularity.json")
    if os.path.exists(f):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}
    return {}




@st.cache_data(ttl=60)
def _awards_score(player):
    want, s = _asset_slug(player), 0.0
    for _season, teams in _load_allleague().items():
        if isinstance(teams, dict):
            for tier, players in teams.items():
                if isinstance(players, list) and any(_asset_slug(p) == want for p in players):
                    s += 3.0 if "1st" in tier else 2.0 if "2nd" in tier else 1.0
    for _stem, info in _load_card_meta().items():
        if _asset_slug(info.get("player", "")) == want:
            s += 2.0
    return s




@st.cache_data(ttl=60)
def _popularity_raw_map():
    pop = _load_popularity()
    mentions = pop.get("mentions", {}) if isinstance(pop, dict) else {}
    roles = pop.get("roles", {}) if isinstance(pop, dict) else {}
    players = set(_career_ratings().keys()) | set(mentions.keys()) | set(roles.keys())
    raw = {}
    for p in players:
        raw[p] = (_awards_score(p) * 2.0
                  + float(mentions.get(p, 0) or 0) * 1.0
                  + float(roles.get(p, 0) or 0) * 1.5)
    return raw




def player_popularity(player):
    raw = _popularity_raw_map()
    v = raw.get(player, _awards_score(player) * 2.0)
    mx = max(raw.values()) if raw else 0
    return (v / mx) if mx > 0 else 0.0




def player_price(player, w_stat=0.5, w_pop=0.5, cap=3.0):
    stat = _career_ratings().get(player, 0.0)
    pop = player_popularity(player)
    tot = max(w_stat + w_pop, 0.01)
    return round(cap * ((w_stat * stat) + (w_pop * pop)) / tot, 2)




def player_form(player):
    d = full_df[(full_df['Type'].astype(str).str.lower() == 'player')
                & (full_df['Player/Team'] == player)].sort_values(['Season', 'Game_ID'])
    pie = pd.to_numeric(d['PIE_Raw'], errors='coerce')
    if len(pie.dropna()) < 4:
        return 0
    recent, base = pie.tail(3).mean(), pie.mean()
    if recent > base * 1.05:
        return 1
    if recent < base * 0.95:
        return -1
    return 0




# ---- 3v3 FANTASY ENGINE --------------------------------------------------
TIER_COST = {"Legendary": 5, "Epic": 4, "Rare": 3, "Uncommon": 2, "Common": 1}
FANTASY_CAP = 9




def card_cost(tier):
    return TIER_COST.get(tier, 2)




def fantasy_points(s, role):
    """Positional fantasy points from a player's per-game averages + role."""
    def g(k):
        return fnum(s.get(k))
    pts, tpm, ast = g('PTS'), g('3PM'), g('AST')
    reb, stl, blk, to = g('REB'), g('STL'), g('BLK'), g('TO')
    fga, fgm = g('FGA'), g('FGM')
    miss = max(fga - fgm, 0)
    usg, ts = g('USG'), g('TS%')
    role = (role or '').lower()
    three_bonus = tpm * 2.0
    neg = to * (-1.5) + miss * (-0.5)


    if role.startswith('g'):        # Guard
        scoring = pts * (0.75 if usg > 30 else 1.0) + three_bonus     # usage tax
        fp = scoring + ast * 1.5 + reb * 2.0 + (stl + blk) * 4.0 + neg  # board buff, 2.0x def
    elif role.startswith('f'):      # Forward
        scoring = pts * 1.5 + three_bonus                             # premium touches
        fp = scoring + ast * 1.5 + reb * 1.0 + (stl + blk) * 5.0 + neg  # 2.5x defense
        if ts >= 60:
            fp += 3.0                                                 # efficiency lockout
    else:                           # Big
        scoring = pts * 1.5 + three_bonus
        fp = scoring + ast * 1.5 + reb * 0.75 + stl * 3.0 + blk * 1.5 + neg  # flipped weights
        if usg < 15 and ts > 65:
            fp += 5.0                                                 # ghost scorer
    return round(fp, 1)




ARCHETYPE_SIG = {
    "Microwave Chucker": "TPM", "Glass Cleaner": "REB", "Pocket Picker": "STL",
    "Rim Protector": "BLK", "Corner Specialist": "TP%", "Dime Dropper": "AST",
    "Combo Guard": "PTS", "Lockdown Wing": "DEF", "Iron Man Grind": "GP",
}




@st.cache_data(ttl=60)
def _archetype_map():
    d = full_df[full_df['Type'].astype(str).str.lower() == 'player']
    if d.empty:
        return {}
    g = d.groupby('Player/Team').agg(
        PTS=('PTS', 'mean'), REB=('REB', 'mean'), AST=('AST', 'mean'),
        STL=('STL', 'mean'), BLK=('BLK', 'mean'), TPM=('3PM', 'mean'),
        TPA=('3PA', 'mean'), GP=('GKey', 'nunique')).reset_index()
    g['DEF'] = g['STL'] + g['BLK']
    g['TP%'] = np.where(g['TPA'] >= 1.0, g['TPM'] / g['TPA'].replace(0, 1) * 100, 0)  # gate low volume
    ranks = {arch: g[col].rank(pct=True) for arch, col in ARCHETYPE_SIG.items()}
    out = {}
    for i in range(len(g)):
        best = max(ARCHETYPE_SIG.keys(), key=lambda a2: ranks[a2].iloc[i])
        out[g.iloc[i]['Player/Team']] = best
    return out




def player_archetype(player):
    return _archetype_map().get(player, "Combo Guard")




# =============================================================================
# 5.5 DERIVED PLAYER BOARD
# =============================================================================
def build_advanced_player_board(scope_df):
    """Build a player-level derived-stat board from the raw game rows."""
    p = scope_df[scope_df['Type'].astype(str).str.lower() == 'player'].copy()
    if p.empty:
        return pd.DataFrame()

    stat_sources = {
        'GP': ('GKey', 'nunique'),
        'Team': ('Team Name', 'last'),
        'PTS': ('PTS', 'mean'), 'REB': ('REB', 'mean'), 'AST': ('AST', 'mean'),
        'STL': ('STL', 'mean'), 'BLK': ('BLK', 'mean'), 'TO': ('TO', 'mean'),
        'FOULS': ('FOULS', 'mean'), 'OREB': ('OREB', 'mean'), 'DREB': ('DREB', 'mean'),
        'FGM': ('FGM', 'mean'), 'FGA': ('FGA', 'mean'),
        '3PM': ('3PM', 'mean'), '3PA': ('3PA', 'mean'),
        'FTM': ('FTM', 'mean'), 'FTA': ('FTA', 'mean'),
        'Poss': ('Poss_Raw', 'mean'), 'Game Score': ('Game_Score', 'mean'),
        'PIE': ('PIE_Raw', 'mean'), 'USG%': ('USG_Game', 'mean'),
        'ORtg': ('ORtg_Game', 'mean'),
        'Tipped Passes': ('Tipped_Passes', 'mean'),
        'Shots Affected': ('Shots_Affected', 'mean'),
        'FB Points': ('FB_Points', 'mean'),
    }
    agg = {out: spec for out, spec in stat_sources.items() if spec[0] in p.columns}
    board = p.groupby('Player/Team').agg(**agg).reset_index()

    def num(col):
        series = board[col] if col in board.columns else pd.Series(0, index=board.index)
        return pd.to_numeric(series, errors='coerce').fillna(0)

    board['2PM'] = num('FGM') - num('3PM')
    board['2PA'] = num('FGA') - num('3PA')
    board['2P%'] = np.where(board['2PA'] > 0, board['2PM'] / board['2PA'] * 100, 0)
    board['3P%'] = np.where(num('3PA') > 0, num('3PM') / num('3PA') * 100, 0)
    board['FT%'] = np.where(num('FTA') > 0, num('FTM') / num('FTA') * 100, 0)
    ts_den = 2 * (num('FGA') + 0.44 * num('FTA'))
    board['TS%'] = np.where(ts_den > 0, num('PTS') / ts_den * 100, 0)
    board['eFG%'] = np.where(num('FGA') > 0,
                             (num('FGM') + 0.5 * num('3PM')) / num('FGA') * 100, 0)
    board['Stocks'] = num('STL') + num('BLK')
    board['Disruption'] = num('Tipped Passes') + num('Shots Affected')
    board['Hustle'] = (num('Tipped Passes') + num('Shots Affected')
                       + num('FB Points') + num('OREB'))
    board['AST/TO'] = np.where(num('TO') > 0, num('AST') / num('TO'), num('AST'))
    board['Creation Load'] = num('FGA') + 0.44 * num('FTA') + 0.5 * num('AST')
    board['Impact Load'] = num('PTS') + 1.5 * num('AST') + num('REB') + 2 * board['Stocks']
    board['Type'] = board['Player/Team'].map(player_archetype)
    board['Rarity'] = board['Player/Team'].map(lambda x: card_rarity(x)[0])
    board['Team'] = board['Team'].fillna('—')

    numeric_cols = [c for c in board.columns if c not in ('Player/Team', 'Team', 'Type', 'Rarity')]
    board[numeric_cols] = board[numeric_cols].apply(pd.to_numeric, errors='coerce').fillna(0)
    board['GP'] = board['GP'].astype(int)
    return board.sort_values(['PIE', 'PTS'], ascending=False).reset_index(drop=True)


def _galaxy_tier_color(percentile):
    """Map a player's percentile to a visual tier for the Galaxy cards."""
    if percentile >= 0.95:
        return GOLD
    if percentile >= 0.85:
        return "#a855f7"
    if percentile >= 0.70:
        return BLUE
    return "#7f8c8d"


def _galaxy_stat_value(value, column):
    """Keep the card's headline value compact and consistent."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _html.escape(str(value))
    if column in {"TS%", "eFG%"}:
        return f"{number:.1f}%"
    if column == "GP":
        return f"{number:.0f}"
    return f"{number:.1f}"


def render_modern_stat_row(rank, name, team, stat_val, percentile, color,
                           stat_label, archetype="", secondary_stats=None):
    """Render one Galaxy player as a responsive, animated stat card."""
    pct_width = max(0, min(100, int(round(percentile * 100))))
    secondary_stats = secondary_stats or []
    chips = "".join(
        f"<span class='galaxy-stat-chip'><b>{_html.escape(str(label))}</b> "
        f"{_html.escape(str(value))}</span>"
        for label, value in secondary_stats
    )
    badge = (
        f"<span class='galaxy-tier-badge' style='color:{color};"
        f"border-color:{color}55;background:{color}18;'>"
        f"{pct_width}th percentile</span>"
    )
    archetype_html = (
        f"<span class='galaxy-archetype'>{_html.escape(str(archetype))}</span>"
        if archetype else ""
    )
    return (
        "<article class='galaxy-stat-card'>"
        f"<div class='galaxy-rank' style='color:{color};'>#{rank}</div>"
        "<div class='galaxy-player-copy'>"
        f"<div class='galaxy-player-name'>{_html.escape(str(name))}</div>"
        f"<div class='galaxy-player-meta'>{_html.escape(str(team))} "
        f"{badge}{archetype_html}</div>"
        f"<div class='galaxy-stat-chips'>{chips}</div>"
        "</div>"
        "<div class='galaxy-card-value'>"
        f"<div class='galaxy-stat-label'>{_html.escape(stat_label)}</div>"
        f"<div class='galaxy-stat-value'>{_html.escape(str(stat_val))}</div>"
        "<div class='galaxy-percentile-track'>"
        f"<span style='width:{pct_width}%;background:linear-gradient(90deg,{color},#00bfff);'></span>"
        "</div>"
        "</div>"
        "</article>"
    )


def render_modern_dataframe(frame, name_hint=None, max_rows=None):
    """Use the Galaxy card language for every tabular view in the app.

    The underlying dataframe remains available to downloads and calculations;
    this only changes the on-screen presentation from a spreadsheet to ranked,
    responsive cards.
    """
    if frame is None or frame.empty:
        st.info("No rows in this view.")
        return

    view = frame.copy()
    text_cols = [
        c for c in view.columns
        if not pd.api.types.is_numeric_dtype(view[c])
    ]
    name_candidates = [name_hint, "Player/Team", "Player", "Team Name", "Team"]
    name_col = next((c for c in name_candidates if c and c in view.columns), None)
    if name_col is None:
        name_col = text_cols[0] if text_cols else view.columns[0]

    numeric_cols = [
        c for c in view.columns
        if pd.api.types.is_numeric_dtype(view[c])
    ]
    preferred = [
        "PIE", "Win%", "PTS", "PROJ PTS", "Point_Diff", "NetRtg",
        "ORtg", "GmSc", "USG", "REB", "AST", "GP",
    ]
    primary_col = next((c for c in preferred if c in numeric_cols), None)
    if primary_col is None and numeric_cols:
        primary_col = numeric_cols[0]

    if max_rows:
        view = view.head(max_rows)

    if primary_col:
        values = pd.to_numeric(view[primary_col], errors="coerce").fillna(0)
        percentiles = values.rank(pct=True)
    else:
        percentiles = pd.Series(0.5, index=view.index)

    secondary_cols = [
        c for c in numeric_cols
        if c != primary_col and c not in ("_GalaxyPercentile",)
    ][:4]
    cards = []
    for rank, (_, row) in enumerate(view.iterrows(), start=1):
        team = row.get("Team", row.get("Team Name", ""))
        if pd.isna(team):
            team = "—"
        secondary = [
            (column, _galaxy_stat_value(row.get(column, 0), column))
            for column in secondary_cols
        ]
        stat_label = primary_col or "Record"
        stat_value = (
            _galaxy_stat_value(row.get(primary_col, 0), primary_col)
            if primary_col else "—"
        )
        percentile = float(percentiles.loc[row.name]) if primary_col else 0.5
        cards.append(
            render_modern_stat_row(
                rank=rank,
                name=row.get(name_col, f"Record {rank}"),
                team=team,
                stat_val=stat_value,
                percentile=percentile,
                color=_galaxy_tier_color(percentile),
                stat_label=stat_label,
                archetype=row.get("Type", row.get("Rarity", "")),
                secondary_stats=secondary,
            )
        )
    st.markdown("".join(cards), unsafe_allow_html=True)


# =============================================================================
# 6. SESSION STATE
# =============================================================================
if 'watchlist' not in st.session_state:
    st.session_state.watchlist = []




def toggle_watch(name):
    if name in st.session_state.watchlist:
        st.session_state.watchlist.remove(name)
    else:
        st.session_state.watchlist.append(name)




# =============================================================================
# 7. SIDEBAR / NAV
# =============================================================================
seasons = sorted([int(s) for s in full_df['Season'].dropna().unique() if int(s) > 0], reverse=True)
if not seasons:
    _closed_season_desk("No seasons with valid game data were found. GM and registration tools remain open.")
    st.stop()


def _season_label(s):
    return f"SPAM S{s - 100}" if s >= 100 else f"S{s}"


_qcl_seasons = sorted([s for s in seasons if s < 100], reverse=True)
_spam_seasons = sorted([s for s in seasons if s >= 100], reverse=True)
_ordered_seasons = _qcl_seasons + _spam_seasons
_season_labels = [_season_label(s) for s in _ordered_seasons]
_active_season = str(_cfg("QCL_ACTIVE_SEASON", "")).strip()
if _active_season:
    try:
        _active_number = int(_active_season)
        if _active_number not in _ordered_seasons:
            st.sidebar.warning(f"Season {_active_season} has not started or has no usable stats. Historical seasons remain available below.")
    except ValueError:
        st.sidebar.warning("QCL_ACTIVE_SEASON must be a number. Historical seasons remain available.")
_career_opts = ["Career (All-Time)"]
if _qcl_seasons and _spam_seasons:
    _career_opts += ["Career (QCL)", "Career (SPAM)"]
scope_opts = _season_labels + _career_opts
scope_choice = st.sidebar.selectbox("Data Scope", scope_opts, index=0)


game_type_opts = ["All Games", "Regular Season", "Playoffs", "Tournament"]
game_type = st.sidebar.selectbox("Game Type", game_type_opts, index=1)  # default: Regular Season (no playoffs)


min_gp_filter = st.sidebar.slider("Min Games Played (tables)", 0, 20, 1)


if st.sidebar.button("🔄 Refresh Sheet", use_container_width=True):
    st.cache_data.clear()
    _rerun()


if DATA_HEALTH:
    with st.sidebar.expander("🩺 Data Health"):
        for hk, hv in DATA_HEALTH.items():
            st.markdown(f"**{hk}:** {hv}")


if st.session_state.watchlist:
    with st.sidebar.expander(f"⭐ Watchlist ({len(st.session_state.watchlist)})", expanded=True):
        for w in list(st.session_state.watchlist):
            wc1, wc2 = st.columns([3, 1])
            wc1.markdown(f"**{w}**")
            if wc2.button("✕", key=f"unwatch_{w}"):
                toggle_watch(w)
                _rerun()


if scope_choice == "Career (All-Time)":
    df_active = full_df.copy()
    selected_scope = "Career Stats"
    target_season = _qcl_seasons[0] if _qcl_seasons else _ordered_seasons[0]
    banner_text = "CAREER — ALL-TIME"
elif scope_choice == "Career (QCL)":
    df_active = full_df[full_df['Era'] == 'QCL'].copy()
    selected_scope = "Career Stats"
    target_season = _qcl_seasons[0] if _qcl_seasons else _ordered_seasons[0]
    banner_text = "CAREER — QCL"
elif scope_choice == "Career (SPAM)":
    df_active = full_df[full_df['Era'] == 'SPAM'].copy()
    selected_scope = "Career Stats"
    target_season = _spam_seasons[0] if _spam_seasons else _ordered_seasons[0]
    banner_text = "CAREER — SPAM"
else:
    target_season = _ordered_seasons[_season_labels.index(scope_choice)]
    df_active = full_df[full_df['Season'] == target_season].copy()
    selected_scope = "Season"   # sentinel: anything != "Career Stats"
    banner_text = scope_choice


if game_type != "All Games":
    df_active = df_active[df_active['Game_Type'] == game_type]
    banner_text += f" • {game_type.upper()}"


st.markdown(f'<div class="header-banner">🏀 QCL LEAGUE HUB — {banner_text}</div>', unsafe_allow_html=True)
if DATA_HEALTH.get("Google Sheet") != "Available":
    st.warning("Current-season sheet stats are unavailable or not started. Historical seasons in the Data Scope selector remain available; GM and registration tools are still open.")


# optional, right after the header-banner markdown
if os.path.exists("Logo.png"):
    st.image("Logo.png", width=120)


if df_active.empty:
    _closed_season_desk("No games match this season or game-type filter. Choose another season in the sidebar to see historical stats.")
    st.stop()




# =============================================================================
# 8. CORE STAT ENGINE
# =============================================================================
full_p_df = full_df[full_df['Type'].astype(str).str.lower() == 'player'].copy()




def compute_stats(scope_df, full_df, min_gp_filter=0):
    """Core stat engine. Identical math for regular season, playoffs, or any scope.
    Returns a dict of frames, or None if the scope lacks player/team rows."""
    p_df = scope_df[scope_df['Type'].astype(str).str.lower() == 'player'].copy()
    t_df = scope_df[scope_df['Type'].astype(str).str.lower() == 'team'].copy()
    fp_df = full_df[full_df['Type'].astype(str).str.lower() == 'player'].copy()


    if p_df.empty or t_df.empty:
        return None


    p_all_time_highs = fp_df.groupby('Player/Team').agg(
        AT_High_PTS=('PTS', 'max'), AT_High_REB=('REB', 'max'), AT_High_AST=('AST', 'max')
    ).reset_index()


    p_stats = p_df.groupby('Player/Team').agg(**{
        'GP': ('GKey', 'nunique'),
        'PTS': ('PTS', 'mean'), 'REB': ('REB', 'mean'), 'AST': ('AST', 'mean'),
        'STL': ('STL', 'mean'), 'BLK': ('BLK', 'mean'), 'TO': ('TO', 'mean'),
        'FGM': ('FGM', 'mean'), 'FGA': ('FGA', 'mean'),
        '3PM': ('3PM', 'mean'), '3PA': ('3PA', 'mean'),
        'FTM': ('FTM', 'mean'), 'FTA': ('FTA', 'mean'),
        'PIE_Raw': ('PIE_Raw', 'mean'), 'POS': ('Position_Num', 'mean'),
        'Team': ('Team Name', 'last'),
        'Tipped_Passes': ('Tipped_Passes', 'mean'), 'Shots_Affected': ('Shots_Affected', 'mean'),
        'FB_Points': ('FB_Points', 'mean'),
        'USG': ('USG_Game', 'mean'), 'ORtg': ('ORtg_Game', 'mean'), 'GmSc': ('Game_Score', 'mean'),
        'Wins': ('Win', 'sum'),
    }).reset_index()


    p_stats.rename(columns={'PIE_Raw': 'PIE'}, inplace=True)
    p_stats['DEF'] = p_stats['STL'] + p_stats['BLK']
    p_stats['Win%'] = (p_stats['Wins'] / p_stats['GP'].replace(0, 1)).round(3)


    # REAL TS% — uses actual FTA (no more 0.2*FGA proxy)
    ts_den = 2 * (p_stats['FGA'] + 0.44 * p_stats['FTA'])
    p_stats['TS%'] = np.where(ts_den > 0, p_stats['PTS'] / ts_den * 100, 0)
    # REAL eFG%
    p_stats['eFG%'] = np.where(p_stats['FGA'] > 0,
                               (p_stats['FGM'] + 0.5 * p_stats['3PM']) / p_stats['FGA'] * 100, 0)


    p_stats = p_stats.merge(player_clubs, on='Player/Team', how='left')
    p_stats['Clubs'] = p_stats['Clubs'].apply(lambda x: x if isinstance(x, list) else [])


    p_highs = p_df.groupby('Player/Team').agg(
        High_PTS=('PTS', 'max'), High_REB=('REB', 'max'), High_AST=('AST', 'max'),
        High_STL=('STL', 'max'), High_BLK=('BLK', 'max'), High_3PM=('3PM', 'max')
    ).reset_index()
    p_stats = p_stats.merge(p_highs, on='Player/Team', how='left')
    p_stats = p_stats.merge(p_all_time_highs, on='Player/Team', how='left')


    p_stats['FG%'] = (p_stats['FGM'] / p_stats['FGA'].replace(0, 1) * 100)
    p_stats['3P%'] = (p_stats['3PM'] / p_stats['3PA'].replace(0, 1) * 100)


    for col in ['PTS', 'REB', 'AST', 'STL', 'BLK', 'TO', '3PM', '3PA', 'FGM', 'FGA', 'FTM', 'FTA',
                'FB_Points', 'Tipped_Passes', 'Shots_Affected', 'PIE', 'TS%', 'eFG%',
                'FG%', '3P%', 'USG', 'ORtg', 'GmSc', 'DEF']:
        if col in p_stats.columns:
            p_stats[col] = p_stats[col].round(1)


    p_stats = p_stats.sort_values('PIE', ascending=False).reset_index(drop=True)
    p_stats['League_Rank'] = p_stats.index + 1


    # --- TEAM STATS ---
    t_stats = t_df.groupby('Team Name').agg(
        GP=('GKey', 'nunique'), Wins=('Win', 'sum'), PPG=('PTS', 'mean'),
        PTS_SD=('PTS', 'std'), OppPPG=('Opp_PTS', 'mean'),
        Diff=('Point_Diff', 'mean'), Opp_PPP=('Opp_PPP', 'mean'), SOS=('SOS_Game', 'mean'),
        Poss=('Poss_Raw', 'mean'), RPG=('REB', 'mean'), APG=('AST', 'mean'),
        SPG=('STL', 'mean'), BPG=('BLK', 'mean'), TOPG=('TO', 'mean'),
        FGM=('FGM', 'mean'), FGA=('FGA', 'mean'), TPM=('3PM', 'mean'),
    ).reset_index()
    t_stats['Win%'] = (t_stats['Wins'] / t_stats['GP'].replace(0, 1)).round(3)
    t_stats['DEF'] = t_stats['SPG'] + t_stats['BPG']
    t_stats['eFG%'] = np.where(t_stats['FGA'] > 0,
                               (t_stats['FGM'] + 0.5 * t_stats['TPM']) / t_stats['FGA'] * 100, 0)
    t_stats['ORtg'] = np.where(t_stats['Poss'] > 0, t_stats['PPG'] / t_stats['Poss'] * 100, 0).round(1)
    _drtg_fill = t_stats['Opp_PPP'].mean()
    _drtg_fill = _drtg_fill if pd.notna(_drtg_fill) else 1.0
    t_stats['DRtg'] = (t_stats['Opp_PPP'].fillna(_drtg_fill) * 100).round(1)
    t_stats['NetRtg'] = (t_stats['ORtg'] - t_stats['DRtg']).round(1)
    t_stats['Pace'] = t_stats['Poss'].round(1)
    t_stats['Diff'] = t_stats['Diff'].fillna(0)
    t_stats['PTS_SD'] = t_stats['PTS_SD'].fillna(7.0).clip(lower=3.5, upper=14.0)


    # --- PLAYER DRtg / NetRtg ---
    if not t_stats.empty:
        p_stats = p_stats.merge(
            t_stats[['Team Name', 'DRtg']].rename(columns={'Team Name': 'Team', 'DRtg': 'Team_DRtg'}),
            on='Team', how='left')
        lg_def = p_stats['DEF'].mean()
        p_stats['DRtg'] = (p_stats['Team_DRtg'].fillna(t_stats['DRtg'].mean())
                           - (p_stats['DEF'] - lg_def) * 2.0).round(1)
    else:
        p_stats['DRtg'] = 0.0
    p_stats['NetRtg'] = (p_stats['ORtg'] - p_stats['DRtg']).round(1)


    p_view = p_stats[p_stats['GP'] >= min_gp_filter].copy()


    return {'p_df': p_df, 't_df': t_df, 'p_stats': p_stats, 't_stats': t_stats, 'p_view': p_view}




_S = compute_stats(df_active, full_df, min_gp_filter)
if _S is None:
    _closed_season_desk("This scope has no player/team stat rows yet. Choose a historical season in the sidebar.")
    st.stop()
p_df, t_df = _S['p_df'], _S['t_df']
p_stats, t_stats, p_view = _S['p_stats'], _S['t_stats'], _S['p_view']




# =============================================================================
# 9. ROTATION + MONTE CARLO ENGINE
# =============================================================================
def get_rotation(team_name, size=ROTATION_SIZE, exclude=None):
    """Only five bodies play in Pro-Am. Rotation = most-used players by GAMES PLAYED,
    PIE as the tiebreak. Anyone in `exclude` is scratched."""
    roster = p_stats[p_stats['Team'] == team_name].copy()
    if exclude:
        roster = roster[~roster['Player/Team'].isin(exclude)]
    roster = roster.sort_values(['GP', 'PIE', 'PTS'], ascending=[False, False, False])
    return roster.head(size).reset_index(drop=True)




def full_roster(team_name):
    r = p_stats[p_stats['Team'] == team_name].copy()
    return r.sort_values(['GP', 'PIE'], ascending=[False, False])




def run_monte_carlo(t1, t2, rot1, rot2, n_sims=2000, hca=1.5, star_conc=6.0,
                    variance=1.0, seed=None):
    """
    Vectorized Monte Carlo over the two FIVE-MAN rotations.


    Team score model:
        expected = PPG x opponent-defense factor x SOS adjustment
                       x rotation-availability factor  ( +/- HCA )
    Rotation availability = (sum of PPG of the five actually playing) /
    (sum of PPG of the default healthy five). Scratch a star, the projected
    score drops accordingly.


    Player distribution: Dirichlet over the five rotation players' scoring
    shares. `star_conc` controls how tightly the ball sticks to the usage
    hierarchy (low = chaotic, high = the star always gets his).
    """
    rng = np.random.default_rng(seed)


    d1 = t_stats[t_stats['Team Name'] == t1].iloc[0]
    d2 = t_stats[t_stats['Team Name'] == t2].iloc[0]


    lg_opp_ppp = t_stats['Opp_PPP'].mean()
    lg_opp_ppp = float(lg_opp_ppp) if pd.notna(lg_opp_ppp) and lg_opp_ppp > 0 else 1.0


    def dfac(row):
        v = row['Opp_PPP']
        return float(v / lg_opp_ppp) if pd.notna(v) and v > 0 else 1.0


    def1 = dfac(d2)   # defense that T1 faces
    def2 = dfac(d1)   # defense that T2 faces


    def avail(team, rot):
        base = get_rotation(team)  # default healthy five
        base_sum = float(base['PTS'].sum())
        rot_sum = float(rot['PTS'].sum())
        if base_sum <= 0:
            return 1.0
        return float(np.clip(rot_sum / base_sum, 0.55, 1.30))


    av1, av2 = avail(t1, rot1), avail(t2, rot2)


    sos1 = float(d1['SOS']) if pd.notna(d1['SOS']) else 0.5
    sos2 = float(d2['SOS']) if pd.notna(d2['SOS']) else 0.5


    exp1 = float(d1['PPG']) * def1 * (1 + (sos1 - 0.5) * 0.5) * av1 + hca
    exp2 = float(d2['PPG']) * def2 * (1 + (sos2 - 0.5) * 0.5) * av2


    sd1 = float(d1['PTS_SD']) * variance
    sd2 = float(d2['PTS_SD']) * variance


    # ---- team scores ----
    s1 = np.rint(rng.normal(exp1, sd1, n_sims)).astype(int)
    s2 = np.rint(rng.normal(exp2, sd2, n_sims)).astype(int)
    s1 = np.clip(s1, 25, None)
    s2 = np.clip(s2, 25, None)


    # ---- overtime: break ties with a coin-flip bucket ----
    tie = s1 == s2
    if tie.any():
        flip = rng.random(int(tie.sum())) < 0.5
        bump = rng.integers(2, 7, int(tie.sum()))
        s1[tie] = s1[tie] + np.where(flip, bump, 0)
        s2[tie] = s2[tie] + np.where(flip, 0, bump)


    w1 = (s1 > s2)


    # ---- player scoring distribution over the five ----
    def player_pts(rot, scores):
        base = rot['PTS'].to_numpy(dtype=float)
        if base.sum() <= 0:
            base = np.ones(len(rot))
        alpha = np.clip(base / base.sum(), 0.02, None) * star_conc * len(rot)
        shares = rng.dirichlet(alpha, size=n_sims)           # (n_sims, k)
        return shares * scores[:, None]


    pp1 = player_pts(rot1, s1)
    pp2 = player_pts(rot2, s2)


    # ---- MVP: scoring + baseline impact + winner bonus ----
    def impact(rot):
        return (1.1 * rot['REB'].to_numpy(dtype=float)
                + 1.4 * rot['AST'].to_numpy(dtype=float)
                + 2.0 * rot['STL'].to_numpy(dtype=float)
                + 2.0 * rot['BLK'].to_numpy(dtype=float))


    m1 = pp1 + impact(rot1)[None, :] + np.where(w1, 4.0, 0.0)[:, None]
    m2 = pp2 + impact(rot2)[None, :] + np.where(~w1, 4.0, 0.0)[:, None]
    mvp_matrix = np.hstack([m1, m2])
    names = list(rot1['Player/Team']) + list(rot2['Player/Team'])
    teams = [t1] * len(rot1) + [t2] * len(rot2)
    mvp_idx = mvp_matrix.argmax(axis=1)
    mvp_counts = np.bincount(mvp_idx, minlength=len(names))


    return {
        'n': n_sims,
        's1': s1, 's2': s2,
        'exp1': exp1, 'exp2': exp2,
        'win1': float(w1.mean()), 'win2': float(1 - w1.mean()),
        'margin': s1 - s2,
        'pp1': pp1, 'pp2': pp2,
        'mvp': pd.DataFrame({'Player': names, 'Team': teams,
                             'MVP %': (mvp_counts / n_sims * 100).round(1)}
                            ).sort_values('MVP %', ascending=False).reset_index(drop=True),
        'avail': (av1, av2),
    }




def projected_box(rot, pp):
    """Median simulated points per player + season-average support stats."""
    med = np.median(pp, axis=0).round(0).astype(int)
    p20 = np.percentile(pp, 20, axis=0).round(0).astype(int)
    p80 = np.percentile(pp, 80, axis=0).round(0).astype(int)
    return pd.DataFrame({
        'Player': rot['Player/Team'],
        'GP': rot['GP'].astype(int),
        'PROJ PTS': med,
        'Range': [f"{a}-{b}" for a, b in zip(p20, p80)],
        'REB': rot['REB'].round(1),
        'AST': rot['AST'].round(1),
        'STL': rot['STL'].round(1),
        'BLK': rot['BLK'].round(1),
        'USG%': rot['USG'].round(1),
    })




# =============================================================================
# 10. VIEWS
# =============================================================================


# ------------------------------------------------------------------ HOME -----


# ═══════════════════════════════════════════════════════════════════════════
#  OPEN A PACK ON THE SITE — logged-in players open packs in the Hub itself
#
#  Uses the SAME card pool + save file the bot reads, so Discord and the site
#  are one economy. Requires the persistent login (current_user()).
#
#  In app.py:
#      from hub_persistent_login import current_user
#      from hub_open_pack import render_open_pack
#      render_open_pack(current_user())
# ═══════════════════════════════════════════════════════════════════════════

import os
import json
import random
import streamlit as st
import streamlit.components.v1 as components


def _base():
    return _ASSET_BASE if "_ASSET_BASE" in globals() else "."


# odds must mirror the bot's TVT_ODDS; edit if yours differ
_SITE_ODDS = [("Legendary", 0.03), ("Epic", 0.10), ("Rare", 0.22),
              ("Uncommon", 0.30), ("Common", 0.35)]
_PACK_SIZE = 5
_TIER_CLASS = {"Legendary": "legendary", "Epic": "epic", "Rare": "rare",
               "Uncommon": "uncommon", "Common": "common"}


def _load_pool():
    """Read the same pool the bot publishes (names + rarity)."""
    for fn in ("fantasy_market.json", "pool.json"):
        p = os.path.join(_base(), fn)
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                names = data.get("names") or list(data.get("cards", {}).keys())
                rarity = data.get("rarity", {})
                if names:
                    return names, rarity
            except Exception:
                pass
    return [], {}


def _draw(names, rarity):
    buckets = {}
    for n in names:
        buckets.setdefault(rarity.get(n, "Common"), []).append(n)
    out = []
    for _ in range(_PACK_SIZE):
        roll, cum, chosen = random.random(), 0.0, "Common"
        for tier, odds in _SITE_ODDS:
            cum += odds
            if roll <= cum:
                chosen = tier
                break
        bucket = buckets.get(chosen) or names
        if bucket:
            name = random.choice(bucket)
            out.append({"name": name, "tier": rarity.get(name, chosen)})
    return out


def _mint_to_save(user_id, pulled):
    """Write into the SAME save file the bot uses, so it's one collection."""
    p = os.path.join(_base(), "fantasy_save.json")
    save = {}
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                save = json.load(f) or {}
        except Exception:
            save = {}
    users = save.setdefault("users", {})
    u = users.setdefault(str(user_id), {"cards": [], "coins": 0})
    u.setdefault("cards", []).extend(c["name"] for c in pulled)
    mint = save.setdefault("mint", {})
    for c in pulled:
        mint[c["name"]] = int(mint.get(c["name"], 0)) + 1
    try:
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(save, f, indent=2, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception as e:
        st.warning(f"Couldn't save collection: {e}")

def render_open_pack(user):
    st.subheader("🎁 Open a Pack")
    if not user:
        st.info("Log in with Discord to open packs — they mint to your collection.")
        return

    names, rarity = _load_pool()
    if not names:
        st.info("The card pool isn't published yet. The bot publishes it on its "
                "next cycle, then packs open here.")
        return

    # Trigger pack draw & minting on button click
    col1, col2 = st.columns([2, 2])
    with col1:
        if st.button("🃏 Draw & Rip a Pack", type="primary", use_container_width=True):
            pulled = _draw(names, rarity)
            _mint_to_save(user["id"], pulled)
            st.session_state["last_site_pull"] = pulled
            st.rerun()

    pulled = st.session_state.get("last_site_pull")
    if pulled:
        with col2:
            if st.button("🔄 Reset Pack View", use_container_width=True):
                st.session_state.pop("last_site_pull", None)
                st.rerun()

    if not pulled:
        st.caption("Click 'Draw & Rip a Pack' above to generate your cards and launch the animation.")
        return

    cards_data = [{"n": c["name"], 
                   "c": _TIER_CLASS.get(c["tier"], "common"), 
                   "t": c["tier"].upper()} for c in pulled]
    cards_js = json.dumps(cards_data)

    pack_html = """
    <!DOCTYPE html>
    <html>
    <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <style>
    * { margin:0; box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
    body {
      background:transparent; min-height:360px; display:flex; flex-direction:column;
      align-items:center; justify-content:center; font-family:-apple-system,system-ui,sans-serif;
      overflow:hidden; touch-action:manipulation; padding:16px;
    }
    /* ---- THE PACK ---- */
    .pack {
      width:220px; height:320px; border-radius:20px; cursor:pointer; position:relative;
      background:linear-gradient(135deg,#5865F2 0%,#8b5cf6 45%,#ec4899 100%);
      box-shadow:0 25px 60px rgba(88,101,242,.55),inset 0 2px 20px rgba(255,255,255,.25);
      display:flex; align-items:center; justify-content:center; overflow:hidden;
      transition:transform .25s;
    }
    .pack:active { transform:scale(.96); }
    .pack::before {
      content:''; position:absolute; top:0; left:-60%; width:40%; height:100%;
      background:linear-gradient(90deg,transparent,rgba(255,255,255,.5),transparent);
      transform:skewX(-20deg); animation:sheen 2.5s infinite;
    }
    @keyframes sheen { 0%{left:-60%;} 60%,100%{left:160%;} }
    .pack .logo {
      color:#fff; font-size:24px; font-weight:900; letter-spacing:3px;
      text-shadow:0 3px 12px rgba(0,0,0,.5); z-index:2; text-align:center;
    }
    .pack .sub { font-size:11px; letter-spacing:4px; opacity:.85; margin-top:6px; }
    .rip {
      position:absolute; inset:0; z-index:3; pointer-events:none;
      background:radial-gradient(circle,rgba(255,255,255,.95),transparent 60%);
      opacity:0;
    }
    .pack.opening { animation:shake .12s ease-in-out 6; }
    @keyframes shake {
      0%,100%{transform:translateX(0) rotate(0);}
      25%{transform:translateX(-6px) rotate(-2deg);}
      75%{transform:translateX(6px) rotate(2deg);}
    }
    .pack.burst { animation:burst .5s ease-out forwards; }
    @keyframes burst { to{transform:scale(1.6);opacity:0;filter:blur(8px);} }
    .pack.burst .rip { animation:flash .5s ease-out; }
    @keyframes flash { 0%{opacity:0;} 40%{opacity:1;} 100%{opacity:0;} }

    /* ---- THE REVEAL ---- */
    .reveal { display:none; gap:12px; flex-wrap:wrap; justify-content:center;
      max-width:640px; padding:0 8px; }
    .reveal.show { display:flex; }
    .rc {
      width:110px; height:158px; border-radius:12px; position:relative;
      display:flex; flex-direction:column; align-items:center; justify-content:center;
      color:#fff; font-weight:900; opacity:0; transform:translateY(40px) rotateY(90deg);
      box-shadow:0 12px 34px rgba(0,0,0,.55);
    }
    .rc.in { animation:flip .6s cubic-bezier(.2,.9,.3,1.2) forwards; }
    @keyframes flip { to{opacity:1;transform:translateY(0) rotateY(0);} }
    .t { font-size:10px; letter-spacing:1px; opacity:.9; }
    .n { font-size:14px; margin-top:6px; text-align:center; padding:0 6px; }
    .common { background:linear-gradient(135deg,#4b5563,#374151); }
    .uncommon { background:linear-gradient(135deg,#16a34a,#15803d); }
    .rare { background:linear-gradient(135deg,#2563eb,#1e40af); }
    .epic { background:linear-gradient(135deg,#7c3aed,#5b21b6); box-shadow:0 0 30px rgba(124,58,237,.5); }
    .legendary { background:linear-gradient(135deg,#f59e0b,#d97706); box-shadow:0 0 40px rgba(245,158,11,.7); }
    .legendary::after {
      content:''; position:absolute; inset:0; border-radius:12px;
      background:linear-gradient(115deg,transparent 30%,rgba(255,255,255,.6) 50%,transparent 70%);
      background-size:200% 200%; animation:holo 2s linear infinite; mix-blend-mode:overlay;
    }
    @keyframes holo { 0%{background-position:0 0;} 100%{background-position:200% 200%;} }
    .hint { color:#9ca3af; font-size:13px; margin-top:20px; height:20px; text-align:center; width:100%; }
    </style>
    </head>
    <body>
      <div class="pack" id="pack" onclick="rip()">
        <div class="rip" id="ripFx"></div>
        <div class="logo">QCL PACK<div class="sub">TAP TO OPEN</div></div>
      </div>
      <div class="reveal" id="reveal"></div>
      <div class="hint" id="hint">Tap the pack</div>
    <script>
      const cards = __CARDS__;
      function rip(){
        const pack=document.getElementById('pack');
        document.getElementById('hint').textContent='';
        pack.classList.add('opening');
        setTimeout(()=>{ pack.classList.remove('opening'); pack.classList.add('burst');
          document.getElementById('ripFx').style.opacity=1; }, 720);
        setTimeout(()=>{ pack.style.display='none'; showCards(); }, 1200);
      }
      function showCards(){
        const wrap=document.getElementById('reveal'); wrap.classList.add('show');
        cards.forEach((card,i)=>{
          const el=document.createElement('div');
          el.className='rc '+card.c;
          el.innerHTML='<div class="t">'+card.t+'</div><div class="n">'+card.n+'</div>';
          wrap.appendChild(el);
          setTimeout(()=>el.classList.add('in'), 150 + i*260);
        });
      }
    </script>
    </body>
    </html>
    """.replace("__CARDS__", cards_js)
    
    components.html(pack_html, height=400)
    st.caption("Minted to your collection — same one your Discord cards live in.")




# ═══════════════════════════════════════════════════════════════════════════
#  MERGED PLAYER CARDS — player-driven truth, coach fallback
#
#  One card per player, resolved field-by-field:
#    PLAYER self-service (social_links.json, from site login)  = WINS
#    COACH registration  (registrations.json, from the bot)    = fallback
#
#  A coach can register someone; the moment that player logs in and sets a
#  field themselves, THEIR value takes over for that field only. Coach values
#  fill anything the player never set.
#
#  Add to app.py:
#     from hub_merged_cards import render_merged_cards
#     render_merged_cards_v2()
# ═══════════════════════════════════════════════════════════════════════════

import os
import json


def _base():
    return _ASSET_BASE if "_ASSET_BASE" in globals() else "."


@st.cache_data(ttl=30)
def _load_json(name):
    p = os.path.join(_base(), name)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


# fields that make up a card, and where each can come from
_CARD_FIELDS = ["gamertag", "twitter", "twitch", "youtube", "psn", "xbox",
                "position", "team"]


def _merge_one(discord_id, player_rec, coach_rec):
    """Field-by-field: player value wins, coach fills the gaps."""
    out = {"discord_id": discord_id}
    # display name + avatar: prefer player (verified via login), else coach
    out["display_name"] = (player_rec.get("discord_name")
                           or coach_rec.get("display_name")
                           or coach_rec.get("discord_tag") or "Unknown")
    out["discord_tag"] = coach_rec.get("discord_tag") or player_rec.get("discord_name", "")
    out["avatar"] = player_rec.get("avatar")
    for f in _CARD_FIELDS:
        pv = (player_rec.get(f) or "").strip() if isinstance(player_rec.get(f), str) else player_rec.get(f)
        cv = (coach_rec.get(f) or "").strip() if isinstance(coach_rec.get(f), str) else coach_rec.get(f)
        out[f] = pv or cv or ""
    # provenance: who set the identity
    out["source"] = "player" if player_rec else "coach"
    return out


def get_merged_players():
    """Return {discord_id: merged_card} across both sources."""
    players = _load_json("social_links.json")      # player-driven
    coaches = _load_json("registrations.json")     # coach fallback
    ids = set(players) | set(coaches)
    merged = {}
    for did in ids:
        merged[did] = _merge_one(did, players.get(did, {}), coaches.get(did, {}))
    return merged


def render_merged_cards_v2():
    st.subheader("\U0001f0cf Player Cards")
    cards = get_merged_players()
    if not cards:
        st.info("No players yet. Players link their own profile via **Login with "
                "Discord**; coaches can pre-register with `/qtcg register`.")
        return

    q = st.text_input("\U0001f50d Search players", key="mc_search")
    rows = list(cards.values())
    if q:
        rows = [p for p in rows if q.lower() in
                (p.get("display_name", "") + p.get("gamertag", "") +
                 p.get("team", "")).lower()]
    st.caption(f"{len(rows)} player(s)  \u00b7  \U0001f7e2 player-verified \u00b7 "
               f"\u26aa coach-entered")

    cols = st.columns(3)
    for i, p in enumerate(sorted(rows, key=lambda x: x.get("display_name", ""))):
        with cols[i % 3]:
            dot = "\U0001f7e2" if p.get("source") == "player" else "\u26aa"
            socials = []
            if p.get("twitter"):
                socials.append(f"<a href='https://x.com/{p['twitter']}' "
                               f"target='_blank' style='color:#1DA1F2;'>X</a>")
            if p.get("twitch"):
                socials.append(f"<a href='https://twitch.tv/{p['twitch']}' "
                               f"target='_blank' style='color:#9146FF;'>Twitch</a>")
            if p.get("youtube"):
                socials.append(f"<a href='https://youtube.com/@{p['youtube']}' "
                               f"target='_blank' style='color:#FF0000;'>YT</a>")
            social_html = " \u00b7 ".join(socials) if socials else \
                "<span style='color:#666;'>no socials yet</span>"
            pos = f" \u00b7 {p['position']}" if p.get("position") else ""
            st.markdown(
                f"<div style='background:#161b22;border:1px solid #30363d;"
                f"border-radius:12px;padding:14px;margin-bottom:12px;'>"
                f"<div style='color:#fff;font-size:16px;font-weight:800;'>"
                f"{dot} {p.get('display_name','?')}</div>"
                f"<div style='color:#8b949e;font-size:12px;margin-bottom:8px;'>"
                f"{p.get('team','')}{pos}</div>"
                f"<div style='color:#e6edf3;font-size:13px;'>\U0001f3ae "
                f"<b>{p.get('gamertag') or '\u2014'}</b></div>"
                f"<div style='color:#8b949e;font-size:12px;'>\U0001f4ac "
                f"@{p.get('discord_tag','?')}</div>"
                f"<div style='margin-top:8px;font-size:13px;'>{social_html}</div>"
                f"</div>", unsafe_allow_html=True)




if view_mode in {"🏢 GM Desk", "👥 Players Desk"}:
    _gm_desk(_access(current_user()))

if view_mode == "🏠 League Home & Awards":
    hc1, hc2, hc3, hc4 = st.columns(4)
    hc1.markdown(f"<div class='metric-box'><div class='metric-title'>Games Logged</div><div class='metric-value'>{df_active['GKey'].nunique()}</div></div>", unsafe_allow_html=True)
    hc2.markdown(f"<div class='metric-box'><div class='metric-title'>Active Players</div><div class='metric-value'>{p_stats['Player/Team'].nunique()}</div></div>", unsafe_allow_html=True)
    hc3.markdown(f"<div class='metric-box'><div class='metric-title'>Teams</div><div class='metric-value'>{t_stats['Team Name'].nunique()}</div></div>", unsafe_allow_html=True)
    hc4.markdown(f"<div class='metric-box'><div class='metric-title'>League PPG</div><div class='metric-value'>{fnum(t_stats['PPG'].mean()):.1f}</div></div>", unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)


    qual_pct = st.slider("Award eligibility — % of league-leading GP required", 30, 90, 60, 5) / 100
    qual_p = p_stats[p_stats['GP'] >= (p_stats['GP'].max() * qual_pct)]
    st.caption(f"{len(qual_p)} of {len(p_stats)} players qualify "
               f"({int(np.ceil(p_stats['GP'].max() * qual_pct))}+ games).")


    def render_award_row(title, sorted_df, stat_col, label=None):
        st.markdown(f"#### {title}")
        if sorted_df.empty:
            st.info("Not enough qualifying players yet.")
            return
        label = label or stat_col
        cols = st.columns(3)
        for i, (_, r) in enumerate(sorted_df.head(3).iterrows()):
            medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉"
            _img = player_card_uri(r['Player/Team'])
            img_html = (f"<img src='{_img}' style='max-height:180px; max-width:100%; object-fit:contain; "
                        f"border-radius:8px; margin-bottom:10px;'>" if _img else "")
            with cols[i]:
                st.markdown(
                    f"<div class='award-card'>{img_html}<h3>{medal} {r['Player/Team']}</h3>"
                    f"<p style='color:#aaa;'>{team_logo_html(r['Team'], px=18)}{r['Team']}</p>"
                    f"<h2 style='color:#d4af37;'>{r[stat_col]:.1f} {label}</h2>"
                    f"<p>{r['PTS']:.1f} PTS | {r['REB']:.1f} REB | {r['AST']:.1f} AST</p></div>",
                    unsafe_allow_html=True)
                if st.button(("★ Watching" if r['Player/Team'] in st.session_state.watchlist else "☆ Watch"),
                             key=f"w_{title}_{i}", use_container_width=True):
                    toggle_watch(r['Player/Team'])
                    _rerun()


    a_tabs = st.tabs(["MVP", "DPOY", "Big Man", "6th Man", "Most Improved", "All-League"])


    with a_tabs[0]:
        render_award_row("Most Valuable Player", qual_p.sort_values('PIE', ascending=False), 'PIE')
    with a_tabs[1]:
        render_award_row("Defensive Player of the Year", qual_p.sort_values('DEF', ascending=False), 'DEF', 'STOCKS')
    with a_tabs[2]:
        render_award_row("Big Man of the Year", qual_p[qual_p['POS'] >= 3].sort_values('PIE', ascending=False), 'PIE')
    with a_tabs[3]:
        rots = [get_rotation(t) for t in t_stats['Team Name']]
        rots = [r for r in rots if not r.empty]
        rot_names = pd.concat(rots)['Player/Team'] if rots else pd.Series(dtype=str)
        bench_pool = qual_p[~qual_p['Player/Team'].isin(rot_names)]
        st.caption("6th Man = best qualifier who is NOT in his team's top-5 rotation (by GP).")
        render_award_row("6th Man of the Year", bench_pool.sort_values('PIE', ascending=False), 'PIE')
    with a_tabs[4]:
        st.markdown("#### 🚀 Most Improved Player")
        prev_seasons = [s for s in seasons if s < target_season]
        if selected_scope == "Career Stats":
            st.info("Switch to a single-season scope to view the MIP race.")
        elif not prev_seasons:
            st.info("MIP requires a previous season for comparison.")
        else:
            prev_s = max(prev_seasons)


            def season_line(s):
                d = full_p_df[full_p_df['Season'] == s]
                return d.groupby('Player/Team').agg(GP=('GKey', 'nunique'),
                                                    PIE=('PIE_Raw', 'mean'),
                                                    PTS=('PTS', 'mean')).reset_index()


            mip = season_line(target_season).merge(season_line(prev_s), on='Player/Team',
                                                   suffixes=('', '_Prev'))
            mip = mip[(mip['GP'] >= 3) & (mip['GP_Prev'] >= 3)]
            mip['Jump'] = mip['PIE'] - mip['PIE_Prev']
            mip = mip.sort_values('Jump', ascending=False)
            if mip.empty:
                st.info("No players with 3+ games in both seasons yet.")
            else:
                mc = st.columns(3)
                for i, (_, r) in enumerate(mip.head(3).iterrows()):
                    medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉"
                    jc = GREEN if r['Jump'] >= 0 else RED
                    with mc[i % 3]:
                        st.markdown(
                            f"<div class='award-card'><h3>{medal} {r['Player/Team']}</h3>"
                            f"<p style='color:#aaa;'>S{prev_s} → S{target_season}</p>"
                            f"<h2 style='color:{jc};'>{r['Jump']:+.1f} PIE</h2>"
                            f"<p>{r['PIE_Prev']:.1f} → {r['PIE']:.1f} PIE | {r['PTS']:.1f} PPG now</p></div>",
                            unsafe_allow_html=True)
                dl(mip[['Player/Team', 'PIE_Prev', 'PIE', 'Jump']], "⬇️ MIP race CSV", "mip_race.csv", "dl_mip")
    with a_tabs[5]:
        st.markdown("#### 🏅 All-League Teams")
        _al_data = _load_allleague().get(str(target_season), {})


        def _squad_df(names):
            rws = []
            for n in names:
                mm = p_stats[p_stats['Player/Team'] == n]
                if not mm.empty:
                    rr = mm.iloc[0]
                    rws.append({"Player/Team": n, "Team": rr['Team'], "PIE": rr['PIE']})
                else:
                    rws.append({"Player/Team": n, "Team": "", "PIE": float('nan')})
            return pd.DataFrame(rws, columns=["Player/Team", "Team", "PIE"])


        if _al_data:
            st.caption("Curated selections (set via /allleague or the editor below).")
            sq1 = _squad_df(_al_data.get("1st Team", []))
            sq2 = _squad_df(_al_data.get("2nd Team", []))
            sq3 = _squad_df(_al_data.get("3rd Team", []))
        else:
            st.caption("Auto-picked by PIE for now \u2014 curate your own below or with /allleague.")
            al = qual_p.sort_values('PIE', ascending=False).head(15).reset_index(drop=True)
            sq1, sq2, sq3 = al.head(5), al.iloc[5:10], al.iloc[10:15]


        def render_all_league(col, title, squad, border):
            with col:
                html = (f"<div style='background:#161b22; border:2px solid {border}; border-radius:8px; padding:15px;'>"
                        f"<h4 style='color:{border}; text-align:center; text-transform:uppercase; margin-top:0;'>{title}</h4>")
                if squad.empty:
                    html += "<p style='color:#666; text-align:center;'>\u2014</p>"
                for _, r in squad.iterrows():
                    html += (f"<div class='stat-row'><span style='color:#fff; font-weight:bold;'>"
                             f"{team_logo_html(r['Team'], px=16)}{r['Player/Team']}</span>"
                             f"<span class='stat-val'>{fnum(r['PIE']):.1f}</span></div>")
                st.markdown(html + "</div>", unsafe_allow_html=True)


        a1, a2, a3 = st.columns(3)
        render_all_league(a1, "1st Team", sq1, GOLD)
        render_all_league(a2, "2nd Team", sq2, SILVER)
        render_all_league(a3, "3rd Team", sq3, BRONZE)


        with st.expander("\u270f\ufe0f Curate All-League (Season " + str(target_season) + ")"):
            _names_all = sorted(p_stats['Player/Team'].tolist())
            _p1 = st.multiselect("1st Team", _names_all,
                                 default=[x for x in _al_data.get("1st Team", []) if x in _names_all], key="al1")
            _p2 = st.multiselect("2nd Team", _names_all,
                                 default=[x for x in _al_data.get("2nd Team", []) if x in _names_all], key="al2")
            _p3 = st.multiselect("3rd Team", _names_all,
                                 default=[x for x in _al_data.get("3rd Team", []) if x in _names_all], key="al3")
            _payload = dict(_load_allleague())
            _payload[str(target_season)] = {t: v for t, v in
                                            [("1st Team", _p1), ("2nd Team", _p2), ("3rd Team", _p3)] if v}
            st.download_button("\u2b07\ufe0f Download allleague.json",
                               json.dumps(_payload, indent=2).encode("utf-8"),
                               file_name="allleague.json", mime="application/json",
                               use_container_width=True)
            st.caption("Commit allleague.json to the repo root, or use /allleague in Discord to persist.")


    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown("### 🔥 Streak Trends")
    look = st.slider("Form window (games)", 2, 8, 3)
    recent_p = p_df.sort_values(['Player/Team', 'Season', 'Game_ID']).groupby('Player/Team').tail(look)
    recent_stats = recent_p.groupby('Player/Team').agg(Recent_PIE=('PIE_Raw', 'mean')).reset_index()
    trend = p_stats.merge(recent_stats, on='Player/Team')
    trend = trend[trend['GP'] >= max(look, 2)]
    trend['Swing'] = trend['Recent_PIE'] - trend['PIE']


    tc1, tc2 = st.columns(2)
    with tc1:
        st.markdown(f"<h4 style='color:{GREEN};'>📈 Heating Up</h4>", unsafe_allow_html=True)
        for _, r in trend.sort_values('Swing', ascending=False).head(4).iterrows():
            st.markdown(f"<div style='background:#1a2b1a; padding:10px; border-left:4px solid {GREEN}; margin-bottom:5px;'>"
                        f"<b>{r['Player/Team']}</b> <span style='color:#888;'>({r['Team']})</span> | "
                        f"+{r['Swing']:.1f} PIE over avg</div>", unsafe_allow_html=True)
    with tc2:
        st.markdown(f"<h4 style='color:{RED};'>📉 Cooling Down</h4>", unsafe_allow_html=True)
        for _, r in trend.sort_values('Swing', ascending=True).head(4).iterrows():
            st.markdown(f"<div style='background:#2b1a1a; padding:10px; border-left:4px solid {RED}; margin-bottom:5px;'>"
                        f"<b>{r['Player/Team']}</b> <span style='color:#888;'>({r['Team']})</span> | "
                        f"{r['Swing']:.1f} PIE under avg</div>", unsafe_allow_html=True)




# --------------------------------------------------------------- TOOLS --------
elif view_mode == "🔬 Advanced Analytics Lab":
    st.markdown(
        "<section class='qcl-tools-hero'>"
        "<div class='qcl-tools-kicker'>QCL / FIELD KIT</div>"
        "<div class='qcl-tools-title'>Tools for the next decision.</div>"
        "<p class='qcl-tools-copy'>Compare players, build a five-man rotation, "
        "pressure-test a matchup, and export the exact scope you are looking at. "
        "Every tool below follows the current season and game-type filters.</p>"
        "</section>",
        unsafe_allow_html=True,
    )

    tool_tabs = st.tabs([
        "⚖️ Player Compare",
        "🔁 Rotation Builder",
        "🆚 Team Matchup",
        "⬇️ Exports & Glossary",
    ])

    with tool_tabs[0]:
        player_names = sorted(p_stats["Player/Team"].dropna().astype(str).tolist())
        if len(player_names) < 2:
            st.info("At least two players are needed for a comparison.")
        else:
            pc1, pc2 = st.columns(2)
            player_a = pc1.selectbox("First player", player_names, index=0, key="tools_player_a")
            player_b = pc2.selectbox(
                "Second player", player_names, index=min(1, len(player_names) - 1),
                key="tools_player_b",
            )

            row_a = p_stats[p_stats["Player/Team"] == player_a].iloc[0]
            row_b = p_stats[p_stats["Player/Team"] == player_b].iloc[0]
            compare_metrics = [
                ("PTS", "PPG"), ("REB", "RPG"), ("AST", "APG"),
                ("PIE", "PIE"), ("TS%", "TS%"), ("NetRtg", "NET RTG"),
            ]

            st.markdown("#### Head-to-head profile")
            for metric, label in compare_metrics:
                av = fnum(row_a.get(metric, 0))
                bv = fnum(row_b.get(metric, 0))
                high = max(abs(av), abs(bv), 1.0)
                aw = min(abs(av) / high * 100, 100)
                bw = min(abs(bv) / high * 100, 100)
                st.markdown(
                    f"<div class='qcl-compare-row'>"
                    f"<div><div class='qcl-compare-name'>{player_a} · {av:.1f}</div>"
                    f"<div class='bar'><span style='width:{aw:.1f}%'></span></div></div>"
                    f"<div class='qcl-compare-stat'>{label}</div>"
                    f"<div class='right'><div class='qcl-compare-name right'>{bv:.1f} · {player_b}</div>"
                    f"<div class='bar'><span style='width:{bw:.1f}%'></span></div></div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

            c1, c2, c3, c4 = st.columns(4)
            c1.markdown(
                f"<div class='qcl-tool-card'><div class='qcl-tool-label'>{player_a} team</div>"
                f"<div class='qcl-tool-value'>{row_a.get('Team', '—')}</div>"
                f"<div class='qcl-tool-meta'>{int(row_a.get('GP', 0))} games logged</div></div>",
                unsafe_allow_html=True,
            )
            c2.markdown(
                f"<div class='qcl-tool-card'><div class='qcl-tool-label'>{player_a} win rate</div>"
                f"<div class='qcl-tool-value'>{fnum(row_a.get('Win%', 0)) * 100:.0f}%</div>"
                f"<div class='qcl-tool-meta'>current scope</div></div>",
                unsafe_allow_html=True,
            )
            c3.markdown(
                f"<div class='qcl-tool-card'><div class='qcl-tool-label'>{player_b} team</div>"
                f"<div class='qcl-tool-value'>{row_b.get('Team', '—')}</div>"
                f"<div class='qcl-tool-meta'>{int(row_b.get('GP', 0))} games logged</div></div>",
                unsafe_allow_html=True,
            )
            c4.markdown(
                f"<div class='qcl-tool-card'><div class='qcl-tool-label'>{player_b} win rate</div>"
                f"<div class='qcl-tool-value'>{fnum(row_b.get('Win%', 0)) * 100:.0f}%</div>"
                f"<div class='qcl-tool-meta'>current scope</div></div>",
                unsafe_allow_html=True,
            )

    with tool_tabs[1]:
        rotation_teams = sorted(t_stats["Team Name"].dropna().astype(str).tolist())
        if not rotation_teams:
            st.info("No teams are available in this scope.")
        else:
            selected_team = st.selectbox("Team", rotation_teams, key="tools_rotation_team")
            roster = full_roster(selected_team)
            scratches = st.multiselect(
                "Optional scratches",
                roster["Player/Team"].tolist(),
                key="tools_rotation_scratches",
                help="Remove a player to see how the active five changes.",
            )
            rotation = get_rotation(selected_team, exclude=scratches)
            default_rotation = get_rotation(selected_team)

            total_ppg = float(rotation["PTS"].sum()) if not rotation.empty else 0.0
            healthy_ppg = float(default_rotation["PTS"].sum()) if not default_rotation.empty else 0.0
            availability = total_ppg / healthy_ppg * 100 if healthy_ppg else 0.0
            r1, r2, r3, r4 = st.columns(4)
            r1.markdown(
                f"<div class='qcl-tool-card'><div class='qcl-tool-label'>Active five</div>"
                f"<div class='qcl-tool-value'>{len(rotation)} / {ROTATION_SIZE}</div>"
                f"<div class='qcl-tool-meta'>rotation bodies</div></div>",
                unsafe_allow_html=True,
            )
            r2.markdown(
                f"<div class='qcl-tool-card'><div class='qcl-tool-label'>Rotation PPG</div>"
                f"<div class='qcl-tool-value'>{total_ppg:.1f}</div>"
                f"<div class='qcl-tool-meta'>average scoring load</div></div>",
                unsafe_allow_html=True,
            )
            r3.markdown(
                f"<div class='qcl-tool-card'><div class='qcl-tool-label'>Health index</div>"
                f"<div class='qcl-tool-value'>{availability:.0f}%</div>"
                f"<div class='qcl-tool-meta'>vs. default top five</div></div>",
                unsafe_allow_html=True,
            )
            top_name = rotation.iloc[0]["Player/Team"] if not rotation.empty else "—"
            r4.markdown(
                f"<div class='qcl-tool-card'><div class='qcl-tool-label'>Usage anchor</div>"
                f"<div class='qcl-tool-value'>{top_name}</div>"
                f"<div class='qcl-tool-meta'>highest GP / PIE rotation slot</div></div>",
                unsafe_allow_html=True,
            )

            st.markdown(f"#### {team_full(selected_team)} active five")
            if rotation.empty:
                st.warning("No available players remain in this rotation.")
            else:
                rotation_view = rotation[
                    ["Player/Team", "GP", "PTS", "REB", "AST", "USG", "PIE"]
                ].copy()
                rotation_view.columns = ["Player", "GP", "PPG", "RPG", "APG", "USG%", "PIE"]
                render_modern_dataframe(rotation_view, name_hint="Player")
                dl(
                    rotation_view,
                    "⬇️ Rotation CSV",
                    f"{selected_team}_rotation.csv",
                    "dl_tools_rotation",
                )

    with tool_tabs[2]:
        matchup_teams = sorted(t_stats["Team Name"].dropna().astype(str).tolist())
        if len(matchup_teams) < 2:
            st.info("At least two teams are needed for a matchup.")
        else:
            m1, m2 = st.columns(2)
            home_team = m1.selectbox("Team one", matchup_teams, index=0, key="tools_team_a")
            away_team = m2.selectbox(
                "Team two", matchup_teams, index=min(1, len(matchup_teams) - 1),
                key="tools_team_b",
            )
            if home_team == away_team:
                st.warning("Choose two different teams to compare.")
            else:
                home = t_stats[t_stats["Team Name"] == home_team].iloc[0]
                away = t_stats[t_stats["Team Name"] == away_team].iloc[0]
                matchup_metrics = [
                    ("Win%", "WIN RATE", 100), ("PPG", "SCORING", 1),
                    ("OppPPG", "OPP. PPG", 1), ("NetRtg", "NET RTG", 1),
                    ("Pace", "PACE", 1),
                ]
                st.markdown("#### Team profile")
                for metric, label, multiplier in matchup_metrics:
                    hv = fnum(home.get(metric, 0)) * multiplier
                    av = fnum(away.get(metric, 0)) * multiplier
                    high = max(abs(hv), abs(av), 1.0)
                    hw = min(abs(hv) / high * 100, 100)
                    aw = min(abs(av) / high * 100, 100)
                    st.markdown(
                        f"<div class='qcl-compare-row'>"
                        f"<div><div class='qcl-compare-name'>{home_team} · {hv:.1f}</div>"
                        f"<div class='bar'><span style='width:{hw:.1f}%'></span></div></div>"
                        f"<div class='qcl-compare-stat'>{label}</div>"
                        f"<div class='right'><div class='qcl-compare-name right'>{av:.1f} · {away_team}</div>"
                        f"<div class='bar'><span style='width:{aw:.1f}%'></span></div></div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                st.caption(
                    "This is a profile comparison, not a simulated result. Use Oracle Predictor "
                    "when you want a score distribution and win probability."
                )

    with tool_tabs[3]:
        st.markdown("#### Download the current view")
        st.caption(
            f"Exports respect the active scope: {banner_text}. "
            "Use these files for scouting, posts, or your own analysis."
        )
        export_cols = st.columns(4)
        export_cols[0].download_button(
            "⬇️ Player stats", p_stats.to_csv(index=False).encode("utf-8"),
            "qcl_player_stats.csv", "text/csv", use_container_width=True,
            key="dl_tools_player_stats",
        )
        export_cols[1].download_button(
            "⬇️ Team stats", t_stats.to_csv(index=False).encode("utf-8"),
            "qcl_team_stats.csv", "text/csv", use_container_width=True,
            key="dl_tools_team_stats",
        )
        export_cols[2].download_button(
            "⬇️ Game logs", df_active.to_csv(index=False).encode("utf-8"),
            "qcl_game_logs.csv", "text/csv", use_container_width=True,
            key="dl_tools_game_logs",
        )
        export_cols[3].download_button(
            "⬇️ Watchlist", "\n".join(st.session_state.watchlist).encode("utf-8"),
            "qcl_watchlist.txt", "text/plain", use_container_width=True,
            key="dl_tools_watchlist",
        )
        with st.expander("📖 Stat glossary", expanded=True):
            st.markdown(
                "- **PIE** — overall game impact score used for league ranking.\n"
                "- **TS%** — true shooting efficiency using field goals and free throws.\n"
                "- **NetRtg** — offensive rating minus defensive rating.\n"
                "- **USG%** — estimated share of team possessions used by a player.\n"
                "- **Health index** — active rotation scoring compared with the default top five."
            )


# ------------------------------------------------------ ADVANCED STAT LAB ---
if view_mode == "🔬 Advanced Analytics Lab":
    advanced_board = build_advanced_player_board(p_df)
    st.markdown(
        "<section class='qcl-tools-hero'>"
        "<div class='qcl-tools-kicker'>QCL / RAW DATA LAYER</div>"
        "<div class='qcl-tools-title'>The numbers between the box score.</div>"
        "<p class='qcl-tools-copy'>Turn possessions, defensive events, rebounding work, "
        "and shot profile into usable player signals. These are derived from the raw game "
        "rows in the current scope, not manually entered ratings.</p>"
        "</section>",
        unsafe_allow_html=True,
    )

    if advanced_board.empty:
        st.info("There are not enough player rows to build the advanced board.")
    else:
        lab_tabs = st.tabs(["📋 Derived Stat Board", "🗺️ Impact Map", "🏅 Leaders & Definitions"])

        with lab_tabs[0]:
            control_a, control_b, control_c = st.columns([1.2, 1.5, 1])
            min_gp = control_a.slider(
                "Minimum GP", 0, int(max(1, advanced_board["GP"].max())), 1,
                key="adv_min_gp",
            )
            sort_labels = {
                "PIE": "PIE", "Impact Load": "Impact Load", "Disruption": "Disruption",
                "Shots Affected": "Shots Affected", "Tipped Passes": "Tipped Passes",
                "Hustle": "Hustle", "Creation Load": "Creation Load",
                "TS%": "TS%", "Game Score": "Game Score", "Net offensive rating": "ORtg",
                "GP": "GP",
            }
            sort_label = control_b.selectbox("Rank players by", list(sort_labels), key="adv_sort")
            descending = control_c.checkbox("Highest first", value=True, key="adv_desc")
            view = advanced_board[advanced_board["GP"] >= min_gp].copy()
            view = view.sort_values(sort_labels[sort_label], ascending=not descending)

            st.caption(
                f"{len(view)} players shown · sorted by {sort_label} · scope: {banner_text}"
            )
            board_cols = [
                "Player/Team", "Team", "Type", "Rarity", "GP", "PTS", "REB", "AST",
                "STL", "BLK", "Stocks", "Tipped Passes", "Shots Affected", "FB Points",
                "Disruption", "Hustle", "Creation Load", "TS%", "eFG%", "AST/TO",
                "Game Score", "PIE", "ORtg",
            ]
            board_cols = [c for c in board_cols if c in view.columns]
            display_board = view[board_cols].copy()
            numeric_display = [
                c for c in display_board.columns
                if c not in ("Player/Team", "Team", "Type", "Rarity", "GP")
            ]
            display_board[numeric_display] = display_board[numeric_display].round(1)
            render_modern_dataframe(display_board, name_hint="Player/Team")
            dl(
                display_board, "⬇️ Advanced player board CSV",
                "qcl_advanced_player_board.csv", "dl_advanced_player_board",
            )

        with lab_tabs[1]:
            st.markdown("#### Creation vs defensive disruption")
            st.caption(
                "Bubble size represents disruption events. Color is the existing player "
                "archetype calculated from league percentiles."
            )
            map_view = advanced_board[advanced_board["GP"] >= min_gp].copy()
            if map_view.empty:
                st.info("Lower the minimum GP filter to see the map.")
            else:
                fig = px.scatter(
                    map_view, x="Creation Load", y="Disruption", size="PTS", color="Type",
                    hover_name="Player/Team",
                    hover_data=["Team", "GP", "PIE", "Shots Affected",
                                "Tipped Passes", "Hustle"],
                    template="plotly_dark", size_max=34,
                    title="Players who create offense and change possessions",
                )
                fig.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    height=560, legend_title_text="Player type",
                )
                st.plotly_chart(fig, use_container_width=True)

        with lab_tabs[2]:
            st.markdown("#### Category leaders")
            leader_stats = [
                ("Shots affected", "Shots Affected"),
                ("Tipped passes", "Tipped Passes"),
                ("Fast-break points", "FB Points"),
                ("Stocks", "Stocks"),
                ("Hustle index", "Hustle"),
                ("True shooting", "TS%"),
            ]
            leader_cols = st.columns(3)
            for i, (title, stat) in enumerate(leader_stats):
                with leader_cols[i % 3]:
                    st.markdown(
                        generate_mini_leaderboard(
                            title, advanced_board[advanced_board["GP"] >= min_gp],
                            stat, GOLD if i % 2 == 0 else BLUE, 5, "Player/Team"
                        ),
                        unsafe_allow_html=True,
                    )
            with st.expander("📖 How the derived stats work", expanded=True):
                st.markdown(
                    "- **Shots Affected** — a position-aware proxy using blocks, rebounds, "
                    "steals, and fouls.\n"
                    "- **Tipped Passes** — a position-aware proxy using steals and fouls "
                    "for perimeter players, with blocks contributing for larger players.\n"
                    "- **Disruption** — Shots Affected + Tipped Passes.\n"
                    "- **Hustle** — Disruption + Fast-Break Points + Offensive Rebounds.\n"
                    "- **Creation Load** — field-goal attempts + free-throw pressure + half "
                    "of assists.\n"
                    "- **Impact Load** — points + assist value + rebounds + defensive stocks."
                )


# -------------------------------------------------------- PLAYER GALAXY -------
if view_mode in ("🔬 Advanced Analytics Lab", "🌌 Player Galaxy"):
    galaxy_board = build_advanced_player_board(p_df)
    st.markdown(
        "<section class='qcl-tools-hero'>"
        "<div class='qcl-tools-kicker'>QCL / PLAYER CONSTELLATION</div>"
        "<div class='qcl-tools-title'>Every player. Every signal.</div>"
        "<p class='qcl-tools-copy'>Filter the league by team and archetype, then sort the "
        "full player universe by the stat that matters to you. Click into Advanced Stat Lab "
        "when you want the formula behind a signal.</p>"
        "</section>",
        unsafe_allow_html=True,
    )

    if galaxy_board.empty:
        st.info("There are no player rows in the current scope.")
    else:
        types = sorted(galaxy_board["Type"].dropna().unique().tolist())
        teams = sorted(galaxy_board["Team"].dropna().unique().tolist())
        f1, f2, f3, f4 = st.columns([1.5, 1.2, 1.2, 1])
        galaxy_search = f1.text_input("Search player", key="galaxy_search")
        galaxy_teams = f2.multiselect("Teams", teams, key="galaxy_teams")
        galaxy_types = f3.multiselect("Player types", types, key="galaxy_types")
        galaxy_min_gp = f4.slider(
            "Min GP", 0, int(max(1, galaxy_board["GP"].max())), 1, key="galaxy_min_gp"
        )

        galaxy_view = galaxy_board[galaxy_board["GP"] >= galaxy_min_gp].copy()
        if galaxy_search:
            galaxy_view = galaxy_view[
                galaxy_view["Player/Team"].str.contains(galaxy_search, case=False, na=False)
            ]
        if galaxy_teams:
            galaxy_view = galaxy_view[galaxy_view["Team"].isin(galaxy_teams)]
        if galaxy_types:
            galaxy_view = galaxy_view[galaxy_view["Type"].isin(galaxy_types)]

        galaxy_sort_map = {
            "PIE": "PIE", "PTS": "PTS", "REB": "REB", "AST": "AST",
            "Stocks": "Stocks", "Shots Affected": "Shots Affected",
            "Tipped Passes": "Tipped Passes", "Disruption": "Disruption",
            "Hustle": "Hustle", "TS%": "TS%", "eFG%": "eFG%",
            "Game Score": "Game Score", "ORtg": "ORtg", "GP": "GP",
        }
        s1, s2, s3 = st.columns([1.5, 1, 1])
        galaxy_sort_label = s1.selectbox(
            "Sort galaxy by", list(galaxy_sort_map), index=0, key="galaxy_sort"
        )
        galaxy_desc = s2.checkbox("Descending", value=True, key="galaxy_desc")
        s3.metric("Players in view", len(galaxy_view))
        galaxy_view = galaxy_view.sort_values(
            galaxy_sort_map[galaxy_sort_label], ascending=not galaxy_desc
        )
        galaxy_sort_column = galaxy_sort_map[galaxy_sort_label]
        galaxy_view["_GalaxyPercentile"] = (
            pd.to_numeric(galaxy_view[galaxy_sort_column], errors="coerce")
            .fillna(0)
            .rank(pct=True)
        )

        if galaxy_view.empty:
            st.info("No players match those filters.")
        else:
            neon_types = [
                "#e6bf55", "#00bfff", "#a855f7", "#58d39a",
                "#ff6b9d", "#f97316", "#7dd3fc", "#c084fc",
            ]
            # A real spiral-galaxy layout rather than a Cartesian stat graph.
            # Higher-ranked players sit nearer the bright core; archetypes form
            # colored stellar populations along four spiral arms.
            galaxy_points = galaxy_view.reset_index(drop=True).copy()
            type_names = sorted(galaxy_points["Type"].fillna("Unknown").unique())
            type_colors = {
                name: neon_types[i % len(neon_types)]
                for i, name in enumerate(type_names)
            }
            point_count = max(1, len(galaxy_points))
            xs, ys, sizes = [], [], []
            for index, row in galaxy_points.iterrows():
                identity = f"{row.get('Player/Team', '')}|{row.get('Team', '')}"
                seed = int(hashlib.sha256(identity.encode()).hexdigest()[:12], 16)
                arm = seed % 4
                jitter = ((seed >> 5) % 1000) / 1000 - .5
                rank_pct = float(row.get("_GalaxyPercentile", 0))
                radius = .28 + 5.1 * (1 - rank_pct) ** .72
                radius += jitter * .58
                theta = (arm * np.pi / 2) + radius * 1.78 + jitter * .48
                xs.append(radius * np.cos(theta) * 1.35)
                ys.append(radius * np.sin(theta) * .78)
                disruption = max(0., float(row.get("Disruption", 0) or 0))
                sizes.append(10 + min(28, np.sqrt(disruption + 1) * 3.3))
            galaxy_points["_GalaxyX"] = xs
            galaxy_points["_GalaxyY"] = ys
            galaxy_points["_GalaxySize"] = sizes
            chart = go.Figure()
            for type_name in type_names:
                stars = galaxy_points[
                    galaxy_points["Type"].fillna("Unknown") == type_name
                ]
                custom = np.column_stack([
                    stars["Player/Team"].astype(str),
                    stars["Team"].astype(str),
                    stars["GP"].astype(str),
                    stars["PTS"].round(1).astype(str),
                    stars["PIE"].round(1).astype(str),
                    stars["TS%"].round(1).astype(str),
                    stars["Hustle"].round(1).astype(str),
                ])
                chart.add_trace(go.Scattergl(
                    x=stars["_GalaxyX"], y=stars["_GalaxyY"],
                    mode="markers", name=type_name,
                    customdata=custom,
                    marker=dict(
                        size=stars["_GalaxySize"],
                        color=type_colors[type_name],
                        opacity=.9,
                        line=dict(width=1, color="rgba(255,255,255,.75)"),
                    ),
                    hovertemplate=(
                        "<b>%{customdata[0]}</b><br>%{customdata[1]}"
                        "<br>GP %{customdata[2]} · PTS %{customdata[3]}"
                        "<br>PIE %{customdata[4]} · TS% %{customdata[5]}"
                        "<br>Hustle %{customdata[6]}<extra>" + type_name + "</extra>"
                    ),
                ))
            # Dense luminous core and faint dust lanes make the data read as a
            # galaxy even before the animated background is considered.
            chart.add_trace(go.Scatter(
                x=[0, 0, 0], y=[0, 0, 0], mode="markers",
                marker=dict(
                    size=[110, 64, 22],
                    color=["rgba(168,85,247,.10)", "rgba(0,191,255,.18)",
                           "rgba(255,232,168,.94)"],
                    line=dict(width=0),
                ),
                hoverinfo="skip", showlegend=False,
            ))
            chart.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                height=620,
                legend_title_text="Archetype",
                margin=dict(l=0, r=0, b=0, t=42),
                title=dict(
                    text=f"Player Galaxy · stellar rank by {galaxy_sort_label}",
                    x=.02, font=dict(color="rgba(240,240,255,.82)", size=16),
                ),
                xaxis=dict(visible=False, range=[-8, 8], fixedrange=False),
                yaxis=dict(visible=False, range=[-5, 5], fixedrange=False,
                           scaleanchor="x", scaleratio=1),
                hoverlabel=dict(bgcolor="#09051a", bordercolor="#8b5cf6"),
            )
            chart_payload = chart.to_json()
            galaxy_scene = """
            <!doctype html>
            <html>
            <head>
                <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
                <style>
                    :root { color-scheme: dark; }
                    * { box-sizing: border-box; }
                    html, body {
                        width: 100%;
                        height: 100%;
                        margin: 0;
                        overflow: hidden;
                        background: #020108;
                        font-family: Inter, system-ui, sans-serif;
                    }
                    #galaxy-stage {
                        position: relative;
                        width: 100%;
                        height: 680px;
                        overflow: hidden;
                        border: 1px solid rgba(138, 43, 226, 0.48);
                        border-radius: 18px;
                        isolation: isolate;
                        background:
                            radial-gradient(ellipse at 50% 52%,
                                rgba(94, 35, 168, 0.24) 0%,
                                rgba(24, 7, 55, 0.37) 24%,
                                transparent 59%),
                            radial-gradient(ellipse at 23% 34%,
                                rgba(0, 191, 255, 0.18) 0%,
                                transparent 37%),
                            radial-gradient(ellipse at 82% 70%,
                                rgba(219, 39, 119, 0.14) 0%,
                                transparent 34%),
                            #020108;
                        box-shadow:
                            inset 0 0 90px rgba(138, 43, 226, 0.32),
                            inset 0 -40px 100px rgba(0, 191, 255, 0.12),
                            0 0 38px rgba(0, 191, 255, 0.15);
                    }
                    #starfield, .nebula-clouds, .energy-rings,
                    #galaxy-plot, .scene-label {
                        position: absolute;
                        inset: 0;
                    }
                    #starfield { z-index: 1; opacity: 0.86; }
                    .nebula-clouds {
                        z-index: 2;
                        pointer-events: none;
                        filter: blur(22px);
                        opacity: 0.78;
                    }
                    .cloud {
                        position: absolute;
                        width: 44%;
                        height: 28%;
                        border-radius: 50%;
                        mix-blend-mode: screen;
                        animation: cloudDrift 16s ease-in-out infinite alternate;
                    }
                    .cloud-a {
                        top: 12%; left: 3%;
                        background: radial-gradient(ellipse, rgba(0,191,255,.38), transparent 68%);
                        transform: rotate(-18deg);
                    }
                    .cloud-b {
                        top: 46%; right: -4%;
                        background: radial-gradient(ellipse, rgba(168,85,247,.48), transparent 68%);
                        transform: rotate(23deg);
                        animation-delay: -5s;
                    }
                    .cloud-c {
                        bottom: -2%; left: 29%;
                        background: radial-gradient(ellipse, rgba(230,191,85,.20), transparent 66%);
                        transform: rotate(-8deg);
                        animation-delay: -10s;
                    }
                    .energy-rings {
                        z-index: 3;
                        pointer-events: none;
                        display: grid;
                        place-items: center;
                    }
                    .energy-rings::before,
                    .energy-rings::after {
                        content: "";
                        position: absolute;
                        width: 42%;
                        aspect-ratio: 2.9 / 1;
                        border: 1px solid rgba(0,191,255,.40);
                        border-radius: 50%;
                        transform: rotate(-13deg);
                        box-shadow: 0 0 18px rgba(0,191,255,.28);
                        animation: ringOrbit 9s linear infinite;
                    }
                    .energy-rings::after {
                        width: 59%;
                        border-color: rgba(168,85,247,.30);
                        transform: rotate(19deg);
                        animation-duration: 14s;
                        animation-direction: reverse;
                    }
                    .energy-rings {
                        background: radial-gradient(circle at 50% 52%,
                            rgba(230,191,85,.20) 0 2px,
                            rgba(0,191,255,.08) 3px,
                            transparent 12%);
                        animation: corePulse 4s ease-in-out infinite;
                    }
                    #galaxy-plot {
                        z-index: 4;
                        pointer-events: auto;
                    }
                    #galaxy-plot .plotly,
                    #galaxy-plot .main-svg {
                        background: transparent !important;
                    }
                    .scene-label {
                        z-index: 5;
                        inset: auto 18px 14px auto;
                        width: auto;
                        height: auto;
                        padding: 6px 10px;
                        border: 1px solid rgba(255,255,255,.12);
                        border-radius: 99px;
                        color: rgba(232,236,255,.70);
                        background: rgba(2,1,8,.48);
                        backdrop-filter: blur(8px);
                        font-size: 10px;
                        letter-spacing: .08em;
                        text-transform: uppercase;
                    }
                    @keyframes cloudDrift {
                        0% { transform: translate3d(-2%, 2%, 0) rotate(-18deg) scale(1); }
                        100% { transform: translate3d(7%, -5%, 0) rotate(-8deg) scale(1.18); }
                    }
                    @keyframes ringOrbit {
                        0% { transform: rotate(-13deg) scale(.92); opacity: .32; }
                        50% { opacity: .78; }
                        100% { transform: rotate(347deg) scale(1.08); opacity: .32; }
                    }
                    @keyframes corePulse {
                        0%, 100% { opacity: .62; }
                        50% { opacity: 1; }
                    }
                    @media (prefers-reduced-motion: reduce) {
                        .cloud, .energy-rings { animation: none; }
                    }
                </style>
            </head>
            <body>
                <main id="galaxy-stage">
                    <canvas id="starfield"></canvas>
                    <div class="nebula-clouds">
                        <div class="cloud cloud-a"></div>
                        <div class="cloud cloud-b"></div>
                        <div class="cloud cloud-c"></div>
                    </div>
                    <div class="energy-rings"></div>
                    <div id="galaxy-plot"></div>
                    <div class="scene-label">Live constellation · drag to orbit</div>
                </main>
                <script>
                    const plotSpec = """ + chart_payload + """;
                    const stage = document.getElementById("galaxy-stage");
                    const canvas = document.getElementById("starfield");
                    const ctx = canvas.getContext("2d");
                    const reduceMotion = window.matchMedia(
                        "(prefers-reduced-motion: reduce)"
                    ).matches;
                    let particles = [];
                    let frame = 0;

                    function resizeStars() {
                        const ratio = window.devicePixelRatio || 1;
                        canvas.width = stage.clientWidth * ratio;
                        canvas.height = stage.clientHeight * ratio;
                        canvas.style.width = stage.clientWidth + "px";
                        canvas.style.height = stage.clientHeight + "px";
                        ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
                    }
                    function seedStars() {
                        particles = Array.from({length: 145}, (_, index) => ({
                            x: Math.random() * stage.clientWidth,
                            y: Math.random() * stage.clientHeight,
                            r: Math.random() * 1.7 + 0.25,
                            a: Math.random() * 0.75 + 0.15,
                            speed: Math.random() * 0.28 + 0.04,
                            phase: Math.random() * Math.PI * 2,
                            color: index % 7 === 0 ? "#e6bf55" :
                                index % 5 === 0 ? "#00bfff" : "#d9d6ff"
                        }));
                    }
                    function drawStars() {
                        const width = stage.clientWidth;
                        const height = stage.clientHeight;
                        ctx.clearRect(0, 0, width, height);
                        particles.forEach((star) => {
                            if (!reduceMotion) {
                                star.y -= star.speed;
                                if (star.y < -4) {
                                    star.y = height + 4;
                                    star.x = Math.random() * width;
                                }
                            }
                            const twinkle = reduceMotion ? 1 :
                                0.70 + Math.sin(frame * 0.025 + star.phase) * 0.30;
                            ctx.beginPath();
                            ctx.fillStyle = star.color;
                            ctx.globalAlpha = star.a * twinkle;
                            ctx.shadowBlur = star.r > 1.25 ? 9 : 3;
                            ctx.shadowColor = star.color;
                            ctx.arc(star.x, star.y, star.r, 0, Math.PI * 2);
                            ctx.fill();
                        });
                        ctx.globalAlpha = 1;
                        ctx.shadowBlur = 0;
                        frame += 1;
                        if (!reduceMotion) requestAnimationFrame(drawStars);
                    }
                    window.addEventListener("resize", () => {
                        resizeStars();
                        seedStars();
                    });
                    resizeStars();
                    seedStars();
                    drawStars();
                    Plotly.newPlot(
                        "galaxy-plot",
                        plotSpec.data,
                        plotSpec.layout,
                        {responsive: true, displaylogo: false, scrollZoom: true}
                    );
                </script>
            </body>
            </html>
            """
            components.html(galaxy_scene, height=700, scrolling=False)
            st.caption(
                "Scroll to zoom · drag to pan · hover a star for the player profile. "
                "Higher-ranked players orbit nearer the galactic core."
            )

            galaxy_cols = [
                "Player/Team", "Team", "Type", "Rarity", "GP", "PTS", "REB", "AST",
                "STL", "BLK", "Stocks", "Shots Affected", "Tipped Passes", "FB Points",
                "Disruption", "Hustle", "TS%", "eFG%", "Game Score", "PIE", "ORtg",
            ]
            galaxy_cols = [c for c in galaxy_cols if c in galaxy_view.columns]
            galaxy_table = galaxy_view[galaxy_cols].copy()
            galaxy_numeric = [
                c for c in galaxy_table.columns
                if c not in ("Player/Team", "Team", "Type", "Rarity", "GP")
            ]
            galaxy_table[galaxy_numeric] = galaxy_table[galaxy_numeric].round(1)
            st.markdown(
                f"#### ✦ {len(galaxy_view)} players in the constellation"
            )
            st.caption(
                f"Cards are ranked by {galaxy_sort_label}. Percentile bars show each "
                "player's standing within the filtered Galaxy."
            )
            card_html = []
            for rank, (_, player_row) in enumerate(galaxy_view.iterrows(), start=1):
                secondary = [
                    ("PTS", _galaxy_stat_value(player_row.get("PTS", 0), "PTS")),
                    ("PIE", _galaxy_stat_value(player_row.get("PIE", 0), "PIE")),
                    ("TS%", _galaxy_stat_value(player_row.get("TS%", 0), "TS%")),
                    ("GP", _galaxy_stat_value(player_row.get("GP", 0), "GP")),
                ]
                card_html.append(
                    render_modern_stat_row(
                        rank=rank,
                        name=player_row.get("Player/Team", "Unknown"),
                        team=player_row.get("Team", "—"),
                        stat_val=_galaxy_stat_value(
                            player_row.get(galaxy_sort_column, 0),
                            galaxy_sort_column,
                        ),
                        percentile=float(player_row.get("_GalaxyPercentile", 0)),
                        color=_galaxy_tier_color(
                            float(player_row.get("_GalaxyPercentile", 0))
                        ),
                        stat_label=galaxy_sort_label,
                        archetype=player_row.get("Type", ""),
                        secondary_stats=secondary,
                    )
                )
            st.markdown("".join(card_html), unsafe_allow_html=True)
            dl(
                galaxy_table, "⬇️ Player Galaxy CSV",
                "qcl_player_galaxy.csv", "dl_player_galaxy",
            )

        with st.expander("🌟 Player type key"):
            st.markdown(
                "**Microwave Chucker** = highest three-point volume · "
                "**Glass Cleaner** = rebound specialist · "
                "**Pocket Picker** = steals leader · "
                "**Rim Protector** = blocks leader · "
                "**Corner Specialist** = three-point efficiency · "
                "**Dime Dropper** = assists leader · "
                "**Lockdown Wing** = steals + blocks · "
                "**Iron Man Grind** = games-played durability · "
                "**Combo Guard** = scoring leader."
            )


# --------------------------------------------------------- POWER RANKINGS ----
elif view_mode == "🏆 Power Rankings & SOS":
    st.subheader("📊 League Power Index")
    st.markdown("Tune the weights — the board re-sorts live.")


    wc1, wc2, wc3 = st.columns(3)
    w_win = wc1.slider("Win% weight", 0.0, 1.0, 0.50, 0.05)
    w_sos = wc2.slider("SOS weight", 0.0, 1.0, 0.25, 0.05)
    w_net = wc3.slider("NetRtg weight", 0.0, 1.0, 0.25, 0.05)
    tot_w = max(w_win + w_sos + w_net, 0.01)


    ranks = t_stats.copy()
    net_span = max(float(ranks['NetRtg'].max() - ranks['NetRtg'].min()), 0.01)
    net_norm = (ranks['NetRtg'] - ranks['NetRtg'].min()) / net_span
    ranks['True_Power'] = ((ranks['Win%'] * w_win) + (ranks['SOS'].fillna(0.5) * w_sos) + (net_norm * w_net)) / tot_w
    ranks = ranks.sort_values('True_Power', ascending=False).reset_index(drop=True)


    form_map, streak_map, win_streak_len = {}, {}, {}
    for team, g in t_df.sort_values(['Season', 'Game_ID']).groupby('Team Name'):
        seq = [int(w) for w in g['Win'].tolist()]
        if not seq:
            form_map[team], streak_map[team], win_streak_len[team] = "-", "-", 0
            continue
        form_map[team] = " ".join([f"<span style='color:{GREEN}; font-weight:bold;'>W</span>" if w
                                   else f"<span style='color:{RED}; font-weight:bold;'>L</span>"
                                   for w in seq[-5:]])
        s = 0
        for w in reversed(seq):
            if w == seq[-1]:
                s += 1
            else:
                break
        streak_map[team] = f"{'W' if seq[-1] else 'L'}{s}"
        win_streak_len[team] = s if seq[-1] else 0


    html = ("<table class='sleek-table'><tr><th>Rank</th><th>Team</th><th>Record</th><th>Win%</th>"
            "<th>SOS</th><th>NetRtg</th><th>Pt Diff</th><th>Form (L5)</th><th>Streak</th></tr>")
    for i, r in ranks.iterrows():
        medal = "🥇 " if i == 0 else "🥈 " if i == 1 else "🥉 " if i == 2 else f"{i+1}. "
        tname = r['Team Name']
        marked = (" <span style='background:#cc0000; color:#fff; font-size:10px; font-weight:bold; "
                  "padding:2px 6px; border-radius:4px; letter-spacing:1px;'>🎯 MARKED</span>"
                  if win_streak_len.get(tname, 0) >= 3 else "")
        stk = streak_map.get(tname, '-')
        sc = GREEN if stk.startswith('W') else RED if stk.startswith('L') else '#888'
        nc = GREEN if r['NetRtg'] >= 0 else RED
        html += (f"<tr><td style='font-size:16px;'>{medal}</td><td class='player-name'>{team_logo_html(tname, px=20)}{tname}{marked}</td>"
                 f"<td>{int(r['Wins'])}-{int(r['GP']-r['Wins'])}</td><td>{r['Win%']:.3f}</td>"
                 f"<td style='color:{BLUE};'>{fnum(r['SOS']):.3f}</td>"
                 f"<td style='color:{nc}; font-weight:bold;'>{r['NetRtg']:+.1f}</td>"
                 f"<td>{fnum(r['Diff']):+.1f}</td><td>{form_map.get(tname, '-')}</td>"
                 f"<td style='color:{sc}; font-weight:bold;'>{stk}</td></tr>")
    st.markdown(html + "</table>", unsafe_allow_html=True)
    st.caption("🎯 MARKED = active 3+ game win streak. Bounty-eligible under The Hunt.")
    dl(ranks[['Team Name', 'Wins', 'GP', 'Win%', 'SOS', 'NetRtg', 'Diff', 'True_Power']],
       "⬇️ Power rankings CSV", "power_rankings.csv", "dl_pr")




# ----------------------------------------------------------- FRANCHISE HUB ---
elif view_mode == "🏢 Franchise Hub":
    teams = sorted([t for t in p_stats['Team'].dropna().unique() if str(t) != '0'])
    if not teams:
        st.info("No teams in scope.")
    else:
        sel_team = st.selectbox("Select Franchise", teams)
        _tlogo = _cached_logo_uri(sel_team)
        if _tlogo:
            st.markdown("<style>.stApp::before{content:'';position:fixed;inset:0;background:url('"
                        + _tlogo + "') center/contain no-repeat;opacity:0.06;pointer-events:none;z-index:0;}</style>",
                        unsafe_allow_html=True)
        st.markdown(f"<div class='header-banner'>{team_logo_html(sel_team, px=34, mr=10)}{sel_team}</div>",
                    unsafe_allow_html=True)


        t_data = t_df[t_df['Team Name'] == sel_team]
        p_data = p_df[p_df['Team Name'] == sel_team]
        t_hit = t_stats[t_stats['Team Name'] == sel_team]


        if t_hit.empty:
            st.warning(f"{sel_team} has player rows but no team-game totals in this scope.")
        else:
            t_row = t_hit.iloc[0]
            tab_dash, tab_rot, tab_binder, tab_box = st.tabs(
                ["📋 Dashboard", "🔁 Rotation", "📇 Player Binder", "📓 Box Scores"])


            with tab_dash:
                c1, c2, c3, c4 = st.columns(4)
                wins = int(t_row['Wins'])
                losses = int(t_row['GP'] - wins)
                c1.markdown(f"<div class='metric-box'><div class='metric-title'>Record</div><div class='metric-value'>{wins} - {losses}</div><div class='metric-sub'>{t_row['Win%']:.3f}</div></div>", unsafe_allow_html=True)
                c2.markdown(f"<div class='metric-box'><div class='metric-title'>Point Diff</div><div class='metric-value'>{fnum(t_row['Diff']):+.1f}</div></div>", unsafe_allow_html=True)
                c3.markdown(f"<div class='metric-box'><div class='metric-title'>Net Rating</div><div class='metric-value'>{t_row['NetRtg']:+.1f}</div><div class='metric-sub'>ORtg {t_row['ORtg']:.1f} / DRtg {t_row['DRtg']:.1f}</div></div>", unsafe_allow_html=True)
                c4.markdown(f"<div class='metric-box'><div class='metric-title'>Strength of Sched</div><div class='metric-value'>{fnum(t_row['SOS']):.3f}</div></div>", unsafe_allow_html=True)


                sc1, sc2 = st.columns(2)
                with sc1:
                    st.markdown("### 🎯 Team Identity vs League")
                    lg_avg = t_stats.mean(numeric_only=True)
                    mx = t_stats.max(numeric_only=True)
                    cats = ['Scoring', 'Playmaking', 'Rebounding', 'Defense', 'Efficiency']
                    r1 = [norm(t_row['PPG'], mx['PPG']), norm(t_row['APG'], mx['APG']),
                          norm(t_row['RPG'], mx['RPG']), norm(t_row['DEF'], mx['DEF']),
                          norm(t_row['eFG%'], mx['eFG%'])]
                    r2 = [norm(lg_avg['PPG'], mx['PPG']), norm(lg_avg['APG'], mx['APG']),
                          norm(lg_avg['RPG'], mx['RPG']), norm(lg_avg['DEF'], mx['DEF']),
                          norm(lg_avg['eFG%'], mx['eFG%'])]
                    st.plotly_chart(draw_dynamic_radar(sel_team, r1, "League Avg", r2, cats, "Team Identity"),
                                    use_container_width=True)
                with sc2:
                    st.markdown("### 📈 Game-by-Game Margin")
                    gl = t_data.sort_values(['Season', 'Game_ID']).reset_index(drop=True)
                    gl['G'] = gl.index + 1
                    gl['Margin'] = gl['Point_Diff'].fillna(0)
                    fig = px.bar(gl, x='G', y='Margin', template='plotly_dark',
                                 color=gl['Margin'].apply(lambda x: 'W' if x > 0 else 'L'),
                                 color_discrete_map={'W': GREEN, 'L': RED},
                                 labels={'Margin': 'Margin', 'G': 'Game'})
                    fig.update_layout(showlegend=False, height=380,
                                      paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
                    st.plotly_chart(fig, use_container_width=True)


            with tab_rot:
                st.markdown("### 🔁 Five-Man Rotation (by Games Played)")
                st.caption("This is the exact five the Oracle simulates. GP is the primary sort — "
                           "PIE only breaks ties.")
                rot = get_rotation(sel_team)
                bench = full_roster(sel_team)
                bench = bench[~bench['Player/Team'].isin(rot['Player/Team'])]


                html = ("<table class='sleek-table'><tr><th>#</th><th>Player</th><th>GP</th><th>PPG</th>"
                        "<th>RPG</th><th>APG</th><th>USG%</th><th>PIE</th></tr>")
                for i, r in rot.iterrows():
                    html += (f"<tr><td style='color:{GOLD}; font-weight:bold;'>{i+1}</td>"
                             f"<td class='player-name'>{r['Player/Team']}</td><td>{int(r['GP'])}</td>"
                             f"<td>{r['PTS']:.1f}</td><td>{r['REB']:.1f}</td><td>{r['AST']:.1f}</td>"
                             f"<td>{r['USG']:.1f}</td><td style='color:{GOLD}; font-weight:bold;'>{r['PIE']:.1f}</td></tr>")
                st.markdown(html + "</table>", unsafe_allow_html=True)


                if not bench.empty:
                    st.markdown("#### 🪑 Depth (outside the five)")
                    render_modern_dataframe(
                        bench[['Player/Team', 'GP', 'PTS', 'REB', 'AST', 'PIE']],
                        name_hint="Player/Team",
                    )


            with tab_binder:
                q = st.text_input("Search roster", "", key="binder_q")
                team_p = p_stats[p_stats['Team'] == sel_team]
                if q:
                    team_p = team_p[team_p['Player/Team'].str.contains(q, case=False, na=False)]
                team_p = team_p.reset_index(drop=True)
                _bnames = team_p['Player/Team'].tolist()
                if _bnames:
                    _bpick = st.selectbox("🔁 Rotating player card", _bnames, key="binder_pick")
                    render_rotating_card(_bpick, key="binder", team=sel_team)
                    st.markdown("<hr>", unsafe_allow_html=True)
                st.markdown("#### 📇 Full Roster")
                cols = st.columns(4)
                for idx, row in team_p.iterrows():
                    with cols[idx % 4]:
                        st.markdown(generate_2k_player_card(row['Player/Team'], row, rank=row['League_Rank']),
                                    unsafe_allow_html=True)


            with tab_box:
                game_opts = sorted(p_data[['Season', 'Game_ID']].dropna().drop_duplicates()
                                   .itertuples(index=False, name=None), reverse=True)
                if not game_opts:
                    st.info("No games logged.")
                else:
                    sel_game = st.selectbox("Select Game", game_opts,
                                            format_func=lambda t: f"S{int(t[0])} • Game {int(t[1])}")
                    g_data = p_data[(p_data['Season'] == sel_game[0]) & (p_data['Game_ID'] == sel_game[1])]
                    if not g_data.empty:
                        potg = g_data.loc[g_data['PIE_Raw'].idxmax()]
                        opp = g_data['Opp_Name'].iloc[0] if 'Opp_Name' in g_data.columns else None
                        opp = opp if pd.notna(opp) else "—"
                        st.markdown(
                            f"<div style='background: linear-gradient(90deg, #111, #333); padding:15px; "
                            f"border-left:5px solid #d4af37; margin-bottom:15px;'>"
                            f"<h4 style='margin:0; color:#aaa;'>PLAYER OF THE GAME — vs {opp}</h4>"
                            f"<h2 style='margin:0; color:#fff;'>{potg['Player/Team']}</h2>"
                            f"<p style='margin:0; color:#d4af37;'>{int(potg['PTS'])} PTS | {int(potg['REB'])} REB "
                            f"| {int(potg['AST'])} AST | {potg['PIE_Raw']:.1f} PIE</p></div>",
                            unsafe_allow_html=True)
                        st.markdown(generate_sleek_box_score(g_data), unsafe_allow_html=True)
                        st.markdown("#### Game Shot Profile")
                        st.markdown(draw_shot_profile(g_data['FGM'].sum(), g_data['FGA'].sum(),
                                                      g_data['3PM'].sum(), g_data['3PA'].sum()),
                                    unsafe_allow_html=True)




# -------------------------------------------------------- PLAYER SPOTLIGHT ---
elif view_mode == "🔦 Player Spotlight":
    st.subheader("🔦 Player Spotlight")
    names = sorted(p_stats['Player/Team'].tolist())
    if not names:
        st.info("No players in scope.")
    else:
        default_i = names.index(st.session_state.watchlist[0]) if (
            st.session_state.watchlist and st.session_state.watchlist[0] in names) else 0
        sel = st.selectbox("Player", names, index=default_i)
        row = p_stats[p_stats['Player/Team'] == sel].iloc[0]
        logs = p_df[p_df['Player/Team'] == sel].sort_values(['Season', 'Game_ID']).reset_index(drop=True)


        render_rotating_card(sel, key="spotlight", team=row.get('Team'))
        if st.button("★ Toggle Watchlist", use_container_width=True):
            toggle_watch(sel)
            _rerun()
        with st.container():
            m = st.columns(5)
            for col, (lab, val) in zip(m, [("PPG", f"{row['PTS']:.1f}"), ("RPG", f"{row['REB']:.1f}"),
                                           ("APG", f"{row['AST']:.1f}"), ("STOCKS", f"{row['DEF']:.1f}"),
                                           ("TS%", f"{row['TS%']:.1f}%")]):
                col.markdown(f"<div class='metric-box'><div class='metric-title'>{lab}</div>"
                             f"<div class='metric-value'>{val}</div></div>", unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)


            metric = st.selectbox("Trend metric", ['PIE_Raw', 'PTS', 'REB', 'AST', 'STL', 'BLK',
                                                   'Game_Score', 'USG_Game'], index=0)
            logs['G'] = logs.index + 1
            logs['Rolling'] = logs[metric].rolling(3, min_periods=1).mean()
            fig = go.Figure()
            fig.add_trace(go.Bar(x=logs['G'], y=logs[metric], name=metric,
                                 marker_color=['#2f6b3f' if w else '#6b2f2f' for w in logs['Win']]))
            fig.add_trace(go.Scatter(x=logs['G'], y=logs['Rolling'], name='3-game avg',
                                     line=dict(color=GOLD, width=3)))
            fig.add_hline(y=float(logs[metric].mean()), line_dash="dot", line_color="#888")
            fig.update_layout(template='plotly_dark', height=320, paper_bgcolor='rgba(0,0,0,0)',
                              plot_bgcolor='rgba(0,0,0,0)', margin=dict(l=10, r=10, t=30, b=10))
            st.plotly_chart(fig, use_container_width=True)


        st.markdown("### 🔀 Splits")
        s1, s2 = st.columns(2)
        with s1:
            st.markdown("**Wins vs Losses**")
            spl = logs.groupby('Win').agg(GP=('GKey', 'nunique'), PTS=('PTS', 'mean'),
                                          REB=('REB', 'mean'), AST=('AST', 'mean'),
                                          PIE=('PIE_Raw', 'mean')).reset_index()
            spl['Win'] = spl['Win'].map({1: 'W', 0: 'L'})
            render_modern_dataframe(spl.round(1), name_hint="Player/Team")
        with s2:
            st.markdown("**By Opponent**")
            if 'Opp_Name' in logs.columns and logs['Opp_Name'].notna().any():
                opp = logs[logs['Opp_Name'].notna()].groupby('Opp_Name').agg(
                    GP=('GKey', 'nunique'), PTS=('PTS', 'mean'), PIE=('PIE_Raw', 'mean')).reset_index()
                render_modern_dataframe(
                    opp.round(1).sort_values('PIE', ascending=False),
                    name_hint="Opp_Name",
                )
            else:
                st.info("No opponent data yet.")


        st.markdown("### 📓 Game Log")
        cols = [c for c in ['Season', 'Game_ID', 'Opp_Name', 'Win', 'PTS', 'REB', 'AST', 'STL', 'BLK',
                            'FGM', 'FGA', '3PM', '3PA', 'TO', 'PIE_Raw', 'Game_Score'] if c in logs.columns]
        show = logs[cols].copy()
        show['Win'] = show['Win'].map({1: 'W', 0: 'L'})
        render_modern_dataframe(show.round(1), name_hint="Player/Team")
        dl(show, "⬇️ Game log CSV", f"{sel}_gamelog.csv", "dl_log")




# ------------------------------------------------------------- DATABASE ------
elif view_mode == "🗃️ Full Player Database":
    st.subheader("🗃️ Interactive Player Universe")


    f1, f2, f3 = st.columns([2, 2, 2])
    q = f1.text_input("🔍 Search player")
    team_filter = f2.multiselect("Teams", sorted(p_view['Team'].dropna().unique().tolist()))
    sort_by = f3.selectbox("Sort by", ['PIE', 'PTS', 'REB', 'AST', 'DEF', 'GmSc', 'NetRtg',
                                       'USG', 'TS%', 'GP'], index=0)


    view = p_view.copy()
    if q:
        view = view[view['Player/Team'].str.contains(q, case=False, na=False)]
    if team_filter:
        view = view[view['Team'].isin(team_filter)]
    view = view.sort_values(sort_by, ascending=False)


    if view.empty:
        st.info("No players match the current filters.")
    else:
        x_ax = st.selectbox("Scatter X-axis", ['TS%', 'USG', 'eFG%', 'ORtg', 'FG%', 'GP'], index=0)
        fig = px.scatter(view, x=x_ax, y="PIE", size="PTS", color="Team",
                         hover_name="Player/Team",
                         hover_data={"PTS": True, "REB": True, "AST": True, "DEF": True, "GP": True, "Team": False},
                         template="plotly_dark", title=f"League Landscape: {x_ax} vs Impact (PIE)")
        fig.update_layout(paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
        st.plotly_chart(fig, use_container_width=True)


        st.markdown(f"### 📊 Master Roster — {len(view)} players")
        cols = ['Player/Team', 'Team', 'GP', 'PTS', 'REB', 'AST', 'STL', 'BLK', 'TO',
                'FG%', '3P%', 'TS%', 'eFG%', 'USG', 'ORtg', 'DRtg', 'NetRtg', 'GmSc', 'PIE']
        render_modern_dataframe(view[cols], name_hint="Player/Team")
        dl(view[cols], "⬇️ Roster CSV", "qcl_roster.csv", "dl_roster")




# ------------------------------------------------------------------ H2H ------
elif view_mode == "⚔️ Head-to-Head Radar":
    st.subheader("🕸️ Player Head-to-Head")
    player_list = sorted(p_stats['Player/Team'].tolist())
    if len(player_list) < 2:
        st.info("Need at least two players.")
    else:
        c1, c2 = st.columns(2)
        p1_sel = c1.selectbox("Player 1 (Gold)", player_list)
        p2_sel = c2.selectbox("Player 2 (Red)", player_list, index=1)


        d1 = p_stats[p_stats['Player/Team'] == p1_sel].iloc[0]
        d2 = p_stats[p_stats['Player/Team'] == p2_sel].iloc[0]
        mx = p_stats.max(numeric_only=True)


        cats = ['Scoring', 'Playmaking', 'Rebounding', 'Defense', 'Efficiency', 'Usage']
        r1 = [norm(d1['PTS'], mx['PTS']), norm(d1['AST'], mx['AST']), norm(d1['REB'], mx['REB']),
              norm(d1['DEF'], mx['DEF']), norm(d1['TS%'], 75), norm(d1['USG'], mx['USG'])]
        r2 = [norm(d2['PTS'], mx['PTS']), norm(d2['AST'], mx['AST']), norm(d2['REB'], mx['REB']),
              norm(d2['DEF'], mx['DEF']), norm(d2['TS%'], 75), norm(d2['USG'], mx['USG'])]


        cA, cB = st.columns([3, 2])
        with cA:
            st.plotly_chart(draw_dynamic_radar(p1_sel, r1, p2_sel, r2, cats, "The 6-Tool Core"),
                            use_container_width=True)
        with cB:
            st.markdown("#### Tale of the Tape")
            rows = [('GP', 'GP', 0), ('PPG', 'PTS', 1), ('RPG', 'REB', 1), ('APG', 'AST', 1),
                    ('STOCKS', 'DEF', 1), ('TS%', 'TS%', 1), ('USG%', 'USG', 1),
                    ('GmSc', 'GmSc', 1), ('PIE', 'PIE', 1)]
            html = "<table class='sleek-table'><tr><th>" + p1_sel + "</th><th>Stat</th><th>" + p2_sel + "</th></tr>"
            for label, col, dec in rows:
                v1, v2 = fnum(d1[col]), fnum(d2[col])
                c1c = GOLD if v1 >= v2 else "#888"
                c2c = "#cc0000" if v2 >= v1 else "#888"
                html += (f"<tr><td style='color:{c1c}; font-weight:bold;'>{v1:.{dec}f}</td>"
                         f"<td style='color:#8b949e;'>{label}</td>"
                         f"<td style='color:{c2c}; font-weight:bold;'>{v2:.{dec}f}</td></tr>")
            st.markdown(html + "</table>", unsafe_allow_html=True)




# ------------------------------------------------------------- LINEUP LAB ----
elif view_mode == "🧪 Lineup Lab":
    st.subheader("🧪 Lineup Lab — Build Any Five")
    st.markdown("Assemble two five-man units from anywhere in the league and see which unit wins on paper.")


    names = sorted(p_stats['Player/Team'].tolist())
    if len(names) < 10:
        st.info("Need at least 10 players in scope.")
    else:
        lc1, lc2 = st.columns(2)
        with lc1:
            u1 = st.multiselect("Unit A (Gold) — pick 5", names, default=names[:5])
        with lc2:
            u2 = st.multiselect("Unit B (Red) — pick 5", names, default=names[5:10])


        if len(u1) != 5 or len(u2) != 5:
            st.info(f"Pick exactly five per unit. Unit A has {len(u1)}, Unit B has {len(u2)}.")
        else:
            a = p_stats[p_stats['Player/Team'].isin(u1)]
            b = p_stats[p_stats['Player/Team'].isin(u2)]


            def unit_line(u):
                return dict(PTS=u['PTS'].sum(), REB=u['REB'].sum(), AST=u['AST'].sum(),
                            DEF=u['DEF'].sum(), TS=u['TS%'].mean(), PIE=u['PIE'].sum(),
                            TO=u['TO'].sum())


            la, lb = unit_line(a), unit_line(b)
            mx = {k: max(la[k], lb[k]) for k in la}
            cats = ['Scoring', 'Rebounding', 'Playmaking', 'Defense', 'Efficiency', 'Impact']
            ra = [norm(la['PTS'], mx['PTS']), norm(la['REB'], mx['REB']), norm(la['AST'], mx['AST']),
                  norm(la['DEF'], mx['DEF']), norm(la['TS'], mx['TS']), norm(la['PIE'], mx['PIE'])]
            rb = [norm(lb['PTS'], mx['PTS']), norm(lb['REB'], mx['REB']), norm(lb['AST'], mx['AST']),
                  norm(lb['DEF'], mx['DEF']), norm(lb['TS'], mx['TS']), norm(lb['PIE'], mx['PIE'])]


            r1, r2 = st.columns([3, 2])
            with r1:
                st.plotly_chart(draw_dynamic_radar("Unit A", ra, "Unit B", rb, cats, "Unit vs Unit"),
                                use_container_width=True)
            with r2:
                edge = la['PIE'] - lb['PIE']
                winner = "UNIT A" if edge > 0 else "UNIT B"
                color = GOLD if edge > 0 else "#cc0000"
                st.markdown(f"<div class='sim-box'><div class='line-label'>Composite Edge</div>"
                            f"<h1 style='color:{color}; margin:6px 0;'>{winner}</h1>"
                            f"<div class='line-value'>{abs(edge):.1f} PIE</div>"
                            f"<p style='color:#888; margin-top:8px;'>A {la['PTS']:.1f} PPG "
                            f"vs B {lb['PTS']:.1f} PPG</p></div>", unsafe_allow_html=True)


            st.markdown("#### Unit Sheets")
            uc1, uc2 = st.columns(2)
            cols = ['Player/Team', 'Team', 'GP', 'PTS', 'REB', 'AST', 'DEF', 'TS%', 'PIE']
            uc1.dataframe(a[cols], use_container_width=True, hide_index=True)
            uc2.dataframe(b[cols], use_container_width=True, hide_index=True)




# ---------------------------------------------------------- RIVALRY CORNER ---
elif view_mode == "🥊 Rivalry Corner":
    st.subheader("🥊 Rivalry Corner")
    min_meets = st.slider("Minimum meetings to qualify as a rivalry", 2, 10, 4)


    matchups = full_df[full_df['Type'].astype(str).str.lower() == 'team'].copy()
    if 'Opp_Name' not in matchups.columns or matchups['Opp_Name'].isna().all():
        st.info("No head-to-head matchup data available yet.")
    else:
        matchups = matchups[matchups['Opp_Name'].notna()]
        matchups['Pairing'] = matchups.apply(
            lambda r: " vs ".join(sorted([str(r['Team Name']), str(r['Opp_Name'])])), axis=1)
        rivals = matchups.groupby('Pairing').agg(Games=('GKey', 'nunique')).reset_index()
        rivals = rivals[rivals['Games'] >= min_meets].sort_values('Games', ascending=False)


        if rivals.empty:
            st.info(f"No teams have played {min_meets}+ games against each other yet.")
        else:
            for _, riv in rivals.iterrows():
                t1, t2 = riv['Pairing'].split(" vs ")
                t1_wins = len(matchups[(matchups['Team Name'] == t1) & (matchups['Opp_Name'] == t2) & (matchups['Win'] == 1)])
                t2_wins = len(matchups[(matchups['Team Name'] == t2) & (matchups['Opp_Name'] == t1) & (matchups['Win'] == 1)])
                with st.expander(f"⚔️ {t1} vs {t2} — {riv['Games']} meetings ({t1_wins}-{t2_wins})", expanded=False):
                    st.markdown(
                        f"<div style='display:flex; justify-content:space-around; margin:10px 0;'>"
                        f"<div style='text-align:center;'>{team_logo_html(t1, px=30, mr=0)}<h2 style='color:{GREEN};'>{t1_wins}</h2><p>{t1}</p></div>"
                        f"<div style='text-align:center;'>{team_logo_html(t2, px=30, mr=0)}<h2 style='color:{RED};'>{t2_wins}</h2><p>{t2}</p></div></div>",
                        unsafe_allow_html=True)
                    gk = matchups[matchups['Pairing'] == riv['Pairing']][['Season', 'Game_ID']].drop_duplicates()
                    for (gs, gid) in sorted(gk.itertuples(index=False, name=None), reverse=True)[:5]:
                        g_rows = matchups[(matchups['Season'] == gs) & (matchups['Game_ID'] == gid)
                                          & (matchups['Pairing'] == riv['Pairing'])]
                        if len(g_rows) == 2:
                            r1, r2 = g_rows.iloc[0], g_rows.iloc[1]
                            st.markdown(f"<span style='color:#888;'>S{int(gs)} • G{int(gid)}</span> &nbsp; "
                                        f"{r1['Team Name']} <b style='color:#fff;'>{int(r1['PTS'])}</b> — "
                                        f"{r2['Team Name']} <b style='color:#fff;'>{int(r2['PTS'])}</b>",
                                        unsafe_allow_html=True)




# ------------------------------------------------------------- PLAYOFFS ------
elif view_mode == "🏆 Playoffs":
    st.subheader("🏆 Playoff Central")
    st.markdown("Playoff games only — **Game_ID 9001–9999**. Same stat engine as the regular "
                "season, run over postseason games in the current scope. (Ignores the sidebar "
                "Game Type filter.)")


    if selected_scope == "Career Stats":
        po_scope = full_df
    else:
        po_scope = full_df[full_df['Season'] == target_season]


    po_src = po_scope[(po_scope['Game_ID'] >= 9001) & (po_scope['Game_ID'] <= 9999)]


    if po_src.empty:
        st.info("No playoff games logged in this scope yet (playoffs = Game_ID 9001–9999).")
    else:
        PO = compute_stats(po_src, full_df, min_gp_filter=0)
        if PO is None:
            st.warning("Playoff games found, but not enough player/team rows to build stats.")
        else:
            po_p_stats, po_t_stats = PO['p_stats'], PO['t_stats']
            po_p_df, po_t_df = PO['p_df'], PO['t_df']


            m1, m2, m3, m4 = st.columns(4)
            m1.markdown(f"<div class='metric-box'><div class='metric-title'>Playoff Games</div><div class='metric-value'>{po_src['GKey'].nunique()}</div></div>", unsafe_allow_html=True)
            m2.markdown(f"<div class='metric-box'><div class='metric-title'>Players</div><div class='metric-value'>{po_p_stats['Player/Team'].nunique()}</div></div>", unsafe_allow_html=True)
            m3.markdown(f"<div class='metric-box'><div class='metric-title'>Teams</div><div class='metric-value'>{po_t_stats['Team Name'].nunique()}</div></div>", unsafe_allow_html=True)
            m4.markdown(f"<div class='metric-box'><div class='metric-title'>Playoff PPG</div><div class='metric-value'>{fnum(po_t_stats['PPG'].mean()):.1f}</div></div>", unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)


            po_tabs = st.tabs(["🏆 Leaders", "📊 Player Stats", "🏟️ Team Stats", "🔥 Single-Game Highs"])


            # ---- LEADERS (per-game + totals) ----
            with po_tabs[0]:
                st.markdown("#### Per-Game Leaders")
                depth = st.slider("Show top N", 3, 15, 5, key="po_leaders_depth")
                lc1, lc2, lc3 = st.columns(3)
                with lc1:
                    st.markdown(generate_mini_leaderboard("PPG", po_p_stats, 'PTS', "#cc0000", depth, "Player/Team"), unsafe_allow_html=True)
                    st.markdown(generate_mini_leaderboard("STOCKS", po_p_stats, 'DEF', "#ff8c00", depth, "Player/Team"), unsafe_allow_html=True)
                with lc2:
                    st.markdown(generate_mini_leaderboard("RPG", po_p_stats, 'REB', "#32cd32", depth, "Player/Team"), unsafe_allow_html=True)
                    st.markdown(generate_mini_leaderboard("PIE", po_p_stats, 'PIE', GOLD, depth, "Player/Team"), unsafe_allow_html=True)
                with lc3:
                    st.markdown(generate_mini_leaderboard("APG", po_p_stats, 'AST', "#00bfff", depth, "Player/Team"), unsafe_allow_html=True)
                    st.markdown(generate_mini_leaderboard("GmSc", po_p_stats, 'GmSc', "#8a2be2", depth, "Player/Team"), unsafe_allow_html=True)


                st.markdown("#### Totals")
                po_tot = po_p_df.groupby('Player/Team').sum(numeric_only=True).reset_index()
                tc1, tc2, tc3 = st.columns(3)
                tc1.markdown(generate_mini_leaderboard("Total PTS", po_tot, 'PTS', "#cc0000", depth, "Player/Team"), unsafe_allow_html=True)
                tc2.markdown(generate_mini_leaderboard("Total REB", po_tot, 'REB', "#32cd32", depth, "Player/Team"), unsafe_allow_html=True)
                tc3.markdown(generate_mini_leaderboard("Total AST", po_tot, 'AST', "#00bfff", depth, "Player/Team"), unsafe_allow_html=True)


            # ---- PLAYER STATS (same columns as Full Player Database) ----
            with po_tabs[1]:
                f1, f2 = st.columns([2, 2])
                q = f1.text_input("🔍 Search player", key="po_pq")
                sort_by = f2.selectbox("Sort by", ['PIE', 'PTS', 'REB', 'AST', 'DEF', 'GmSc',
                                                   'NetRtg', 'USG', 'TS%', 'GP'], index=0, key="po_sort")
                view = po_p_stats.copy()
                if q:
                    view = view[view['Player/Team'].str.contains(q, case=False, na=False)]
                view = view.sort_values(sort_by, ascending=False)
                cols = ['Player/Team', 'Team', 'GP', 'PTS', 'REB', 'AST', 'STL', 'BLK', 'TO',
                        'FG%', '3P%', 'TS%', 'eFG%', 'USG', 'ORtg', 'DRtg', 'NetRtg', 'GmSc', 'PIE']
                cols = [c for c in cols if c in view.columns]
                st.markdown(f"##### Playoff Player Stats — {len(view)} players")
                render_modern_dataframe(view[cols], name_hint="Player/Team")
                dl(view[cols], "⬇️ Playoff stats CSV", "qcl_playoff_players.csv", "dl_po_players")


            # ---- TEAM STATS ----
            with po_tabs[2]:
                st.markdown("##### Playoff Team Board")
                html = ("<table class='sleek-table'><tr><th>Team</th><th>Record</th><th>Win%</th>"
                        "<th>PPG</th><th>Opp PPG</th><th>ORtg</th><th>DRtg</th><th>NetRtg</th><th>Pace</th></tr>")
                for _, r in po_t_stats.sort_values(['Win%', 'NetRtg'], ascending=False).iterrows():
                    wins = int(r['Wins'])
                    losses = int(r['GP'] - r['Wins'])
                    nc = GREEN if r['NetRtg'] >= 0 else RED
                    html += (f"<tr><td class='player-name'>{team_logo_html(r['Team Name'], px=18)}{r['Team Name']}</td>"
                             f"<td>{wins}-{losses}</td><td>{r['Win%']:.3f}</td>"
                             f"<td>{r['PPG']:.1f}</td><td>{fnum(r['OppPPG']):.1f}</td>"
                             f"<td>{r['ORtg']:.1f}</td><td>{r['DRtg']:.1f}</td>"
                             f"<td style='color:{nc}; font-weight:bold;'>{r['NetRtg']:+.1f}</td>"
                             f"<td>{r['Pace']:.1f}</td></tr>")
                st.markdown(html + "</table>", unsafe_allow_html=True)
                dl(po_t_stats, "⬇️ Playoff team stats CSV", "qcl_playoff_teams.csv", "dl_po_teams")


                st.markdown("##### 💥 Biggest Playoff Blowouts")
                blow = po_t_df[po_t_df['Point_Diff'].notna() & (po_t_df['Point_Diff'] > 0)] \
                    .sort_values('Point_Diff', ascending=False).head(10)
                if blow.empty:
                    st.info("No head-to-head playoff games recorded yet.")
                else:
                    html = "<table class='sleek-table'><tr><th>Season</th><th>Game</th><th>Winner</th><th>Score</th><th>Margin</th></tr>"
                    for _, r in blow.iterrows():
                        opp_pts = int(r['Opp_PTS']) if ('Opp_PTS' in r and pd.notna(r['Opp_PTS'])) else '?'
                        html += (f"<tr><td>S{int(r['Season'])}</td><td>G{int(r['Game_ID'])}</td>"
                                 f"<td class='player-name'>{r['Team Name']}</td>"
                                 f"<td>{int(r['PTS'])} — {opp_pts}</td>"
                                 f"<td style='color:{GOLD}; font-weight:bold;'>+{int(r['Point_Diff'])}</td></tr>")
                    st.markdown(html + "</table>", unsafe_allow_html=True)


            # ---- SINGLE-GAME HIGHS ----
            with po_tabs[3]:
                depth = st.slider("Show top N", 3, 15, 5, key="po_sg_depth")
                st.markdown("#### Playoff Single-Game Highs")
                c1, c2, c3 = st.columns(3)
                with c1:
                    st.markdown(generate_mini_leaderboard("Points", po_p_df, 'PTS', "#cc0000", depth, "Player/Team"), unsafe_allow_html=True)
                    st.markdown(generate_mini_leaderboard("Steals", po_p_df, 'STL', "#ff8c00", depth, "Player/Team"), unsafe_allow_html=True)
                with c2:
                    st.markdown(generate_mini_leaderboard("Rebounds", po_p_df, 'REB', "#32cd32", depth, "Player/Team"), unsafe_allow_html=True)
                    st.markdown(generate_mini_leaderboard("Blocks", po_p_df, 'BLK', "#8a2be2", depth, "Player/Team"), unsafe_allow_html=True)
                with c3:
                    st.markdown(generate_mini_leaderboard("Assists", po_p_df, 'AST', "#00bfff", depth, "Player/Team"), unsafe_allow_html=True)
                    st.markdown(generate_mini_leaderboard("3-Pointers", po_p_df, '3PM', GOLD, depth, "Player/Team"), unsafe_allow_html=True)




# ------------------------------------------------------- ORACLE PREDICTOR ----
elif view_mode == "🔮 Oracle Predictor":
    st.subheader("🔮 QCL Oracle — Monte Carlo Matchup Engine")
    st.markdown("**Five men on the floor.** The Oracle simulates only each team's top-5 rotation "
                "by games played — not the whole roster. Scratch a starter and the projection moves.")


    fieldable = [t for t in t_stats['Team Name'] if not get_rotation(t).empty]
    if len(fieldable) < 2:
        st.info("Need at least two teams with logged player games.")
    else:
        team_names = (t_stats[t_stats['Team Name'].isin(fieldable)]
                      .sort_values('Win%', ascending=False)['Team Name'].tolist())
        c1, c2 = st.columns(2)
        t1_sel = c1.selectbox("🏠 Home Team", team_names, index=0)
        t2_sel = c2.selectbox("✈️ Away Team", team_names, index=min(1, len(team_names) - 1))


        if t1_sel == t2_sel:
            st.warning("Pick two different teams.")
        else:
            with st.expander("⚙️ Simulation Settings", expanded=True):
                sc1, sc2, sc3, sc4 = st.columns(4)
                n_sims = sc1.select_slider("Simulations", [500, 1000, 2500, 5000, 10000], value=2500)
                hca = sc2.slider("Home court edge (pts)", 0.0, 5.0, 1.5, 0.5)
                variance = sc3.slider("Chaos multiplier", 0.5, 2.0, 1.0, 0.1,
                                      help="Scales game-to-game score variance. 2.0 = anything can happen.")
                star_conc = sc4.slider("Ball-hog factor", 2.0, 15.0, 6.0, 0.5,
                                       help="High = the star always eats. Low = points spread randomly.")


                ec1, ec2 = st.columns(2)
                r1_full = full_roster(t1_sel)['Player/Team'].tolist()
                r2_full = full_roster(t2_sel)['Player/Team'].tolist()
                out1 = ec1.multiselect(f"🚑 Scratches — {t1_sel}", r1_full, key="out1")
                out2 = ec2.multiselect(f"🚑 Scratches — {t2_sel}", r2_full, key="out2")


            rot1 = get_rotation(t1_sel, exclude=out1)
            rot2 = get_rotation(t2_sel, exclude=out2)


            if rot1.empty or rot2.empty:
                st.error("Not enough available players to field a rotation. Un-scratch somebody.")
            else:
                rc1, rc2 = st.columns(2)
                for col, tname, rot in [(rc1, t1_sel, rot1), (rc2, t2_sel, rot2)]:
                    with col:
                        st.markdown(f"<h5>🔁 {team_logo_html(tname, px=20)}{tname} — Active Five</h5>", unsafe_allow_html=True)
                        html = "<table class='sleek-table'><tr><th>Player</th><th>GP</th><th>PPG</th><th>USG%</th><th>PIE</th></tr>"
                        for _, r in rot.iterrows():
                            html += (f"<tr><td class='player-name'>{r['Player/Team']}</td><td>{int(r['GP'])}</td>"
                                     f"<td>{r['PTS']:.1f}</td><td>{r['USG']:.1f}</td>"
                                     f"<td style='color:{GOLD}; font-weight:bold;'>{r['PIE']:.1f}</td></tr>")
                        st.markdown(html + "</table>", unsafe_allow_html=True)
                        if len(rot) < ROTATION_SIZE:
                            st.warning(f"Only {len(rot)} available — shorthanded.")


                if st.button("🔮 RUN SIMULATION", type="primary", use_container_width=True):
                    with st.spinner(f"Running {n_sims:,} games..."):
                        res = run_monte_carlo(t1_sel, t2_sel, rot1, rot2, n_sims=n_sims, hca=hca,
                                              star_conc=star_conc, variance=variance)


                    p1, p2 = res['win1'], res['win2']
                    med1, med2 = int(np.median(res['s1'])), int(np.median(res['s2']))
                    spread = float(np.mean(res['margin']))
                    total = float(np.mean(res['s1'] + res['s2']))
                    fav = t1_sel if spread > 0 else t2_sel


                    st.markdown(
                        f"<div class='sim-box'>"
                        f"<h4 style='color:#888; letter-spacing:2px;'>PROJECTED FINAL — MEDIAN OF {res['n']:,} SIMS</h4>"
                        f"<h1 style='font-size:54px; margin:6px 0;'>"
                        f"<span style='color:{GREEN if med1 > med2 else '#fff'};'>{med1}</span>"
                        f" <span style='color:#555;'>—</span> "
                        f"<span style='color:{GREEN if med2 > med1 else '#fff'};'>{med2}</span></h1>"
                        f"<p style='color:#aaa; margin:0;'>{t1_sel} (H) vs {t2_sel} (A)</p></div>",
                        unsafe_allow_html=True)


                    fig = go.Figure()
                    fig.add_trace(go.Bar(y=['Win Probability'], x=[p1 * 100], orientation='h',
                                         name=t1_sel, marker_color=GOLD,
                                         text=f"{t1_sel} {p1*100:.1f}%", textposition='inside',
                                         insidetextanchor='middle'))
                    fig.add_trace(go.Bar(y=['Win Probability'], x=[p2 * 100], orientation='h',
                                         name=t2_sel, marker_color='#cc0000',
                                         text=f"{t2_sel} {p2*100:.1f}%", textposition='inside',
                                         insidetextanchor='middle'))
                    fig.update_layout(barmode='stack', height=110, template='plotly_dark', showlegend=False,
                                      xaxis=dict(visible=False), yaxis=dict(visible=False),
                                      paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                                      margin=dict(l=0, r=0, t=10, b=10))
                    st.plotly_chart(fig, use_container_width=True)


                    b1, b2, b3, b4 = st.columns(4)
                    b1.markdown(f"<div class='line-box'><div class='line-label'>Spread</div>"
                                f"<div class='line-value'>{fav} {-abs(spread):.1f}</div></div>", unsafe_allow_html=True)
                    b2.markdown(f"<div class='line-box'><div class='line-label'>Total (O/U)</div>"
                                f"<div class='line-value'>{total:.1f}</div></div>", unsafe_allow_html=True)
                    b3.markdown(f"<div class='line-box'><div class='line-label'>{t1_sel} ML</div>"
                                f"<div class='line-value'>{american_odds(p1)}</div></div>", unsafe_allow_html=True)
                    b4.markdown(f"<div class='line-box'><div class='line-label'>{t2_sel} ML</div>"
                                f"<div class='line-value'>{american_odds(p2)}</div></div>", unsafe_allow_html=True)
                    st.caption("Lines are model output, not a sportsbook. Feed them to the casino module at your own risk.")


                    st.markdown("<br>", unsafe_allow_html=True)
                    o_tabs = st.tabs(["📊 Distributions", "📋 Projected Box Scores", "🏆 MVP Odds"])


                    with o_tabs[0]:
                        dc1, dc2 = st.columns(2)
                        with dc1:
                            mfig = px.histogram(x=res['margin'], nbins=40, template='plotly_dark',
                                                labels={'x': f'Margin ({t1_sel} − {t2_sel})'},
                                                title="Margin of Victory Distribution")
                            mfig.update_traces(marker_color=GOLD)
                            mfig.add_vline(x=0, line_dash="dash", line_color=RED)
                            mfig.update_layout(paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                                               showlegend=False)
                            st.plotly_chart(mfig, use_container_width=True)
                        with dc2:
                            sfig = go.Figure()
                            sfig.add_trace(go.Histogram(x=res['s1'], name=t1_sel, marker_color=GOLD, opacity=0.7))
                            sfig.add_trace(go.Histogram(x=res['s2'], name=t2_sel, marker_color='#cc0000', opacity=0.7))
                            sfig.update_layout(barmode='overlay', template='plotly_dark',
                                               title="Score Distributions",
                                               paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
                            st.plotly_chart(sfig, use_container_width=True)


                        blow = float((np.abs(res['margin']) >= 15).mean() * 100)
                        close = float((np.abs(res['margin']) <= 5).mean() * 100)
                        k1, k2, k3 = st.columns(3)
                        k1.markdown(f"<div class='line-box'><div class='line-label'>Nail-biter (≤5)</div>"
                                    f"<div class='line-value'>{close:.0f}%</div></div>", unsafe_allow_html=True)
                        k2.markdown(f"<div class='line-box'><div class='line-label'>Blowout (15+)</div>"
                                    f"<div class='line-value'>{blow:.0f}%</div></div>", unsafe_allow_html=True)
                        k3.markdown(f"<div class='line-box'><div class='line-label'>Roster Health</div>"
                                    f"<div class='line-value'>{res['avail'][0]*100:.0f}% / {res['avail'][1]*100:.0f}%</div></div>",
                                    unsafe_allow_html=True)


                    with o_tabs[1]:
                        bx1 = projected_box(rot1, res['pp1'])
                        bx2 = projected_box(rot2, res['pp2'])
                        pc1, pc2 = st.columns(2)
                        with pc1:
                            st.markdown(f"##### {t1_sel}")
                            render_modern_dataframe(bx1, name_hint="Player")
                            dl(bx1, "⬇️ CSV", f"{t1_sel}_proj.csv", "dl_bx1")
                        with pc2:
                            st.markdown(f"##### {t2_sel}")
                            render_modern_dataframe(bx2, name_hint="Player")
                            dl(bx2, "⬇️ CSV", f"{t2_sel}_proj.csv", "dl_bx2")
                        st.caption("PROJ PTS = median simulated points. Range = 20th–80th percentile outcomes.")


                    with o_tabs[2]:
                        mvp = res['mvp']
                        top = mvp.iloc[0]
                        st.markdown(f"<div class='sim-box'><div class='line-label'>Most Likely Game MVP</div>"
                                    f"<h1 style='color:{GOLD}; margin:8px 0;'>{top['Player']}</h1>"
                                    f"<p style='color:#aaa;'>{top['Team']} — wins it in {top['MVP %']}% of simulations</p></div>",
                                    unsafe_allow_html=True)
                        mfig = px.bar(mvp, x='MVP %', y='Player', orientation='h', color='Team',
                                      template='plotly_dark',
                                      color_discrete_map={t1_sel: GOLD, t2_sel: '#cc0000'})
                        mfig.update_layout(yaxis=dict(autorange="reversed"), height=380,
                                           paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
                        st.plotly_chart(mfig, use_container_width=True)
                        dl(mvp, "⬇️ MVP odds CSV", "mvp_odds.csv", "dl_mvp")




# ------------------------------------------------------------ LEAGUE TEAMS ---
elif view_mode == "\U0001f6e1\ufe0f League Teams":
    st.subheader("\U0001f6e1\ufe0f League Teams")
    st.markdown("Every franchise in one place. Branding (full name, colour, division) lives in "
                "**`teams.json`** \u2014 set it from Discord with **/team set**, or edit + download below. "
                "The colour drives each team's accent on player cards.")


    tmeta = _load_team_meta()
    sheet_teams = {str(t) for t in (set(t_stats['Team Name'].dropna()) | set(p_stats['Team'].dropna()))
                   if str(t) not in ('', '0')}
    all_teams = sorted(sheet_teams | set(tmeta.keys()))


    rec = {r['Team Name']: (int(r['Wins']), int(r['GP'] - r['Wins'])) for _, r in t_stats.iterrows()}
    roster_ct = p_stats.groupby('Team')['Player/Team'].nunique().to_dict()


    st.markdown(f"#### {len(all_teams)} teams")
    if not all_teams:
        st.info("No teams found yet.")
    else:
        grid = st.columns(3)
        for i, tm in enumerate(all_teams):
            ent = _team_entry(tm)
            col = team_color(tm)
            logo = _cached_logo_uri(tm)
            w, l = rec.get(tm, (0, 0))
            rc = int(roster_ct.get(tm, 0))
            _tp = p_stats[p_stats['Team'] == tm].sort_values('PIE', ascending=False)
            top = (f"{_tp.iloc[0]['Player/Team']} ({_tp.iloc[0]['PIE']:.1f} PIE)"
                   if not _tp.empty else "")
            with grid[i % 3]:
                logo_html = (f"<img src='{logo}' style='width:52px;height:52px;object-fit:contain;"
                             f"border-radius:8px;background:#111;'>" if logo else
                             "<div style='width:52px;height:52px;border-radius:8px;background:#111;"
                             "display:flex;align-items:center;justify-content:center;color:#555;'>\u2014</div>")
                html = (
                    f"<div style='background:#161b22;border-radius:10px;border-left:6px solid {col};"
                    f"padding:14px;margin-bottom:12px;'>"
                    f"<div style='display:flex;align-items:center;gap:12px;'>{logo_html}"
                    f"<div><div style='color:#fff;font-weight:900;font-size:17px;'>{team_full(tm)}</div>"
                    f"<div style='color:#888;font-size:12px;'>{ent.get('division', '') or tm}</div></div></div>"
                    f"<div style='display:flex;justify-content:space-between;margin-top:10px;color:#ccc;"
                    f"font-size:13px;'><span>Record <b style='color:#fff;'>{w}-{l}</b></span>"
                    f"<span>Roster <b style='color:#fff;'>{rc}</b></span></div>")
                if top:
                    html += f"<div style='color:{col};font-size:12px;margin-top:6px;'>Top: {top}</div>"
                html += "</div>"
                st.markdown(html, unsafe_allow_html=True)


    st.caption("Branding is managed from Discord with **/teamset** (full name, division, abbr). "
               "The accent colour is pulled automatically from each team's logo in **logos/**.")




# ------------------------------------------------------------- CARD MARKET ---
elif view_mode == "\U0001f4c8 Card Market":
    st.subheader("\U0001f4c8 Card Market (preview)")
    st.markdown("Live card values \u2014 **stat \u00d7 popularity**, capped at **$3**. Popularity = "
                "awards + community mentions + roles. No trading yet; this is the market board.")
    wc1, wc2 = st.columns(2)
    w_stat = wc1.slider("Stat weight", 0.0, 1.0, 0.5, 0.05, key="mk_ws")
    w_pop = wc2.slider("Popularity weight", 0.0, 1.0, 0.5, 0.05, key="mk_wp")
    q = st.text_input("\U0001f50d Search player", key="mk_q")


    market = _load_market()
    rows = []
    for _, r in p_stats.iterrows():
        pl = r['Player/Team']
        tier, col = card_rarity(pl)
        m = market.get(pl)
        if m:  # bot is the source of truth for serialized cards
            tier = m.get("tier", tier)
            col = RARITY_COLOR_BY_NAME.get(tier, col)
            run = int(m.get("run", 0))
            minted = int(m.get("minted", 0))
            stock = f"{max(run - minted, 0)}/{run}"
        else:
            run = RARITY_SUPPLY.get(tier, 0)
            stock = "∞" if run == 0 else str(run)
        rows.append({"Player": pl, "Team": r['Team'], "Tier": tier, "Color": col,
                     "Stat": _career_ratings().get(pl, 0.0), "Pop": player_popularity(pl),
                     "Price": player_price(pl, w_stat, w_pop),
                     "Stock": stock, "Form": player_form(pl)})
    mk = pd.DataFrame(rows)
    if q:
        mk = mk[mk['Player'].str.contains(q, case=False, na=False)]
    mk = mk.sort_values("Price", ascending=False)


    html = ("<table class='sleek-table'><tr><th>Player</th><th>Team</th><th>Tier</th>"
            "<th>Stat</th><th>Pop</th><th>Price</th><th>Left</th><th>Trend</th></tr>")
    for _, r in mk.iterrows():
        arrow = "\u25b2" if r['Form'] > 0 else "\u25bc" if r['Form'] < 0 else "\u2014"
        acol = GREEN if r['Form'] > 0 else RED if r['Form'] < 0 else "#888"
        html += (f"<tr><td class='player-name'>{team_logo_html(r['Team'], px=16)}{r['Player']}</td>"
                 f"<td>{r['Team']}</td>"
                 f"<td><span style='background:{r['Color']};color:#000;font-weight:800;font-size:11px;"
                 f"padding:2px 8px;border-radius:8px;'>{r['Tier']}</span></td>"
                 f"<td>{r['Stat'] * 100:.0f}</td><td>{r['Pop'] * 100:.0f}</td>"
                 f"<td style='color:#d4af37;font-weight:900;'>${r['Price']:.2f}</td>"
                 f"<td>{r['Stock']}</td><td style='color:{acol};font-weight:bold;'>{arrow}</td></tr>")
    st.markdown(html + "</table>", unsafe_allow_html=True)
    st.caption("Stat = career impact percentile. Pop = awards + mentions + roles (0-100). "
               "Left = serials still available (matches Discord). Trend = recent form.")
    dl(mk[['Player', 'Team', 'Tier', 'Stat', 'Pop', 'Price', 'Stock', 'Form']],
       "\u2b07\ufe0f Market CSV", "card_market.csv", "dl_market")




# --------------------------------------------------------------- 3v3 FANTASY -
elif view_mode == "\U0001f3b4 Qwiks TCG":
    st.subheader("\U0001f3b4 Qwiks TCG \u2014 Lineup Lab")
    st.markdown("Build a 3-man unit: one **Guard**, one **Forward**, one **Big**. Positional "
                f"fantasy scoring, a **{FANTASY_CAP}-point** tier cap, and **synergy combos** that "
                "reward budget archetype pairings.")
    st.caption("Tier cost \u2014 Legendary 5 \u00b7 Epic 4 \u00b7 Rare 3 \u00b7 Uncommon 2 \u00b7 Common 1.")


    names = sorted(p_stats['Player/Team'].tolist())
    sc1, sc2, sc3 = st.columns(3)
    picks = [("Guard", sc1.selectbox("\U0001f6e1\ufe0f Guard", ["\u2014"] + names, key="f3_g")),
             ("Forward", sc2.selectbox("\u2694\ufe0f Forward", ["\u2014"] + names, key="f3_f")),
             ("Big", sc3.selectbox("\U0001f5fc Big", ["\u2014"] + names, key="f3_b"))]


    total_cost, total_fp, filled = 0, 0.0, []
    cards = st.columns(3)
    for (role, nm), col in zip(picks, cards):
        with col:
            if not nm or nm == "\u2014":
                st.markdown(f"<div style='border:2px dashed #444;border-radius:12px;padding:30px;"
                            f"text-align:center;color:#666;'>Empty {role}</div>", unsafe_allow_html=True)
                continue
            r = p_stats[p_stats['Player/Team'] == nm].iloc[0]
            tier, tcol = card_rarity(nm)
            cost = card_cost(tier)
            fp = fantasy_points(r, role)
            arche = player_archetype(nm)
            total_cost += cost
            total_fp += fp
            filled.append({"role": role, "name": nm, "arch": arche, "tier": tier, "fp": fp})
            logo = _cached_logo_uri(r['Team'])
            logo_html = (f"<img src='{logo}' style='width:20px;height:20px;object-fit:contain;"
                         f"vertical-align:middle;margin-right:5px;border-radius:3px;'>" if logo else "")
            st.markdown(
                f"<div style='background:#161b22;border-radius:12px;border:2px solid {tcol};padding:14px;'>"
                f"<div style='color:#888;font-size:11px;letter-spacing:1px;'>{role.upper()}</div>"
                f"<div style='color:#fff;font-weight:900;font-size:18px;margin:2px 0;'>{nm}</div>"
                f"<div style='color:#bbb;font-size:12px;'>{logo_html}{r['Team']}</div>"
                f"<div style='color:#00bfff;font-size:12px;margin-top:4px;'>\U0001f9ec {arche}</div>"
                f"<div style='display:flex;justify-content:space-between;margin-top:10px;'>"
                f"<span style='background:{tcol};color:#000;font-weight:800;font-size:11px;"
                f"padding:2px 8px;border-radius:8px;'>{tier} \u00b7 {cost}pt</span>"
                f"<span style='color:{GOLD};font-weight:900;'>{fp:.1f} FP</span></div></div>",
                unsafe_allow_html=True)


    # ---- synergy combos (auto from archetypes) ----
    combos, mult = [], 1.0
    arches = [x["arch"] for x in filled]
    if arches.count("Microwave Chucker") >= 2:
        mult *= 1.10
        combos.append(("Microwave Combo", "Two hot-hand shooters \u2014 +10% team FP"))
    _hustle = {"Pocket Picker", "Rim Protector", "Lockdown Wing", "Glass Cleaner"}
    if sum(1 for a in arches if a in _hustle) >= 2:
        mult *= 1.06
        combos.append(("Specialist Synergy", "Dual hustle coverage \u2014 +6% team FP"))
    if arches.count("Iron Man Grind") >= 2:
        mult *= 1.05
        combos.append(("Iron Man Duo", "Endurance core \u2014 +5% team FP"))
    _budget = [x for x in filled if x["tier"] in ("Common", "Uncommon")]
    for i in range(len(_budget)):
        for j in range(i + 1, len(_budget)):
            if _budget[i]["arch"] != _budget[j]["arch"]:
                mult *= 1.04
                combos.append((f"{_budget[i]['arch']} \u00d7 {_budget[j]['arch']}",
                               "Budget synergy \u2014 +4%"))
    mult = min(mult, 1.30)
    fp_final = total_fp * mult


    st.markdown("<br>", unsafe_allow_html=True)
    m1, m2, m3, m4 = st.columns(4)
    cap_col = RED if total_cost > FANTASY_CAP else GREEN
    m1.markdown(f"<div class='metric-box'><div class='metric-title'>Salary</div>"
                f"<div class='metric-value' style='color:{cap_col};'>{total_cost} / {FANTASY_CAP}</div></div>",
                unsafe_allow_html=True)
    m2.markdown(f"<div class='metric-box'><div class='metric-title'>Base FP</div>"
                f"<div class='metric-value'>{total_fp:.1f}</div></div>", unsafe_allow_html=True)
    m3.markdown(f"<div class='metric-box'><div class='metric-title'>Synergy</div>"
                f"<div class='metric-value' style='color:{GOLD};'>\u00d7{mult:.2f}</div></div>",
                unsafe_allow_html=True)
    m4.markdown(f"<div class='metric-box'><div class='metric-title'>Total FP</div>"
                f"<div class='metric-value' style='color:{GOLD};'>{fp_final:.1f}</div></div>",
                unsafe_allow_html=True)
    if total_cost > FANTASY_CAP:
        st.error(f"Over the {FANTASY_CAP}-point cap by {total_cost - FANTASY_CAP}. Swap in a cheaper tier.")


    if combos:
        st.markdown("#### \u26a1 Active Synergies")
        for nm, desc in combos:
            st.markdown(f"<div style='background:#1a2b1a;border-left:4px solid {GREEN};padding:8px 12px;"
                        f"border-radius:6px;margin-bottom:6px;'><b style='color:#fff;'>{nm}</b> "
                        f"<span style='color:#aaa;'>\u2014 {desc}</span></div>", unsafe_allow_html=True)
    elif len(filled) == 3:
        st.caption("No synergies active \u2014 pair two Common/Uncommon cards with different archetypes to unlock combos.")


    # ---- best fantasy assets by role ----
    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown("### \U0001f3c6 Best Fantasy Assets by Role")
    depth = st.slider("Show top N", 3, 15, 8, key="f3_depth")
    lc = st.columns(3)
    for role, col in zip(["Guard", "Forward", "Big"], lc):
        with col:
            board = p_stats.copy()
            board['FP'] = board.apply(lambda rr: fantasy_points(rr, role), axis=1)
            board['Cost'] = board['Player/Team'].apply(lambda n: card_cost(card_rarity(n)[0]))
            board['Arch'] = board['Player/Team'].apply(player_archetype)
            board = board.sort_values('FP', ascending=False).head(depth)
            html = (f"<div style='background:#1c2128;padding:12px;border-radius:8px;border-left:4px solid {GOLD};'>"
                    f"<h4 style='color:#fff;margin-top:0;text-transform:uppercase;'>{role}</h4>")
            for i, (_, rr) in enumerate(board.iterrows()):
                html += (f"<div style='font-size:13px;margin-bottom:6px;'>"
                         f"<b style='color:#ffd700;'>{i+1}.</b> <span style='color:#ddd;'>{rr['Player/Team']}</span> "
                         f"<span style='color:#666;'>({int(rr['Cost'])}pt)</span>"
                         f"<span style='color:{GOLD};font-weight:bold;float:right;'>{rr['FP']:.1f}</span>"
                         f"<br><span style='color:#00bfff;font-size:11px;'>\U0001f9ec {rr['Arch']}</span></div>")
            st.markdown(html + "</div>", unsafe_allow_html=True)




# -------------------------------------------------------- AWARDS & REWARDS ---
elif view_mode == "\U0001f3c5 Awards & Rewards":
    st.subheader("\U0001f3c5 Awards & Rewards")
    st.markdown("Award-winner cards rotate automatically below. Drop images in **`cards/`** and "
                "team badges in **`logos/`** (in the repo) and they show up on their own.")


    try:
        _BASE = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        _BASE = os.getcwd()
    CARDS_DIR = os.path.join(_BASE, "cards")
    LOGOS_DIR = os.path.join(_BASE, "logos")
    for _d in (CARDS_DIR, LOGOS_DIR):
        try:
            os.makedirs(_d, exist_ok=True)
        except Exception:
            pass


    IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif")


    def _nice(fn):
        return os.path.splitext(fn)[0].replace("_", " ").replace("-", " ").title()


    def _slug(s):
        return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


    def _data_uri(path):
        try:
            ext = os.path.splitext(path)[1].lower().lstrip(".")
            mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext or 'png'}"
            with open(path, "rb") as fh:
                return f"data:{mime};base64," + base64.b64encode(fh.read()).decode("utf-8")
        except Exception:
            return ""


    # OPTIONAL: stat line / real names per file (key = filename without extension).
    CARD_META = {
        "opoy_iboola_s6": {"award": "OPOY", "player": "iBoola", "team": "Team Obsidian",
                           "stats": "33.7 PPG \u2022 7.2 APG \u2022 8.2 3PM/G"},
        "mvp_dynastyontop_s6": {"award": "MVP", "player": "DynastyOnTop", "team": "Miracles",
                                "stats": "19.1 PPG \u2022 11.9 RPG \u2022 1.7 STKS"},
    }
    # Merge in whatever the Discord bot recorded (cards/meta.json) \u2014 fully automatic.
    _meta_file = os.path.join(CARDS_DIR, "meta.json")
    if os.path.exists(_meta_file):
        try:
            with open(_meta_file, "r", encoding="utf-8") as _mf:
                CARD_META.update(json.load(_mf))
        except Exception:
            pass


    # ---- index team logos: logos/<team>.png  ->  matched by squashed team name ----
    _logo_index = {}
    try:
        for lf in os.listdir(LOGOS_DIR):
            if lf.lower().endswith(IMG_EXT):
                _logo_index[_slug(os.path.splitext(lf)[0])] = os.path.join(LOGOS_DIR, lf)
    except Exception:
        pass


    def _logo_uri(team):
        p = _logo_index.get(_slug(team))
        return _data_uri(p) if p else ""


    # ---- gather winner cards ----
    try:
        files = sorted(f for f in os.listdir(CARDS_DIR) if f.lower().endswith(IMG_EXT))
    except Exception:
        files = []


    cards_data = []
    for fn in files:
        meta = CARD_META.get(os.path.splitext(fn)[0], {})
        cards_data.append({
            "img": _data_uri(os.path.join(CARDS_DIR, fn)),
            "award": meta.get("award", "") or _nice(fn),
            "player": meta.get("player", "") or _nice(fn),
            "team": meta.get("team", ""),
            "stats": meta.get("stats", ""),
            "logo": _logo_uri(meta.get("team", "")),
            "blurb": meta.get("blurb", ""),
            "highlights": meta.get("highlights", []) if isinstance(meta.get("highlights"), list) else [],
        })


    # ---- ROTATING CAROUSEL ----
    if cards_data:
        speed = st.slider("Rotation speed (seconds per card)", 2, 12, 5, key="ar_speed")
        _tpl = r"""
<div id="qcl-car">
  <div class="stage"></div>
  <button class="nav prev">&#8249;</button>
  <button class="nav next">&#8250;</button>
  <div class="dots"></div>
</div>
<style>
  #qcl-car { position:relative; width:100%; height:__H__px; background:#0c0c0c;
             border:2px solid #d4af37; border-radius:16px; overflow:hidden;
             box-shadow:0 10px 30px rgba(0,0,0,.6); font-family:'Helvetica Neue',sans-serif; }
  #qcl-car .stage { display:flex; width:100%; height:calc(100% - 26px); }
  #qcl-car .card { display:flex; width:100%; height:100%; }
  #qcl-car .imgwrap { flex:1.3; display:flex; align-items:center; justify-content:center;
                      background:radial-gradient(circle at 30% 20%,#1a1a1a,#000); padding:14px; }
  #qcl-car .imgwrap img { max-width:100%; max-height:100%; object-fit:contain;
                          border-radius:10px; box-shadow:0 6px 20px rgba(0,0,0,.7); }
  #qcl-car .info { flex:1; display:flex; flex-direction:column; justify-content:center;
                   padding:24px 26px; background:linear-gradient(145deg,#141414,#0a0a0a);
                   border-left:1px solid #222; }
  #qcl-car .badge { display:inline-block; align-self:flex-start; background:#d4af37; color:#000;
                    font-weight:900; letter-spacing:2px; font-size:14px; padding:5px 14px;
                    border-radius:20px; text-transform:uppercase; }
  #qcl-car .player { color:#fff; font-size:38px; font-weight:900; margin:14px 0 4px; line-height:1.05; }
  #qcl-car .team { display:flex; align-items:center; gap:10px; color:#bbb; font-size:15px; margin-bottom:14px; }
  #qcl-car .team img { width:34px; height:34px; object-fit:contain; border-radius:6px; background:#111; }
  #qcl-car .stats { color:#d4af37; font-size:16px; font-weight:700; border-top:1px dashed #333; padding-top:12px; }
  #qcl-car .fade { animation:qclfade .55s ease; }
  @keyframes qclfade { from{opacity:0; transform:translateY(8px);} to{opacity:1; transform:none;} }
  #qcl-car .nav { position:absolute; top:calc(50% - 40px); transform:translateY(-50%);
                  background:rgba(0,0,0,.55); color:#d4af37; border:1px solid #d4af37;
                  width:40px; height:40px; border-radius:50%; font-size:22px; cursor:pointer; z-index:5; }
  #qcl-car .prev { left:12px; }  #qcl-car .next { right:12px; }
  #qcl-car .nav:hover { background:#d4af37; color:#000; }
  #qcl-car .dots { position:absolute; bottom:8px; width:100%; text-align:center; }
  #qcl-car .dot { display:inline-block; width:9px; height:9px; border-radius:50%; margin:0 4px;
                  background:#444; cursor:pointer; }
  #qcl-car .dot.on { background:#d4af37; }
  @media (max-width:640px){ #qcl-car .card{flex-direction:column;} #qcl-car .player{font-size:26px;} }
</style>
<script>
  const CARDS = __DATA__;
  const SPEED = __SPEED__;
  const stage = document.querySelector('#qcl-car .stage');
  const dotsC = document.querySelector('#qcl-car .dots');
  let idx = 0, timer = null;
  function slide(c){
    const logo = c.logo ? '<img src="'+c.logo+'">' : '';
    const team = c.team ? '<div class="team">'+logo+'<span>'+c.team+'</span></div>' : '';
    const stats = c.stats ? '<div class="stats">'+c.stats+'</div>' : '';
    return '<div class="card"><div class="imgwrap"><img src="'+c.img+'"></div>'+
           '<div class="info"><div class="badge">'+(c.award||'AWARD')+'</div>'+
           '<div class="player">'+(c.player||'')+'</div>'+team+stats+'</div></div>';
  }
  function render(){
    stage.innerHTML = slide(CARDS[idx]);
    stage.classList.remove('fade'); void stage.offsetWidth; stage.classList.add('fade');
    dotsC.innerHTML = CARDS.map(function(_,i){
      return '<span class="dot '+(i===idx?'on':'')+'" data-i="'+i+'"></span>'; }).join('');
    dotsC.querySelectorAll('.dot').forEach(function(d){
      d.onclick = function(){ idx = +d.dataset.i; render(); reset(); }; });
  }
  function go(n){ idx = (idx + n + CARDS.length) % CARDS.length; render(); reset(); }
  function reset(){ clearInterval(timer); if(CARDS.length>1){ timer=setInterval(function(){ go(1); }, SPEED); } }
  document.querySelector('#qcl-car .next').onclick = function(){ go(1); };
  document.querySelector('#qcl-car .prev').onclick = function(){ go(-1); };
  render(); reset();
</script>
"""
        html = (_tpl.replace("__DATA__", json.dumps(cards_data))
                    .replace("__SPEED__", str(int(speed) * 1000))
                    .replace("__H__", "520"))
        components.html(html, height=560, scrolling=False)
    else:
        st.info("No cards yet. Post one in Discord with **/award card**, or drop images into the "
                "**cards/** folder in the repo. Team badges go in **logos/** (named after the team).")


    # ---- phone uploader (quick view, this session) ----
    with st.expander("\U0001f4e4 Upload a card from your phone (quick view)"):
        ups = st.file_uploader("Card images", type=["png", "jpg", "jpeg", "webp"],
                               accept_multiple_files=True, key="ar_upload")
        if ups:
            ug = st.columns(3)
            for i, uf in enumerate(ups):
                with ug[i % 3]:
                    st.image(uf, use_container_width=True)
                    st.caption(_nice(uf.name))


    # ---- flip-card trophy wall (front = art, hover to flip to details) ----
    if cards_data:
        st.markdown(f"#### \U0001f5bc\ufe0f Trophy Wall \u2014 {len(cards_data)} card(s)")
        st.caption("Hover / tap a card to flip it over for the details.")
        grid = st.columns(3)
        for i, c in enumerate(cards_data):
            with grid[i % 3]:
                logo_img = (f"<img src='{c['logo']}' style='width:20px;height:20px;object-fit:contain;"
                            f"vertical-align:middle;margin-right:6px;border-radius:3px;'>" if c['logo'] else "")
                back = (f"<h3 style='color:#d4af37;margin:0 0 6px;letter-spacing:1px;'>{c['award']}</h3>"
                        f"<h2 style='color:#fff;margin:0 0 8px;font-size:22px;'>{c['player']}</h2>")
                if c['team']:
                    back += f"<div style='color:#bbb;margin-bottom:10px;'>{logo_img}{c['team']}</div>"
                if c['stats']:
                    back += (f"<div style='color:#d4af37;font-weight:700;border-top:1px dashed #333;"
                             f"padding-top:8px;'>{c['stats']}</div>")
                if c.get('blurb'):
                    back += (f"<div style='color:#ddd;margin-top:10px;font-size:13px;"
                             f"line-height:1.45;'>{c['blurb']}</div>")
                if c.get('highlights'):
                    _items = "".join(f"<li style='margin-bottom:3px;'>{h}</li>" for h in c['highlights'])
                    back += ("<div style='color:#8b949e;margin-top:10px;font-size:11px;"
                             "text-transform:uppercase;letter-spacing:1px;'>Highlights</div>"
                             f"<ul style='color:#d4af37;margin:4px 0 0;padding-left:18px;"
                             f"font-size:13px;'>{_items}</ul>")
                html = (
                    "<div class='flip-card' style='height:430px;'>"
                    "<div class='flip-card-inner'>"
                    "<div class='flip-card-front' style='padding:8px;'>"
                    f"<img src='{c['img']}' style='max-width:100%;max-height:100%;"
                    "object-fit:contain;border-radius:10px;'>"
                    "</div>"
                    "<div class='flip-card-back'>"
                    f"{back}"
                    "</div></div></div>")
                st.markdown(html, unsafe_allow_html=True)




# ------------------------------------------------------- ANALYTICS LAB -------
if view_mode == "🔬 Advanced Analytics Lab":
    st.subheader("🔬 The Analytics Lab")


    lab = st.tabs(["Four Factors", "Pace & Space", "Team Ratings", "Player Ratings", "Correlations"])


    with lab[0]:
        st.markdown("### 📈 Four Factors")
        html = ("<table class='sleek-table'><tr><th>Team</th><th>eFG%</th><th>TO/G</th>"
                "<th>Opp PPP</th><th>Pace</th></tr>")
        for _, r in t_stats.sort_values('Win%', ascending=False).iterrows():
            html += (f"<tr><td class='player-name'>{team_logo_html(r['Team Name'], px=18)}{r['Team Name']}</td><td>{r['eFG%']:.1f}%</td>"
                     f"<td>{fnum(r['TOPG']):.1f}</td><td>{fnum(r['Opp_PPP']):.2f}</td><td>{r['Pace']:.1f}</td></tr>")
        st.markdown(html + "</table>", unsafe_allow_html=True)


    with lab[1]:
        st.markdown("### 🏃 Offense vs Defense Quadrants")
        qd = t_stats.dropna(subset=['Opp_PPP'])
        if qd.empty:
            st.info("No head-to-head defensive data yet.")
        else:
            fig = px.scatter(qd, x='PPG', y='Opp_PPP', text='Team Name', size='GP',
                             title="Right = better offense. Lower = better defense.",
                             template="plotly_dark", color='Win%', color_continuous_scale='YlOrBr')
            fig.update_traces(textposition='top center')
            fig.update_yaxes(autorange="reversed")
            fig.add_hline(y=float(qd['Opp_PPP'].mean()), line_dash="dot", line_color="#555")
            fig.add_vline(x=float(qd['PPG'].mean()), line_dash="dot", line_color="#555")
            fig.update_layout(paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', height=520)
            st.plotly_chart(fig, use_container_width=True)


    with lab[2]:
        st.markdown("### 🧮 Team Ratings Board")
        st.caption("Points scored / allowed per 100 possessions. Pace = possessions per game.")
        html = "<table class='sleek-table'><tr><th>Team</th><th>ORtg</th><th>DRtg</th><th>NetRtg</th><th>Pace</th></tr>"
        for _, r in t_stats.sort_values('NetRtg', ascending=False).iterrows():
            nc = GREEN if r['NetRtg'] >= 0 else RED
            html += (f"<tr><td class='player-name'>{team_logo_html(r['Team Name'], px=18)}{r['Team Name']}</td><td>{r['ORtg']:.1f}</td>"
                     f"<td>{r['DRtg']:.1f}</td><td style='color:{nc}; font-weight:bold;'>{r['NetRtg']:+.1f}</td>"
                     f"<td>{r['Pace']:.1f}</td></tr>")
        st.markdown(html + "</table>", unsafe_allow_html=True)
        dl(t_stats, "⬇️ Team ratings CSV", "team_ratings.csv", "dl_tr")


    with lab[3]:
        st.markdown("### 🎖️ Player Ratings Engine")
        st.caption("USG% = share of team possessions used. ORtg = pts per 100 individual possessions. "
                   "DRtg = team defense adjusted for stocks. GmSc = Hollinger Game Score.")
        render_modern_dataframe(
            p_view[
                ['Player/Team', 'Team', 'GP', 'USG', 'ORtg',
                 'DRtg', 'NetRtg', 'GmSc', 'PIE']
            ].sort_values('NetRtg', ascending=False),
            name_hint="Player/Team",
        )


    with lab[4]:
        st.markdown("### 🔗 What Actually Wins Games?")
        if len(t_stats) >= 3:
            corr_cols = ['PPG', 'OppPPG', 'eFG%', 'TOPG', 'RPG', 'APG', 'SPG', 'BPG', 'Pace', 'NetRtg']
            cd = t_stats[corr_cols + ['Win%']].apply(pd.to_numeric, errors='coerce')
            corr = cd.corr()['Win%'].drop('Win%').dropna().sort_values()
            if corr.empty:
                st.info("Not enough varied data for correlations yet.")
            else:
                fig = px.bar(x=corr.values, y=corr.index, orientation='h', template='plotly_dark',
                             labels={'x': 'Correlation with Win%', 'y': ''},
                             color=corr.values, color_continuous_scale='RdYlGn')
                fig.update_layout(paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                                  height=420, coloraxis_showscale=False)
                st.plotly_chart(fig, use_container_width=True)
                st.caption("Correlation, not causation — and with a small sample it moves fast.")
        else:
            st.info("Need at least 3 teams for correlation analysis.")




# ---------------------------------------------------------------- VAULT ------
elif view_mode == "🏦 The Vault":
    st.subheader("🏦 THE VAULT — Master Ledger & Hall of Fame")
    p_tot = p_df.groupby('Player/Team').sum(numeric_only=True).reset_index()


    st.markdown("### 🏆 Hall of Fame Podiums")
    stat_pick = st.multiselect("Podiums to show", ['PTS', 'AST', 'REB', 'STL', 'BLK', '3PM'],
                               default=['PTS', 'AST', 'REB', 'STL'])
    labels = {'PTS': 'Scoring', 'AST': 'Assist', 'REB': 'Rebound', 'STL': 'Steals',
              'BLK': 'Blocks', '3PM': '3-Point'}
    for i in range(0, len(stat_pick), 2):
        cols = st.columns(2)
        for j, s in enumerate(stat_pick[i:i + 2]):
            with cols[j]:
                st.markdown(render_podium(f"All-Time {labels[s]} Leaders",
                                          p_tot.sort_values(s, ascending=False), s),
                            unsafe_allow_html=True)


    st.markdown("### 🗃️ The Master Ledger")
    q = st.text_input("🔍 Search the ledger")
    cols = [c for c in ['Player/Team', 'PTS', 'REB', 'AST', 'STL', 'BLK', 'FGM', 'FGA', '3PM', '3PA',
                        'FTM', 'FTA', 'Tipped_Passes', 'Shots_Affected', 'FB_Points', 'TO', 'FOULS']
            if c in p_tot.columns]
    ledger = p_tot[cols]
    if q:
        ledger = ledger[ledger['Player/Team'].str.contains(q, case=False, na=False)]
    render_modern_dataframe(
        ledger.sort_values('PTS', ascending=False),
        name_hint="Player/Team",
    )
    dl(ledger, "⬇️ Master ledger CSV", "qcl_master_ledger.csv", "dl_vault")




# ----------------------------------------------------------- RECORD BOOK -----
elif view_mode == "📖 Record Book & Milestones":
    st.subheader(f"📖 {banner_text} Record Book")
    tab_game, tab_miles, tab_team = st.tabs(["🔥 Single Game", "🏔️ Career Totals", "🏟️ Team Records"])


    with tab_game:
        depth = st.slider("Show top N", 3, 15, 5, key="rb_depth")
        st.markdown("### 🏆 Current Scope — Single Game Highs")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown(generate_mini_leaderboard("Points", p_df, 'PTS', "#cc0000", depth, "Player/Team"), unsafe_allow_html=True)
            st.markdown(generate_mini_leaderboard("Steals", p_df, 'STL', "#ff8c00", depth, "Player/Team"), unsafe_allow_html=True)
        with c2:
            st.markdown(generate_mini_leaderboard("Rebounds", p_df, 'REB', "#32cd32", depth, "Player/Team"), unsafe_allow_html=True)
            st.markdown(generate_mini_leaderboard("Blocks", p_df, 'BLK', "#8a2be2", depth, "Player/Team"), unsafe_allow_html=True)
        with c3:
            st.markdown(generate_mini_leaderboard("Assists", p_df, 'AST', "#00bfff", depth, "Player/Team"), unsafe_allow_html=True)
            st.markdown(generate_mini_leaderboard("3-Pointers", p_df, '3PM', GOLD, depth, "Player/Team"), unsafe_allow_html=True)


        if selected_scope != "Career Stats":
            st.markdown("<hr>", unsafe_allow_html=True)
            st.markdown("### 🏛️ All-Time Single Game Highs (Franchise History)")
            ac1, ac2, ac3 = st.columns(3)
            with ac1:
                st.markdown(generate_mini_leaderboard("All-Time Points", full_p_df, 'PTS', "#cc0000", depth, "Player/Team"), unsafe_allow_html=True)
                st.markdown(generate_mini_leaderboard("All-Time Steals", full_p_df, 'STL', "#ff8c00", depth, "Player/Team"), unsafe_allow_html=True)
            with ac2:
                st.markdown(generate_mini_leaderboard("All-Time Rebounds", full_p_df, 'REB', "#32cd32", depth, "Player/Team"), unsafe_allow_html=True)
                st.markdown(generate_mini_leaderboard("All-Time Blocks", full_p_df, 'BLK', "#8a2be2", depth, "Player/Team"), unsafe_allow_html=True)
            with ac3:
                st.markdown(generate_mini_leaderboard("All-Time Assists", full_p_df, 'AST', "#00bfff", depth, "Player/Team"), unsafe_allow_html=True)
                st.markdown(generate_mini_leaderboard("All-Time 3PM", full_p_df, '3PM', GOLD, depth, "Player/Team"), unsafe_allow_html=True)


    with tab_miles:
        p_totals = p_df.groupby('Player/Team').sum(numeric_only=True).reset_index()
        mc1, mc2, mc3 = st.columns(3)
        mc1.markdown(generate_mini_leaderboard("Total Points", p_totals, 'PTS', "#cc0000", 10, "Player/Team"), unsafe_allow_html=True)
        mc2.markdown(generate_mini_leaderboard("Total Rebounds", p_totals, 'REB', "#32cd32", 10, "Player/Team"), unsafe_allow_html=True)
        mc3.markdown(generate_mini_leaderboard("Total Assists", p_totals, 'AST', "#00bfff", 10, "Player/Team"), unsafe_allow_html=True)
        mc4, mc5, mc6 = st.columns(3)
        mc4.markdown(generate_mini_leaderboard("Total Steals", p_totals, 'STL', "#ff8c00", 10, "Player/Team"), unsafe_allow_html=True)
        mc5.markdown(generate_mini_leaderboard("Total Blocks", p_totals, 'BLK', "#8a2be2", 10, "Player/Team"), unsafe_allow_html=True)
        mc6.markdown(generate_mini_leaderboard("Total 3PM", p_totals, '3PM', GOLD, 10, "Player/Team"), unsafe_allow_html=True)


        st.markdown("### 🎖️ Club Memberships")
        clubbed = p_stats[p_stats['Clubs'].apply(len) > 0]
        if clubbed.empty:
            st.info("No club memberships earned yet.")
        else:
            for _, r in clubbed.iterrows():
                chips = "".join([f"<span class='chip'>{c}</span>" for c in r['Clubs']])
                st.markdown(f"<div style='background:#161b22; padding:10px; border-left:3px solid {GOLD}; "
                            f"margin-bottom:6px;'><b style='color:#fff;'>{r['Player/Team']}</b> "
                            f"<span style='color:#888;'>({r['Team']})</span><br>{chips}</div>",
                            unsafe_allow_html=True)


    with tab_team:
        t_totals = t_df.groupby('Team Name').sum(numeric_only=True).reset_index()
        if t_totals.empty:
            st.info("No team totals in scope.")
        else:
            tc1, tc2 = st.columns(2)
            tc1.markdown(generate_mini_leaderboard("Most Wins", t_totals, 'Win', "#ffd700", 8, "Team Name"), unsafe_allow_html=True)
            tc2.markdown(generate_mini_leaderboard("Total Points Scored", t_totals, 'PTS', "#cc0000", 8, "Team Name"), unsafe_allow_html=True)


            st.markdown("### 💥 Biggest Blowouts")
            blow = t_df[t_df['Point_Diff'].notna() & (t_df['Point_Diff'] > 0)] \
                .sort_values('Point_Diff', ascending=False).head(10)
            if blow.empty:
                st.info("No head-to-head games recorded yet.")
            else:
                html = "<table class='sleek-table'><tr><th>Season</th><th>Game</th><th>Winner</th><th>Score</th><th>Margin</th></tr>"
                for _, r in blow.iterrows():
                    opp_pts = int(r['Opp_PTS']) if ('Opp_PTS' in r and pd.notna(r['Opp_PTS'])) else '?'
                    html += (f"<tr><td>S{int(r['Season'])}</td><td>G{int(r['Game_ID'])}</td>"
                             f"<td class='player-name'>{r['Team Name']}</td>"
                             f"<td>{int(r['PTS'])} — {opp_pts}</td>"
                             f"<td style='color:{GOLD}; font-weight:bold;'>+{int(r['Point_Diff'])}</td></tr>")
                st.markdown(html + "</table>", unsafe_allow_html=True)




st.sidebar.divider()
st.sidebar.caption("QCL HUB v3.3 • QSPN Analytics • Ball or Mute")


# ═══════════════════════════════════════════════════════════════════════════
#  PLAYER CARDS + DISCORD WIDGET  (added)
# ═══════════════════════════════════════════════════════════════════════════
QCL_GUILD_ID = "1406018430607298693"   # <- your Discord server id


@st.cache_data(ttl=60)
def _hub_load_registrations():
    f = os.path.join(_ASSET_BASE, "registrations.json")
    if not os.path.exists(f):
        return {}
    try:
        with open(f, "r", encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


@st.cache_data(ttl=60)
def _hub_discord_widget(guild_id):
    try:
        r = _hub_requests.get(f"https://discord.com/api/guilds/{guild_id}/widget.json", timeout=6)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 403:
            return {"_error": "Server Widget is off. Enable it in Server Settings -> Widget."}
        return {"_error": f"Discord returned {r.status_code}."}
    except Exception as e:
        return {"_error": f"Couldn't reach Discord ({type(e).__name__})."}


def render_merged_cards():
    st.subheader("\U0001f0cf Player Cards")
    regs = _hub_load_registrations()
    if not regs:
        st.info("No players registered yet. Coaches register players in Discord with "
                "`/qtcg register`, and the cards appear here automatically.")
        return
    q = st.text_input("\U0001f50d Search players", key="pc_search")
    players = list(regs.values())
    if q:
        players = [p for p in players if q.lower() in
                   (p.get("display_name","")+p.get("gamertag","")+p.get("team","")).lower()]
    st.caption(f"{len(players)} player(s)")
    cols = st.columns(3)
    for i, p in enumerate(sorted(players, key=lambda x: x.get("display_name",""))):
        with cols[i % 3]:
            socials = []
            if p.get("twitter"):
                socials.append(f"<a href='https://x.com/{p['twitter']}' target='_blank' style='color:#1DA1F2;'>X</a>")
            if p.get("twitch"):
                socials.append(f"<a href='https://twitch.tv/{p['twitch']}' target='_blank' style='color:#9146FF;'>Twitch</a>")
            if p.get("youtube"):
                socials.append(f"<a href='https://youtube.com/@{p['youtube']}' target='_blank' style='color:#FF0000;'>YT</a>")
            social_html = " \u00b7 ".join(socials) if socials else "<span style='color:#666;'>no socials yet</span>"
            pos = f" \u00b7 {p['position']}" if p.get("position") else ""
            st.markdown(
                f"<div style='background:#161b22;border:1px solid #30363d;border-radius:12px;"
                f"padding:14px;margin-bottom:12px;'>"
                f"<div style='color:#fff;font-size:17px;font-weight:800;'>{p.get('display_name','?')}</div>"
                f"<div style='color:#8b949e;font-size:12px;margin-bottom:8px;'>{p.get('team','')}{pos}</div>"
                f"<div style='color:#e6edf3;font-size:13px;'>\U0001f3ae <b>{p.get('gamertag','\u2014')}</b></div>"
                f"<div style='color:#8b949e;font-size:12px;'>\U0001f4ac @{p.get('discord_tag','?')}</div>"
                f"<div style='margin-top:8px;font-size:13px;'>{social_html}</div></div>",
                unsafe_allow_html=True)


def render_discord_page():
    st.subheader("\U0001f4ac Our Discord")
    d = _hub_discord_widget(QCL_GUILD_ID)
    if d.get("_error"):
        st.info(d["_error"])
        return
    online = d.get("presence_count", 0)
    members = d.get("members", [])
    invite = d.get("instant_invite") or "https://discord.gg/"
    c1, c2, c3 = st.columns([1,1,2])
    c1.markdown(f"<div style='background:#5865F2;border-radius:12px;padding:16px;text-align:center;'>"
                f"<div style='color:#fff;font-size:32px;font-weight:900;'>{online}</div>"
                f"<div style='color:#dbe0ff;font-size:12px;'>ONLINE NOW</div></div>", unsafe_allow_html=True)
    c2.markdown(f"<div style='background:#161b22;border:1px solid #30363d;border-radius:12px;padding:16px;text-align:center;'>"
                f"<div style='color:#fff;font-size:32px;font-weight:900;'>{len(members)}</div>"
                f"<div style='color:#8b949e;font-size:12px;'>SHOWN</div></div>", unsafe_allow_html=True)
    c3.markdown(f"<div style='background:#161b22;border:1px solid #30363d;border-radius:12px;padding:16px;'>"
                f"<div style='color:#fff;font-weight:800;'>\U0001f7e2 {d.get('name','Server')}</div>"
                f"<a href='{invite}' target='_blank' style='color:#5865F2;font-weight:700;text-decoration:none;'>"
                f"Join the Discord \u2192</a></div>", unsafe_allow_html=True)
    if members:
        chips = []
        for m in members[:40]:
            dot = {"online":"#3ba55d","idle":"#faa81a","dnd":"#ed4245"}.get(m.get("status","online"),"#747f8d")
            chips.append(f"<span style='display:inline-block;background:#21262d;border-radius:14px;"
                         f"padding:4px 10px;margin:3px;font-size:12px;color:#e6edf3;'>"
                         f"<span style='color:{dot};'>\u25cf</span> {m.get('username','?')}</span>")
        st.markdown("<div style='margin-top:12px;'>"+"".join(chips)+"</div>", unsafe_allow_html=True)
        st.caption(f"Live from Discord \u00b7 refreshes each minute")


if view_mode == "\U0001f0cf Player Cards" or view_mode == "🃏 Player Cards":
    render_merged_cards_v2()
elif view_mode == "💬 Discord":
    render_discord_page()


if view_mode == "👤 My Profile":
    st.header("👤 My Profile")
    _u = current_user()
    login_widget(key="profile")
    if _u:
        st.success(f"Verified as {_u['global_name']}")
        st.markdown("#### Link your accounts")
        _gt = st.text_input("Gamertag (PSN/Xbox)")
        _tw = st.text_input("Twitter/X (no @)")
        _tv = st.text_input("Twitch")
        _yt = st.text_input("YouTube")
        if st.button("Save my profile"):
            import json as _j, os as _o
            _p = _o.path.join(_ASSET_BASE, "social_links.json")
            _d = {}
            if _o.path.exists(_p):
                try: _d = _j.load(open(_p, encoding="utf-8")) or {}
                except Exception: _d = {}
            _d[_u["id"]] = {"discord_id": _u["id"], "discord_name": _u["global_name"],
                            "avatar": _u.get("avatar"), "gamertag": _gt.strip(),
                            "twitter": _tw.strip().lstrip("@"), "twitch": _tv.strip(),
                            "youtube": _yt.strip()}
            _tmp = _p + ".tmp"
            _j.dump(_d, open(_tmp, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
            _o.replace(_tmp, _p)
            st.success("Saved — your card is linked to your verified Discord.")
    else:
        st.info("Log in with Discord above to set up your profile.")

if view_mode == "🎁 Open Packs":
    render_open_pack(current_user())


# =============================================================================
# 10. FILM TERMINAL — VOD UPLOAD + DUAL-STATE OCR
# =============================================================================
# EasyOCR handles the moving gameplay HUD. When a post-game/box-score screen is
# detected, the optional PaddleOCR reader can be used for the larger table
# region. EasyOCR remains the required reader so the GitHub/Streamlit runtime
# works on Python versions where PaddlePaddle wheels are unavailable.
FILM_HUD_PRESETS = {
    "Bottom-center broadcast scoreboard": (.22, .76, .78, .99),
    "Bottom-right HUD": (.58, .76, .99, .99),
    "Top-right scoreboard": (.55, .01, .99, .25),
    "Top-left scoreboard": (.01, .01, .45, .25),
}

FILM_POSTGAME_WORDS = (
    "box score", "game stats", "team comparison", "post game",
    "postgame", "game over", "quarter recap",
)


@st.cache_resource
def _film_hud_reader():
    import easyocr
    return easyocr.Reader(["en"], gpu=False)


@st.cache_resource
def _film_table_reader():
    from paddleocr import PaddleOCR
    try:
        return PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
    except TypeError:
        return PaddleOCR(use_angle_cls=True, lang="en")


def _film_crop(frame, roi):
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = roi
    left, top = int(width * x1), int(height * y1)
    right, bottom = int(width * x2), int(height * y2)
    return frame[max(0, top):min(height, bottom), max(0, left):min(width, right)]


def _film_easy_reads(reader, image):
    import cv2
    if image is None or image.size == 0:
        return []
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    thresholded = cv2.threshold(
        gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )[1]
    output = reader.readtext(thresholded, detail=1, paragraph=False)
    return [
        (str(item[1]).strip(), float(item[2]))
        for item in output
        if len(item) >= 3 and str(item[1]).strip() and float(item[2]) >= .45
    ]


def _film_table_reads(reader, image):
    raw = reader.predict(image) if hasattr(reader, "predict") else reader.ocr(image, cls=True)
    output = []
    for page in raw or []:
        if isinstance(page, dict):
            texts, scores = page.get("rec_texts", []), page.get("rec_scores", [])
            for index, text in enumerate(texts):
                if str(text).strip():
                    score = float(scores[index]) if index < len(scores) else 0.0
                    output.append((str(text).strip(), score))
        else:
            for line in page or []:
                if len(line) >= 2 and isinstance(line[1], (list, tuple)):
                    text, score = line[1][0], line[1][1]
                    if str(text).strip() and float(score) >= .45:
                        output.append((str(text).strip(), float(score)))
    return [(text, score) for text, score in output if score >= .45]


def _film_signal_fields(text):
    clock = re.search(r"\b\d{1,2}:[0-5]\d\b", text)
    quarter = re.search(r"\bQ[1-4]\b", text, re.IGNORECASE)
    score = re.search(r"\b\d{1,3}\s*[-–]\s*\d{1,3}\b", text)
    return {
        "clock": clock.group(0) if clock else "",
        "quarter": quarter.group(0).upper() if quarter else "",
        "score_hint": score.group(0) if score else "",
    }


def _film_analyze_vod(uploaded_file, hud_roi, table_roi, sample_seconds,
                      max_samples, scan_table, progress_callback=None):
    import cv2
    import tempfile

    try:
        hud_reader = _film_hud_reader()
    except Exception as exc:
        return {
            "error": (
                "EasyOCR could not initialize. Install the packages from "
                "requirements.txt. "
                f"{type(exc).__name__}: {exc}"
            )
        }

    temp_path = None
    capture = None
    rows = []
    last_frame = None
    postgame_detected = False
    try:
        suffix = os.path.splitext(uploaded_file.name)[1] or ".mp4"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp:
            temp.write(uploaded_file.getvalue())
            temp_path = temp.name

        capture = cv2.VideoCapture(temp_path)
        if not capture.isOpened():
            return {"error": "OpenCV could not open this VOD. Try an H.264 MP4."}

        fps = float(capture.get(cv2.CAP_PROP_FPS) or 30.0)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        sample_interval = max(1, int(fps * sample_seconds))
        frame_id, samples, state = 0, 0, "GAMEPLAY"
        expected_samples = min(max_samples, max(1, (frame_count + sample_interval - 1) // sample_interval))
        scan_started = time.monotonic()

        while capture.isOpened() and samples < max_samples:
            ok, frame = capture.read()
            if not ok:
                break
            last_frame = frame
            if frame_id % sample_interval != 0:
                frame_id += 1
                continue

            timestamp = frame_id / fps
            reads = _film_easy_reads(hud_reader, _film_crop(frame, hud_roi))
            joined = " ".join(text for text, _ in reads).lower()
            if any(word in joined for word in FILM_POSTGAME_WORDS):
                state = "POST_GAME"
                postgame_detected = True

            for text, confidence in reads:
                rows.append({
                    "timestamp": round(timestamp, 2),
                    "frame": frame_id,
                    "state": state,
                    "text": text,
                    "confidence": round(confidence, 3),
                    **_film_signal_fields(text),
                })
            samples += 1
            if progress_callback is not None:
                elapsed = time.monotonic() - scan_started
                remaining = (expected_samples - samples) * elapsed / samples if samples >= 3 else None
                progress_callback(samples, expected_samples, remaining)

            if postgame_detected and scan_table:
                try:
                    table_reads = _film_table_reads(
                        _film_table_reader(), _film_crop(frame, table_roi)
                    )
                except Exception:
                    table_reads = _film_easy_reads(
                        hud_reader, _film_crop(frame, table_roi)
                    )
                rows.extend({
                    "timestamp": round(timestamp, 2),
                    "frame": frame_id,
                    "state": "POST_GAME_TABLE",
                    "text": text,
                    "confidence": round(confidence, 3),
                    "clock": "",
                    "quarter": "",
                    "score_hint": "",
                } for text, confidence in table_reads)
                break
            frame_id += 1

        if scan_table and not postgame_detected and last_frame is not None:
            try:
                table_reads = _film_table_reads(
                    _film_table_reader(), _film_crop(last_frame, table_roi)
                )
            except Exception:
                table_reads = _film_easy_reads(
                    hud_reader, _film_crop(last_frame, table_roi)
                )
            rows.extend({
                "timestamp": round(max(0, frame_count / fps - 1), 2),
                "frame": max(0, frame_count - 1),
                "state": "POST_GAME_TABLE",
                "text": text,
                "confidence": round(confidence, 3),
                "clock": "",
                "quarter": "",
                "score_hint": "",
            } for text, confidence in table_reads)

        return {
            "error": None,
            "rows": rows,
            "fps": round(fps, 2),
            "width": width,
            "height": height,
            "duration": round(frame_count / fps, 1) if fps else 0,
            "samples": samples,
            "state": state,
        }
    finally:
        if capture is not None:
            capture.release()
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def _film_terminal_time(seconds):
    seconds = int(seconds)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def render_film_terminal_page():
    st.markdown(
        "<div class='header-banner'>QCL FILM TERMINAL — VOD OCR</div>",
        unsafe_allow_html=True,
    )
    st.title("Film terminal")
    st.caption(
        "Upload an NBA 2K Pro-Am VOD, read the fixed gameplay scoreboard, "
        "and switch to a table pass for post-game recap screens."
    )
    uploaded_file = st.file_uploader(
        "Upload VOD",
        type=["mp4", "mov", "webm", "m4v"],
        help="H.264 MP4 is the most reliable format for OpenCV and browser playback.",
        key="qcl_film_upload",
    )
    if uploaded_file is None:
        st.info("Upload a VOD to begin.")
        return

    video_bytes = uploaded_file.getvalue()
    st.video(video_bytes)
    st.caption(
        f"{uploaded_file.name} · {len(video_bytes) / (1024 * 1024):.1f} MB"
    )

    with st.expander("OCR capture settings", expanded=True):
        hud_name = st.selectbox(
            "Gameplay HUD region",
            list(FILM_HUD_PRESETS),
            key="qcl_film_hud",
        )
        controls = st.columns(3)
        sample_seconds = controls[0].slider(
            "Sample every (seconds)", .5, 5.0, 1.0, .5, key="qcl_film_sample"
        )
        max_samples = controls[1].slider(
            "Maximum samples", 10, 600, 120, 10, key="qcl_film_max_samples"
        )
        scan_table = controls[2].checkbox(
            "Scan post-game table", True, key="qcl_film_scan_table"
        )
        st.caption(
            "Target the fixed broadcast scoreboard, not the moving player indicator. "
            "The table pass covers the full recap region."
        )

    if st.button(
        "Run dual-state OCR",
        type="primary",
        use_container_width=True,
        key="qcl_film_run_ocr",
    ):
        st.caption("First run: EasyOCR may need to download its detection model. The download has no reliable ETA; the timer starts after initialization.")
        status = st.empty()
        status.info("Initializing video reader and OCR models. The first model download can take several minutes.")
        progress = st.progress(0.0)

        def update_scan(done, total, remaining):
            progress.progress(min(1.0, done / total))
            estimate = (f"About {_film_terminal_time(remaining)} left at the current speed."
                        if remaining is not None else "Calculating remaining time…")
            status.info(f"Scanning video: {done}/{total} samples. {estimate}")

        try:
            st.session_state["qcl_film_result"] = _film_analyze_vod(
                uploaded_file,
                FILM_HUD_PRESETS[hud_name],
                (.03, .08, .97, .96),
                sample_seconds,
                max_samples,
                scan_table,
                progress_callback=update_scan,
            )
        finally:
            progress.empty()
            status.empty()

    result = st.session_state.get("qcl_film_result")
    if not result:
        return
    st.divider()
    st.subheader("OCR results")
    if result.get("error"):
        st.error(result["error"])
        st.code("pip install -r requirements.txt", language="bash")
        return

    metrics = st.columns(5)
    metrics[0].metric("Resolution", f"{result['width']}×{result['height']}")
    metrics[1].metric("FPS", result["fps"])
    metrics[2].metric("Duration", _film_terminal_time(result["duration"]))
    metrics[3].metric("Frames sampled", result["samples"])
    metrics[4].metric("Detected state", result["state"])
    result_df = pd.DataFrame(result.get("rows", []))
    if result_df.empty:
        st.warning(
            "No confident text was found. Try another HUD region or a wider "
            "capture region."
        )
        return
    st.dataframe(result_df, use_container_width=True, hide_index=True)
    st.download_button(
        "Download OCR CSV",
        result_df.to_csv(index=False).encode("utf-8"),
        "qcl-vod-ocr-results.csv",
        "text/csv",
        use_container_width=True,
        key="qcl_film_download_ocr",
    )


if view_mode == "Film Terminal":
    render_film_terminal_page()
