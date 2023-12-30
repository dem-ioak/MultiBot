import discord
from discord import SelectOption, Embed, Color, ButtonStyle
from discord.ext import commands
from discord.ui import Button, View, TextInput, Modal, Select
from math import ceil

from util.constants import WATCHLIST
from util.dataclasses import WatchListEntry

WL_EMOJIS = [-1, "❌", "⏱️", "✅"]
WATCHLIST_EMBED = Embed(title="WatchList")

WATCHLIST_EMBED.set_footer(text="Use the provided buttons below to make edits to existing watch list entries, or to add and remove listings.")
SUCCESSFUL_ADD = Embed(description="Successfully added the entry to the watchlist.", color = Color.green())
FAILED_ADD = Embed(description="Failed to add that entry. Please make sure the curent season is less than the total, and all season fields are numbers", color = Color.red())
SUCCESSFUL_EDIT = Embed(description="Successfully edited/deleted this entry of the watchlist.", color = Color.yellow())
OUT_OF_BOUNDS = Embed(description="❌ Cannot flip to the requested page. This is because the page is out of bounds.")
DIRECTIONS = {"left" : "◀", "right" : "▶"}

def generate_wl_page(page, entries):
    if len(entries) == 0:
        return WATCHLIST_EMBED
    start = 10 * (page - 1)
    page_count = ceil(len(entries)/10)
    end = start + 10 if page != page_count else len(entries)
    ind = start
    desc = ""
    title = "Watch List (Page {}/{})".format(page, page_count)
    for entry in entries[start:end]:
        name, status, curr, total = entry.values()
        if curr == None:
            desc += f"`{ind+1}` " + f"🎥 **{name}** {WL_EMOJIS[status]}" + "\n"
        else:
            desc += f"`{ind+1}` " + f"**📺 {name}** (Season {curr}/{total}) {WL_EMOJIS[status]}" + "\n"
        ind +=1
    res = Embed(
        title = title,
        description = desc.strip(),
        color = Color.gold()
    )
    res.set_footer(text="Use the provided buttons below to make edits to existing watch list entries, or to add and remove listings.")
    return res

def upgrade_wl_entry(entries, ind):
    if not is_upgradeable(entries, ind): 
        return entries
    name, status, curr, total = entries[ind].values()
    if curr == None: # if it's a movie
        entries[ind]["status"] = 3
    else:
        if status == 2: 
            if curr < total: # if theres more seasons
                entries[ind]["status"] = 1
                entries[ind]["curr"] += 1
            else:
                entries[ind]["status"] += 1
        else:
            entries[ind]["status"] +=1
    entries.sort(key=lambda x: (x["status"], x["name"]))
    return entries

def delete_wl_entry(entries, ind):
    if ind < 0 or ind >= len(entries):
        return (False, entries)
    entries.remove(entries[ind])
    entries.sort(key=lambda x: (x["status"], x["name"]))
    return entries

def is_upgradeable(entries, ind):
    name, status, curr, total = entries[ind].values()
    if curr == None:
        return status != 3
    else:
        return not (curr == total and status == 3)

def update_watchlist(guild_id, entries):
    WATCHLIST.update_one({"_id" : guild_id}, {"$set" : {"entries" : entries}})

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
        view.message = self.view.message
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

class ModifyEntryButton(Button):
    def __init__(self, client):
        super().__init__(label="📂", style = discord.ButtonStyle.green, custom_id="ModifyEntry")
        self.client = client
    async def callback(self, interaction):
        view = View()
        view.message = self.view.message
        dropdown = Select(min_values=1, max_values=1, placeholder="How would you like to edit existing entries?")
        dropdown.options = [
            SelectOption(label="📈 Upgrade"),
            SelectOption(label="❌ Delete")
        ]
        
        async def dropdown_callback(interaction):
            choice = dropdown.values[0]
            if choice == "📈 Upgrade": mode = "upgrade"
            else: mode = "delete"
            view = View()
            view.message = self.view.message
            view.add_item(
                ModifyEntryDropdown(self.client, interaction.guild, mode)
            )
            await interaction.response.send_message(view=view, ephemeral=True)
        
        dropdown.callback=dropdown_callback
        view.add_item(dropdown)
        await interaction.response.send_message(view=view, ephemeral=True)

