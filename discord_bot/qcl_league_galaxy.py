"""Tap-only League Galaxy extension for QCL2K.

Loaded immediately before ``bot.run`` with::

    install_galaxy(bot, globals())

It deliberately keeps all state in the view, uses the bot's existing data
frame, and never makes network requests.  The attachment is decorative; all
navigation is done with Discord components.
"""
from __future__ import annotations

import io
import math
import os
import random
import time
from pathlib import Path


def install_galaxy(bot, namespace):
    """Install the Galaxy button and replace the existing stats-panel class."""
    discord = namespace.get("_tvt_discord") or namespace.get("discord")
    if discord is None:
        import discord  # type: ignore
    ns = namespace
    old = ns.get("QCLStatsPanelView")
    galaxy = _make_galaxy(discord, ns)
    if old is not None:
        class GalaxyStatsPanelView(old):  # type: ignore[misc, valid-type]
            @discord.ui.button(label="🌌 League Galaxy",
                               style=discord.ButtonStyle.primary, row=0)
            async def _galaxy_button(self, interaction, button):
                view = galaxy(interaction.user.id)
                await interaction.response.edit_message(
                    embed=view.embed(), attachments=view.attachments(), view=view)
        ns["QCLStatsPanelView"] = GalaxyStatsPanelView
    else:
        ns["QCLStatsPanelView"] = galaxy
    return galaxy


