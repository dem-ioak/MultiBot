import discord
from discord import SelectOption, Embed, Color, ButtonStyle
from discord.ext import commands
from discord.ui import Button, View, TextInput, Modal, Select
import asyncio

from util.constants import BOARDS
from util.helper_functions import board_view_description, board_info_embed, sort_dict, parse_id
import util.dataclasses as DataClasses
from util.log_messages import SCORE_INCREMENTED, SCORE_DECREMENTED, BOARD_ADD_USER
from datetime import datetime

import logging

data_logger = logging.getLogger("data")

BOARD_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]

class BoardDropDown(Select):
    def __init__(self, board, guild, index, mode, message):
        super().__init__(placeholder = "Select a member of this server", min_values = 1)
        self.options = []
        self.index = index
        self.mode = mode
        self.board = board
        self.guild = guild
        self.message = message
        players = self.board["scores"]
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
        players = self.board["scores"]
        for target in self.values:
            user_id = parse_id(target)
            user_id = str(user_id)
            if self.mode == "add":
                players[user_id] = 0
                data_logger.info(BOARD_ADD_USER.format(
                    interaction.user.id, user_id, 
                    self.board["name"], (interaction.guild.id, interaction.guild.name)
                ))
            else:
                del players[user_id]
        
        board = self.board
        board["scores"] = players
        embed = board_info_embed(board, self.guild.members)

        BOARDS.update_one({"_id" : self.guild.id}, {"$set" : {f"boards.{self.index}.scores" : board["scores"]}})
        await self.message.edit(embed = embed)
        await interaction.response.send_message(content = "✅", ephemeral = True)

class BoardAddition(Modal):
    """Text-Box Prompt to Insert a New Board"""
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

        await interaction.response.edit_message(embed = embed, view = BoardView(self.index, self.view.message))

# View that will display the list of this server's leaderboard names
class BoardListView(View):
    """Board Selection View"""
    def __init__(self, guild_id, author, message, page = 1):
        super().__init__(timeout = 120)
        self.clear_items()
        self.guild_id = guild_id
        self.author = author
        self.message = message
        self.page = page
        self.init_view(page)

        self.add_item(self.prev_page)
        self.add_item(self.next_page)
        self.add_item(self.add_board)
    
    def init_view(self, start):
        """Add buttons for 1-5, disabling those that cannot be mapped to an entry"""
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
        """Navigate to the previous page if possible"""
        guild_id = self.guild_id
        prev_page = self.page - 1
        server_boards = BOARDS.find_one({"_id" : guild_id})
        num_boards = len(server_boards["boards"])
        if ((prev_page - 1) * 5) < 0:
            await interaction.response.send_message(content = "❌", ephemeral = True)

        description = board_view_description(guild_id, prev_page)
        embed = Embed(title = "Server Boards", description = description, color = Color.red())
        await interaction.response.edit_message(
            embed = embed,
            view = BoardListView(guild_id, self.author, self.message, prev_page))
    
    @discord.ui.button(style = ButtonStyle.gray, emoji = "▶️", row = 2)
    async def next_page(self, interaction : discord.Interaction, button : discord.ui.Button):
        """Navigate to the next page if possible"""
        guild_id = self.guild_id
        server_boards = BOARDS.find_one({"_id" : guild_id})
        num_boards = len(server_boards["boards"])
        next_page = self.page + 1
        if ((next_page - 1) * 5) >= num_boards:
            next_page = 1

        description = board_view_description(guild_id, next_page)
        embed = Embed(title = "Server Boards", description = description, color = Color.red())
        await interaction.response.edit_message(
            embed = embed,
            view = BoardListView(guild_id, self.author, self.message, next_page))

    @discord.ui.button(style = ButtonStyle.gray, emoji = "➕", row = 2)
    async def add_board(self, interaction : discord.Interaction, button : discord.ui.Button):
        """Send `BoardAddition` Modal to add a new baord"""
        await interaction.response.send_modal(BoardAddition())
    
    async def on_timeout(self) -> None:
        """Remove board data / buttons on view timeout"""
        await self.message.edit(embed = Embed(
            description= "This board has timed out, use /boards to refresh",
            color = Color.red()
        ), view = None)


