"""Expose legacy application-command callbacks as prefix commands only.

The imported bots have hundreds of handlers written for Interaction. This
gateway preserves their business logic while making their entry points text
commands and removing the application commands from Discord's command tree.
"""

import asyncio
import inspect
import shlex
from types import SimpleNamespace
from typing import get_args, get_origin

import discord
from discord import app_commands
from discord.ext import commands


class _ModalLauncher(discord.ui.View):
    def __init__(self, modal, author_id):
        super().__init__(timeout=300)
        self.modal = modal
        self.author_id = author_id

    @discord.ui.button(label="Open form", style=discord.ButtonStyle.primary)
    async def open_form(self, interaction, button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message(
                "This form belongs to someone else.", ephemeral=True
            )
        await interaction.response.send_modal(self.modal)
        self.stop()


class _Response:
    def __init__(self, interaction):
        self.interaction = interaction
        self.done = False

    def is_done(self):
        return self.done

    async def defer(self, **kwargs):
        self.done = True
        if kwargs.get("thinking"):
            self.interaction._last_message = await self.interaction.context.send(
                "Working on that..."
            )

    async def send_message(self, content=None, *, ephemeral=False, **kwargs):
        self.done = True
        target = self.interaction.context
        # Never expose an ephemeral response (potentially containing account
        # details) to the channel; send it privately instead.
        if ephemeral:
            target = target.author
        try:
            self.interaction._last_message = await target.send(content, **kwargs)
        except discord.Forbidden:
            if ephemeral:
                self.interaction._last_message = await self.interaction.context.send(
                    "I couldn't DM you. Enable DMs from this server and try again."
                )
            else:
                raise
        return self.interaction._last_message

    async def edit_message(self, **kwargs):
        self.done = True
        message = self.interaction._last_message
        if message is None:
            return await self.send_message(**kwargs)
        return await message.edit(**kwargs)

    async def send_modal(self, modal):
        self.done = True
        return await self.interaction.context.send(
            "Click to open the form:",
            view=_ModalLauncher(modal, self.interaction.user.id),
        )


class _PrefixInteraction:
    def __init__(self, context, command):
        self.context = context
        self.user = context.author
        self.guild = context.guild
        self.guild_id = context.guild.id
        self.channel = context.channel
        self.channel_id = context.channel.id
        self.client = context.bot
        self.command = command
        self.message = context.message
        self.created_at = context.message.created_at
        self.permissions = context.channel.permissions_for(context.author)
        self.app_permissions = context.channel.permissions_for(context.guild.me)
        self.data = {}
        self.response = _Response(self)
        self.followup = _Response(self)
        self._last_message = None
        self.namespace = SimpleNamespace()

    async def original_response(self):
        return self._last_message

    async def edit_original_response(self, **kwargs):
        return await self.response.edit_message(**kwargs)

    async def delete_original_response(self):
        if self._last_message is not None:
            await self._last_message.delete()


def _commands_by_path(tree):
    result = {}

    def visit(command, path):
        current = (*path, command.name)
        if isinstance(command, app_commands.Group):
            for child in command.commands:
                visit(child, current)
        elif isinstance(command, app_commands.Command):
            result[current] = command

    for root in tree.get_commands():
        visit(root, ())
    return result


async def _pin_from_dm(ctx):
    try:
        await ctx.message.delete()
    except (discord.Forbidden, discord.HTTPException):
        pass
    try:
        await ctx.author.send("Reply here with your admin PIN within 60 seconds. It will not appear in the server.")
    except discord.Forbidden:
        raise ValueError("Enable DMs so I can request the admin PIN privately.")

    def is_pin(message):
        return (
            message.author.id == ctx.author.id
            and isinstance(message.channel, discord.DMChannel)
            and message.content.strip()
        )

    try:
        message = await ctx.bot.wait_for("message", check=is_pin, timeout=60)
    except asyncio.TimeoutError:
        raise ValueError("PIN request timed out. Run the command again.")
    try:
        await message.delete()
    except (discord.Forbidden, discord.HTTPException):
        pass
    return message.content.strip()


def _convert(raw, annotation, param, ctx):
    if param is not None and param.choices:
        for choice in param.choices:
            if raw.casefold() in (str(choice.name).casefold(), str(choice.value).casefold()):
                return choice
        raise ValueError(f"Choose one of: {', '.join(str(c.value) for c in param.choices)}")

    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is not None and type(None) in args:
        annotation = next((a for a in args if a is not type(None)), str)
        origin = get_origin(annotation)
        args = get_args(annotation)
    if origin is app_commands.Choice or annotation is app_commands.Choice:
        inner = args[0] if args else str
        value = _convert(raw, inner, None, ctx)
        return app_commands.Choice(name=str(value), value=value)
    if annotation is discord.Attachment:
        raise ValueError("Attach the image/file to the command message.")
    if annotation in (discord.Member, discord.User):
        member_id = raw.strip("<@!>")
        member = ctx.guild.get_member(int(member_id)) if member_id.isdecimal() else None
        if member is None:
            raise ValueError(f"Could not find member {raw}. Mention the member.")
        return member
    if annotation in (discord.TextChannel, discord.VoiceChannel, discord.abc.GuildChannel):
        channel_id = raw.strip("<#>")
        channel = ctx.guild.get_channel(int(channel_id)) if channel_id.isdecimal() else None
        if channel is None:
            raise ValueError(f"Could not find channel {raw}. Mention the channel.")
        return channel
    if annotation is discord.Role:
        role_id = raw.strip("<@&>")
        role = ctx.guild.get_role(int(role_id)) if role_id.isdecimal() else None
        if role is None:
            raise ValueError(f"Could not find role {raw}. Mention the role.")
        return role
    if annotation is int:
        return int(raw)
    if annotation is float:
        return float(raw)
    if annotation is bool:
        if raw.casefold() not in ("true", "false", "yes", "no", "1", "0"):
            raise ValueError("Use yes or no for boolean options.")
        return raw.casefold() in ("true", "yes", "1")
    return raw


async def _invoke(ctx, command, raw):
    if ctx.guild is None:
        return await ctx.send("This bot only accepts commands in its home server.")
    try:
        interaction = _PrefixInteraction(ctx, command)
        if not await command._check_can_run(interaction):
            raise ValueError("You are not allowed to run this command.")
        tokens = shlex.split(raw)
        signature = inspect.signature(command.callback)
        parameters = list(signature.parameters.values())[1:]
        options = {p.name: p for p in command.parameters}
        named = {}
        positional = []
        for token in tokens:
            key, sep, value = token.partition("=")
            if sep and key in {p.name for p in parameters}:
                named[key] = value
            else:
                positional.append(token)
        kwargs = {}
        for index, parameter in enumerate(parameters):
            name = parameter.name
            annotation = parameter.annotation
            if name == "pin":
                if name in named or (positional and len(positional) > index):
                    raise ValueError("Do not post a PIN in a server channel. Leave it out; I will DM you.")
                kwargs[name] = await _pin_from_dm(ctx)
                continue
            if name in named:
                raw_value = named.pop(name)
            elif positional:
                # The last free-text parameter consumes the remaining message,
                # unless later required parameters still need their own token.
                remaining = parameters[index + 1:]
                if annotation is str and not remaining:
                    raw_value = " ".join(positional)
                    positional.clear()
                else:
                    raw_value = positional.pop(0)
            elif annotation is discord.Attachment and ctx.message.attachments:
                kwargs[name] = ctx.message.attachments.pop(0)
                continue
            elif parameter.default is not inspect.Parameter.empty:
                continue
            else:
                raise ValueError(f"Missing {name}. Use !commands {command.qualified_name} for usage.")
            kwargs[name] = _convert(raw_value, annotation, options.get(name), ctx)
        if positional or named:
            raise ValueError("Too many arguments, or an unknown option name.")
        await command.callback(interaction, **kwargs)
    except (ValueError, TypeError, app_commands.AppCommandError) as error:
        await ctx.send(f"Command not run: {error}")


async def install_prefix_commands(bot, allowed_guild_ids):
    """Register all legacy handlers as text commands and delete remote slash commands."""
    handlers = _commands_by_path(bot.tree)
    by_root = {}
    for path, command in handlers.items():
        by_root.setdefault(path[0], {})[path[1:]] = command

    async def guild_guard(ctx):
        if ctx.guild is None or ctx.guild.id not in allowed_guild_ids:
            await ctx.send("Access denied: this bot only works in its configured server.")
            return False
        return True

    bot.add_check(guild_guard)

    if bot.get_command("commands") is None:
        @bot.command(name="commands")
        async def prefix_command_list(ctx, *, path=""):
            parts = tuple(path.casefold().split())
            if parts:
                command = handlers.get(parts)
                if command is not None:
                    params = " ".join(
                        f"<{p.name}>" if p.required else f"[{p.name}]"
                        for p in command.parameters if p.name != "pin"
                    )
                    return await ctx.send(
                        f"Usage: !{' '.join(parts)} {params}\n"
                        "Use quotes for multi-word values, attach images to the same message, "
                        "and omit PINs (they are requested in DMs)."
                    )
                if parts[0] in by_root:
                    paths = sorted(
                        " ".join(p) for p in handlers if p[:len(parts)] == parts
                    )
                    return await ctx.send("Commands: " + ", ".join(paths)[:1850])
                return await ctx.send("Unknown command path.")
            await ctx.send(
                "Prefix commands: " + ", ".join(
                    f"!{root if bot.get_command(root) is not None else root + '_app'}"
                    for root in sorted(by_root)
                )[:1800] + "\nUse !commands <name> for subcommands and usage."
            )

    for root, routes in by_root.items():
        # Keep the hand-written prefix handler when a name collides (e.g.
        # !help), and expose the old application handler under !help_app.
        entry_name = root if bot.get_command(root) is None else f"{root}_app"

        prefixes = {p[:i] for p in routes for i in range(1, len(p) + 1)}

        async def dispatch(ctx, *, raw="", _routes=routes, _prefixes=prefixes, _root=root):
            if ctx.guild is None or ctx.guild.id not in allowed_guild_ids:
                return await ctx.send("Access denied: this bot only works in its configured server.")
            try:
                tokens = shlex.split(raw)
            except ValueError as error:
                return await ctx.send(f"Invalid quoting: {error}")
            path = ()
            while tokens and (*path, tokens[0].casefold()) in _prefixes:
                path = (*path, tokens.pop(0).casefold())
            if not path and () not in _routes:
                children = sorted(" ".join(p) for p in routes)
                return await ctx.send(
                    f"Usage: !{_root} <subcommand> ...\nAvailable: {', '.join(children)[:1800]}"
                )
            command = routes.get(path)
            if command is None:
                return await ctx.send(f"Unknown subcommand. Try !{_root} without arguments.")
            await _invoke(ctx, command, shlex.join(tokens))

        # discord.py's parser must see only ctx and a keyword-only raw string.
        dispatch.__signature__ = inspect.Signature([
            inspect.Parameter("ctx", inspect.Parameter.POSITIONAL_OR_KEYWORD),
            inspect.Parameter("raw", inspect.Parameter.KEYWORD_ONLY, default="", annotation=str),
        ])
        bot.add_command(commands.Command(dispatch, name=entry_name))

    bot.tree.clear_commands(guild=None)
    # Remove stale global commands from the previous deployment, too.
    await bot.tree.sync()
    for guild_id in allowed_guild_ids:
        guild = discord.Object(id=guild_id)
        bot.tree.clear_commands(guild=guild)
        await bot.tree.sync(guild=guild)
    print(f"[Prefix] Enabled {len(handlers)} prefix handlers; removed Discord slash commands.")