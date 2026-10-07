import discord
from discord.ext import commands
from discord import Embed, Color, app_commands
from discord.app_commands import Choice
from urllib.parse import urlparse

from util.constants import SERVERS
from util.log_manager import get_logger

MAX_VIBE_GIFS = 200
CHOICE_LIMIT = 100  # Longest name / value Discord accepts for an autocomplete suggestion
GIF_HOSTS = ("tenor.com", "giphy.com", "klipy.com")
# Links to files uploaded to Discord stop working after about a day
EXPIRING_HOSTS = ("discordapp.com", "discordapp.net", "discord.com")

INVALID_GIF = (
    "That does not look like a gif. Send a link from Tenor or Giphy "
    "(in the gif picker: right click a gif, then *Copy Link*), or a direct link ending in `.gif`."
)
EXPIRING_GIF = "Links to files uploaded to Discord expire, so that gif would stop working. Use a Tenor or Giphy link instead."
DUPLICATE_GIF = "That gif is already part of this server's good vibes."
TOO_MANY_GIFS = f"This server already has the maximum of {MAX_VIBE_GIFS} good vibes gifs. An admin can make room with `/vibe_remove`."
GIF_NOT_FOUND = "That gif is not part of this server's good vibes. Pick one of the suggestions."


def error_embed(description):
    return Embed(description=description, color=Color.red())


def is_host(host, domains):
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def check_gif(url):
    """Get the reason a link cannot be used as a good vibes gif, None if it is fine"""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or not host or " " in url:
        return INVALID_GIF
    if is_host(host, EXPIRING_HOSTS):
        return EXPIRING_GIF
    if not is_host(host, GIF_HOSTS) and not parsed.path.lower().endswith(".gif"):
        return INVALID_GIF
    return None


def get_vibe_gifs(guild_id):
    server_data = SERVERS.find_one({"_id": guild_id}, {"vibe_gifs": 1})
    return (server_data or {}).get("vibe_gifs") or []


class Vibes(commands.Cog):
    def __init__(self, client):
        self.client = client

    @app_commands.command(description="Add a gif to this server's daily good vibes")
    @app_commands.describe(gif="A link to the gif, from Tenor or Giphy")
    @app_commands.guild_only()
    async def vibe_add(self, interaction: discord.Interaction, gif: str):
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /vibe_add [gif={gif}]")
        gif = gif.strip()
        problem = check_gif(gif)
        if problem:
            await interaction.response.send_message(embed=error_embed(problem), ephemeral=True)
            return

        # One step that only goes through if the gif is new and there is still room for it
        result = SERVERS.update_one(
            {
                "_id": interaction.guild.id,
                "vibe_gifs": {"$ne": gif},
                f"vibe_gifs.{MAX_VIBE_GIFS - 1}": {"$exists": False},
            },
            {"$push": {"vibe_gifs": gif}},
        )
        gifs = get_vibe_gifs(interaction.guild.id)
        if result.modified_count == 0:
            problem = DUPLICATE_GIF if gif in gifs else TOO_MANY_GIFS
            await interaction.response.send_message(embed=error_embed(problem), ephemeral=True)
            return

        log.info(f"Added a good vibes gif, this server now has {len(gifs)}")
        # Sent as plain text so that Discord shows the gif itself
        await interaction.response.send_message(
            f"✅ {interaction.user.mention} added a gif to this server's good vibes! There are now **{len(gifs)}**.\n{gif}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(description="Remove a gif from this server's daily good vibes")
    @app_commands.describe(gif="Start typing to search this server's gifs")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def vibe_remove(self, interaction: discord.Interaction, gif: str):
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /vibe_remove [gif={gif}]")
        gif = gif.strip()
        gifs = get_vibe_gifs(interaction.guild.id)

        # A suggestion can only carry the first part of a long link, so fall back to matching on that
        matches = [url for url in gifs if url == gif] or [url for url in gifs if gif and url.startswith(gif)]
        if not matches:
            await interaction.response.send_message(embed=error_embed(GIF_NOT_FOUND), ephemeral=True)
            return

        removed = matches[0]
        SERVERS.update_one({"_id": interaction.guild.id}, {"$pull": {"vibe_gifs": removed}})
        log.info(f"Removed a good vibes gif: {removed}")
        await interaction.response.send_message(
            f"✅ Removed from this server's good vibes. There are now **{len(gifs) - 1}**.\n<{removed}>",
            ephemeral=True,
        )

    @vibe_remove.autocomplete("gif")
    async def vibe_remove_autocomplete(self, interaction: discord.Interaction, current: str):
        query = current.strip().lower()
        choices = []
        for url in get_vibe_gifs(interaction.guild.id):
            if query in url.lower():
                # Drop the part every link shares, to leave room for the part that tells them apart
                name = url.split("://", 1)[-1].replace("tenor.com/view/", "")
                choices.append(Choice(name=name[:CHOICE_LIMIT], value=url[:CHOICE_LIMIT]))
        return choices[:25]


async def setup(client):
    await client.add_cog(Vibes(client))
