import discord
from discord import SelectOption, Embed, Color, ButtonStyle
from discord.ext import commands
from discord.ui import Button, View, TextInput, Modal, Select, UserSelect
from pymongo import ReturnDocument

from util.constants import BOARDS
from util.helper_functions import board_view_description, board_info_embed, board_players, board_page_count
import util.dataclasses as DataClasses
from util.log_messages import SCORE_CHANGE, BOARD_ADD_USER, BOARD_ADD
from util.log_manager import get_logger
from datetime import datetime

import logging

BOARD_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]

# Discord allows at most 25 options in a list we fill in ourselves. Past that, its own member picker is used
OPTION_LIMIT = 25

BOARD_GONE = Embed(description = "This board no longer exists. It was renamed or deleted.", color = Color.red())
NAME_TAKEN = Embed(description = "A leaderboard with this name already exists. Please try a diferent name.", color = Color.red())

# Which board each boards message is showing right now (absent while it shows the list of boards).
# Everyone shares that one message, so a change to a board only redraws it if that board is what is on screen
shown_board = {}


def get_boards(guild_id):
    server_boards = BOARDS.find_one({"_id" : guild_id})
    return server_boards["boards"] if server_boards else []

def find_board(boards, name):
    return next((board for board in boards if board["name"] == name), None)

def get_board(guild_id, name):
    return find_board(get_boards(guild_id), name)

def name_taken(guild_id, name):
    return name.lower() in set(board["name"].lower() for board in get_boards(guild_id))

def update_board(interaction, name, update, filters = ()):
    """Apply an update to one board in a single step, returning the board as it is afterwards (None if it is gone).

    Boards are found by name rather than position, so deleting one cannot send an update to its neighbour.
    Within the update, the board is referred to as `boards.$[b]`."""
    update.setdefault("$set", {}).update({
        "boards.$[b].last_edited_time" : datetime.utcnow().replace(microsecond = 0),
        "boards.$[b].last_edited_user" : interaction.user.id,
    })
    server_boards = BOARDS.find_one_and_update(
        {"_id" : interaction.guild.id, "boards.name" : name},
        update,
        array_filters = [{"b.name" : name}, *filters],
        return_document = ReturnDocument.AFTER)
    return server_boards["boards"] if server_boards else None

def same_user(user_ids):
    """Match a player whether their id was stored as text or as a number"""
    return {"$in" : [str(user_id) for user_id in user_ids] + [int(user_id) for user_id in user_ids]}

def player_options(guild, players):
    options = []
    for user_id, score in players:
        member = guild.get_member(int(user_id))
        name = member.name if member else f"Unknown user ({user_id})"
        options.append(SelectOption(label = name, value = user_id, description = f"{score} point{'' if score == 1 else 's'}"))
    return options

async def redraw_board(message, guild, board):
    """Show a board's new state, unless the message has since been moved on to something else"""
    if shown_board.get(message.id) == board["name"]:
        await message.edit(embed = board_info_embed(board, guild.members))

async def show_board_list(message, guild_id, author):
    shown_board.pop(message.id, None)
    embed = Embed(title = "Server Boards", description = board_view_description(guild_id, 1), color = Color.red())
    await message.edit(embed = embed, view = BoardListView(guild_id, author, message))


# Changing a score. The board itself only shows scores, who gets the point is picked privately by
# whoever pressed the button, so two people using a board at once can never redirect each other
async def change_score(interaction, name, change, message, user_id):
    guild = interaction.guild
    log = get_logger(__name__, server=guild.name, user=interaction.user.name)

    # A single atomic step on that player's own entry, so points given at the same moment all count
    player = {"p.0" : same_user([user_id])}
    if change < 0:
        player["p.1"] = {"$gt" : 0} # Scores do not go below 0
    boards = update_board(interaction, name, {"$inc" : {"boards.$[b].scores.$[p].1" : change}}, [player])
    if boards is None:
        await interaction.response.edit_message(embed = BOARD_GONE, view = None)
        return

    board = find_board(boards, name)
    score = dict(board_players(board)).get(str(user_id))
    if score is None:
        embed = Embed(description = f"<@{user_id}> is not on this board. Use 👤 to add them.", color = Color.red())
    else:
        await redraw_board(message, guild, board)
        embed = Embed(description = f"<@{user_id}> now has **{score}** on **{name}**. Pick again to keep going.", color = Color.green())
        log.info(f"[board_name={name}] Score changed by {change} for user_id={user_id}, now {score}")

    # Sent with a fresh picker, which lets the same player be picked again straight away
    await interaction.response.edit_message(embed = embed, view = ScoreView(guild, name, change, message))

class ScoreSelect(Select):
    def __init__(self, guild, players):
        super().__init__(placeholder = "Select a player", options = player_options(guild, players))

    async def callback(self, interaction):
        await change_score(interaction, self.view.name, self.view.change, self.view.message, self.values[0])

