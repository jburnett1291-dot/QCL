"""Public-safe GitHub publisher for the QCL draft server.

Install from QCL2K.py with::

    import qcl_draft_github_sync
    qcl_draft_github_sync.install_draft_sync(bot, globals())

The module deliberately does not import QCL2K, so it can be tested in
isolation and cannot accidentally expose private draft fields.
"""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import io
import json
import os
import re
from typing import Any, Mapping
from urllib.parse import quote

import aiohttp
from PIL import Image


DRAFT_PATH = os.getenv("DRAFT_PATH", "qcl_draft_activity.json")
_asset_root = os.getenv("DRAFT_GITHUB_ASSET_ROOT", "draft_players").replace("\\", "/")
DRAFT_GITHUB_ASSET_ROOT = "/".join(
    re.sub(r"[^a-zA-Z0-9_-]+", "-", part).strip("-")
    for part in _asset_root.split("/")
    if part and part not in {".", ".."}
) or "draft_players"
try:
    DRAFT_SYNC_SECONDS = max(1, int(os.getenv("DRAFT_SYNC_SECONDS", "5")))
except ValueError:
    DRAFT_SYNC_SECONDS = 5

_PUBLIC_PLAYER_FIELDS = ("gamertag", "build", "position", "drafted_by",
                         "pick_number", "team", "archetype", "primary_position")
_PUBLIC_PICK_FIELDS = ("pick", "round", "team", "player", "timestamp")
_SAFE_SLUG_RE = re.compile(r"[^a-z0-9]+")
_PRIVATE_KEY_PARTS = (
    "discord", "guild_id", "channel_id", "role_id", "player_id", "coach_id",
    "availability", "proof", "photo_path", "photo_filename", "data_folder",
    "socials", "registration_requirement",
)
_EXISTING_PLAYER_FIELDS = set(_PUBLIC_PLAYER_FIELDS) | {"combine_games", "eligible"}


def safe_slug(value: Any) -> str:
    """Return a stable, path-safe key that contains no user/Discord ID."""
    original = str(value or "").strip()
    slug = _SAFE_SLUG_RE.sub("-", original.lower()).strip("-")
    if not slug or slug.isdigit():
        digest = hashlib.sha256(original.casefold().encode("utf-8")).hexdigest()[:12]
        return f"player-{digest}"
    return slug[:80]


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _private_key(key: Any) -> bool:
    normalized = str(key or "").lower().replace("-", "_")
    return (normalized == "id" or normalized.endswith("_id")
            or any(part in normalized for part in _PRIVATE_KEY_PARTS))


