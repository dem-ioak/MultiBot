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
    logger = logging.getLogger(name)
    logger.setLevel(level)
    handler = logging.FileHandler(filename)
    handler.setFormatter(formatter)
    logger.addHandler(handler)

configs = [
    {"name" : "events", "level" : logging.INFO, "filename" : "logs/events.log"},
    {"name" : "errors", "level" : logging.DEBUG, "filename" : "logs/errors.log"},
    {"name" : "data", "level" : logging.INFO, "filename" : "logs/data.log"}
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
        await self.load_cogs()

    async def load_cogs(self):
        for filename in os.listdir('bot/cogs'):
            if filename.endswith(".py") and "__init__" not in filename:
                try:
                    await self.load_extension(f"cogs.{filename[:-3]}")
                    event_logger.info(f"Successfully loaded cog {filename}")
                except Exception as e:
                    error_logger.error(f"Failed to load cog {filename} with Exception {e}", exc_info = True)
                    continue
    
    async def on_error(self, event, *args, **kwargs):
        print(traceback.format_exc())

client = MyBot()
async def main():
    async with client:
        await client.start(CLIENT_KEY)

asyncio.run(main())



