import discord
from discord import SelectOption, Embed, Color, ButtonStyle
from discord.ext import commands
from discord.ui import Button, View, TextInput, Modal, Select
from math import ceil
from uuid import uuid4

from util.constants import WATCHLIST, WATCHLIST_EMBED, WL_EMOJIS
from util.dataclasses import WatchListEntry
from util.helper_functions import generate_wl_page, sort_wl_entries, clamp_wl_page


SUCCESSFUL_ADD = Embed(description="Successfully added the entry to the watchlist.", color = Color.green())
FAILED_ADD = Embed(description="Failed to add that entry. Please make sure the curent season is between 1 and the total, and all season fields are numbers", color = Color.red())
DUPLICATE_ADD = Embed(description="Failed to add that entry, as it is already on the watchlist.", color = Color.red())
OUT_OF_BOUNDS = Embed(description="❌ Cannot flip to the requested page. This is because the page is out of bounds.")
DIRECTIONS = {"left" : "◀", "right" : "▶"}

# Discord allows at most 25 options in a dropdown, so longer watchlists are picked from a page at a time
OPTION_LIMIT = 25
STATUS_NAMES = [-1, "Not started", "Watching", "Finished"]
ENTRY_GONE = "That entry is no longer on the watchlist."
ENTRY_CHANGED = "Somebody else changed that entry at the same moment. Here is how it looks now."


def load_watchlist(guild_id):
    """Get this server's (entries, current page)"""
    wl = WATCHLIST.find_one({"_id" : guild_id}) or {}
    entries = wl.get("entries", [])

    # Entries are told apart by an id, which the ones added before ids existed need giving
    if any("id" not in entry for entry in entries):
        for entry in entries:
            entry.setdefault("id", uuid4().hex)
        WATCHLIST.update_one({"_id" : guild_id}, {"$set" : {"entries" : entries}})

    return entries, wl.get("current_page", 1)

def find_entry(entries, entry_id):
    return next((entry for entry in entries if entry["id"] == entry_id), None)

def describe_entry(entry):
    if entry["curr"] == None:
        return f"🎥 **{entry['name']}** {WL_EMOJIS[entry['status']]}"
    return f"📺 **{entry['name']}** (Season {entry['curr']}/{entry['total']}) {WL_EMOJIS[entry['status']]}"

def upgraded(entry):
    """Get the (status, season) an entry moves on to, None if it is already finished"""
    status, curr, total = entry["status"], entry["curr"], entry["total"]
    if curr == None: # if it's a movie
        return (3, None) if status != 3 else None
    if status == 1:
        return (2, curr)
    if status == 2:
        return (1, curr + 1) if curr < total else (3, curr) # if theres more seasons
    return None

def downgraded(entry):
    """Get the (status, season) an entry moves back to, undoing an upgrade. None if it is at the very start"""
    status, curr = entry["status"], entry["curr"]
    if curr == None:
        return (1, None) if status != 1 else None
    if status == 3:
        return (2, curr)
    if status == 2:
        return (1, curr)
    return (2, curr - 1) if curr > 1 else None

def move_entry(guild_id, entry, new):
    """Move an entry to a new (status, season). Only goes through if nobody changed the entry since it was read"""
    status, curr = new
    result = WATCHLIST.update_one(
        {"_id" : guild_id, "entries" : {"$elemMatch" : {"id" : entry["id"], "status" : entry["status"], "curr" : entry["curr"]}}},
        {"$set" : {"entries.$.status" : status, "entries.$.curr" : curr}})
    return result.modified_count == 1

async def refresh_watchlist(message, guild_id):
    """Redraw the watchlist message from what is stored, so it can never drift from it"""
    entries, current_page = load_watchlist(guild_id)
    page = clamp_wl_page(current_page, entries)
    if page != current_page:
        WATCHLIST.update_one({"_id" : guild_id}, {"$set" : {"current_page" : page}})
    await message.edit(embed=generate_wl_page(page, entries))

async def add_entry(interaction, message, entry):
    guild_id = interaction.guild.id
    entries, current_page = load_watchlist(guild_id)
    if any(existing["name"].lower() == entry.name.lower() for existing in entries):
        await interaction.response.edit_message(embed=DUPLICATE_ADD, view=None)
        return

    WATCHLIST.update_one(
        {"_id" : guild_id},
        {"$push" : {"entries" : entry.__dict__}, "$setOnInsert" : {"current_page" : 1}},
        upsert=True)
    await refresh_watchlist(message, guild_id)
    await interaction.response.edit_message(embed=SUCCESSFUL_ADD, view=None)


