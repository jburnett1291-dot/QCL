"""Admin-only prefix tools for QCL registrations, settings, and GitHub uploads."""

from __future__ import annotations

import ast
import base64
import io
import json
import os
import re
import time
from typing import Any

import aiohttp
import discord
from discord.ext import commands


MAX_TEMPLATE_BYTES = 1_000_000
MAX_GITHUB_UPLOAD_BYTES = 5_000_000
REGISTRATION_ROLES = {"byot_gm", "draft_gm", "draft_player"}
REGISTRATION_STATUSES = {
    "draft",
    "submitted",
    "needs_review",
    "needs_info",
    "approved",
    "rejected",
    "cancelled",
}
REGISTRATION_FIELDS = {
    "discord_id",
    "discord_tag",
    "display_name",
    "gamertag_or_psn",
    "platform",
    "position",
    "availability",
    "social_proof",
    "proof_platforms",
    "added_at",
}
ALLOWED_TEMPLATE_KEYS = {
    "template_version",
    "registration",
    "season",
    "github",
    "admin",
}


def _admin(ctx: commands.Context) -> bool:
    guild = getattr(ctx, "guild", None)
    member = getattr(ctx, "author", None)
    if not guild or not member:
        return False
    return bool(
        getattr(member, "id", None) == getattr(guild, "owner_id", None)
        or getattr(getattr(member, "guild_permissions", None), "administrator", False)
    )


def _discord_id(value: Any) -> str:
    raw = str(value or "").strip()
    match = re.fullmatch(r"<@!?(\d{5,25})>", raw) or re.fullmatch(r"(\d{5,25})", raw)
    return match.group(1) if match else ""


def _file_bytes(attachment: discord.Attachment, limit: int) -> bytes:
    if int(getattr(attachment, "size", 0) or 0) > limit:
        raise ValueError(f"File exceeds the {limit // 1_000_000} MB limit.")
    return b""


def _json_attachment(data: Any, filename: str) -> discord.File:
    body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    return discord.File(io.BytesIO(body), filename=filename)


def _history(record: dict, action: str, actor_id: str, **details: Any) -> None:
    history = record.setdefault("history", [])
    history.append(
        {"at": int(time.time()), "action": action, "by": actor_id, **details}
    )
    del history[:-100]


def _default_form_schemas() -> dict:
    return {
        "byot_gm": {
            "label": "BYOT GM",
            "fields": ["team_name", "team_logo", "gamertag_or_psn", "platform"],
        },
        "draft_gm": {
            "label": "Draft GM",
            "fields": ["team_name", "team_logo", "gamertag_or_psn", "platform"],
        },
        "draft_player": {
            "label": "Draft Player",
            "fields": [
                "gamertag_or_psn",
                "platform",
                "position",
                "availability",
            ],
        },
    }


def _default_admin_settings(module) -> dict:
    return {
        "template_version": 1,
        "registration": {
            "socials_required": list(
                getattr(module, "QCL_REGISTRATION_SOCIALS", [])
            ),
            "form_schemas": _default_form_schemas(),
        },
        "season": {
            "default_days": int(getattr(module, "QCL_DEFAULT_SEASON_DAYS", 14)),
            "reveal_hour": int(
                getattr(module, "SCHED_DEFAULT_REVEAL_HOUR", 9)
            ),
            "window_hours": int(getattr(module, "QCL_SEASON_WINDOW_HOURS", 36)),
        },
        "github": {
            "repo": str(getattr(module, "GH_REPO", "jburnett1291-dot/QCL")),
            "branch": str(getattr(module, "GH_BRANCH", "main")),
        },
        "admin": {
            "audit_log_limit": 100,
        },
    }


