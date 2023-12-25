import discord
import asyncio
import os

from discord.ext import commands
from dotenv import load_dotenv
from discord.app_commands import AppCommandError
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
        cogs_failed = await self.load_cogs()
        await self.tree.sync()
        event_logger.info(f"Bot is Online: {cogs_failed} Cog Failures")

    async def load_cogs(self):
        count = 0
        for filename in os.listdir('bot/cogs'):
            if filename.endswith(".py") and "__init__" not in filename:
                try:
                    await self.load_extension(f"cogs.{filename[:-3]}")
                except Exception as e:
                    error_logger.error(f"Failed to load cog {filename} with Exception {e}", exc_info = True)
                    count += 1
                    continue
        return count

    async def on_error(self, *args, **kwargs):
        error_logger.error("Error Occured", exc_info=True)
    
client = MyBot()

@client.tree.error
async def on_app_command_error(interaction : discord.Interaction, error : AppCommandError):
    error_logger.error(f"Error while using {interaction.command.name}", exc_info=True)

async def main():
    async with client:
        await client.start(CLIENT_KEY)

asyncio.run(main())



