import discord
from discord import SelectOption, Embed, Color, ButtonStyle
from discord.ext import commands
from discord.ui import Button, View, TextInput, Modal, Select
import asyncio

from util.constants import BOARDS
from util.helper_functions import board_view_description, board_info_embed
import util.dataclasses as DataClasses
from datetime import datetime

BOARD_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]

async def page_callback(interaction : discord.Interaction, button : discord.ui.Button):
    print(interaction, button)

class BoardDropDown(Select):
    def __init__(self, players, guild, index, mode):
        super().__init__(placeholder = "Select a member of this server", min_values = 1)
        self.options = []
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

class BoardAddition(Modal):
    def __init__(self):
        super().__init__(title = "Board Addition")
        self.input = TextInput(
            label = "What is the name of the board?",
            min_length = 1,
            max_length = 32
        )
        self.add_item(self.input)
    
    async def on_submit(self, interaction : discord.Interaction):
        name = self.input.value
        guild_id = interaction.guild.id
        server_boards = BOARDS.find_one({"_id" : guild_id})
        names = set([board["name"].lower() for board in server_boards["boards"]])
        print(names)
        if name.lower() in names:
            await interaction.response.send_message(
                embed = Embed(
                    description = "A leaderboard with this name already exists. Please try a diferent name.",
                    color = Color.red()
                ), ephemeral = True
            )
        else:
            curr_time = datetime.utcnow()
            board_obj = DataClasses.Board(name = name, cursor = 0,
                                          author = interaction.user.id,
                                          last_edited_time = curr_time,
                                          last_edited_user = interaction.user.id,
                                          scores = {})
        
        BOARDS.update_one({"_id" : guild_id}, {"$push" : {"boards" : board_obj.__dict__}})
        await interaction.response.send_message(embed = Embed(
                    description= f"Successfully added **{name}** to this servers leaderboards.",
                    color = Color.green()
            ), ephemeral= True)

class BoardViewButton(Button):
    """Selection Numbers on the Options List of Boards"""
    def __init__(self, index, row):
        super().__init__(style = ButtonStyle.gray, emoji = BOARD_EMOJIS[index % 5], row = row)
        self.index = index
    
    async def callback(self, interaction : discord.Interaction):
        guild_id = interaction.guild.id
        server_boards = BOARDS.find_one({"_id" : guild_id})
        board_info = server_boards["boards"][self.index]
        embed = board_info_embed(board_info, interaction.guild.members)
        await interaction.response.edit_message(embed = embed, view = BoardView(self.index))

# View that will display the list of this server's leaderboard names
class BoardListView(View):
    """Board Selection View"""
    def __init__(self, guild_id, author, page = 1):
        super().__init__(timeout = 180)
        self.clear_items()
        self.guild_id = guild_id
        self.author = author
        self.page = page
        self.init_view(page)
        self.add_item(self.prev_page)
        self.add_item(self.next_page)
        self.add_item(self.add_board)
    
    def init_view(self, start):
        guild_id = self.guild_id
        server_boards = BOARDS.find_one({"_id" : guild_id})
        num_boards = len(server_boards["boards"])
        start = (self.page - 1) * 5

        for i in range(start, start + 5):
            rank = i % 5
            to_add = BoardViewButton(index = i, row = 1 if rank < 4 else 2)
            if i >= num_boards:
                to_add.disabled = True
            
            self.add_item(to_add)
    
    async def interaction_check(self, interaction : discord.Interaction):
        return interaction.user.id == self.author
        
    
    @discord.ui.button(style = ButtonStyle.gray, emoji = "◀️", row = 2)
    async def prev_page(self, interaction : discord.Interaction, button : discord.ui.Button):
        guild_id = self.guild_id
        prev_page = self.page - 1
        if ((prev_page - 1) * 5) < 0:
            await interaction.response.send_message(content = "❌")

        description = board_view_description(guild_id, prev_page)
        embed = Embed(title = "Server Boards", description = description, color = Color.red())
        await interaction.response.edit_message(
            embed = embed,
            view = BoardListView(guild_id, self.author, prev_page))
    
    @discord.ui.button(style = ButtonStyle.gray, emoji = "▶️", row = 2)
    async def next_page(self, interaction : discord.Interaction, button : discord.ui.Button):
        guild_id = self.guild_id
        server_boards = BOARDS.find_one({"_id" : guild_id})
        num_boards = len(server_boards["boards"])
        next_page = self.page + 1
        if ((next_page - 1) * 5) >= num_boards:
            await interaction.response.send_message(content = "❌")

        description = board_view_description(guild_id, next_page)
        embed = Embed(title = "Server Boards", description = description, color = Color.red())
        await interaction.response.edit_message(
            embed = embed,
            view = BoardListView(guild_id, self.author, next_page))

    @discord.ui.button(style = ButtonStyle.gray, emoji = "➕", row = 2)
    async def add_board(self, interaction : discord.Interaction, button : discord.ui.Button):
        await interaction.response.send_modal(BoardAddition())


# View that will be on messages displaying an actual leaderboard
class BoardView(View):
    """Board modification view"""
    def __init__(self, selection):
        super().__init__(timeout = 180)
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
    
    @discord.ui.button(emoji = "📃", style = ButtonStyle.red, row = 1)
    async def board_back_button(self, interaction : discord.Interaction, button : discord.ui.Button):
        guild_id = interaction.guild.id
        desciption = board_view_description(guild_id, 1)
        embed = Embed(title = "Server Boards", color = Color.red())
        embed.description = desciption
        await interaction.response.edit_message(embed = embed, view = BoardListView(guild_id, interaction.user.id))




