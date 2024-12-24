import discord
from discord import Embed, Color, app_commands
from discord.app_commands import Choice
from discord.ext import commands, tasks
import json
import requests
import os

from util.constants import SERVERS, USERS, BANISHED_SUCCESS_MESSAGE, BANISHED_FAILURE_MESSAGE, MY_USER_ID, FORBIDDEN_COMMAND, DAMNIT_GUILD
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
            description = f"Successfully added {role.mention} to this server's auto roles",
            color = Color.green()
        )
        await interaction.response.send_message(embed = embed)
    
    @app_commands.command(description = "Set a channel to a specific bot feature")
    @app_commands.choices(channel_type = [
        Choice(name = "Good Vibes / Birthday", value = "vibe_id"),
        Choice(name = "Logs", value = "logs_id"),
        Choice(name = "Polls", value = "polls_id"),
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
        
    
    @app_commands.command(description = "Banish a user.")
    # @app_commands.guilds(DAMNIT_GUILD)
    async def banish(self, interaction : discord.Interaction, user : discord.Member, reason : str = None):
        await interaction.response.defer()

        guild_id = interaction.guild.id
        embed = Embed(color = Color.green())
        server_data = SERVERS.find_one({"_id" : interaction.guild.id})
        primary_key = {"guild_id" : guild_id, "user_id" : user.id}
        user_data = USERS.find_one({"_id" : primary_key})
        banished_role = server_data["banished_id"]
        user_status = user_data["banished_id"]
        if user_status == -1 and banished_role != -1:

            role = interaction.guild.get_role(banished_role)
            if role is None:
                await interaction.followup.send(embed = Embed(
                    description = "This server does not have a banished role setup, user /setup Banish to create the role."
                ))
                return
            
            await user.add_roles(role)

            if user.voice:
                await user.move_to(None)

            name = f"{user.name}s corner"
            channel = await interaction.guild.create_text_channel(
                name = name,
                overwrites = {
                    interaction.guild.default_role : discord.PermissionOverwrite(
                        view_channel = False
                    ),
                    user : discord.PermissionOverwrite(
                        view_channel = True,
                        send_messages = True
                    )
                }
            )
            embed.description = BANISHED_SUCCESS_MESSAGE.format(user.mention, reason)
            USERS.update_one({"_id" : primary_key}, {"$set" : {"banished_id" : channel.id}})
        else:
            embed.description = BANISHED_FAILURE_MESSAGE.format(user.mention)
            embed.color = Color.red()
        
        await interaction.followup.send(embed = embed)
    
    @app_commands.command(description="Unbanish a currently banished user.")
    async def unbanish(self, interaction : discord.Interaction, user : discord.Member):
        guild_id = interaction.guild.id
        server_data = SERVERS.find_one({"_id" : interaction.guild.id})
        user_data = USERS.find_one({"_id" : {"guild_id" : guild_id, "user_id" : user.id}})
        banished_users = USERS.find({"banished_id" : {"$ne" : -1}})
        invalid_channels = [i["banished_id"] for i in banished_users]
        if interaction.channel.id in invalid_channels:
            await interaction.response.send_message(
                embed = Embed(
                    description = "Please do not try to unbanish someone in a banished channel.",
                    color = Color.red()
                )
            )
            return
        banished_role = server_data["banished_id"]
        user_status = user_data["banished_id"]
        if user_status != -1 and banished_role != -1:
            role = interaction.guild.get_role(banished_role)
            await user.remove_roles(role)
            to_be_deleted = discord.utils.get(interaction.guild.text_channels, id=user_status)
            await to_be_deleted.delete()
            USERS.update_one(user_data, {"$set" : {"banished_id" : -1}})
            await interaction.response.send_message(
                embed = Embed(
                    description = f"Successfully unbanished {user.mention}",
                    color = Color.green()
                )
            )
        else:
            await interaction.response.send_message(
                embed = Embed(
                    description = f"Could not unbanish {user.mention}, as they are not currently banished",
                    color = Color.red()
                )
            )


async def setup(client):
    await client.add_cog(Moderation(client))
