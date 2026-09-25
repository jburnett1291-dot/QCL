#!/usr/bin/env python3
"""Launch the legacy handlers as prefix-only Discord commands."""

import importlib.util
import os
import sys
import types

from discord.ext import commands

from prefix_gateway import install_prefix_commands


def _install_federal_reserve_stub():
    """Keep QCL2K import-compatible without loading the separate FR bot."""
    stub = types.ModuleType("FEDERAL_RESERVE_BOT")

    def install_into(_bot):
        return None

    async def start_services(_bot):
        return None

    stub.install_into = install_into
    stub.start_services = start_services
    sys.modules["FEDERAL_RESERVE_BOT"] = stub


def main():
    source = os.environ.get("QCL_SOURCE") or os.path.join(
        os.path.dirname(__file__), "QCL2K.py"
    )
    _install_federal_reserve_stub()
    spec = importlib.util.spec_from_file_location("QCL2K", source)
    module = importlib.util.module_from_spec(spec)

    # The original module starts the bot at import time. Capture that call
    # instead so we can set up prefix commands before connecting to Discord.
    original_run = commands.Bot.run
    commands.Bot.run = lambda self, token, **kwargs: None
    try:
        spec.loader.exec_module(module)
    finally:
        commands.Bot.run = original_run

    bot = module.bot
    from qcl_admin import install_qcl_admin

    install_qcl_admin(bot, module)
    # The legacy installer writes a standalone launcher that bypasses this
    # prefix-only layer. Keep the current safe supervisor as the only launcher.
    if bot.get_command("install") is not None:
        bot.remove_command("install")
    original_setup_hook = bot.setup_hook

    async def prefix_setup_hook():
        # Clear previous Discord registrations before legacy startup begins.
        # Its later sync calls see an empty tree, never the imported handlers.
        await install_prefix_commands(bot, module.ALLOWED_GUILD_IDS, module=module)
        await original_setup_hook()

    bot.setup_hook = prefix_setup_hook
    original_run(bot, module.TOKEN)


if __name__ == "__main__":
    main()