class ScoreUserSelect(UserSelect):
    def __init__(self):
        super().__init__(placeholder = "Select a player (type to search)")

    async def callback(self, interaction):
        await change_score(interaction, self.view.name, self.view.change, self.view.message, str(self.values[0].id))

class ScoreView(View):
    """Private prompt asking which player a point goes to / comes from"""
    def __init__(self, guild, name, change, message):
        super().__init__()
        self.name = name
        self.change = change
        self.message = message
        board = get_board(guild.id, name)
        players = board_players(board) if board else []
        self.has_players = len(players) > 0
        if len(players) > OPTION_LIMIT:
            self.add_item(ScoreUserSelect())
        elif players:
            self.add_item(ScoreSelect(guild, players))

async def send_score_prompt(interaction, name, change):
    view = ScoreView(interaction.guild, name, change, interaction.message)
    if not view.has_players:
        await interaction.response.send_message(
            embed = Embed(description = "Nobody is on this board yet. Use 👤 to add players.", color = Color.red()),
            ephemeral = True)
        return

    action = "gets a point" if change > 0 else "loses a point"
    await interaction.response.send_message(
        embed = Embed(description = f"Who {action} on **{name}**?"), view = view, ephemeral = True)


# Adding and removing players
async def change_players(interaction, name, mode, message, targets):
    """Add or remove the (user_id, name, is_bot) targets, telling the user which of them it applied to"""
    guild = interaction.guild
    log = get_logger(__name__, server=guild.name, user=interaction.user.name)

    # Read the board fresh, it may have changed while the picker was open
    board = get_board(guild.id, name)
    if board is None:
        await interaction.response.edit_message(embed = BOARD_GONE, view = None)
        return
    on_board = set(user_id for user_id, score in board_players(board))

    # The member picker lists everyone, so leave out whoever the action does not apply to
    changed, skipped = [], []
    for user_id, user_name, is_bot in targets:
        if is_bot or (user_id in on_board) == (mode == "add"):
            skipped.append(user_name)
        else:
            changed.append((user_id, user_name))

    if changed:
        user_ids = [user_id for user_id, user_name in changed]
        if mode == "add":
            update = {"$push" : {"boards.$[b].scores" : {"$each" : [[user_id, 0] for user_id in user_ids]}}}
        else:
            update = {"$pull" : {"boards.$[b].scores" : {"$elemMatch" : same_user(user_ids)}}}

        boards = update_board(interaction, name, update)
        if boards is None:
            await interaction.response.edit_message(embed = BOARD_GONE, view = None)
            return

        await redraw_board(message, guild, find_board(boards, name))
        names = ", ".join(user_name for user_id, user_name in changed)
        log.info(f"[board_name={name}] {'Added' if mode == 'add' else 'Removed'} users: {names}")

    verb = "Added" if mode == "add" else "Removed"
    reason = "already on this board" if mode == "add" else "not on this board"
    description = ""
    if changed:
        description += f"✅ {verb} " + ", ".join(f"**{user_name}**" for user_id, user_name in changed) + "\n"
    if skipped:
        description += "Skipped " + ", ".join(f"**{user_name}**" for user_name in skipped) + f" (bots, or {reason})"

    await interaction.response.edit_message(
        embed = Embed(description = description, color = Color.green() if changed else Color.red()),
        view = ManageView(guild, name, message))

class BoardDropDown(UserSelect):
    """Discord's own member picker. Unlike a list of options it is not capped at 25 members, and can be searched"""
    def __init__(self, mode):
        action = "add" if mode == "add" else "remove"
        super().__init__(placeholder = f"Select members to {action} (type to search)", min_values = 1, max_values = OPTION_LIMIT, row = 0 if mode == "add" else 1)
        self.mode = mode

    async def callback(self, interaction):
        targets = [(str(target.id), target.name, target.bot) for target in self.values]
        await change_players(interaction, self.view.name, self.mode, self.view.message, targets)

class PlayerRemoveSelect(Select):
    """Only the players on the board, for boards small enough to list them"""
    def __init__(self, guild, players):
        options = player_options(guild, players)
        super().__init__(placeholder = "Select players to remove", options = options, min_values = 1, max_values = len(options), row = 1)

    async def callback(self, interaction):
        names = {option.value : option.label for option in self.options}
        targets = [(user_id, names[user_id], False) for user_id in self.values]
        await change_players(interaction, self.view.name, "delete", self.view.message, targets)