def _looks_like_id(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    text = str(value or "").strip()
    return text.isdigit() or text.startswith(("<@", "<#", "<@&"))


def _scrub_existing(value: Any, parent: str = "") -> Any:
    """Remove private/account identifiers from legacy public JSON recursively."""
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if _private_key(key):
                continue
            if parent.lower() in {"coaches", "staff"} and str(key).isdigit():
                continue
            if parent.lower() in {"coaches", "staff"} and _looks_like_id(item):
                continue
            result[str(key)] = _scrub_existing(item, str(key))
        return result
    if isinstance(value, list):
        return [_scrub_existing(item, parent) for item in value]
    return value


def _safe_existing_player(value: Mapping[str, Any]) -> dict[str, Any]:
    """Keep useful public player data while dropping legacy private fields."""
    return {
        key: _scrub_existing(value[key], key)
        for key in _EXISTING_PLAYER_FIELDS
        if key in value and not _private_key(key)
    }


def _public_player(source: Mapping[str, Any], image_url: str = "",
                   asset_path: str = "") -> dict[str, Any]:
    result = {key: source.get(key) for key in _PUBLIC_PLAYER_FIELDS
              if source.get(key) not in (None, "") and not _private_key(key)}
    if image_url:
        result["image_url"] = image_url
    if asset_path:
        result["asset_path"] = asset_path
    return result


def build_public_snapshot(draft: Mapping[str, Any],
                          existing: Mapping[str, Any] | None = None,
                          image_info: Mapping[str, Mapping[str, str]] | None = None
                          ) -> dict[str, Any]:
    """Merge a draft into an existing public document without private fields."""
    old = _scrub_existing(dict(existing or {}))
    current_players = old.get("players")
    current_players = dict(current_players) if isinstance(current_players, dict) else {}
    by_name = {}
    for key, value in current_players.items():
        if isinstance(value, dict) and value.get("gamertag"):
            by_name.setdefault(_norm(value["gamertag"]), (key, value))
    output = dict(old)  # Preserve unknown top-level metadata.
    # These are public league state, not Discord/account metadata.
    for key in ("status", "season", "draft_name", "war_room_open", "teams",
                "picks_per_team", "current_pick"):
        if key in draft:
            output[key] = draft[key]
    exported: dict[str, dict[str, Any]] = {}
    for source in (draft.get("players") or {}).values():
        if not isinstance(source, Mapping) or not source.get("gamertag"):
            continue
        name = str(source["gamertag"]).strip()
        slug = safe_slug(name)
        norm_name = _norm(name)
        prior_match = by_name.get(norm_name, (slug, {}))
        _, prior = prior_match
        # Existing player data is allowlisted; legacy IDs, availability,
        # proofs, local paths, and social/contact fields are removed.
        record = _safe_existing_player(prior) if isinstance(prior, dict) else {}
        record.update(_public_player(source))
        info = (image_info or {}).get(_norm(name), {})
        if info.get("image_url"):
            record["image_url"] = info["image_url"]
        if info.get("asset_path"):
            record["asset_path"] = info["asset_path"]
        exported[slug] = record
    # Keep pre-existing players that disappeared from the local state.
    matched_names = {_norm((source or {}).get("gamertag"))
                     for source in (draft.get("players") or {}).values()
                     if isinstance(source, Mapping) and source.get("gamertag")}
    for prior in current_players.values():
        if not isinstance(prior, dict):
            continue
        norm_name = _norm(prior.get("gamertag"))
        slug = safe_slug(prior.get("gamertag"))
        if norm_name and norm_name not in matched_names and slug not in exported:
            exported[slug] = _safe_existing_player(prior)
    output["players"] = exported
    if isinstance(output.get("coaches"), Mapping):
        output["coaches"] = {
            str(team): value for team, value in output["coaches"].items()
            if not _looks_like_id(value)
        }
    if "picks" in draft:
        picks = []
        for pick in draft.get("picks") or []:
            if isinstance(pick, Mapping):
                picks.append({k: pick[k] for k in _PUBLIC_PICK_FIELDS if k in pick})
        output["picks"] = picks
    output["revision"] = int(old.get("revision", 0) or 0)
    if "source" not in output:
        output["source"] = "QCL draft server"
    return output


def _is_empty_default(draft: Mapping[str, Any]) -> bool:
    return not (draft.get("players") or draft.get("picks"))


def _should_skip_empty(draft: Mapping[str, Any],
                       existing: Mapping[str, Any]) -> bool:
    return _is_empty_default(draft) and bool(
        existing.get("players") or existing.get("picks")
    )


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "QCL-draft-sync"}


