import discord
from discord.ext import commands, tasks
from discord import Color, Embed, app_commands
from util.buttons.watchlist_buttons import WatchListView

WATCHLIST_EMBED = Embed(title="WatchList")

class Watchlist(commands.Cog):
    def __init__(self, client):
        self.client = client
    
    @app_commands.command(name = "watchlist")
    async def watchlist(self, interaction : discord.Interaction):
        view = WatchListView(self.client, interaction.message)
        await interaction.response.send_message(embed = WATCHLIST_EMBED, view = view)
        view.message = await interaction.original_response()
        print(view.message)
async def setup(client):
    await client.add_cog(Watchlist(client))