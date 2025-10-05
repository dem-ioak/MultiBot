import discord
import asyncio
import os

from discord.ext import commands
from dotenv import load_dotenv
from discord.app_commands import AppCommandError
from discord import Embed, Color, app_commands
import traceback
import logging

load_dotenv()
CLIENT_KEY = os.getenv("CLIENT_KEY")
APPLICATION_ID = os.getenv("APPLICATION_ID")

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=".",
            intents=discord.Intents.all(),
            application_id=APPLICATION_ID,
        )

    async def setup_hook(self):
        cog_count, cogs_loaded = await self.load_cogs()
        await self.tree.sync()

    async def load_cogs(self):
        count = 0
        success = 0
        for filename in os.listdir("bot/cogs"):
            if filename.endswith(".py") and "__init__" not in filename:
                count += 1
                try:
                    await self.load_extension(f"cogs.{filename[:-3]}")
                    success += 1
                except Exception as e:
                    pass

                    continue
        return count, success

    async def on_error(self, *args, **kwargs):
        pass


client = MyBot()


@client.tree.error
async def on_app_command_error(
    interaction: discord.Interaction, error: AppCommandError
):
    if isinstance(error, app_commands.errors.CommandOnCooldown):
        await interaction.response.send_message(
            embed=Embed(
                description="This command is currently on cooldown, as it can only be used once every 2 minutes.",
                color=Color.red(),
            ),
            ephemeral=True,
        )
    else:
        pass


async def main():
    async with client:
        await client.start(CLIENT_KEY)


asyncio.run(main())
