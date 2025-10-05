import discord
from discord import Embed, Color, app_commands
from discord.ext import commands

from util.constants import TEXT_CHANNELS, SERVERS, POLL_FAIL
from util.log_messages import SNIPE_FAIL
from util.buttons.poll_buttons import PollModal
import logging

NO_SNIPE = Embed(description="There is nothing to snipe in this channel", color = Color.red())


class Chat(commands.Cog):
    def __init__(self, client):
        self.client = client
    
    @app_commands.command(description = "Create a poll for the server")
    async def poll(self, interaction : discord.Interaction):

        guild_id = interaction.guild.id
        user_id = interaction.user.id
        server_data = SERVERS.find_one({"_id" : guild_id})
        polls = server_data["polls_id"]
        if polls != -1:
            channel = interaction.guild.get_channel(polls)
            await interaction.response.send_modal(PollModal(self.client, channel))

        else:
            await interaction.response.send_message(
                embed = Embed(
                    description = POLL_FAIL,
                    color = Color.red()
                )
            )

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
            return 
        
        
        content = tc_data["deleted_content"]
        author = tc_data["deleted_author"]
        user = self.client.get_user(author)
        if author == -1 or user is None:
            await interaction.response.send_message(embed = NO_SNIPE, ephemeral = True)
            return

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
            return 
        
        
        content = tc_data["edited_content"]
        author = tc_data["edited_author"]
        user = self.client.get_user(author)
        if author == -1 or user is None:
            await interaction.response.send_message(embed = NO_SNIPE, ephemeral = True)
            return

        embed = Embed(
            title = "Sniped Edited Message",
            description = content,
            color = Color.dark_magenta()
        )
        embed.set_author(name = user.name, icon_url = user.avatar.url)
        await interaction.response.send_message(embed = embed)

async def setup(client):
    await client.add_cog(Chat(client))




        

    