def _validate_admin_settings(data: Any, module) -> tuple[dict, list[str]]:
    errors = []
    if not isinstance(data, dict):
        return {}, ["Root must be a JSON object."]
    if set(data) - ALLOWED_TEMPLATE_KEYS:
        errors.append(
            "Unsupported root keys: " + ", ".join(sorted(set(data) - ALLOWED_TEMPLATE_KEYS))
        )
    if data.get("template_version") != 1:
        errors.append("template_version must be 1.")
    defaults = _default_admin_settings(module)
    cleaned = defaults

    def section(name: str, allowed: set[str]) -> dict:
        value = data.get(name, {})
        if not isinstance(value, dict):
            errors.append(f"{name} must be an object.")
            return {}
        unknown = set(value) - allowed
        if unknown:
            errors.append(f"Unsupported {name} keys: " + ", ".join(sorted(unknown)))
        return value

    reg = section("registration", {"socials_required", "form_schemas"})
    if "socials_required" in reg:
        values = reg["socials_required"]
        if (
            not isinstance(values, list)
            or len(values) > 12
            or any(not isinstance(v, str) or not v.strip() or len(v) > 50 for v in values)
        ):
            errors.append("registration.socials_required must be a list of up to 12 short labels.")
        else:
            cleaned["registration"]["socials_required"] = list(
                dict.fromkeys(v.strip() for v in values)
            )
    if "form_schemas" in reg:
        forms = reg["form_schemas"]
        if not isinstance(forms, dict) or set(forms) != REGISTRATION_ROLES:
            errors.append("registration.form_schemas must define byot_gm, draft_gm, and draft_player.")
        else:
            for role, spec in forms.items():
                if (
                    not isinstance(spec, dict)
                    or set(spec) - {"label", "fields"}
                    or not isinstance(spec.get("label"), str)
                    or len(spec["label"]) > 60
                    or not isinstance(spec.get("fields"), list)
                    or len(spec["fields"]) > 20
                    or any(not isinstance(f, str) or len(f) > 50 for f in spec["fields"])
                ):
                    errors.append(f"registration.form_schemas.{role} is malformed.")
            if not errors:
                cleaned["registration"]["form_schemas"] = forms

    season = section("season", {"default_days", "reveal_hour", "window_hours"})
    for key, minimum, maximum in (
        ("default_days", 1, 365),
        ("reveal_hour", 0, 23),
        ("window_hours", 1, 168),
    ):
        if key in season:
            value = season[key]
            if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
                errors.append(f"season.{key} must be an integer from {minimum} to {maximum}.")
            else:
                cleaned["season"][key] = value

    github = section("github", {"repo", "branch"})
    if "repo" in github:
        repo = github["repo"]
        if not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            errors.append("github.repo must use owner/repository format.")
        else:
            cleaned["github"]["repo"] = repo
    if "branch" in github:
        branch = github["branch"]
        if not isinstance(branch, str) or not re.fullmatch(r"[A-Za-z0-9._/-]{1,100}", branch) or ".." in branch:
            errors.append("github.branch contains unsupported characters.")
        else:
            cleaned["github"]["branch"] = branch

    admin = section("admin", {"audit_log_limit"})
    if "audit_log_limit" in admin:
        limit = admin["audit_log_limit"]
        if not isinstance(limit, int) or isinstance(limit, bool) or not 10 <= limit <= 500:
            errors.append("admin.audit_log_limit must be an integer from 10 to 500.")
        else:
            cleaned["admin"]["audit_log_limit"] = limit
    return cleaned, errors


def _registration_template(form_schemas: dict | None = None) -> dict:
    return {
        "schema_version": 1,
        "instructions": [
            "Fill registrations with one object per applicant/team.",
            "Use the applicant's numeric Discord ID or <@ID> in owner_id.",
            "Each person needs a Discord ID so the record is indexed to that account.",
            "Leave registration_id blank to create a record. To edit, use its existing ID.",
            "Allowed roles: byot_gm, draft_gm, draft_player.",
            "Allowed statuses: draft, submitted, needs_review, needs_info, approved, rejected, cancelled.",
            "Import only adds or updates listed records; it never deletes unlisted records.",
        ],
        "form_types": form_schemas or _default_form_schemas(),
        "registrations": [
            {
                "registration_id": "",
                "role": "draft_player",
                "status": "submitted",
                "owner_id": "123456789012345678",
                "owner_tag": "Discord display name",
                "team_name": "",
                "people": [
                    {
                        "discord_id": "123456789012345678",
                        "display_name": "Player name",
                        "gamertag_or_psn": "",
                        "platform": "",
                        "position": "",
                        "availability": "",
                        "social_proof": [],
                        "proof_platforms": [],
                    }
                ],
                "socials_required": ["Instagram", "TikTok", "YouTube", "X/Twitter"],
                "admin_note": "",
            }
        ],
    }


