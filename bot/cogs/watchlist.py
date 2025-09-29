import discord
from discord.ext import commands, tasks
from discord import Color, Embed, app_commands

from util.buttons.watchlist_buttons import WatchListView
from util.constants import WATCHLIST, WATCHLIST_EMBED, DEPRECEATED
from util.helper_functions import generate_wl_page

WATCHLIST_EMBED = Embed(title="WatchList")

class Watchlist(commands.Cog):
    def __init__(self, client):
        self.client = client
    
    @app_commands.command(name = "watchlist", description = "Load this server's watchlist interface")
    async def watchlist(self, interaction : discord.Interaction):
        await interaction.response.send_message(embed = DEPRECEATED, ephemeral=True)
        # guild_id = interaction.guild.id
        # wl_data = WATCHLIST.find_one({"_id" : guild_id})
        # entries = wl_data["entries"]
        # embed = WATCHLIST_EMBED if not entries else generate_wl_page(1, entries)

        # view = WatchListView(self.client, interaction.message)
        # await interaction.response.send_message(embed = embed, view = view)
        # view.message = await interaction.original_response()
        
async def setup(client):
    await client.add_cog(Watchlist(client))