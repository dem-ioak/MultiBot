import discord
from discord.ext import commands, tasks
from discord import app_commands, Embed, Color

from util.buttons.board_buttons import BoardView, BoardListView
from util.constants import BOARDS
import util.dataclasses as DataClasses
from util.helper_functions import board_view_description

from datetime import datetime

class Boards(commands.Cog):
    def __init__(self, client):
        self.client = client
    

    @app_commands.command(name = "boards", description = "View and select a board for this server")
    @app_commands.checks.cooldown(1, 120)
    async def board_view(self, interaction : discord.Interaction):
        guild_id = interaction.guild.id
        embed = Embed(title = "Server Boards", color = Color.red())
        embed.description = board_view_description(guild_id, 1)
        view = BoardListView(guild_id, interaction.user.id, interaction.message)
        await interaction.response.send_message(embed = embed, view = view)
        view.message = await interaction.original_response()

async def setup(client):
    await client.add_cog(Boards(client))