# WatchList
class WatchListView(View): # Creates the Message with control buttons and current watch list
    def __init__(self, client, message):
        super().__init__(timeout=None)
        self.client = client
        self.message = message
        self.init_view()

    def init_view(self):
        client = self.client
        self.add_item(AddEntryButton(client))
        self.add_item(ModifyEntryButton(client))
        self.add_item(DirectionButton(client,"left"))
        self.add_item(DirectionButton(client, "right"))


class AddEntryButton(Button):
    def __init__(self, client):
        super().__init__(label="➕", style = discord.ButtonStyle.green, custom_id="AddEntry")
        self.client = client
    async def callback(self, interaction):
        view = View()
        view.message = interaction.message
        view.add_item(AddEntryDropDown(self.client))
        await interaction.response.send_message(
            view=view,
            ephemeral=True)

class AddEntryDropDown(Select):
    def __init__(self, client):
        super().__init__(placeholder="Chose what type of entry you wish to add", min_values=1, max_values=1)
        self.client = client
        self.options = [
            SelectOption(label="📺 TV Show"),
            SelectOption(label="🎥 Movie")
        ]

    async def callback(self, interaction):
        choice = self.values[0]
        if choice == "📺 TV Show":
            await interaction.response.send_modal(ShowModal(self.client, self.view.message))
        else:
            await interaction.response.send_modal(MovieModal(self.client, self.view.message))


# Editing entries. One private prompt: pick an entry, then upgrade it, undo an upgrade, or delete it
class ModifyEntryButton(Button):
    def __init__(self, client):
        super().__init__(label="📂", style = discord.ButtonStyle.green, custom_id="ModifyEntry")
        self.client = client
    async def callback(self, interaction):
        await show_entry_picker(interaction, interaction.message, new_message=True)

async def show_entry_picker(interaction, message, page=0, note=None, new_message=False):
    entries, current_page = load_watchlist(interaction.guild.id)
    description = "📂 Select the entry you would like to edit." if entries else "The watchlist is empty."
    view = EntryPickerView(message, entries, page)
    if view.page_count > 1:
        start = view.page * OPTION_LIMIT
        description += f"\nShowing entries {start + 1}-{min(start + OPTION_LIMIT, len(entries))} of {len(entries)}, use the arrows for more."
    if note:
        description = f"{note}\n\n{description}"

    embed = Embed(description=description)
    if new_message:
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    else:
        await interaction.response.edit_message(embed=embed, view=view)

class EntrySelect(Select):
    def __init__(self, entries):
        options = [
            SelectOption(
                label="{} {}".format("🎥" if entry["curr"] == None else "📺", entry["name"]),
                value=entry["id"],
                description=STATUS_NAMES[entry["status"]] if entry["curr"] == None
                    else f"Season {entry['curr']}/{entry['total']} - {STATUS_NAMES[entry['status']]}")
            for entry in entries
        ]
        super().__init__(placeholder="Select an entry", min_values=1, max_values=1, options=options)

    async def callback(self, interaction):
        await show_entry(interaction, self.view.message, self.values[0])

class EntryPickerView(View):
    def __init__(self, message, entries, page):
        super().__init__()
        self.message = message
        entries = sort_wl_entries(entries)
        self.page_count = max(1, ceil(len(entries) / OPTION_LIMIT))
        self.page = min(max(page, 0), self.page_count - 1)
        start = self.page * OPTION_LIMIT
        if entries:
            self.add_item(EntrySelect(entries[start:start + OPTION_LIMIT]))

        # Only needed once there are more entries than fit in one dropdown
        if self.page_count == 1:
            self.remove_item(self.previous_page)
            self.remove_item(self.next_page)
        else:
            self.previous_page.disabled = self.page == 0
            self.next_page.disabled = self.page == self.page_count - 1

    @discord.ui.button(emoji="◀", style=ButtonStyle.gray, row=1)
    async def previous_page(self, interaction, button):
        await show_entry_picker(interaction, self.message, self.page - 1)

    @discord.ui.button(emoji="▶", style=ButtonStyle.gray, row=1)
    async def next_page(self, interaction, button):
        await show_entry_picker(interaction, self.message, self.page + 1)