def _apply_runtime_settings(module, settings: Any) -> None:
    cleaned, errors = _validate_admin_settings(settings, module)
    if errors:
        print("[QCL admin] Ignoring invalid saved admin template: " + "; ".join(errors[:5]))
        return
    module.QCL_REGISTRATION_SOCIALS = cleaned["registration"]["socials_required"]
    module.QCL_REGISTRATION_FORM_SCHEMAS = cleaned["registration"]["form_schemas"]
    module.QCL_DEFAULT_SEASON_DAYS = cleaned["season"]["default_days"]
    module.QCL_SEASON_WINDOW_HOURS = cleaned["season"]["window_hours"]
    module.SCHED_DEFAULT_REVEAL_HOUR = cleaned["season"]["reveal_hour"]
    module.GH_REPO = cleaned["github"]["repo"]
    module.TVT_GH_REPO = cleaned["github"]["repo"]
    module.GH_BRANCH = cleaned["github"]["branch"]
    module.TVT_GH_BRANCH = cleaned["github"]["branch"]


def _clean_person(raw: Any, fallback_id: str = "") -> tuple[dict | None, str | None]:
    if not isinstance(raw, dict):
        return None, "Each people entry must be an object."
    user_id = _discord_id(raw.get("discord_id") or fallback_id)
    if not user_id:
        return None, "Each person needs a valid Discord ID."
    person = {key: raw[key] for key in REGISTRATION_FIELDS if key in raw}
    person["discord_id"] = user_id
    person["display_name"] = str(
        raw.get("display_name") or raw.get("discord_tag") or ""
    )[:100]
    person["discord_tag"] = str(raw.get("discord_tag") or person["display_name"])[:100]
    person["gamertag_or_psn"] = str(raw.get("gamertag_or_psn") or "")[:100]
    person["platform"] = str(raw.get("platform") or "")[:60]
    person["position"] = str(raw.get("position") or "")[:60]
    person["availability"] = str(raw.get("availability") or "")[:500]
    proofs = raw.get("social_proof", [])
    if not isinstance(proofs, list) or len(proofs) > 30:
        return None, f"social_proof for {user_id} must be a list of up to 30 entries."
    safe_proofs = []
    for proof in proofs:
        if isinstance(proof, str):
            proof = {"url": proof}
        if not isinstance(proof, dict):
            return None, f"Invalid social_proof entry for {user_id}."
        url = str(proof.get("url") or "")
        if url and not re.match(r"^https?://", url, re.I):
            return None, f"Social-proof URLs for {user_id} must start with http:// or https://."
        safe_proofs.append(
            {"url": url[:1000], "filename": str(proof.get("filename") or "")[:200]}
        )
    person["social_proof"] = safe_proofs
    platforms = raw.get("proof_platforms", [])
    if isinstance(platforms, str):
        platforms = [v.strip() for v in platforms.split(",") if v.strip()]
    if not isinstance(platforms, list) or len(platforms) > 20:
        return None, f"proof_platforms for {user_id} must be a list of up to 20 labels."
    person["proof_platforms"] = [str(v)[:50] for v in platforms if str(v).strip()]
    person["added_at"] = int(raw.get("added_at") or time.time())
    return person, None


