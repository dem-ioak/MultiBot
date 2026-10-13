import discord
from discord import Embed, Color, app_commands
from discord.app_commands import Range
from discord.ext import commands
from typing import Union
import re

from util.constants import MY_USER_ID, FORBIDDEN_COMMAND
from util.log_manager import get_logger

# The part of a message link that matters: .../channels/<guild>/<channel>/<message>
MESSAGE_LINK = re.compile(r"channels/(?:\d+|@me)/(\d+)/(\d+)")
EMOJI_NAME = re.compile(r":(\w+):")

Sendable = Union[discord.TextChannel, discord.Thread, discord.VoiceChannel, discord.StageChannel]


def failure(description):
    return Embed(description=description, color=Color.red())


def success(description):
    return Embed(description=description, color=Color.green())


class Relay(commands.Cog):
    """Lets the owner speak and react as the bot"""

    def __init__(self, client):
        self.client = client

    async def allowed(self, interaction, command):
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: {command}")
        if interaction.user.id != MY_USER_ID:
            await interaction.response.send_message(embed=FORBIDDEN_COMMAND, ephemeral=True)
            return False
        await interaction.response.defer(ephemeral=True)
        return True

    async def find_message(self, interaction, reference):
        """Find a message from its link, or from just its ID if it is in the channel the command was used in"""
        reference = reference.strip()
        link = MESSAGE_LINK.search(reference)
        if link:
            channel_id, message_id = int(link.group(1)), int(link.group(2))
            channel = self.client.get_channel(channel_id) or await self.client.fetch_channel(channel_id)
        elif reference.isdigit():
            channel, message_id = interaction.channel, int(reference)
        else:
            return None
        return await channel.fetch_message(message_id)

    def find_emoji(self, text):
        """Accepts a regular emoji, a custom one (<:name:id>), or the :name: of a custom one the bot can use"""
        text = text.strip()
        name = EMOJI_NAME.fullmatch(text)
        if name:
            return discord.utils.get(self.client.emojis, name=name.group(1)) or text
        return discord.PartialEmoji.from_str(text)

    @app_commands.command(description="Send a message to a channel as the bot")
    @app_commands.describe(channel="The channel to send to", message="What to send")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def send_to(self, interaction: discord.Interaction, channel: Sendable, message: Range[str, 1, 2000]):
        if not await self.allowed(interaction, f"/send_to [channel={channel}]"):
            return
        try:
            sent = await channel.send(message)
        except discord.HTTPException as error:
            await interaction.followup.send(embed=failure(f"Could not send to {channel.mention}: {error.text or error}"))
            return
        await interaction.followup.send(embed=success(f"Sent to {channel.mention}. [Jump to it]({sent.jump_url})"))

    @app_commands.command(description="Reply to a message as the bot")
    @app_commands.describe(
        message="A link to the message (or just its ID if it is in this channel)",
        reply="What to reply with",
        ping="Whether the reply pings the author of the message (defaults to yes)",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def reply_to(self, interaction: discord.Interaction, message: str, reply: Range[str, 1, 2000], ping: bool = True):
        if not await self.allowed(interaction, f"/reply_to [message={message}]"):
            return
        try:
            target = await self.find_message(interaction, message)
            if target is None:
                await interaction.followup.send(embed=failure("That is not a message link or a message ID."))
                return
            sent = await target.reply(reply, mention_author=ping)
        except discord.HTTPException as error:
            await interaction.followup.send(embed=failure(f"Could not reply to that message: {error.text or error}"))
            return
        await interaction.followup.send(embed=success(f"Replied. [Jump to it]({sent.jump_url})"))

    @app_commands.command(description="React to a message as the bot")
    @app_commands.describe(
        message="A link to the message (or just its ID if it is in this channel)",
        emoji="The emoji to react with",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def react_to(self, interaction: discord.Interaction, message: str, emoji: str):
        if not await self.allowed(interaction, f"/react_to [message={message}, emoji={emoji}]"):
            return
        try:
            target = await self.find_message(interaction, message)
            if target is None:
                await interaction.followup.send(embed=failure("That is not a message link or a message ID."))
                return
            await target.add_reaction(self.find_emoji(emoji))
        except discord.HTTPException as error:
            await interaction.followup.send(embed=failure(f"Could not react to that message: {error.text or error}"))
            return
        await interaction.followup.send(embed=success(f"Reacted with {emoji}. [Jump to it]({target.jump_url})"))


async def setup(client):
    await client.add_cog(Relay(client))