class ModifyEntryDropdown(Select):

    def __init__(self, client, guild, mode):
        super().__init__(placeholder=f"Select an entry to {mode}", min_values=1, max_values=1)
        self.client = client
        self.guild_id = guild.id
        self.options = []
        self.mode = mode
        self.wl = WATCHLIST.find_one({"_id" : guild.id})
        self.entries = self.wl["entries"]
        for x, i in enumerate(self.entries):
            if is_upgradeable(self.entries, x):
                self.options.append(
                    (SelectOption(
                        label="{} {} {}".format(x, "🎥" if i["curr"] == None else "📺", i["name"]))))
        if len(self.options) == 0:
            self.options.append(SelectOption(label="There are no modifiable entries!"))
        
    async def callback(self, interaction):
        guild_id = interaction.guild.id
        try:
            choice = ""
            for i in self.values[0]:
                try:
                    x = int(i)
                    choice += i
                except ValueError: break
                
            choice = int(choice)
            wl = WATCHLIST.find_one({"_id" : guild_id})
        
            entries = wl["entries"]
            current_page = wl["current_page"]
            if self.mode == "upgrade":
                entries = upgrade_wl_entry(entries, choice)
            else:
                entries = delete_wl_entry(entries, choice)

            embed = generate_wl_page(current_page, entries)
            update_watchlist(guild_id, entries)
            await self.view.message.edit(embed=embed)
            await interaction.response.edit_message(embed=SUCCESSFUL_EDIT, view=None)
        except ValueError:
            await interaction.response.defer()

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
        try:
            show = self.fields[0].value
            seasons = int(self.fields[1].value)
            current = int(self.fields[2].value)
            if seasons < current:
                await interaction.response.edit_message(embed=FAILED_ADD, view=None)
                return

            guild_id = interaction.guild.id
            wl = WATCHLIST.find_one({"_id" : guild_id})
            entries = wl["entries"]
            toInsert = WatchListEntry(
                name = show,
                status = 1,
                curr= current,
                total= seasons
            )
            entries.append(toInsert.__dict__)
            entries.sort(key = lambda x : (x["status"], x["name"]))
            embed = generate_wl_page(1, entries)

            update_watchlist(guild_id, entries)
            await self.message.edit(embed=embed)
            await interaction.response.edit_message(embed=SUCCESSFUL_ADD, view=None)
        except Exception as e:
            await interaction.response.edit_message(embed=FAILED_ADD, view=None)

class MovieModal(Modal):

    def __init__(self, client, message):
        super().__init__(title="Movie Addition")
        self.message = message
        self.client = client
        self.inp = TextInput(label="What is the name of the movie?", min_length=1, max_length=32, required=True)
        self.add_item(self.inp)
    
    async def on_submit(self, interaction):
        try:
            movie = self.inp.value
            guild_id = interaction.guild.id
            wl = WATCHLIST.find_one({"_id" : guild_id})
            current_page = wl["current_page"]
            entries = wl["entries"]

            toInsert = WatchListEntry(name = movie)
            entries.append(toInsert.__dict__)
            embed = generate_wl_page(current_page, entries)
            entries.sort(key = lambda x : (x["status"], x["name"]))
            await self.message.edit(embed=embed)
            await interaction.response.edit_message(embed=SUCCESSFUL_ADD, view=None)
            update_watchlist(guild_id, entries)
        except Exception as e:
            print(e)
            await interaction.response.edit_message(embed=FAILED_ADD, view=None)
        
    
class DirectionButton(Button):
    def __init__(self, client, mode):
        self.mode = mode
        self.client = client
        super().__init__(label=f"{DIRECTIONS[self.mode]}", style = discord.ButtonStyle.green, custom_id=mode)
    
    async def callback(self, interaction):
        guild_id = interaction.guild.id
        wl = WATCHLIST.find_one({"_id" : guild_id})
        current_page = wl["current_page"]
        entries = wl["entries"]
        if self.mode == "left":
            if current_page == 1:
                await interaction.response.send_message(embed=OUT_OF_BOUNDS, ephemeral=True)
                return
            current_page -=1
        else:
            if current_page + 1 > ceil(len(entries)/10):
                await interaction.response.send_message(embed=OUT_OF_BOUNDS, ephemeral=True)
                return
            current_page +=1
        
        WATCHLIST.update_one(wl, {"$set" : {"current_page" : current_page}})
        await interaction.response.edit_message(embed=generate_wl_page(current_page, entries))