# Renaming and deleting a board
class BoardRename(Modal):
    """Text-Box Prompt to Rename a Board"""
    def __init__(self, name, message):
        super().__init__(title = "Board Rename")
        self.name = name
        self.message = message
        self.input = TextInput(
            label = "What should this board be called?",
            default = name,
            min_length = 1,
            max_length = 32
        )
        self.add_item(self.input)

    async def on_submit(self, interaction : discord.Interaction):
        guild = interaction.guild
        log = get_logger(__name__, server=guild.name, user=interaction.user.name)
        new_name = self.input.value.strip()

        # Changing only the capitalisation of a board's own name is fine
        if not new_name or (new_name.lower() != self.name.lower() and name_taken(guild.id, new_name)):
            await interaction.response.edit_message(embed = NAME_TAKEN, view = ManageView(guild, self.name, self.message))
            return

        boards = update_board(interaction, self.name, {"$set" : {"boards.$[b].name" : new_name}})
        if boards is None:
            await interaction.response.edit_message(embed = BOARD_GONE, view = None)
            return

        if shown_board.get(self.message.id) == self.name:
            # The buttons under the board act on it by name, so they have to be replaced as well
            shown_board[self.message.id] = new_name
            embed = board_info_embed(find_board(boards, new_name), guild.members)
            await self.message.edit(embed = embed, view = BoardView(new_name, self.message))
        elif self.message.id not in shown_board:
            await show_board_list(self.message, guild.id, interaction.user.id)

        log.info(f"[board_name={self.name}] Renamed to {new_name}")
        await interaction.response.edit_message(
            embed = Embed(description = f"✅ Renamed **{self.name}** to **{new_name}**.", color = Color.green()),
            view = ManageView(guild, new_name, self.message))

class DeleteConfirmView(View):
    """Second step of deleting a board, as it cannot be undone"""
    def __init__(self, name, message):
        super().__init__()
        self.name = name
        self.message = message

    @discord.ui.button(label = "Delete", emoji = "🗑️", style = ButtonStyle.red)
    async def confirm(self, interaction : discord.Interaction, button : discord.ui.Button):
        guild = interaction.guild
        log = get_logger(__name__, server=guild.name, user=interaction.user.name)
        result = BOARDS.update_one({"_id" : guild.id}, {"$pull" : {"boards" : {"name" : self.name}}})
        if result.modified_count == 0:
            await interaction.response.edit_message(embed = BOARD_GONE, view = None)
            return

        log.info(f"[board_name={self.name}] Board deleted")
        # Whether it was showing this board or the list of boards, what is on screen is now out of date
        if shown_board.get(self.message.id) in (self.name, None):
            await show_board_list(self.message, guild.id, interaction.user.id)

        await interaction.response.edit_message(
            embed = Embed(description = f"✅ Deleted **{self.name}**.", color = Color.green()), view = None)

    @discord.ui.button(label = "Cancel", style = ButtonStyle.gray)
    async def cancel(self, interaction : discord.Interaction, button : discord.ui.Button):
        await interaction.response.edit_message(
            embed = Embed(description = f"⚙️ Manage **{self.name}**."),
            view = ManageView(interaction.guild, self.name, self.message))

class ManageView(View):
    """Private prompt to add / remove a board's players, rename it, or delete it"""
    def __init__(self, guild, name, message):
        super().__init__()
        self.name = name
        self.message = message
        self.add_item(BoardDropDown("add"))
        board = get_board(guild.id, name)
        players = board_players(board) if board else []
        if len(players) > OPTION_LIMIT:
            self.add_item(BoardDropDown("delete"))
        elif players:
            self.add_item(PlayerRemoveSelect(guild, players))

    @discord.ui.button(label = "Rename", emoji = "✏️", style = ButtonStyle.gray, row = 2)
    async def rename(self, interaction : discord.Interaction, button : discord.ui.Button):
        await interaction.response.send_modal(BoardRename(self.name, self.message))

    @discord.ui.button(label = "Delete board", emoji = "🗑️", style = ButtonStyle.red, row = 2)
    async def delete(self, interaction : discord.Interaction, button : discord.ui.Button):
        await interaction.response.edit_message(
            embed = Embed(
                description = f"Delete **{self.name}** and all of its scores? This cannot be undone.",
                color = Color.red()),
            view = DeleteConfirmView(self.name, self.message))


