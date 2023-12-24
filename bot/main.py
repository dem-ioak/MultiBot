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
        for filename in os.listdir('bot/cogs'):
            if filename.endswith(".py") and "__init__" not in filename:
                try:
                    print(filename)
                    await self.load_extension(f"cogs.{filename[:-3]}")
                except Exception as e:
                    print(e)
                    continue

client = MyBot()
async def main():
    async with client:
        await client.start(CLIENT_KEY)

asyncio.run(main())