async def show_entry(interaction, message, entry_id, note=None):
    entries, current_page = load_watchlist(interaction.guild.id)
    entry = find_entry(entries, entry_id)
    if entry is None:
        await show_entry_picker(interaction, message, note=ENTRY_GONE)
        return

    description = describe_entry(entry)
    if note:
        description = f"{note}\n\n{description}"
    await interaction.response.edit_message(embed=Embed(description=description), view=EntryView(message, entry))

class EntryView(View):
    """The actions available for one entry of the watchlist"""
    def __init__(self, message, entry):
        super().__init__()
        self.message = message
        self.entry_id = entry["id"]
        self.upgrade.disabled = upgraded(entry) is None
        self.undo.disabled = downgraded(entry) is None

    async def move(self, interaction, get_new):
        guild_id = interaction.guild.id
        entry = find_entry(load_watchlist(guild_id)[0], self.entry_id)
        new = get_new(entry) if entry else None
        note = None
        if new is not None:
            if move_entry(guild_id, entry, new):
                await refresh_watchlist(self.message, guild_id)
            else:
                note = ENTRY_CHANGED

        await show_entry(interaction, self.message, self.entry_id, note)

    @discord.ui.button(label="Upgrade", emoji="📈", style=ButtonStyle.green)
    async def upgrade(self, interaction, button):
        await self.move(interaction, upgraded)

    @discord.ui.button(label="Undo", emoji="↩️", style=ButtonStyle.gray)
    async def undo(self, interaction, button):
        await self.move(interaction, downgraded)

    @discord.ui.button(label="Delete", emoji="❌", style=ButtonStyle.red)
    async def delete(self, interaction, button):
        guild_id = interaction.guild.id
        entry = find_entry(load_watchlist(guild_id)[0], self.entry_id)
        if entry is None:
            await show_entry_picker(interaction, self.message, note=ENTRY_GONE)
            return

        WATCHLIST.update_one({"_id" : guild_id}, {"$pull" : {"entries" : {"id" : self.entry_id}}})
        await refresh_watchlist(self.message, guild_id)
        await show_entry_picker(interaction, self.message, note=f"✅ Deleted **{entry['name']}**.")

    @discord.ui.button(label="Back", style=ButtonStyle.blurple)
    async def back(self, interaction, button):
        await show_entry_picker(interaction, self.message)


class ShowModal(Modal):

    def __init__(self, client, message):
        super().__init__(title="Show Addition")
        self.client = client
        self.message = message
        self.fields = [
            TextInput(label="What is the name of the show?", min_length=1, max_length=32, required=True),
            TextInput(label="How many seasons are there?", min_length=1, max_length=3, required=True),
            TextInput(label = "What season are you currently on? (default 1)", min_length=1,max_length=3, required=False, default="1")
        ]
        for i in self.fields: self.add_item(i)

    async def on_submit(self, interaction):
        show = self.fields[0].value.strip()
        try:
            seasons = int(self.fields[1].value)
            current = int(self.fields[2].value or 1)
        except ValueError:
            seasons = current = 0

        if not show or not 1 <= current <= seasons:
            await interaction.response.edit_message(embed=FAILED_ADD, view=None)
            return

        toInsert = WatchListEntry(
            name = show,
            status = 1,
            curr= current,
            total= seasons
        )
        await add_entry(interaction, self.message, toInsert)

class MovieModal(Modal):

    def __init__(self, client, message):
        super().__init__(title="Movie Addition")
        self.message = message
        self.client = client
        self.inp = TextInput(label="What is the name of the movie?", min_length=1, max_length=32, required=True)
        self.add_item(self.inp)

    async def on_submit(self, interaction):
        movie = self.inp.value.strip()
        if not movie:
            await interaction.response.edit_message(embed=FAILED_ADD, view=None)
            return

        await add_entry(interaction, self.message, WatchListEntry(name = movie))


class DirectionButton(Button):
    def __init__(self, client, mode):
        self.mode = mode
        self.client = client
        super().__init__(label=f"{DIRECTIONS[self.mode]}", style = discord.ButtonStyle.green, custom_id=mode)

    async def callback(self, interaction):
        guild_id = interaction.guild.id
        entries, current_page = load_watchlist(guild_id)
        page = clamp_wl_page(current_page + (-1 if self.mode == "left" else 1), entries)
        if page == clamp_wl_page(current_page, entries):
            await interaction.response.send_message(embed=OUT_OF_BOUNDS, ephemeral=True)
            return

        WATCHLIST.update_one({"_id" : guild_id}, {"$set" : {"current_page" : page}})
        await interaction.response.edit_message(embed=generate_wl_page(page, entries))
