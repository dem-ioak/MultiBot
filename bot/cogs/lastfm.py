import discord
from discord import Embed, Color, guild, app_commands
from discord.ext import commands, tasks
from discord.app_commands import Choice
from util.helper_functions import handle_get_top_call

CHOICES = [
        Choice(name = "Weekly", value = "7day"),
        Choice(name = "Monthly", value = "1month"),
        Choice(name = "Tri-Monthly", value = "3month"),
        Choice(name = "Bi-Anually", value = "6month"),
        Choice(name = "Yearly", value = "12month")
    ]



class Lastfm(commands.Cog):
    def __init__(self, client):
        self.client = client
    
    lastfm = app_commands.Group(name = "lastfm", description = "Commands utilizing LastFM Functionality")
    
    @lastfm.command(name="topartists",description="Generate a list of any users Top Artists.")
    @app_commands.choices(time = CHOICES)
    async def topartists(self, interaction : discord.Interaction, target : discord.Member = None, time : Choice[str] = None):
        await interaction.response.defer()
        embed = handle_get_top_call(interaction, target, time, "artists")
        await interaction.followup.send(embed = embed)
    
    @lastfm.command(name="topalbums", description="Generate a list of any users Top Albums.")
    @app_commands.choices(time = CHOICES)
    async def topalbums(self, interaction : discord.Interaction, target : discord.Member = None, time : Choice[str] = None):
        await interaction.response.defer()
        embed = handle_get_top_call(interaction, target, time, "albums")
        await interaction.followup.send(embed = embed)
    
    @lastfm.command(name="toptracks",description="Generate a list of any users Top Tracks.")
    @app_commands.choices(time = CHOICES)
    async def toptracks(self, interaction : discord.Interaction, target : discord.Member = None, time : Choice[str] = None):
        await interaction.response.defer()
        embed = handle_get_top_call(interaction, target, time, "tracks")
        await interaction.followup.send(embed = embed)

async def setup(client):
    await client.add_cog(Lastfm(client))