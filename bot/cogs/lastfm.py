import discord
from discord import Embed, Color, guild, app_commands
from discord.ext import commands, tasks
from discord.app_commands import Choice

from util.constants import USERS
from util.classes import FMUser



class Lastfm(commands.Cog):
    def __init__(self, client):
        self.client = client
    
    lastfm = app_commands.Group(name = "lastfm", description = "Commands utilizing LastFM Functionality")
    
    @lastfm.command(
        name="topartists",
        description="Generate a list of any users Top Artists.")
    @app_commands.choices(time = [
        Choice(name = "Weekly", value = "7day"),
        Choice(name = "Monthly", value = "1month"),
        Choice(name = "Tri-Monthly", value = "3month"),
        Choice(name = "Bi-Anually", value = "6month"),
        Choice(name = "Yearly", value = "12month")
    ])
    async def topartists(self, interaction : discord.Interaction, target : discord.Member = None, time : Choice[str] = None):
        await interaction.response.defer()
        target = target if target else interaction.user
        time = time.value if time else "Overall"
        guild_id = interaction.guild.id
        user_id = target.id
        primary_key = {"guild_id" : guild_id, "user_id" : user_id}
        user_data = USERS.find_one({"_id" : primary_key})
        fm_username = user_data["last_fm"]

        # Username does not set
        if not fm_username:
            return

        fm_obj = FMUser(fm_username)

        # Username does not exist
        if not fm_obj.is_valid():
            return
        
        description = fm_obj.get_top_artists(time)

        # No data received
        if not description:
            return
        
        embed = Embed(
            color = Color.red(), 
            title = f"{target.name}'s Top {time} Artists",
            description = description)
        embed.set_author(name = interaction.user.name, icon_url= interaction.user.avatar.url)
        await interaction.followup.send(embed = embed)

async def setup(client):
    await client.add_cog(Lastfm(client))