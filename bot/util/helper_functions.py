from util.constants import VC_EVENTS, EVENT_ARCHIVE_DIR, cluster
import os
from datetime import datetime
import json
from bson import ObjectId
from discord import Embed, Color
from math import ceil

from util.classes.FMUser import FMUser
from util.constants import USERS, BOARDS, WATCHLIST_EMBED, WL_EMOJIS



class JSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (ObjectId, datetime)):
            return str(obj)
        return super().default(obj)

def leveled_up(current_xp, current_level):
    new_level = int(current_xp** (1/2.5))
    return new_level > current_level

def archive_event_data():
    all_events = VC_EVENTS.find()
    event_list = list(all_events)
    dir_files = os.listdir(EVENT_ARCHIVE_DIR)
    sorted_names = sorted(dir_files)
    suffix = "_archive.json"
    filename = ("0" if not dir_files else sorted_names[-1][0]) + suffix

    with open(EVENT_ARCHIVE_DIR + filename, "w") as f:
        json.dump(event_list, f, indent = 2, cls = JSONEncoder)
    
    VC_EVENTS.delete_many({})

def get_size_and_limit():
    coll_stats = cluster.command("collstats", "test_vcevents")
    limit, size = coll_stats["storageSize"], coll_stats["size"]
    return limit, size

def handle_get_top_call(interaction, target, time, mode):
    """Handle logic to create embeds for get_top_x calls"""
    target = target if target else interaction.user
    time = time.value if time else "Overall"
    guild_id = interaction.guild.id
    user_id = target.id
    primary_key = {"guild_id" : guild_id, "user_id" : user_id}
    user_data = USERS.find_one({"_id" : primary_key})
    fm_username = user_data["last_fm"]

    # Username does not set
    if not fm_username:
        return Embed(color = Color.red(), description = "You do not have a LastFM account setup. Use /lastfm set to set your username.")

    fm_obj = FMUser(fm_username)

    # Username does not exist
    if not fm_obj.is_valid():
        return Embed(color = Color.red(), description = f"**{fm_username}** is not a valid username according to LastFM. Use /lastfm set to set your username.")
    
    funcs = {
        "artists" : fm_obj.get_top_artists,
        "albums" : fm_obj.get_top_albums,
        "tracks" : fm_obj.get_top_tracks
    }

    description = funcs[mode](time)

    # No data received
    if not description:
        return
    
    embed = Embed(
        color = Color.red(), 
        title = f"{target.name}'s Top {time} Artists",
        description = description)
    embed.set_author(name = interaction.user.name, icon_url= interaction.user.avatar.url)
    return embed

def board_view_description(guild_id, page):
    start = (page - 1) * 5
    server_boards = BOARDS.find_one({"_id" : guild_id})
    boards = server_boards["boards"]
    num_boards = len(boards)
    desciption = ""
    for i in range(start, start + 5):
        if i >= num_boards:
            break
        
        rank = (i % 5) + 1
        name = boards[i]["name"]
        desciption += f"`{rank}` **{name}**\n"

    return desciption

def board_info_embed(info, guild_members):

    name = info["name"]
    cursor = info["cursor"]
    
    scores = info["scores"]

    last_edited_time = info["last_edited_time"]
    last_edited_user = info["last_edited_user"]
    player_count = len(scores) - 1
    embed = Embed(title = name, color = Color.red())
    description = ""
    id_to_member = {user.id : user for user in guild_members}

    for rank, user in enumerate(scores):
        prefix = "👑" if rank == 0 else str(rank + 1)
        to_add = ""
        user_id, score = user
        user_id = int(user_id)
        if user_id not in id_to_member:
            continue

        member_obj = id_to_member[user_id]
        to_add += f"`{prefix}` {member_obj.mention} - {score}"
        if rank == cursor:
            to_add += " ⬅️"
        
        description += to_add + "\n"
    
    description += "`👤` **Add/Remove a player!**"
    if cursor == player_count:
        description += " ⬅️"

    footer_text = f"Last edit at {last_edited_time} by "
    footer_text += id_to_member[last_edited_user].name if last_edited_user in id_to_member else "Unknown"
    embed.description = description
    embed.set_footer(text = footer_text)
    return embed


def sort_dict(dictionary):
    sorted_dict = dict(sorted(dictionary.items(), key=lambda item: (item[1], item[0]), reverse=True))
    return sorted_dict

def parse_id(x):
    return int(x[len(x)-18::])

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







