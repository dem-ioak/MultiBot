import discord
import asyncio
import os

from discord.ext import commands
from dotenv import load_dotenv
from discord.app_commands import AppCommandError

load_dotenv()
CLIENT_KEY = os.getenv("CLIENT_KEY")
APPLICATION_ID = os.getenv("APPLICATION_ID")


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
        for filename in os.listdir('./cogs'):
            if filename.endswith(".py"):
                try:
                    await self.load_extension(filename)
                except Exception as e:
                    continue
    
    async def load_extension(self, filename):
        await self.client.load_extension(f"cogs.{filename[:-3]}")