def _validate_registration_batch(data: Any, module) -> tuple[list[dict], list[str]]:
    errors = []
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        return [], ["Registration template must be a JSON object with schema_version: 1."]
    rows = data.get("registrations")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 300:
        return [], ["registrations must contain between 1 and 300 records."]
    cleaned = []
    for index, row in enumerate(rows, 1):
        label = f"registrations[{index}]"
        if not isinstance(row, dict):
            errors.append(f"{label} must be an object.")
            continue
        role = str(row.get("role") or "").strip()
        if role not in REGISTRATION_ROLES:
            errors.append(f"{label}.role must be one of {', '.join(sorted(REGISTRATION_ROLES))}.")
        status = str(row.get("status") or "submitted").strip().lower()
        if status not in REGISTRATION_STATUSES:
            errors.append(f"{label}.status is not supported.")
        owner_id = _discord_id(row.get("owner_id") or row.get("discord_id"))
        if not owner_id:
            errors.append(f"{label}.owner_id must be a Discord mention or numeric ID.")
            continue
        people_input = row.get("people", [])
        if not isinstance(people_input, list) or len(people_input) > 100:
            errors.append(f"{label}.people must be a list of up to 100 people.")
            continue
        people = {}
        for raw_person in people_input:
            person, error = _clean_person(raw_person, owner_id)
            if error:
                errors.append(f"{label}: {error}")
                continue
            if person["discord_id"] in people:
                errors.append(f"{label} repeats Discord ID {person['discord_id']}.")
            else:
                people[person["discord_id"]] = person
        if owner_id not in people:
            owner, error = _clean_person(
                {
                    "discord_id": owner_id,
                    "display_name": row.get("owner_tag", ""),
                    "gamertag_or_psn": row.get("gamertag_or_psn", ""),
                    "platform": row.get("platform", ""),
                    "position": row.get("position", ""),
                    "availability": row.get("availability", ""),
                }
            )
            if error:
                errors.append(f"{label}: owner record could not be created.")
            else:
                people[owner_id] = owner
        socials = row.get("socials_required", [])
        if not isinstance(socials, list) or len(socials) > 12 or any(
            not isinstance(value, str) or len(value) > 50 for value in socials
        ):
            errors.append(f"{label}.socials_required must be a list of up to 12 short labels.")
            socials = []
        rid = str(row.get("registration_id") or "").strip()
        if rid and (len(rid) > 100 or not re.fullmatch(r"[A-Za-z0-9_-]+", rid)):
            errors.append(f"{label}.registration_id contains unsupported characters.")
        cleaned.append(
            {
                "registration_id": rid,
                "role": role,
                "status": status,
                "owner_id": owner_id,
                "owner_tag": str(row.get("owner_tag") or "")[:100],
                "source_guild_id": str(row.get("source_guild_id") or "")[:25] or None,
                "team_name": str(row.get("team_name") or "")[:100],
                "team_logo": str(row.get("team_logo") or "")[:1000] or None,
                "socials_required": list(dict.fromkeys(v.strip() for v in socials if v.strip())),
                "people": list(people.values()),
                "admin_note": str(row.get("admin_note") or "")[:1000],
            }
        )
    ids = [row["registration_id"] for row in cleaned if row["registration_id"]]
    if len(ids) != len(set(ids)):
        errors.append("A registration_id appears more than once in this upload.")
    return cleaned, errors


