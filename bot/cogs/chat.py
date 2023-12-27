import discord
from discord import Embed, Color, app_commands
from discord.ext import commands

from util.constants import TEXT_CHANNELS
from util.log_messages import SNIPE_FAIL
import logging

error_logger = logging.getLogger("errors")


class Chat(commands.Cog):
    def __init__(self, client):
        self.client = client

    @app_commands.command(description="Display any users avatar.")
    async def avatar(self, interaction : discord.Interaction, user : discord.Member = None):
        embed = Embed(color = Color.random())
        if user:
            embed.set_image(url=user.avatar.url)
            embed.set_footer(text = "{}'s Avatar".format(user.name))
        else:
            embed.set_image(url = interaction.user.avatar.url)
            embed.set_footer(text = "{}'s Avatar".format(interaction.user.name))
        
        await interaction.response.send_message(embed=embed)

    @app_commands.command(description="Display any users server avatar (default if None)")
    async def savatar(self, interaction : discord.Interaction, user : discord.Member = None):
        embed = Embed(color = Color.random())
        if user:
            embed.set_image(url=user.display_avatar.url)
            embed.set_footer(text = "{}'s Avatar".format(user.name))
        else:
            embed.set_image(url = interaction.user.display_avatar.url)
            embed.set_footer(text = "{}'s Avatar".format(interaction.user.name))
        
        await interaction.response.send_message(embed=embed)
    
    @app_commands.command(description="Display the most recently deleted message in a channel")
    async def s(self, interaction : discord.Interaction):
        channel_id = interaction.channel.id
        guild_id = interaction.guild.id
        guild_name = interaction.guild.name
        tc_data = TEXT_CHANNELS.find_one({"_id" : channel_id})

        # Should never happen, log if does
        if tc_data is None:
            error_logger.error(SNIPE_FAIL.format(channel_id, (guild_id, guild_name)))
            return 
        
        
        content = tc_data["deleted_content"]
        author = tc_data["deleted_author"]
        if author == -1:
            pass

        user = self.client.get_user(author)
        if user is None:
            pass

        embed = Embed(
            title = "Sniped Deleted Message",
            description = content,
            color = Color.dark_magenta()
        )
        embed.set_author(name = user.name, icon_url = user.avatar.url)
        await interaction.response.send_message(embed = embed)

    @app_commands.command(description="Display the most recently edited message in a channel")
    async def es(self, interaction : discord.Interaction):
        channel_id = interaction.channel.id
        guild_id = interaction.guild.id
        guild_name = interaction.guild.name
        tc_data = TEXT_CHANNELS.find_one({"_id" : channel_id})

        # Should never happen, log if does
        if tc_data is None:
            error_logger.error(SNIPE_FAIL.format(channel_id, (guild_id, guild_name)))
            return 
        
        
        content = tc_data["edited_content"]
        author = tc_data["edited_author"]
        if author == -1:
            pass

        user = self.client.get_user(author)
        if user is None:
            pass

        embed = Embed(
            title = "Sniped Edited Message",
            description = content,
            color = Color.dark_magenta()
        )
        embed.set_author(name = user.name, icon_url = user.avatar.url)
        await interaction.response.send_message(embed = embed)

async def setup(client):
    await client.add_cog(Chat(client))




        

    