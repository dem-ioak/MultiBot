import discord
from discord import SelectOption, Embed, Color, ButtonStyle
from discord.ext import commands
from discord.ui import Button, View, TextInput, Modal, Select
import asyncio

from util.constants import BOARDS

BOARD_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]

class BoardDropDown(Select):
    def __init__(self, client, players, guild, index, mode):
        super().__init__(placeholder = "Select a member of this server", min_values = 1)
        self.options = []
        self.client = client
        self.index = index
        self.mode = mode
        for user in guild.members:
            if user.bot:
                continue
            user_id = user.id
            user_in_list = str(user_id) in players
            if self.mode == "add" and user_in_list:
                continue
            
            if self.mode == "delete" and not user_in_list:
                continue

            self.options.append(SelectOption(label = f"👤 {user.name} - {user_id}"))

        self.max_values = len(self.options)
    
    async def callback(self, interaction):
        pass

# View that will display the list of this server's leaderboard names
class BoardListView(View):
    def __init__(self, client, guild_id, start = 0, limit = 5):
        super().__init__(timeout = 180)
        self.client = client
        self.guild_id = guild_id
        self.init_view(start)
    
    def init_view(self, start):
        guild_id = self.guild_id
        server_boards = BOARDS.find_one({"_id" : guild_id})
        num_boards = len(server_boards["boards"])
        for i in range(start, start + 5):
            to_add = Button(
                    style = ButtonStyle.gray,
                    emoji = BOARD_EMOJIS[i % 5]
                )
            if i >= num_boards:
                to_add.disabled = True
            
            self.add_item(to_add)
                

# View that will be on messages displaying an actual leaderboard
class BoardView(View):
    def __init__(self, client, selection):
        super().__init__(timeout = 180)
        self.client = client
        self.selection = selection
    
    # TODO: Throw this code into a function
    @discord.ui.button(emoji = "⬆️", style = ButtonStyle.gray, row = 1)
    async def board_curson_up(self, interaction : discord.Interaction, button : discord.ui.Button):
        guild_id = interaction.guild.id
        index = self.selection
        server_boards = BOARDS.find_one({"_id" : guild_id})
        board_data = server_boards["boards"][index]
        num_players = len(board_data["scores"])
        cursor = board_data["cursor"]
        new_cursor = (cursor - 1) % num_players
        update_filter = {"$set" : {f"scores.{index}" : new_cursor}}

        BOARDS.update_one(server_boards, update_filter)
    
    @discord.ui.button(emoji = "⬇️", style = ButtonStyle.gray, row = 1)
    async def board_curson_down(self, interaction : discord.Interaction, button : discord.ui.Button):
        guild_id = interaction.guild.id
        index = self.selection
        server_boards = BOARDS.find_one({"_id" : guild_id})
        board_data = server_boards["boards"][index]
        num_players = len(board_data["scores"])
        cursor = board_data["cursor"]
        new_cursor = (cursor + 1) % num_players
        update_filter = {"$set" : {f"scores.{index}" : new_cursor}}

        BOARDS.update_one(server_boards, update_filter)




