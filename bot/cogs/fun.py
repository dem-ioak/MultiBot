import discord
from discord.ext import commands, tasks
from discord import Embed, Color, app_commands
import random
import re
from time import sleep
from datetime import datetime
from pymongo.errors import DuplicateKeyError
from util.constants import INTEGER_CONVERSION_FAIL, USERS, RNG_SPINS, EST_TIME
from util.helper_functions import run_db
from util.log_manager import get_logger

# The one range that each user only gets a single roll of per day (the day turns over at midnight Eastern)
DAILY_RANGE = (1, 1000)

# What a second roll in the same day gets instead of a number
RESPIN_GIF = "https://klipy.com/gifs/outta-pocket-red-card"

# Beast Bot's /67 rolls the same range, so a roll there uses up the day's roll here too
BEAST_BOT_ID = 956617402781671465
BEAST_NUMBER = re.compile(r"The number (-?\d+) was chosen")
BEAST_RANGE = re.compile(r"Range provided\s*:\s*(-?\d+)\s*-\s*(-?\d+)")


def claim_daily_spin(guild_id, user_id, choice, now, source="rng"):
    """Record a user's roll of the daily range. Returns None if it counts, or today's earlier roll if they already had one"""
    spin_id = {"guild_id": guild_id, "user_id": user_id, "day": now.strftime("%Y-%m-%d")}
    try:
        # The day is part of the _id, so two rolls at the same moment cannot both get in
        RNG_SPINS.insert_one({"_id": spin_id, "number": choice, "rolled_at": datetime.utcnow(), "source": source})
        return None
    except DuplicateKeyError:
        return RNG_SPINS.find_one({"_id": spin_id})


class Fun(commands.Cog):
    def __init__(self, client):
        self.client = client

    @app_commands.command(description="Flip a coin.")
    async def flip(self, interaction : discord.Interaction):
        
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /flip")
        
        choice = random.choice(["Heads", "Tails"])
        embed = Embed(
            color = Color.green(),
            description = f"{choice} wins!"
        )
        embed.set_author(
            name = interaction.user.name, 
            icon_url=interaction.user.avatar.url)
        await interaction.response.send_message(embed=embed)
    
    @app_commands.command(description="Make a randomized decision.")
    async def decide(self, interaction : discord.Interaction, args : str):
        
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /decide [args={args}]")
        
        first, second = Embed(), Embed()
        first.color = second.color = Color.dark_orange()
        options = args.split() if "-" not in args else args.split("-")
        choice = random.choice(options)
        
        emoji = discord.utils.get(self.client.emojis, name="loading")
        first.description = "{} Loading Randomized Choice...".format(emoji)
        second.description = "**{}** was chosen!".format(choice)

        name = interaction.user.name
        icon_url = interaction.user.avatar.url
        
        first.set_author(
            name = name, 
            icon_url = icon_url)
        
        second.set_author(
            name = name,
            icon_url = icon_url
        )

        await interaction.response.send_message(embed=first)
        sleep(3)
        message = await interaction.original_response()
        await message.edit(embed=second)
    
    @app_commands.command(description="Chose a random number between x-y.")
    async def rng(self, interaction : discord.Interaction, first : str, last : str):
        
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /rng [first={first}, last={last}]")
        try:
            first = int(first)
            last = int(last)
            assert first > float("-inf")
            assert last < float("inf")
        except ValueError:
            await interaction.response.send_message(embed = INTEGER_CONVERSION_FAIL)
            return
        except AssertionError:
            await interaction.response.send_message(
                embed = Embed(
                    description = "The bounds you set exceed the minimum or maximum values I can provide. Please chose a smaller range.",
                    color = Color.red()
                )
            )
            return
        
        choice = random.randint(first, last)
        if (first, last) == DAILY_RANGE:
            earlier = await run_db(
                claim_daily_spin, interaction.guild.id, interaction.user.id, choice, datetime.now(EST_TIME)
            )
            if earlier is not None:
                log.info("RNG_DAILY_LIMIT: Already rolled today")
                # Out in the open, so everyone sees the attempt at a second roll
                await interaction.response.send_message(RESPIN_GIF)
                return

        embed = Embed(
                description = f"The number **{choice}** was chosen!",
                color = Color.green() 
            )
        embed.set_author(name = interaction.user.name, icon_url= interaction.user.avatar.url)
        embed.set_footer(text = f"Range provided : {first} - {last}")
        await interaction.response.send_message(
            embed = embed
        )
    
    @commands.Cog.listener()
    async def on_message(self, message):
        """Count a roll of the daily range on Beast Bot as the user's roll for the day"""
        if message.author.id != BEAST_BOT_ID or message.guild is None or not message.embeds:
            return
        embed = message.embeds[0]
        number = BEAST_NUMBER.search(embed.description or "")
        bounds = BEAST_RANGE.search(embed.footer.text or "")
        # Whoever ran the command, which is not the author of the message (that is the bot)
        command = getattr(message, "interaction_metadata", None) or message.interaction
        if not number or not bounds or command is None:
            return
        if (int(bounds.group(1)), int(bounds.group(2))) != DAILY_RANGE:
            return

        log = get_logger(__name__, server=message.guild.name, user=command.user.name)
        earlier = await run_db(
            claim_daily_spin, message.guild.id, command.user.id, int(number.group(1)), datetime.now(EST_TIME), "beast"
        )
        # A second roll over there is only logged, calling it out is left to Beast Bot
        log.info(f"RNG_DAILY_BEAST: {'Recorded' if earlier is None else 'Already rolled today'}")

    @app_commands.command(description = "Display this server's level leaderboard")
    async def leaderboard(self, interaction : discord.Interaction):
        
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /leaderboard")
        server_users = USERS.find({"_id.guild_id" : interaction.guild.id})
        entries = []
        for user in server_users:
            user_id = user["_id"]["user_id"]
            user_obj = self.client.get_user(user_id)
            
            # User is no longer in the server
            if user_obj is None:
                continue
            
            username = user_obj.name
            level = user["level"]
            xp = user["xp"]
            entries.append((username, level, xp))
        
        entries.sort(key = lambda x : x[2], reverse = True)
        description = str()
        embed = Embed(title = f"{interaction.guild.name} Levels Leaderboard", color = Color.gold())
        embed.set_footer(text = "Users are given 5-15 xp per message, with a cooldown of 1 message per minute")
        for i, entry in enumerate(entries):
            username, level, xp = entry
            description += f"**{i + 1}. {username}** - Level {level} ({xp} XP)\n"
        embed.description = description
        
        await interaction.response.send_message(embed = embed, ephemeral = True)
            
        
        

async def setup(client):
    await client.add_cog(Fun(client))