class DraftGitHubSync:
    def __init__(self, bot: Any, namespace: Any):
        self.bot = bot
        self.ns = namespace
        self.task: asyncio.Task | None = None
        self._last_json = ""
        self._image_hashes: dict[str, str] = {}
        self._avatar_cache: dict[str, tuple[str, bytes]] = {}

    def _value(self, name: str, default: Any = "") -> Any:
        if isinstance(self.ns, Mapping):
            return self.ns.get(name, default)
        return getattr(self.ns, name, default)

    def _config(self) -> tuple[str, str, str]:
        configured_repo = os.getenv("DRAFT_GITHUB_REPO")
        global_repo = os.getenv("GITHUB_REPO")
        legacy_repo = str(self._value("GH_REPO", "") or "")
        if configured_repo:
            repo = configured_repo
        elif global_repo and global_repo != "jburnett1291-dot/SPAM_HUB":
            repo = global_repo
        elif legacy_repo and legacy_repo != "jburnett1291-dot/SPAM_HUB":
            repo = legacy_repo
        else:
            repo = "jburnett1291-dot/QCL"
        return (str(self._value("GH_TOKEN", "") or ""),
                repo,
                os.getenv("DRAFT_GITHUB_BRANCH") or os.getenv("GITHUB_BRANCH")
                or str(self._value("GH_BRANCH", "main") or "main"))

    async def _get(self, session: aiohttp.ClientSession, path: str, binary: bool = False):
        token, repo, branch = self._config()
        if not token or not repo:
            return None, None
        url = f"https://api.github.com/repos/{repo}/contents/{quote(path, safe='/')}"
        async with session.get(url, params={"ref": branch}, headers=_headers(token)) as r:
            if r.status != 200:
                return None, None
            data = await r.json()
        try:
            content = base64.b64decode(data.get("content", ""))
            return data.get("sha"), content if binary else content.decode()
        except (ValueError, UnicodeDecodeError):
            return data.get("sha"), None

    async def _put(self, session: aiohttp.ClientSession, path: str, raw: bytes,
                   message: str, sha: str | None = None) -> bool:
        token, repo, branch = self._config()
        if not token or not repo:
            return False
        payload: dict[str, Any] = {"message": message,
                                   "content": base64.b64encode(raw).decode(),
                                   "branch": branch}
        if sha:
            payload["sha"] = sha
        url = f"https://api.github.com/repos/{repo}/contents/{quote(path, safe='/')}"
        async with session.put(url, json=payload, headers=_headers(token)) as r:
            return r.status in (200, 201)

    async def _avatar(self, session: aiohttp.ClientSession,
                      player: Mapping[str, Any], fetch_budget: list[int],
                      download_budget: list[int]
                      ) -> tuple[bytes | None, str]:
        uid = player.get("discord_id")
        if uid is None:
            return None, ""
        try:
            user = self.bot.get_user(int(uid))
            if user is None and fetch_budget[0] > 0 and hasattr(self.bot, "fetch_user"):
                fetch_budget[0] -= 1
                try:
                    user = await self.bot.fetch_user(int(uid))
                except Exception:
                    return None, ""
            avatar = getattr(user, "display_avatar", None) or getattr(user, "avatar", None)
            url = str(getattr(avatar, "url", "") or "")
            if not url:
                return None, ""
            cached = self._avatar_cache.get(str(uid))
            if cached and cached[0] == url:
                return cached[1], url
            if download_budget[0] <= 0:
                return None, url
            download_budget[0] -= 1
            async with session.get(url) as response:
                if response.status == 200:
                    source = await response.read()
                    if len(source) > 2 * 1024 * 1024:
                        return None, url
                    with Image.open(io.BytesIO(source)) as image:
                        image = image.convert("RGBA")
                        out = io.BytesIO()
                        image.save(out, format="PNG", optimize=True)
                        data = out.getvalue()
                    if len(data) <= 2 * 1024 * 1024:
                        self._avatar_cache[str(uid)] = (url, data)
                        return data, url
        except Exception:
            pass
        return None, ""

    async def sync_once(self) -> bool:
        token, repo, branch = self._config()
        if not token or not repo:
            return False
        getter = self._value("_draft_data")
        if not callable(getter):
            return False
        _, draft = getter()
        async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20)) as session:
            sha, old_raw = await self._get(session, DRAFT_PATH)
            existing = {}
            if old_raw:
                try:
                    existing = json.loads(old_raw)
                except json.JSONDecodeError:
                    existing = {}
            if _should_skip_empty(draft, existing):
                # The newly started bot has no local draft state yet. Keep the
                # existing public draft, but still scrub legacy private fields.
                draft = {}
            image_info: dict[str, dict[str, str]] = {}
            fetch_budget = [2]
            download_budget = [2]
            upload_budget = [2]
            for player in (draft.get("players") or {}).values():
                if not isinstance(player, Mapping) or not player.get("gamertag"):
                    continue
                data, _ = await self._avatar(session, player, fetch_budget, download_budget)
                cached = self._avatar_cache.get(str(player.get("discord_id")))
                if not data and cached:
                    data = cached[1]
                if not data:
                    continue
                name = str(player["gamertag"])
                digest = hashlib.sha256(data).hexdigest()
                slug = safe_slug(name)
                path = f"{DRAFT_GITHUB_ASSET_ROOT.strip('/')}/{slug}.png"
                uploaded = self._image_hashes.get(path) == digest
                if not uploaded and upload_budget[0] > 0:
                    upload_budget[0] -= 1
                    asset_sha, remote_data = await self._get(session, path, binary=True)
                    if remote_data and hashlib.sha256(remote_data).hexdigest() == digest:
                        self._image_hashes[path] = digest
                        uploaded = True
                    elif await self._put(session, path, data,
                                         f"Update draft player image: {slug}", asset_sha):
                        self._image_hashes[path] = digest
                        uploaded = True
                if not uploaded:
                    continue
                image_info[_norm(name)] = {
                    "image_url": f"https://raw.githubusercontent.com/{repo}/{branch}/{quote(path, safe='/')}",
                    "asset_path": path,
                }
            snapshot = build_public_snapshot(draft, existing, image_info)
            if isinstance(snapshot.get("source"), dict):
                snapshot["source"]["repository"] = repo
            snapshot["revision"] = int(existing.get("revision", 0) or 0)
            candidate_raw = (json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
            if candidate_raw.decode() == old_raw or candidate_raw.decode() == self._last_json:
                return False
            snapshot["revision"] += 1
            if isinstance(snapshot.get("source"), dict):
                snapshot["source"]["updated_at"] = (
                    datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                )
            raw = (json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
            if await self._put(session, DRAFT_PATH, raw, "Update public QCL draft activity", sha):
                self._last_json = raw.decode()
                return True
        return False

    async def _loop(self):
        while True:
            try:
                await self.sync_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[DraftSync] {type(exc).__name__}")
            await asyncio.sleep(DRAFT_SYNC_SECONDS)

    def start(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._loop(), name="qcl-draft-github-sync")


def install_draft_sync(bot: Any, namespace: Any) -> DraftGitHubSync:
    """Attach one non-destructive on_ready listener and return the sync object."""
    sync = DraftGitHubSync(bot, namespace)

    @bot.listen("on_ready")
    async def _qcl_draft_sync_ready():
        sync.start()

    return sync


if __name__ == "__main__":
    import unittest

    class PublicSyncTests(unittest.TestCase):
        def test_redaction_and_safe_key(self):
            result = build_public_snapshot({"players": {"123": {
                "discord_id": 123, "discord_name": "Secret", "availability": "never",
                "gamertag": "../Ace", "build": "Lock", "position": "SF",
                "entry_fee_proof": "secret"}}, "picks": [{"pick": 1, "player_id": 123,
                "player": "../Ace", "team": "A"}]})
            self.assertEqual(list(result["players"]), ["ace"])
            self.assertNotIn("discord_id", result["players"]["ace"])
            self.assertNotIn("player_id", result["picks"][0])

        def test_empty_default_does_not_replace_active(self):
            self.assertTrue(_is_empty_default({"players": {}, "picks": []}))
            existing = {"players": {"123456": {
                "gamertag": "Ace", "discord_id": 123456,
                "availability": "private"}}, "picks": [{"pick": 1,
                "player_id": 123456, "player": "Ace"}], "status": "active"}
            self.assertTrue(_should_skip_empty(
                {"players": {}, "picks": [], "teams": ["Team A"]}, existing))
            result = build_public_snapshot({}, existing)
            self.assertEqual(result["status"], "active")
            self.assertEqual(result["players"]["ace"], {"gamertag": "Ace"})
            self.assertEqual(result["picks"], [{"pick": 1, "player": "Ace"}])

        def test_path_safety(self):
            self.assertEqual(safe_slug("../../Discord ID 42"), "discord-id-42")
            self.assertNotIn("/", safe_slug("a/../../b"))
            self.assertTrue(safe_slug("123456789012345678").startswith("player-"))
            self.assertFalse(safe_slug("123456789012345678").isdigit())

        def test_idempotent_revision(self):
            draft = {"status": "active", "players": {"1": {"gamertag": "Ace",
                "build": "Lock"}}, "picks": []}
            first = build_public_snapshot(draft)
            second = build_public_snapshot(draft, first)
            self.assertEqual(first["revision"], second["revision"])
            self.assertEqual(first["players"], second["players"])

        def test_existing_public_document_is_scrubbed(self):
            old = {"players": {"private-key": {
                "gamertag": "Ace", "discord_id": 123,
                "discord_username": "private", "availability": "private",
                "availability_label": "hidden", "entry_fee_proof": "secret",
                "proof_message_url": "private", "photo_path": "/private/path",
                "data_folder": "private", "socials": {"account": "private"},
                "build": "Lock", "position": "PG"}},
                "coaches": {"Team A": 987654321}, "staff": {"123456": "Coach"},
                "teams": ["Team A"]}
            result = build_public_snapshot(
                {"players": {"1": {"discord_id": 1, "gamertag": "Ace",
                                    "image_url": "https://cdn.discordapp.com/123",
                                    "position": "PG"}}, "picks": []}, old)
            player = result["players"]["ace"]
            self.assertEqual(set(player), {"gamertag", "build", "position"})
            self.assertEqual(result["coaches"], {})
            self.assertEqual(result["staff"], {})
            self.assertEqual(result["teams"], ["Team A"])

        def test_id_keyed_merge_has_no_duplicate(self):
            old = {"players": {"987654321": {"gamertag": "Ace",
                "discord_id": 987654321, "availability": "private",
                "entry_fee_proof": "private", "build": "Lock"}},
                "coaches": {"Team A": 123456789}, "draft_name": "QCL"}
            result = build_public_snapshot(
                {"players": {"123": {"discord_id": 123, "gamertag": "Ace",
                                     "position": "PG"}}, "picks": []}, old)
            self.assertEqual(list(result["players"]), ["ace"])
            self.assertNotIn("discord_id", result["players"]["ace"])
            self.assertNotIn("availability", result["players"]["ace"])
            self.assertNotIn("entry_fee_proof", result["players"]["ace"])
            self.assertEqual(result["coaches"], {})
            self.assertEqual(result["draft_name"], "QCL")

    unittest.main()