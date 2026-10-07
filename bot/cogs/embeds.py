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
# sites that serve the same post in a form Discord will embed with a playable video. The bot
# reacts to such a link, and posts the link pointed at them instead once somebody clicks that
# reaction. They come and go, hence being easy to swap out
TWITTER_EMBED_HOST = os.getenv("TWITTER_EMBED_HOST", "fxtwitter.com")
INSTAGRAM_EMBED_HOST = os.getenv("INSTAGRAM_EMBED_HOST", "kkinstagram.com")
TWITTER_API = "https://api.fxtwitter.com/i/status/{}"
API_TIMEOUT = 5
MAX_LINKS = 4  # Per message, so a wall of links does not become a wall of embeds
EMBED_EMOJI = "▶️"

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


def spoilered(text, original):
    """Keep whatever was posted behind a spoiler hidden"""
    return f"||{text}||" if "||" in original else text


class Embeds(commands.Cog):
    def __init__(self, client):
        self.client = client
        # Messages being embedded right now, so two people clicking at once only embeds it once
        self.embedding = set()

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

        # Only offered when there is something to embed, a tweet without a video gets no reaction
        if await find_video_links(message.content):
            try:
                await message.add_reaction(EMBED_EMOJI)
            except discord.HTTPException:
                pass  # Deleted already, or reactions are not allowed here

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        """Embed a link's video once somebody clicks the reaction the bot left on it.

        The bot's own reaction is what marks a message as waiting to be embedded, and it is taken
        off once that is done. Nothing is stored, so this keeps working after a restart"""
        if str(payload.emoji) != EMBED_EMOJI or payload.guild_id is None:
            return
        if payload.user_id == self.client.user.id or payload.message_id in self.embedding:
            return

        channel = self.client.get_channel(payload.channel_id)
        if channel is None:
            return

        self.embedding.add(payload.message_id)
        try:
            try:
                message = await channel.fetch_message(payload.message_id)
            except discord.HTTPException:
                return

            offered = any(str(reaction.emoji) == EMBED_EMOJI and reaction.me for reaction in message.reactions)
            if not offered:
                return

            links = await find_video_links(message.content)
            if links:
                log = get_logger(__name__, server=message.guild.name, user=message.author.name)
                log.info(f"Embedding the video of {len(links)} links")
                await message.reply(spoilered("\n".join(links), message.content), mention_author=False)

            # Take the reaction off, the whole of it if allowed, so that it cannot be clicked again.
            # Then hide the original's own embed, which would otherwise sit there without the video.
            # Both need Manage Messages, and the video is embedded regardless
            try:
                await message.clear_reaction(EMBED_EMOJI)
            except discord.HTTPException:
                await message.remove_reaction(EMBED_EMOJI, self.client.user)
            if links:
                try:
                    await message.edit(suppress=True)
                except discord.HTTPException:
                    pass
        finally:
            self.embedding.discard(payload.message_id)

    @app_commands.command(description="Turn the embed video reaction on Twitter / Instagram links on or off")
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
                description=f"Twitter / Instagram links will {state} get a {EMBED_EMOJI} reaction that embeds their video.",
                color=Color.green(),
            ),
            ephemeral=True,
        )


async def setup(client):
    await client.add_cog(Embeds(client))
