import discord
from discord import Embed, Color, app_commands
from discord.app_commands import Choice
from discord.ext import commands, tasks

from util.constants import SERVERS
from util.log_messages import *

import logging
logger = logging.getLogger("data")

class Moderation(commands.Cog):
    def __init__(self, client):
        self.client = client
    
    @app_commands.command(description = "Add to this servers auto roles")
    async def autor(self, interaction : discord.Interaction, role : discord.Role):
        guild_id = interaction.guild.id
        server_data = SERVERS.find_one({"_id" : guild_id})
        SERVERS.update_one(server_data,
            {"$push" : {"auto_roles" : role.id}
        })
        embed = Embed(
            description = "Successfully added {role.mention} to this server's auto roles",
            color = Color.green()
        )
        await interaction.response.send_message(embed = embed)
    
    @app_commands.command(description = "Set a channel to a specific bot feature")
    @app_commands.choices(channel_type = [
        Choice(name = "Good Vibes", value = "vibe"),
        Choice(name = "Logs", value = "logs"),
        Choice(name = "Polls", value = "polls"),
        Choice(name = "AFK Corner", value = "afk_corner"),
        Choice(name = "Join to Create", value = "join_to_create")
    ])
    async def set_channel(self, interaction : discord.Interaction, channel_type : Choice[str], channel_id : str):
        guild_id = interaction.guild.id
        channel_id = int(channel_id)

        choice_value = channel_type.value
        text_channel_ids = set(channel.id for channel in interaction.guild.text_channels)
        voice_channel_ids = set(channel.id for channel in interaction.guild.voice_channels)

        voice_categories = ["afk_corner", "join_to_create"]

        invalid_text_channel = choice_value not in voice_categories and channel_id not in text_channel_ids
        invalid_voice_channel = choice_value in voice_categories and channel_id not in voice_channel_ids

        if invalid_text_channel or invalid_voice_channel:
            await interaction.response.send_message(ephemeral=True, content = "❌")

        else:
            server_data = SERVERS.find_one({"_id" : guild_id})
            SERVERS.update_one(server_data, {"$set" : {choice_value : channel_id}})
            logger.info(CHANNEL_SET.format(guild_id, choice_value, channel_id))
            await interaction.response.send_message(ephemeral=True, content = "✅")

async def setup(client):
    await client.add_cog(Moderation(client))