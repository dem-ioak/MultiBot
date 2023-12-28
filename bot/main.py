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

formatter = logging.Formatter("[%(levelname)s] %(asctime)s - %(message)s")
def configure_logger(name, level, filename):
    """Configure custom logger with given attributes"""

    LOG_DIR = "bot/logs/"

    logger = logging.getLogger(name)
    logger.setLevel(level)
    handler = logging.FileHandler(LOG_DIR + filename)
    handler.setFormatter(formatter)
    logger.addHandler(handler)

configs = [
    {"name" : "events", "level" : logging.INFO, "filename" : "events.log"},
    {"name" : "errors", "level" : logging.DEBUG, "filename" : "errors.log"},
    {"name" : "data", "level" : logging.INFO, "filename" : "data.log"}
]

for config in configs:
    configure_logger(**config)

event_logger = logging.getLogger("events")
error_logger = logging.getLogger("errors")

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=".",
            intents = discord.Intents.all(),
            application_id = APPLICATION_ID
        )
    
    async def setup_hook(self):
        cog_count, cogs_loaded = await self.load_cogs()
        await self.tree.sync()
        event_logger.info(f"Bot is Online: {cogs_loaded}/{cog_count} Cogs Successfully Loaded")

    async def load_cogs(self):
        count = 0
        success = 0
        for filename in os.listdir('bot/cogs'):
            if filename.endswith(".py") and "__init__" not in filename:
                count += 1
                try:
                    await self.load_extension(f"cogs.{filename[:-3]}")
                    success += 1
                except Exception as e:
                    error_logger.error(f"Failed to load cog {filename} with Exception {e}", exc_info = True)
                    
                    continue
        return count, success

    async def on_error(self, *args, **kwargs):
        error_logger.error("Error Occured", exc_info=True)
    
client = MyBot()

@client.tree.error
async def on_app_command_error(interaction : discord.Interaction, error : AppCommandError):
    if isinstance(error, app_commands.errors.CommandOnCooldown):
        await interaction.response.send_message(embed = Embed(
            description= "This command is currently on cooldown, as it can only be used once every 2 minutes.",
            color = Color.red()
        ), ephemeral = True)
    else:
        error_logger.error(f"Error while using {interaction.command.name}", exc_info=True)

async def main():
    async with client:
        await client.start(CLIENT_KEY)

asyncio.run(main())



