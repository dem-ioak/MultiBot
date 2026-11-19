import discord
from discord.ext import commands, tasks
from discord import Embed, Color, app_commands
import random
import re
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
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


# Board of every number in the daily range, one cell each
BOARD_COLUMNS = 40
BOARD_CELL, BOARD_GAP, BOARD_MARGIN, BOARD_LABEL = 22, 2, 16, 44
BOARD_BACKGROUND = (30, 31, 34)
BOARD_EMPTY = (49, 51, 56)
# Rolled once, twice, three or more times
BOARD_HEAT = [(35, 134, 54), (87, 242, 135), (254, 231, 92)]
BOARD_YOURS = (255, 255, 255)
SOURCE_NAMES = {"rng": "Yeat Bot", "beast": "Beast Bot"}


def pretty_day(day):
    """2026-10-12 as Oct 12, 2026"""
    date = datetime.strptime(day, "%Y-%m-%d")
    return f"{date:%b} {date.day}, {date.year}"


def board_font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def draw_board(counts, yours):
    """Picture of the daily range, colored by how often each number has been rolled"""
    low, high = DAILY_RANGE
    total = high - low + 1
    rows = -(-total // BOARD_COLUMNS)
    step = BOARD_CELL + BOARD_GAP
    width = BOARD_MARGIN * 2 + BOARD_LABEL + BOARD_COLUMNS * step - BOARD_GAP
    legend_height = 40
    height = BOARD_MARGIN * 2 + rows * step - BOARD_GAP + legend_height
    image = Image.new("RGB", (width, height), BOARD_BACKGROUND)
    draw = ImageDraw.Draw(image)
    label_font, legend_font = board_font(13), board_font(15)

    for row in range(rows):
        y = BOARD_MARGIN + row * step
        draw.text((BOARD_MARGIN + BOARD_LABEL - 8, y + BOARD_CELL / 2), str(low + row * BOARD_COLUMNS),
                  fill=(148, 155, 164), font=label_font, anchor="rm")
        for column in range(BOARD_COLUMNS):
            number = low + row * BOARD_COLUMNS + column
            if number > high:
                break
            x = BOARD_MARGIN + BOARD_LABEL + column * step
            hits = counts.get(number, 0)
            color = BOARD_HEAT[min(hits, len(BOARD_HEAT)) - 1] if hits else BOARD_EMPTY
            draw.rectangle((x, y, x + BOARD_CELL - 1, y + BOARD_CELL - 1), fill=color)
            if number in yours:
                draw.rectangle((x, y, x + BOARD_CELL - 1, y + BOARD_CELL - 1), outline=BOARD_YOURS, width=2)
            if number == 67:
                draw.text((x + BOARD_CELL / 2, y + BOARD_CELL / 2), "67",
                          fill=(0, 0, 0) if hits else (200, 200, 200), font=board_font(10), anchor="mm")

    # Legend
    y = height - BOARD_MARGIN - 18
    x = BOARD_MARGIN + BOARD_LABEL
    for color, label in [(BOARD_EMPTY, "Never"), (BOARD_HEAT[0], "1x"), (BOARD_HEAT[1], "2x"),
                         (BOARD_HEAT[2], "3x+"), (None, "Your rolls")]:
        if color is None:
            draw.rectangle((x, y, x + 17, y + 17), outline=BOARD_YOURS, width=2)
        else:
            draw.rectangle((x, y, x + 17, y + 17), fill=color)
        draw.text((x + 25, y + 9), label, fill=(219, 222, 225), font=legend_font, anchor="lm")
        x += 25 + draw.textlength(label, font=legend_font) + 28

    out = BytesIO()
    image.save(out, format="PNG")
    out.seek(0)
    return out


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

    @app_commands.command(description=f"See which numbers {DAILY_RANGE[0]}-{DAILY_RANGE[1]} have been rolled in this server")
    @app_commands.describe(number="Look up who has rolled a specific number")
    @app_commands.guild_only()
    async def rolls(self, interaction: discord.Interaction,
                    number: app_commands.Range[int, DAILY_RANGE[0], DAILY_RANGE[1]] = None):
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /rolls [number={number}]")
        await interaction.response.defer(ephemeral=True)

        spins = await run_db(lambda: list(RNG_SPINS.find({"_id.guild_id": interaction.guild.id})))
        low, high = DAILY_RANGE
        counts = {}
        for spin in spins:
            counts[spin["number"]] = counts.get(spin["number"], 0) + 1
        by_source = {}
        for spin in spins:
            source = SOURCE_NAMES.get(spin.get("source", "rng"), "Yeat Bot")
            by_source[source] = by_source.get(source, 0) + 1
        yours = {spin["number"] for spin in spins if spin["_id"]["user_id"] == interaction.user.id}

        embed = Embed(title=f"Daily rolls ({low}-{high})", color=Color.green())
        embed.set_author(name=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)

        if number is not None:
            hits = sorted((spin for spin in spins if spin["number"] == number), key=lambda spin: spin["_id"]["day"])
            if hits:
                lines = [
                    f"<@{spin['_id']['user_id']}> on {pretty_day(spin['_id']['day'])}"
                    f" ({SOURCE_NAMES.get(spin.get('source', 'rng'), 'Yeat Bot')})"
                    for spin in hits
                ]
                embed.add_field(name=f"{number} has been rolled {len(hits)} time{'s' if len(hits) != 1 else ''}",
                                value="\n".join(lines)[:1024], inline=False)
            else:
                embed.add_field(name=f"{number} has never been rolled", value="Still up for grabs.", inline=False)

        if not spins:
            embed.description = "Nobody has rolled the daily range here yet."
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        reached = len(counts)
        first_day = min(spin["_id"]["day"] for spin in spins)
        top = max(counts.values())
        most = sorted(n for n, hits in counts.items() if hits == top)
        sixty_seven = counts.get(67, 0)
        embed.description = (
            f"**{reached}** of {high - low + 1} numbers reached ({reached / (high - low + 1):.1%})\n"
            f"**{len(spins)}** rolls since {pretty_day(first_day)}: "
            + " · ".join(f"{name} {count}" for name, count in sorted(by_source.items()))
        )
        embed.add_field(name=f"Most rolled ({top}x)", value=", ".join(map(str, most[:15])) + (" ..." if len(most) > 15 else ""))
        embed.add_field(name="67", value=f"Hit {sixty_seven} time{'s' if sixty_seven != 1 else ''}" if sixty_seven else "Never hit")
        embed.add_field(name="Your rolls", value=f"{sum(1 for s in spins if s['_id']['user_id'] == interaction.user.id)} rolls, {len(yours)} numbers")
        embed.set_image(url="attachment://rolls.png")
        board = await run_db(draw_board, counts, yours)
        await interaction.followup.send(embed=embed, file=discord.File(board, filename="rolls.png"), ephemeral=True)

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