class BoardAddition(Modal):
    """Text-Box Prompt to Insert a New Board"""
    def __init__(self, message):
        super().__init__(title = "Board Addition")
        self.input = TextInput(
            label = "What is the name of the board?",
            min_length = 1,
            max_length = 32
        )
        self.message = message
        self.add_item(self.input)

    async def on_submit(self, interaction : discord.Interaction):
        name = self.input.value.strip()
        guild_id = interaction.guild.id
        if not name or name_taken(guild_id, name):
            await interaction.response.send_message(embed = NAME_TAKEN, ephemeral = True)
            return

        curr_time = datetime.utcnow().replace(microsecond = 0)
        board_obj = DataClasses.Board(name = name,
                                      author = interaction.user.id,
                                      last_edited_time = curr_time,
                                      last_edited_user = interaction.user.id,
                                      scores = []) # [user_id, score] pairs

        BOARDS.update_one({"_id" : guild_id}, {"$push" : {"boards" : board_obj.__dict__}}, upsert = True)
        await show_board_list(self.message, guild_id, interaction.user.id)
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
        boards = get_boards(guild_id)
        message = interaction.message
        if self.index >= len(boards):
            # The list on screen is out of date, a board has been deleted since it was drawn
            shown_board.pop(message.id, None)
            embed = Embed(title = "Server Boards", description = board_view_description(guild_id, 1), color = Color.red())
            await interaction.response.edit_message(embed = embed, view = BoardListView(guild_id, interaction.user.id, message))
            return

        board_info = boards[self.index]
        embed = board_info_embed(board_info, interaction.guild.members)
        shown_board[message.id] = board_info["name"]
        await interaction.response.edit_message(embed = embed, view = BoardView(board_info["name"], message))

# View that will display the list of this server's leaderboard names
class BoardListView(View):
    """Board Selection View"""
    def __init__(self, guild_id, author, message, page = 1):
        super().__init__(timeout = None)
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
        num_boards = len(get_boards(self.guild_id))
        start = (self.page - 1) * 5

        for i in range(start, start + 5):
            rank = i % 5
            to_add = BoardViewButton(index = i, row = 1 if rank < 4 else 2)
            if i >= num_boards:
                to_add.disabled = True

            self.add_item(to_add)

    async def interaction_check(self, interaction : discord.Interaction):
        # No longer in use
        return True

    async def flip_page(self, interaction : discord.Interaction, change):
        """Move `change` pages along, wrapping around at either end"""
        guild_id = self.guild_id
        page = (self.page - 1 + change) % board_page_count(guild_id) + 1

        description = board_view_description(guild_id, page)
        embed = Embed(title = "Server Boards", description = description, color = Color.red())
        await interaction.response.edit_message(
            embed = embed,
            view = BoardListView(guild_id, self.author, interaction.message, page))

    @discord.ui.button(style = ButtonStyle.gray, emoji = "◀️", row = 2)
    async def prev_page(self, interaction : discord.Interaction, button : discord.ui.Button):
        """Navigate to the previous page"""
        await self.flip_page(interaction, -1)

    @discord.ui.button(style = ButtonStyle.gray, emoji = "▶️", row = 2)
    async def next_page(self, interaction : discord.Interaction, button : discord.ui.Button):
        """Navigate to the next page"""
        await self.flip_page(interaction, 1)

    @discord.ui.button(style = ButtonStyle.gray, emoji = "➕", row = 2)
    async def add_board(self, interaction : discord.Interaction, button : discord.ui.Button):
        """Send `BoardAddition` Modal to add a new baord"""
        await interaction.response.send_modal(BoardAddition(interaction.message))

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
        super().__init__(timeout = None)
        self.selection = selection # Name of the board
        self.message = message

    @discord.ui.button(emoji = "➕", style = discord.ButtonStyle.green, row = 1)
    async def board_increment_score(self, interaction : discord.Interaction, button : discord.ui.Button):
        """Ask which player to give a point to"""
        await send_score_prompt(interaction, self.selection, 1)

    @discord.ui.button(emoji = "➖", style = discord.ButtonStyle.red, row = 1)
    async def board_decrement_score(self, interaction : discord.Interaction, button : discord.ui.Button):
        """Ask which player to take a point from"""
        await send_score_prompt(interaction, self.selection, -1)

    @discord.ui.button(emoji = "👤", style = ButtonStyle.gray, row = 1)
    async def board_manage(self, interaction : discord.Interaction, button : discord.ui.Button):
        """Add / remove players, rename the board, or delete it"""
        await interaction.response.send_message(
            embed = Embed(description = f"⚙️ Manage **{self.selection}**."),
            view = ManageView(interaction.guild, self.selection, interaction.message),
            ephemeral = True)

    @discord.ui.button(emoji = "📃", style = ButtonStyle.blurple, row = 1)
    async def board_back_button(self, interaction : discord.Interaction, button : discord.ui.Button):
        """Navigate back to the list of available leaderboards"""
        guild_id = interaction.guild.id
        desciption = board_view_description(guild_id, 1)
        embed = Embed(title = "Server Boards", color = Color.red())
        embed.description = desciption
        shown_board.pop(interaction.message.id, None)
        await interaction.response.edit_message(embed = embed, view = BoardListView(guild_id, interaction.user.id, interaction.message))

    async def on_timeout(self) -> None:
        """Remove board data / buttons on view timeout"""
        await self.message.edit(embed = Embed(
            description= "This board has timed out, use /boards to refresh",
            color = Color.red()
        ), view = None)
