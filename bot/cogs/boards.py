import discord
from discord.ext import commands, tasks
from discord import app_commands, Embed, Color

from util.buttons.board_buttons import BoardView, BoardListView
from util.constants import BOARDS
import util.dataclasses as DataClasses

from datetime import datetime

class Boards(commands.Cog):
    def __init__(self, client):
        self.client = client
    

    @app_commands.command(name = "boards", description = "View and select a board for this server")
    async def board_view(self, interaction : discord.Interaction):
        guild_id = interaction.guild.id
        server_boards = None
        embed = Embed(title = "Server Boards")
        description = ""

        await interaction.response.send_message(embed = Embed(title = "test"), view = BoardListView(self.client, guild_id))
    
    @app_commands.command(name = "add_board")
    async def add_board(self, interaction : discord.Interaction, name : str):
        guild_id = interaction.guild.id
        curr_time = datetime.utcnow()
        board_data = DataClasses.Board(name = name, cursor = 0,
                                       author = interaction.user.id,
                                       last_edited_time = curr_time,
                                       last_edited_user= interaction.user.id,
                                       scores = []
                                       )
        
        BOARDS.update_one({"_id" : guild_id}, {"$push" : {"boards" : board_data.__dict__}})




async def setup(client):
    await client.add_cog(Boards(client))