def _make_galaxy(discord, ns):
    def frame(era):
        try:
            value = ns["_era_player_frame"](era)
            return value
        except Exception:
            return None

    def records(era):
        df = frame(era)
        if df is None:
            return []
        try:
            return list(df.to_dict("records"))
        except Exception:
            try:
                return [dict(x) for _, x in df.iterrows()]
            except Exception:
                return []

    def num(row, key):
        try:
            value = row.get(key, 0)
            value = float(value) if value is not None else 0.0
            return value if math.isfinite(value) else 0.0
        except (TypeError, ValueError):
            return 0.0

    def name(row):
        return str(row.get("Player/Team") or row.get("Player") or "Unknown")

    def player_rows(era):
        result = []
        for row in records(era):
            if str(row.get("Type", "player")).lower() == "player" and name(row) != "Unknown":
                result.append(row)
        return result

    _draft_info_cache = {"at": 0.0, "data": {}}

    def aggregate(era):
        grouped = {}
        for row in player_rows(era):
            key = name(row)
            grouped.setdefault(key, []).append(row)
        out = []
        for key, rows in grouped.items():
            avg = lambda field: sum(num(x, field) for x in rows) / max(1, len(rows))
            total = lambda field: sum(num(x, field) for x in rows)
            pts, reb, ast = avg("PTS"), avg("REB"), avg("AST")
            stl, blk = avg("STL"), avg("BLK")
            fga, fta = total("FGA"), total("FTA")
            ts = total("PTS") / (2 * (fga + .44 * fta)) * 100 if fga + .44 * fta else 0
            gp = len({str(x.get("Game_ID")) for x in rows if x.get("Game_ID") is not None})
            if not gp:
                gp = len(rows)
            fgm, three_pm, three_pa = total("FGM"), total("3PM"), total("3PA")
            ftm = total("FTM")
            fg_pct = fgm / fga * 100 if fga else 0
            three_pct = three_pm / three_pa * 100 if three_pa else 0
            ft_pct = ftm / fta * 100 if fta else 0
            score = pts + .7 * reb + .9 * ast + 1.3 * (stl + blk)

            def recent_key(row):
                value = (row.get("date") or row.get("Date") or
                         row.get("game_date") or row.get("Game_ID") or "")
                try:
                    return (0, float(value))
                except (TypeError, ValueError):
                    return (1, str(value))

            rows = sorted(rows, key=recent_key)
            out.append({"name": key, "rows": rows, "pts": pts, "reb": reb, "ast": ast,
                        "stl": stl, "blk": blk, "ts": ts, "gp": gp, "score": score,
                        "fg_pct": fg_pct, "three_pct": three_pct, "ft_pct": ft_pct,
                        "fgm": fgm / gp, "fga": fga / gp,
                        "three_pm": three_pm / gp, "three_pa": three_pa / gp,
                        "ftm": ftm / gp, "fta": fta / gp})
        return out

    def draft_info():
        now = time.monotonic()
        if now - _draft_info_cache["at"] < 5:
            return _draft_info_cache["data"]
        try:
            draft = ns["_draft_data"]()[1]
            result = {str(v.get("gamertag", k)).lower(): v
                      for k, v in (draft.get("players") or {}).items()
                      if isinstance(v, dict)}
        except Exception:
            result = {}
        _draft_info_cache.update(at=now, data=result)
        return result

    def info(item):
        draft = draft_info().get(item["name"].lower(), {})
        row = item["rows"][-1] if item["rows"] else {}
        team = draft.get("team") or draft.get("drafted_by") or row.get("Team Name") or "Free Agent"
        position = draft.get("position") or row.get("Position") or "Open"
        build = draft.get("build") or draft.get("archetype") or "Unlisted"
        return str(team), str(position), str(build)

    def archetype(item):
        p, a, r, d, t = item["pts"], item["ast"], item["reb"], item["stl"] + item["blk"], item["ts"]
        if p >= 20 and a >= 5:
            return "Shot-Creating Playmaker"
        if p >= 20:
            return "Scoring Wing"
        if a >= 7:
            return "Floor General"
        if r >= 9 and d >= 2:
            return "Two-Way Big"
        if r >= 9:
            return "Glass Cleaner"
        if d >= 3:
            return "Defensive Specialist"
        if t >= 58:
            return "Efficient Role Player"
        return "All-Around"

    def metric(item, key):
        return {"Scoring": item["pts"], "Playmaking": item["ast"], "Rebounding": item["reb"],
                "Defense": item["stl"] + item["blk"], "Efficiency": item["ts"],
                "Impact": item["score"]}.get(key, item["score"])

    def asset_paths():
        roots = [Path(__file__).parent / "backgrounds", Path(__file__).parent.parent / "backgrounds"]
        for root in roots:
            if root.exists():
                files = sorted(p for p in root.iterdir() if p.suffix.lower() in
                               {".png", ".jpg", ".jpeg", ".webp"})
                if files:
                    return files
        return []

    def galaxy_gif(items, graph, background, metric_key="Impact"):
        try:
            from PIL import Image, ImageDraw, ImageOps
        except Exception:
            return None
        rng = random.Random(9173 + len(items) * 31 + len(graph) + background)
        bg = None
        assets = asset_paths()
        path = assets[background - 3] if background >= 3 and background - 3 < len(assets) else None
        if path:
            try:
                bg = ImageOps.fit(Image.open(path).convert("RGB"), (900, 480))
            except Exception:
                bg = None
        frames = []
        palette = [(35, 20, 84), (20, 55, 70), (8, 12, 30)]
        metric_key_name = metric_key if metric_key in {
            "Scoring", "Playmaking", "Rebounding", "Defense", "Efficiency"
        } else "Impact"

        def value_for(item):
            return metric(item, metric_key_name)

        for tick in range(3):
            image = bg.copy() if bg else Image.new("RGB", (900, 480), palette[background % 3])
            draw = ImageDraw.Draw(image, "RGBA")
            if bg:
                shade = Image.new("RGBA", image.size, (5, 9, 24, 145))
                image = Image.alpha_composite(image.convert("RGBA"), shade).convert("RGB")
                draw = ImageDraw.Draw(image, "RGBA")
            elif background % 3 == 1:
                # A simple court floor for a second non-space visual treatment.
                draw.rectangle((24, 28, 876, 452), outline=(120, 210, 225, 110), width=2)
                draw.line((450, 28, 450, 452), fill=(120, 210, 225, 100), width=2)
                draw.ellipse((365, 160, 535, 330), outline=(120, 210, 225, 95), width=2)
                draw.rectangle((24, 140, 155, 340), outline=(120, 210, 225, 90), width=2)
                draw.rectangle((745, 140, 876, 340), outline=(120, 210, 225, 90), width=2)
            else:
                for _ in range(160):
                    x, y = rng.randrange(900), rng.randrange(480)
                    draw.ellipse((x, y, x + rng.choice((1, 2, 3)), y + 2),
                                 fill=(150, 190, 255, rng.randrange(70, 230)))
            draw.text((28, 20), f"QCL  ·  {graph.upper()}", fill=(255, 220, 110, 255))
            ranked = sorted(items, key=value_for, reverse=True)
            if graph == "Galaxy":
                center = (450, 260)
                draw.ellipse((center[0]-105, center[1]-105, center[0]+105, center[1]+105),
                             outline=(255, 214, 74, 150), width=2)
                draw.text((center[0]-48, center[1]-8), "QCL", fill=(255, 238, 170, 255))
                for i, item in enumerate(ranked[:24]):
                    theta = i / max(1, min(24, len(ranked))) * math.tau + tick * .12
                    radius = 118 + (i % 3) * 46
                    x = int(center[0] + math.cos(theta) * radius)
                    y = int(center[1] + math.sin(theta) * radius * .58)
                    glow = 8 + tick % 2 * 2
                    draw.ellipse((x-glow, y-glow, x+glow, y+glow),
                                 fill=(93, 118, 255, 75))
                    draw.ellipse((x-5, y-5, x+5, y+5), fill=(255, 214, 74, 245))
                    if i < 12:
                        draw.text((x+9, y-7), item["name"][:17],
                                  fill=(240, 245, 255, 235))
            elif graph == "Leaders":
                bars = ranked[:8]
                largest = max((value_for(x) for x in bars), default=1) or 1
                for i, item in enumerate(bars):
                    y = 76 + i * 44
                    amount = max(0, value_for(item))
                    width = int(540 * amount / largest)
                    draw.text((30, y+3), item["name"][:24], fill=(245, 247, 255, 245))
                    draw.rounded_rectangle((250, y, 250+width, y+25), radius=8,
                                           fill=(89, 185, 255, 210))
                    draw.text((260+width, y+4), f"{amount:.1f}",
                              fill=(255, 222, 130, 255))
            elif graph == "Profile trend":
                item = ranked[0] if ranked else None
                if item:
                    values = item["rows"][-8:]
                    points = [num(row, "PTS") for row in values]
                    low = min(points + [0])
                    high = max(points + [1])
                    draw.text((36, 60), f"{item['name']} · recent PTS/game",
                              fill=(245, 247, 255, 245))
                    draw.line((60, 385, 840, 385), fill=(210, 225, 245, 140), width=2)
                    coords = []
                    for i, point in enumerate(points):
                        x = 72 + i * (744 / max(1, len(points)-1))
                        y = 360 - int((point-low) / max(1, high-low) * 245)
                        coords.append((int(x), y))
                    if len(coords) > 1:
                        draw.line(coords, fill=(80, 215, 255, 255), width=5)
                    for i, (x, y) in enumerate(coords):
                        draw.ellipse((x-7, y-7, x+7, y+7), fill=(255, 214, 74, 255))
                        draw.text((x-10, y+15), str(i+1), fill=(230, 238, 255, 230))
            elif graph == "Radar":
                item = ranked[0] if ranked else None
                if item:
                    axes = [("PTS", min(item["pts"] / 30, 1)),
                            ("AST", min(item["ast"] / 12, 1)),
                            ("REB", min(item["reb"] / 15, 1)),
                            ("DEF", min((item["stl"] + item["blk"]) / 6, 1)),
                            ("TS%", min(item["ts"] / 70, 1))]
                    cx, cy, radius = 450, 270, 165
                    coords = []
                    for index, (label, score) in enumerate(axes):
                        angle = -math.pi / 2 + index * math.tau / len(axes)
                        ex, ey = cx + math.cos(angle)*radius, cy + math.sin(angle)*radius
                        draw.line((cx, cy, ex, ey), fill=(190, 205, 235, 155), width=2)
                        draw.text((int(cx + math.cos(angle)*(radius+20)-14),
                                   int(cy + math.sin(angle)*(radius+20)-8)),
                                  label, fill=(245, 247, 255, 245))
                        coords.append((int(cx + math.cos(angle)*radius*score),
                                       int(cy + math.sin(angle)*radius*score)))
                    for fraction in (.25, .5, .75, 1):
                        ring = []
                        for index in range(len(axes)):
                            angle = -math.pi / 2 + index * math.tau / len(axes)
                            ring.append((int(cx + math.cos(angle)*radius*fraction),
                                         int(cy + math.sin(angle)*radius*fraction)))
                        draw.line(ring + [ring[0]], fill=(145, 165, 205, 85), width=1)
                    draw.line(coords + [coords[0]], fill=(80, 215, 255, 245), width=5)
                    draw.text((32, 60), f"{item['name']} · stat profile",
                              fill=(245, 247, 255, 245))
            frames.append(image)
        out = io.BytesIO()
        frames[0].save(out, format="GIF", save_all=True, append_images=frames[1:],
                       duration=360, loop=0, optimize=True)
        out.seek(0)
        return out

    class SearchModal(discord.ui.Modal, title="Search the League Galaxy"):
        query = discord.ui.TextInput(label="Player name", placeholder="Type a name…",
                                     required=False, max_length=80)

        def __init__(self, galaxy_view):
            super().__init__()
            self.galaxy_view = galaxy_view
            self.query.default = galaxy_view.query

        async def on_submit(self, interaction):
            v = GalaxyView(self.galaxy_view.opener_id, self.galaxy_view.era,
                           self.galaxy_view.group, self.galaxy_view.metric_key,
                           str(self.query.value or "").strip(), 0,
                           self.galaxy_view.graph, self.galaxy_view.background)
            await self.galaxy_view.edit(interaction, v)

    class GalaxyView(discord.ui.View):
        PER = 20
        def __init__(self, opener_id=None, era=None, group="All players", metric_key="Impact",
                     query="", page=0, graph="Galaxy", background=0, chart=None, timeout=300):
            # Cooperative initialization: the legacy stats view calls super()
            # with only ``timeout`` while this class is its second base.
            if opener_id is None:
                super().__init__(timeout=timeout)
                return
            super().__init__(timeout=timeout)
            if era is None:
                try:
                    era = ns.get("_active_era", lambda: "ALL")()
                except Exception:
                    era = "ALL"
            self.opener_id, self.era, self.group = opener_id, era or "ALL", group
            self.metric_key, self.query, self.page = metric_key, query, page
            self.graph, self.background = graph if chart is None else chart, background
            self.items = aggregate(self.era)
            self._build()

        def matches(self, item):
            team, pos, build = info(item)
            if self.group == "Archetype: " + archetype(item):
                return True
            if self.group == "Build: " + info(item)[2]:
                return True
            if self.group == "Position: " + pos:
                return True
            if self.group == "Team: " + team:
                return True
            if self.group == "Metric: Elite":
                return metric(item, self.metric_key) >= (70 if self.metric_key == "Efficiency" else
                                                         20 if self.metric_key == "Scoring" else 8)
            if self.group == "Metric: Rising":
                return metric(item, self.metric_key) >= (55 if self.metric_key == "Efficiency" else 5)
            return self.group == "All players"

        def filtered(self):
            needle = self.query.lower().strip()
            values = [x for x in self.items if self.matches(x) and
                      (not needle or needle in x["name"].lower())]
            return sorted(values, key=lambda x: metric(x, self.metric_key), reverse=True)

        def add_select(self, placeholder, options, callback, row):
            if not options:
                options = [discord.SelectOption(label="No data", value="none")]
            select = discord.ui.Select(placeholder=placeholder[:150], options=options[:25], row=row)
            select.callback = callback
            self.add_item(select)

        def _build(self):
            eras = [discord.SelectOption(label=x, value=x, default=x == self.era)
                    for x in ("ALL", "SPAM", "QCL")]
            self.add_select("Choose era…", eras, self._era, 0)
            dynamic_groups = (
                ["Archetype: " + x for x in sorted({archetype(i) for i in self.items})]
                + ["Build: " + x for x in sorted({info(i)[2] for i in self.items})]
                + ["Position: " + x for x in sorted({info(i)[1] for i in self.items})]
                + ["Team: " + x for x in sorted({info(i)[0] for i in self.items})]
            )
            groups = (["All players", "Metric: Elite", "Metric: Rising"]
                      + ["Rank: " + x for x in
                         ("Impact", "Scoring", "Playmaking", "Rebounding", "Defense", "Efficiency")]
                      + dynamic_groups)[:25]
            self._group_options = {f"group:{i}": label for i, label in enumerate(groups)}
            self.add_select("Group / rank…", [discord.SelectOption(
                label=label[:100], value=key, default=label == self.group)
                for key, label in self._group_options.items()], self._group, 1)
            values = self.filtered()
            start, end = self.page * self.PER, (self.page + 1) * self.PER
            self.add_select("Browse players…", [discord.SelectOption(
                label=f"{i['name'][:72]} · {metric(i, self.metric_key):.1f}",
                value=f"player:{start + n}") for n, i in enumerate(values[start:end])], self._player, 2)
            search = discord.ui.Button(label="🔎 Search", style=discord.ButtonStyle.primary, row=3)
            search.callback = self._search
            self.add_item(search)

        def embed(self):
            values = self.filtered()
            pages = max(1, math.ceil(len(values) / self.PER))
            desc = (f"**{self.group}** · sorted by **{self.metric_key}**\n"
                    f"{len(values)} players · page {self.page + 1}/{pages}\n"
                    "Tap a player for a richer dossier. Use **Search** for a name, or "
                    "filter by archetype, build, position, team, and stat signal.")
            e = discord.Embed(title="🌌 League Galaxy", description=desc, color=0x6C63FF)
            e.add_field(name="Galaxy mode", value=f"{self.graph} · background {self.background + 1} · animated map attached", inline=False)
            return e

        def attachments(self):
            gif = galaxy_gif(self.filtered(), self.graph, self.background, self.metric_key)
            return [discord.File(gif, filename="league_galaxy.gif")] if gif else []

        async def edit(self, interaction, view):
            await interaction.response.edit_message(embed=view.embed(),
                                                     attachments=view.attachments(), view=view)

        async def _era(self, interaction):
            v = GalaxyView(self.opener_id, interaction.data["values"][0], self.group,
                           self.metric_key, self.query, 0, self.graph, self.background)
            await self.edit(interaction, v)

        async def _group(self, interaction):
            selected = self._group_options.get(interaction.data["values"][0], "All players")
            group, metric_key = selected, self.metric_key
            if selected.startswith("Rank: "):
                group, metric_key = self.group, selected[6:]
            v = GalaxyView(self.opener_id, self.era, group, metric_key,
                           self.query, 0, self.graph, self.background)
            await self.edit(interaction, v)

        async def _metric(self, interaction):
            v = GalaxyView(self.opener_id, self.era, self.group, interaction.data["values"][0],
                           self.query, 0, self.graph, self.background)
            await self.edit(interaction, v)

        async def _player(self, interaction):
            selected = interaction.data["values"][0]
            values = self.filtered()
            try:
                item = values[int(selected.split(":", 1)[1])]
            except (ValueError, IndexError):
                item = None
            if item:
                v = DossierView(self, item)
                await self.edit(interaction, v)

        async def _search(self, interaction):
            modal = SearchModal(self)
            await interaction.response.send_modal(modal)

        @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary, row=4)
        async def _prev(self, interaction, button):
            v = GalaxyView(self.opener_id, self.era, self.group, self.metric_key,
                           self.query, max(0, self.page - 1), self.graph, self.background)
            await self.edit(interaction, v)

        @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary, row=4)
        async def _next(self, interaction, button):
            pages = max(1, math.ceil(len(self.filtered()) / self.PER))
            v = GalaxyView(self.opener_id, self.era, self.group, self.metric_key,
                           self.query, min(pages - 1, self.page + 1), self.graph, self.background)
            await self.edit(interaction, v)

        @discord.ui.button(label="📈 Graph", style=discord.ButtonStyle.primary, row=4)
        async def _cycle(self, interaction, button):
            graph = {"Galaxy": "Leaders", "Leaders": "Profile trend",
                     "Profile trend": "Radar", "Radar": "Galaxy"}[self.graph]
            v = GalaxyView(self.opener_id, self.era, self.group, self.metric_key,
                           self.query, self.page, graph, self.background)
            await self.edit(interaction, v)

        @discord.ui.button(label="🖼 Background", style=discord.ButtonStyle.primary, row=4)
        async def _background(self, interaction, button):
            count = 3 + len(asset_paths())
            v = GalaxyView(self.opener_id, self.era, self.group, self.metric_key,
                           self.query, self.page, self.graph, (self.background + 1) % count)
            await self.edit(interaction, v)

        @discord.ui.button(label="⬅ Stats", style=discord.ButtonStyle.secondary, row=4)
        async def _back(self, interaction, button):
            parent = ns.get("QCLStatsPanelView")
            v = parent(self.opener_id) if parent else self
            await interaction.response.edit_message(embed=ns["_qcl_stats_embed"](),
                                                     attachments=[], view=v)

    class DossierView(discord.ui.View):
        def __init__(self, galaxy_view, item):
            super().__init__(timeout=300)
            self.galaxy_view, self.item = galaxy_view, item

        def embed(self):
            i = self.item
            team, pos, build = info(i)
            recent = i["rows"][-5:]
            recent_pts = sum(num(x, "PTS") for x in recent) / max(1, len(recent))
            e = discord.Embed(title=f"🪐 {i['name']}", color=0xFFD66B,
                              description=f"**{archetype(i)}** · {pos} · {team}\nBuild: `{build}`")
            e.add_field(name="Per-game", value=(f"PTS `{i['pts']:.1f}` · REB `{i['reb']:.1f}` · "
                                                f"AST `{i['ast']:.1f}`\nSTL `{i['stl']:.1f}` · BLK `{i['blk']:.1f}`"),
                        inline=False)
            e.add_field(name="Shooting / efficiency",
                        value=(f"FG `{i['fg_pct']:.1f}%` · 3P `{i['three_pct']:.1f}%` · "
                               f"FT `{i['ft_pct']:.1f}%` · TS `{i['ts']:.1f}%`\n"
                               f"Per game: FGM `{i['fgm']:.1f}/{i['fga']:.1f}` · "
                               f"3PM `{i['three_pm']:.1f}/{i['three_pa']:.1f}` · "
                               f"FTM `{i['ftm']:.1f}/{i['fta']:.1f}`"),
                        inline=False)
            recent_reb = sum(num(x, "REB") for x in recent) / max(1, len(recent))
            recent_ast = sum(num(x, "AST") for x in recent) / max(1, len(recent))
            e.add_field(name="Recent form",
                        value=(f"Last {len(recent)} games: `{recent_pts:.1f} PPG` · "
                               f"`{recent_reb:.1f} RPG` · `{recent_ast:.1f} APG`"),
                        inline=False)
            return e

        @discord.ui.button(label="Compare", style=discord.ButtonStyle.primary, row=4)
        async def _compare(self, interaction, button):
            values = self.galaxy_view.filtered()
            candidates = [x for x in values if x["name"] != self.item["name"]]
            if not candidates:
                return await interaction.response.send_message("No comparable player in this galaxy.", ephemeral=True)
            v = CompareView(self.galaxy_view, self.item, candidates)
            await interaction.response.edit_message(embed=v.embed(), attachments=v.attachments(), view=v)

        @discord.ui.button(label="⬅ Galaxy", style=discord.ButtonStyle.secondary, row=4)
        async def _back(self, interaction, button):
            await self.galaxy_view.edit(interaction, self.galaxy_view)

    class CompareView(discord.ui.View):
        def __init__(self, galaxy_view, first, candidates):
            super().__init__(timeout=300)
            self.galaxy_view, self.first, self.candidates = galaxy_view, first, candidates
            self._candidate_options = {f"candidate:{i}": x
                                      for i, x in enumerate(candidates[:25])}
            opts = [discord.SelectOption(label=x["name"][:100], value=key)
                    for key, x in self._candidate_options.items()]
            sel = discord.ui.Select(placeholder="Choose comparison player…", options=opts, row=0)
            sel.callback = self._choose
            self.add_item(sel)

        def embed(self, second=None):
            e = discord.Embed(title="⚔ Galaxy comparison",
                              description=f"**{self.first['name']}** vs **{second['name']}**"
                              if second else "Choose a second player.", color=0xFF8A65)
            if second:
                for label, key in (("PTS", "pts"), ("AST", "ast"), ("REB", "reb"),
                                   ("DEF", None), ("TS%", "ts")):
                    a = self.first["stl"] + self.first["blk"] if key is None else self.first[key]
                    b = second["stl"] + second["blk"] if key is None else second[key]
                    e.add_field(name=label, value=f"`{a:.1f}`  vs  `{b:.1f}`", inline=True)
            return e

        def attachments(self):
            selected = getattr(self, "second", None)
            gif = galaxy_gif([self.first] + ([selected] if selected else []),
                             self.galaxy_view.graph, self.galaxy_view.background,
                             self.galaxy_view.metric_key)
            return [discord.File(gif, filename="league_galaxy.gif")] if gif else []

        async def _choose(self, interaction):
            second = self._candidate_options.get(interaction.data["values"][0])
            if second is None:
                return await interaction.response.send_message(
                    "That comparison option is no longer available.", ephemeral=True)
            self.second = second
            await interaction.response.edit_message(embed=self.embed(second),
                                                     attachments=self.attachments(), view=self)

        @discord.ui.button(label="⬅ Back", style=discord.ButtonStyle.secondary, row=4)
        async def _back(self, interaction, button):
            await interaction.response.edit_message(
                embed=self.galaxy_view.embed(), attachments=self.galaxy_view.attachments(),
                view=self.galaxy_view)

    class GalaxyStatsPanelView(discord.ui.View):  # fallback only; normal path subclasses old panel
        def __init__(self, opener_id):
            super().__init__(timeout=300)
            self.opener_id = opener_id

    GalaxyView.DossierView = DossierView
    return GalaxyView