# View that will be on messages displaying an actual leaderboard
class BoardView(View):
    """Board modification view"""
    def __init__(self, selection, message):
        super().__init__(timeout = 120)
        self.selection = selection
        self.message = message
    
    # TODO: Throw this code into a function
    @discord.ui.button(emoji = "⬆️", style = ButtonStyle.gray, row = 1)
    async def board_curson_up(self, interaction : discord.Interaction, button : discord.ui.Button):
        """Move the cursor on the selected board up"""
        guild_id = interaction.guild.id
        index = self.selection
        server_boards = BOARDS.find_one({"_id" : guild_id})
        board_data = server_boards["boards"][index]
        num_players = len(board_data["scores"])
        cursor = board_data["cursor"]
        new_cursor = (cursor - 1) % (num_players + 1)
        update_filter = {"$set" : {f"boards.{index}.cursor" : new_cursor}}

        
        BOARDS.update_one(server_boards, update_filter)

        board_data["cursor"] = new_cursor
        embed = board_info_embed(board_data, interaction.guild.members)
        await interaction.response.edit_message(embed = embed)

    
    @discord.ui.button(emoji = "⬇️", style = ButtonStyle.gray, row = 1)
    async def board_curson_down(self, interaction : discord.Interaction, button : discord.ui.Button):
        """Move the cursor on the selected board down"""

        guild_id = interaction.guild.id
        index = self.selection
        server_boards = BOARDS.find_one({"_id" : guild_id})
        board_data = server_boards["boards"][index]
        num_players = len(board_data["scores"])
        cursor = board_data["cursor"]
        new_cursor = (cursor + 1) % (num_players + 1)
        update_filter = {"$set" : {f"boards.{index}.cursor" : new_cursor}}

        BOARDS.update_one(server_boards, update_filter)

        board_data["cursor"] = new_cursor
        embed = board_info_embed(board_data, interaction.guild.members)
        await interaction.response.edit_message(embed = embed)

    
    @discord.ui.button(emoji = "📃", style = ButtonStyle.blurple, row = 1)
    async def board_back_button(self, interaction : discord.Interaction, button : discord.ui.Button):
        """Navigate back to the list of available leaderboards"""
        guild_id = interaction.guild.id
        desciption = board_view_description(guild_id, 1)
        embed = Embed(title = "Server Boards", color = Color.red())
        embed.description = desciption
        await interaction.response.edit_message(embed = embed, view = BoardListView(guild_id, interaction.user.id, self.message))
    
    # CODE FOR BOTH OF THESE IS EXACTLY THE SAME
    # I MIGHT PUT IT IN A FUNCTION BUT RIGHT NOW ITS TOO ANNOYING FOR 0 GAIN
    @discord.ui.button(emoji = "➕", style = discord.ButtonStyle.green, row = 1)
    async def board_increment_score(self, interaction : discord.Interaction, button : discord.ui.Button):
        """Increment a user's score OR add a user to this board"""
        guild_id = interaction.guild.id
        guild_name = interaction.guild.name
        index = self.selection
        server_boards = BOARDS.find_one({"_id" : guild_id})
        board_data = server_boards["boards"]

        board = board_data[index]
        scores = board["scores"]
        cursor_pos = board["cursor"]
        if cursor_pos == len(scores):
            embed = Embed(description = "👤 Select the player you would like to add")
            view = View()
            view.add_item(BoardDropDown(board, interaction.guild, index, "add", self.message))
            await interaction.response.send_message(embed = embed, view = view, ephemeral = True)
        else:
            user_id = None
            for idx, val in enumerate(scores):
                if idx == cursor_pos:
                    user_id = val
            
            
            BOARDS.update_one(server_boards, {"$inc" : {f"boards.{index}.scores.{user_id}" : 1}})

            scores[user_id] += 1
            embed = board_info_embed(board, interaction.guild.members)
            await interaction.response.edit_message(embed = embed)
            data_logger.info(SCORE_INCREMENTED.format(
                interaction.user.id, user_id, board["name"], (guild_id, guild_name)))

    @discord.ui.button(emoji = "➖", style = discord.ButtonStyle.red, row = 1)
    async def board_decrement_score(self, interaction : discord.Interaction, button : discord.ui.Button):
        """Increment a user's score OR add a user to this board"""
        guild_id = interaction.guild.id
        guild_name = interaction.guild.name
        index = self.selection
        server_boards = BOARDS.find_one({"_id" : guild_id})
        board_data = server_boards["boards"]

        board = board_data[index]
        scores = board["scores"]
        cursor_pos = board["cursor"]
        if cursor_pos == len(scores):
            embed = Embed(description = "👤 Select the player you would like to add")
            view = View()
            view.add_item(BoardDropDown(board, interaction.guild, index, "delete", self.message))
            await interaction.response.send_message(embed = embed, view = view, ephemeral = True)
        else:
            user_id = None
            for idx, val in enumerate(scores):
                if idx == cursor_pos:
                    user_id = val
            
            
            BOARDS.update_one(server_boards, {"$inc" : {f"boards.{index}.scores.{user_id}" : -1}})

            scores[user_id] -= 1
            embed = board_info_embed(board, interaction.guild.members)
            await interaction.response.edit_message(embed = embed)
            data_logger.info(SCORE_DECREMENTED.format(
                interaction.user.id, user_id, board["name"], (guild_id, guild_name)))


    
    async def on_timeout(self) -> None:
        """Remove board data / buttons on view timeout"""
        await self.message.edit(embed = Embed(
            description= "This board has timed out, use /boards to refresh",
            color = Color.red()
        ), view = None)





