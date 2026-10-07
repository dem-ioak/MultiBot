import discord
from discord import Embed, Color, app_commands
from discord.ext import commands
import aiohttp
import asyncio
import os
import re

from util.constants import SERVERS
from util.helper_functions import run_db
from util.log_manager import get_logger

# Discord does not play the video of a Twitter / Instagram link itself. These are third party
# sites that serve the same post in a form Discord will embed with a playable video, so the bot
# replies with the link pointed at them instead. They come and go, hence being easy to swap out
TWITTER_EMBED_HOST = os.getenv("TWITTER_EMBED_HOST", "fxtwitter.com")
INSTAGRAM_EMBED_HOST = os.getenv("INSTAGRAM_EMBED_HOST", "kkinstagram.com")
TWITTER_API = "https://api.fxtwitter.com/i/status/{}"
API_TIMEOUT = 5
MAX_LINKS = 4  # Per message, so a wall of links does not become a wall of replies

# A link wrapped in <> has had its embed turned off on purpose, so those are left alone
TWITTER_LINK = re.compile(
    r"(?<!<)https?://(?:www\.|mobile\.)?(?:twitter|x)\.com/(\w+)/status/(\d+)", re.IGNORECASE
)
INSTAGRAM_LINK = re.compile(
    r"(?<!<)https?://(?:www\.)?instagram\.com/((?:[\w.]+/)?(?:p|reel|reels|tv)/[\w-]+)", re.IGNORECASE
)


async def tweet_has_video(session, tweet_id):
    """Check whether a tweet has a video (or gif) on it. False if that cannot be found out"""
    try:
        async with session.get(TWITTER_API.format(tweet_id)) as response:
            data = await response.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return False

    media = (data.get("tweet") or {}).get("media") or {}
    return len(media.get("videos") or []) > 0


async def find_video_links(content):
    """Get an embeddable version of every Twitter / Instagram link in a message that needs one"""
    links = []
    tweets = TWITTER_LINK.findall(content)
    if tweets:
        timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for user, tweet_id in tweets[:MAX_LINKS]:
                # Tweets that are only text or pictures embed fine as they are
                if await tweet_has_video(session, tweet_id):
                    links.append(f"https://{TWITTER_EMBED_HOST}/{user}/status/{tweet_id}")

    for path in INSTAGRAM_LINK.findall(content):
        links.append(f"https://{INSTAGRAM_EMBED_HOST}/{path}/")

    return list(dict.fromkeys(links))[:MAX_LINKS]


class Embeds(commands.Cog):
    def __init__(self, client):
        self.client = client

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.guild is None:
            return
        if not (TWITTER_LINK.search(message.content) or INSTAGRAM_LINK.search(message.content)):
            return

        # On unless a server has turned it off
        server_data = await run_db(SERVERS.find_one, {"_id": message.guild.id}, {"fix_embeds": 1})
        if server_data and server_data.get("fix_embeds") is False:
            return

        links = await find_video_links(message.content)
        if not links:
            return

        log = get_logger(__name__, server=message.guild.name, user=message.author.name)
        log.info(f"Embedding the video of {len(links)} links")
        text = "\n".join(links)
        if "||" in message.content:
            text = f"||{text}||"  # Keep whatever they posted behind a spoiler hidden
        await message.reply(text, mention_author=False)

        # Hide the original's own embed, which would otherwise sit above ours without the video
        try:
            await message.edit(suppress=True)
        except discord.HTTPException:
            pass  # Needs Manage Messages, the reply is still there without it

    @app_commands.command(description="Turn replying to Twitter / Instagram links with a playable video on or off")
    @app_commands.describe(enabled="Whether the bot should do this in this server")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def embed_fix(self, interaction: discord.Interaction, enabled: bool):
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /embed_fix [enabled={enabled}]")
        SERVERS.update_one({"_id": interaction.guild.id}, {"$set": {"fix_embeds": enabled}})
        state = "now" if enabled else "no longer"
        await interaction.response.send_message(
            embed=Embed(
                description=f"Twitter / Instagram links will {state} be replied to with a playable video.",
                color=Color.green(),
            ),
            ephemeral=True,
        )


async def setup(client):
    await client.add_cog(Embeds(client))
