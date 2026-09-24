#!/usr/bin/env python3
"""Launch the legacy handlers as prefix-only Discord commands."""

import importlib.util
import os

from discord.ext import commands

from prefix_gateway import install_prefix_commands


def main():
    source = os.environ.get("QCL_SOURCE") or os.path.join(
        os.path.dirname(__file__), "QCL2K.py"
    )
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
    original_setup_hook = bot.setup_hook

    async def prefix_setup_hook():
        # Clear previous Discord registrations before legacy startup begins.
        # Its later sync calls see an empty tree, never the imported handlers.
        await install_prefix_commands(bot, module.ALLOWED_GUILD_IDS)
        await original_setup_hook()

    bot.setup_hook = prefix_setup_hook
    original_run(bot, module.TOKEN)


if __name__ == "__main__":
    main()