def install_qcl_admin(bot, module) -> None:
    @bot.command(name="registration_templates", aliases=["regtemplates", "formtemplates"])
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def registration_templates(ctx):
        schemas = getattr(module, "QCL_REGISTRATION_FORM_SCHEMAS", None)
        await ctx.send(
            "Template contains BYOT GM, Draft GM, and Draft Player forms. "
            "Fill one or more `registrations` entries and attach it to "
            "`!registration_import`.",
            file=_json_attachment(
                _registration_template(schemas),
                "qcl_registration_forms_template.json",
            ),
        )

    @bot.command(name="registration_import", aliases=["regimport"])
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def registration_import(ctx):
        if not ctx.message.attachments:
            return await ctx.send("Attach the filled JSON from `!registration_templates`.")
        attachment = ctx.message.attachments[0]
        if not attachment.filename.lower().endswith(".json"):
            return await ctx.send("Registration imports must be `.json` files.")
        if attachment.size > MAX_TEMPLATE_BYTES:
            return await ctx.send("Template is too large. Maximum size is 1 MB.")
        try:
            data = json.loads((await attachment.read()).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return await ctx.send(f"Could not read valid UTF-8 JSON: `{exc}`")
        rows, errors = _validate_registration_batch(data, module)
        if errors:
            return await ctx.send(
                "Import rejected; nothing was changed:\n"
                + "\n".join(f"• {error}" for error in errors[:15])
                + (f"\n…and {len(errors) - 15} more errors." if len(errors) > 15 else "")
            )
        state = module.load_state()
        registrations = state.setdefault("qcl_registrations", {})
        imported_ids = []
        now = int(time.time())
        for row in rows:
            rid = row["registration_id"] or module._qcl_reg_new_id(row["owner_id"])
            existing = registrations.get(rid, {})
            record = dict(existing)
            record.update(
                {
                    "registration_id": rid,
                    "created_at": existing.get("created_at", now),
                    "updated_at": now,
                    "role": row["role"],
                    "status": row["status"],
                    "owner_id": row["owner_id"],
                    "owner_tag": row["owner_tag"],
                    "source_guild_id": row["source_guild_id"],
                    "team_name": row["team_name"],
                    "team_logo": row["team_logo"],
                    "socials_required": row["socials_required"],
                    "people": row["people"],
                    "admin_note": row["admin_note"],
                }
            )
            action = "template_import_updated" if existing else "template_import_created"
            _history(record, action, str(ctx.author.id), people_count=len(row["people"]))
            registrations[rid] = record
            imported_ids.append(rid)
        module._qcl_reg_rebuild_people_index(state)
        log = state.setdefault("qcl_registration_import_log", [])
        log.append(
            {
                "at": now,
                "by": str(ctx.author.id),
                "guild_id": str(ctx.guild.id),
                "attachment": attachment.filename[:200],
                "registration_ids": imported_ids,
            }
        )
        audit_limit = int(
            state.get("qcl_admin_template", {})
            .get("admin", {})
            .get("audit_log_limit", 100)
        )
        del log[:-audit_limit]
        module.save_state(state)
        await ctx.send(
            f"Imported **{len(imported_ids)}** registration(s). "
            "Each person is indexed to their Discord ID. "
            "No unlisted records were deleted."
        )

    @bot.command(name="registration_export", aliases=["regexport"])
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def registration_export(ctx, registration_id: str = ""):
        state = module.load_state()
        records = state.get("qcl_registrations", {})
        if registration_id:
            record = module._qcl_reg_find(state, registration_id)
            if not record:
                return await ctx.send("Registration not found.")
            payload = {
                "schema_version": 1,
                "registrations": [
                    {
                        key: record.get(key)
                        for key in (
                            "registration_id",
                            "role",
                            "status",
                            "owner_id",
                            "owner_tag",
                            "source_guild_id",
                            "team_name",
                            "team_logo",
                            "socials_required",
                            "people",
                            "admin_note",
                        )
                    }
                ],
            }
            filename = f"registration_{registration_id[:60]}.json"
        else:
            payload = {
                "schema_version": 1,
                "registrations": list(records.values()),
            }
            filename = "qcl_registrations_export.json"
        try:
            await ctx.author.send(
                f"Private registration export: {len(payload['registrations'])} record(s).",
                file=_json_attachment(payload, filename),
            )
        except discord.Forbidden:
            return await ctx.send(
                "I could not DM the export. Enable DMs from this server and run the command again."
            )
        await ctx.send("Sent the private registration export to your DMs.")

    @bot.command(name="admin_template", aliases=["settingstemplate"])
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def admin_template(ctx):
        state = module.load_state()
        payload = state.get("qcl_admin_template") or _default_admin_settings(module)
        await ctx.send(
            "Edit only the documented allowlisted settings, then attach the file "
            "to `!admin_apply`. Tokens, PINs, arbitrary paths, and code are not accepted.",
            file=_json_attachment(payload, "qcl_admin_settings_template.json"),
        )

    @bot.command(name="admin_apply", aliases=["applytemplate"])
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def admin_apply(ctx):
        if not ctx.message.attachments:
            return await ctx.send("Attach an edited JSON file from `!admin_template`.")
        attachment = ctx.message.attachments[0]
        if not attachment.filename.lower().endswith(".json"):
            return await ctx.send("Settings imports must be `.json` files.")
        if attachment.size > MAX_TEMPLATE_BYTES:
            return await ctx.send("Template is too large. Maximum size is 1 MB.")
        try:
            data = json.loads((await attachment.read()).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return await ctx.send(f"Could not read valid UTF-8 JSON: `{exc}`")
        cleaned, errors = _validate_admin_settings(data, module)
        if errors:
            return await ctx.send(
                "Settings rejected; nothing was changed:\n"
                + "\n".join(f"• {error}" for error in errors[:15])
            )
        state = module.load_state()
        state["qcl_admin_template"] = cleaned
        audit = state.setdefault("qcl_admin_template_audit", [])
        audit.append(
            {
                "at": int(time.time()),
                "by": str(ctx.author.id),
                "guild_id": str(ctx.guild.id),
                "filename": attachment.filename[:200],
            }
        )
        del audit[:-cleaned["admin"]["audit_log_limit"]]
        module.save_state(state)
        _apply_runtime_settings(module, cleaned)
        await ctx.send(
            "Applied the validated template and recorded the admin audit entry. "
            "Registration social requirements, season defaults, and GitHub target "
            "are active now. The admin desk reads the saved settings after restart."
        )

    @bot.command(name="github_upload", aliases=["ghupload", "githubcode"])
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def github_upload(ctx):
        if not ctx.message.attachments:
            return await ctx.send("Attach a source or data file to upload.")
        attachment = ctx.message.attachments[0]
        if attachment.size > MAX_GITHUB_UPLOAD_BYTES:
            return await ctx.send("Maximum GitHub upload size is 5 MB.")
        filename = os.path.basename(attachment.filename)
        if not filename or filename in {".", ".."} or not re.fullmatch(
            r"[A-Za-z0-9_.-]{1,120}", filename
        ):
            return await ctx.send("Filename contains unsupported characters.")
        extension = os.path.splitext(filename)[1].lower()
        if extension not in {".py", ".json", ".md", ".csv", ".txt"}:
            return await ctx.send("Allowed upload types: `.py`, `.json`, `.md`, `.csv`, `.txt`.")
        raw = await attachment.read()
        if extension == ".py":
            try:
                ast.parse(raw.decode("utf-8"))
            except (UnicodeDecodeError, SyntaxError) as exc:
                return await ctx.send(f"Python upload rejected by syntax check: `{exc}`")
        token = str(getattr(module, "GH_TOKEN", "") or os.getenv("GITHUB_TOKEN", ""))
        repo = str(getattr(module, "GH_REPO", "") or "")
        branch = str(getattr(module, "GH_BRANCH", "main") or "main")
        if not token:
            return await ctx.send(
                "GitHub upload is not configured. Set `GITHUB_TOKEN` in the bot host's "
                "environment or secrets manager; do not post it in Discord."
            )
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            return await ctx.send("GitHub repository is not configured as owner/repository.")
        path = f"qcl_admin_uploads/{filename}"
        api = f"https://api.github.com/repos/{repo}/contents/{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "QCL-admin-uploader",
        }
        async with aiohttp.ClientSession() as session:
            sha = None
            async with session.get(api, params={"ref": branch}, headers=headers) as response:
                if response.status == 200:
                    existing = await response.json()
                    sha = existing.get("sha")
                elif response.status != 404:
                    detail = await response.text()
                    return await ctx.send(
                        f"GitHub lookup failed ({response.status}): `{detail[:300]}`"
                    )
            payload = {
                "message": f"QCL admin upload: {filename}",
                "content": base64.b64encode(raw).decode("ascii"),
                "branch": branch,
            }
            if sha:
                payload["sha"] = sha
            async with session.put(api, json=payload, headers=headers) as response:
                result = await response.json(content_type=None)
                if response.status not in (200, 201):
                    detail = result.get("message", str(result)) if isinstance(result, dict) else str(result)
                    return await ctx.send(f"GitHub rejected the upload ({response.status}): `{detail[:400]}`")
        await ctx.send(
            f"Uploaded `{filename}` to `{repo}/{path}` on branch `{branch}`. "
            "Python files are syntax-checked and stored as uploads; they are not executed."
        )

    class QCLAdminDesk(discord.ui.View):
        def __init__(self, owner_id: int):
            super().__init__(timeout=900)
            self.owner_id = owner_id

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message(
                    "This admin desk belongs to another administrator.",
                    ephemeral=True,
                )
                return False
            if not (
                interaction.user.id == getattr(interaction.guild, "owner_id", None)
                or getattr(
                    getattr(interaction.user, "guild_permissions", None),
                    "administrator",
                    False,
                )
            ):
                await interaction.response.send_message(
                    "Administrator permission is required.", ephemeral=True
                )
                return False
            return True

        @discord.ui.button(label="Registration desk", style=discord.ButtonStyle.primary)
        async def registration_desk(self, interaction, button):
            try:
                await interaction.response.send_message(
                    embed=module._qcl_reg_desk_embed(module.load_state(), "all"),
                    view=module.QCLRegistrationDeskView("all"),
                    ephemeral=True,
                )
            except Exception as exc:
                await interaction.response.send_message(
                    f"Could not open registrations: `{type(exc).__name__}: {exc}`",
                    ephemeral=True,
                )

        @discord.ui.button(label="Form templates", style=discord.ButtonStyle.secondary)
        async def form_templates(self, interaction, button):
            schemas = getattr(module, "QCL_REGISTRATION_FORM_SCHEMAS", None)
            await interaction.response.send_message(
                "Fill and import applicant records with `!registration_import`.",
                file=_json_attachment(
                    _registration_template(schemas),
                    "qcl_registration_forms_template.json",
                ),
                ephemeral=True,
            )

        @discord.ui.button(label="Settings template", style=discord.ButtonStyle.secondary)
        async def settings_template(self, interaction, button):
            state = module.load_state()
            payload = state.get("qcl_admin_template") or _default_admin_settings(module)
            await interaction.response.send_message(
                "Edit only supported settings; import with `!admin_apply`.",
                file=_json_attachment(payload, "qcl_admin_settings_template.json"),
                ephemeral=True,
            )

        @discord.ui.button(label="Publish stats", style=discord.ButtonStyle.success)
        async def publish_stats(self, interaction, button):
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                stats = await __import__("asyncio").to_thread(module._ps_compute_all)
                if not stats:
                    return await interaction.followup.send(
                        "No stats were computed. Check the configured stats sheet.",
                        ephemeral=True,
                    )
                publish = getattr(module, "_tvt_gh_put_json", None)
                if not publish or not getattr(module, "TVT_GH_TOKEN", ""):
                    return await interaction.followup.send(
                        "Stats are ready, but GitHub publishing needs `GITHUB_TOKEN`.",
                        ephemeral=True,
                    )
                ok = await publish(
                    "player_stats.json", stats, f"QCL stats: {len(stats)} players"
                )
                await interaction.followup.send(
                    f"{'Published' if ok else 'Could not publish'} {len(stats)} player stats.",
                    ephemeral=True,
                )
            except Exception as exc:
                await interaction.followup.send(
                    f"Stats publish failed: `{type(exc).__name__}: {exc}`",
                    ephemeral=True,
                )

        @discord.ui.button(label="URG command center", style=discord.ButtonStyle.danger)
        async def urg_command_center(self, interaction, button):
            try:
                await interaction.response.defer(ephemeral=True)
                await interaction.followup.send(
                    "Use `!commandcenter` in the channel where you want the URG control panel.",
                    ephemeral=True,
                )
            except Exception:
                pass

        @discord.ui.button(label="Box-score tools", style=discord.ButtonStyle.primary)
        async def boxscore_tools(self, interaction, button):
            await interaction.response.send_message(
                "Use `!sweepboxscores` to review queued screenshots. "
                "Individual submissions use the `submit_boxscore` prefix route; "
                "it parses, validates, and sends the reviewed result to the stats sheet.",
                ephemeral=True,
            )

        @discord.ui.button(label="Brain / file feed", style=discord.ButtonStyle.secondary)
        async def brain_tools(self, interaction, button):
            await interaction.response.send_message(
                "Use the `brain feed` command with a supported file attachment, or "
                "`brain sync` to scan the configured knowledge vault. PIN checks "
                "remain private and are never stored in the template.",
                ephemeral=True,
            )

    @bot.command(name="qcladmin", aliases=["qcl_admin", "adminhub"])
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def qcl_admin_desk(ctx):
        description = (
            "**QCL / URG administration**\n"
            "• Registrations: review, import, export, and attach each person to a Discord ID.\n"
            "• Configuration: download and apply the allowlisted settings template.\n"
            "• Stats: publish player totals to the configured GitHub repository.\n"
            "• Box scores: parse and review game submissions before sheet updates.\n"
            "• URG: open `!commandcenter` for event tools.\n"
            "• Files: feed CupMuse or upload syntax-checked code/data to GitHub.\n\n"
            "Every template import is validated before saving and writes an audit entry."
        )
        await ctx.send(
            embed=discord.Embed(
                title="QCL / URG Admin Desk",
                description=description,
                color=0x2456A6,
            ),
            view=QCLAdminDesk(ctx.author.id),
        )

    @bot.command(name="stats_upload", aliases=["qclstats"])
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def stats_upload(ctx):
        if not ctx.message.attachments:
            return await ctx.send(
                "Attach a `.csv` or `.json` file. It will be syntax/format checked "
                "and uploaded to the configured GitHub repository; it will not overwrite "
                "the live stats source. Use `!publishstats` to publish calculated player totals."
            )
        attachment = ctx.message.attachments[0]
        if attachment.size > MAX_GITHUB_UPLOAD_BYTES:
            return await ctx.send("Maximum stats upload size is 5 MB.")
        ext = os.path.splitext(attachment.filename)[1].lower()
        if ext not in {".csv", ".json"}:
            return await ctx.send("Stats upload accepts `.csv` or `.json` only.")
        raw = await attachment.read()
        try:
            if ext == ".json":
                parsed = json.loads(raw.decode("utf-8"))
                if not isinstance(parsed, (dict, list)):
                    raise ValueError("JSON root must be an object or array.")
            else:
                text = raw.decode("utf-8-sig")
                import csv

                rows = list(csv.DictReader(io.StringIO(text)))
                if not rows or not ({"PlayerName", "Player", "Name", "Player/Team"} & set(rows[0])):
                    raise ValueError("CSV needs a player-name column (PlayerName, Player, Name, or Player/Team).")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            return await ctx.send(f"Stats file rejected: `{exc}`")
        # Forward the already validated attachment through the same guarded uploader.
        await github_upload.callback(ctx)

    # Load saved non-secret settings before the bot accepts commands.
    saved_settings = module.load_state().get("qcl_admin_template")
    if saved_settings:
        _apply_runtime_settings(module, saved_settings)

    # Mention the supported upload aliases in help without registering slash commands.
    bot.qcl_admin_commands = {
        "qcladmin": qcl_admin_desk,
        "registration_templates": registration_templates,
        "registration_import": registration_import,
        "registration_export": registration_export,
        "admin_template": admin_template,
        "admin_apply": admin_apply,
        "github_upload": github_upload,
        "stats_upload": stats_upload,
    }