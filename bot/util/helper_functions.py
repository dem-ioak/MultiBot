import asyncio
from datetime import datetime, timezone
from discord import Embed, Color
from math import ceil

from util.constants import BOARDS, WATCHLIST_EMBED, WL_EMOJIS
from util.dataclasses import User


async def run_db(func, *args, **kwargs):
    """Run a blocking database call off of the event loop"""
    return await asyncio.to_thread(func, *args, **kwargs)

def user_defaults(*exclude):
    """Default fields of a new user, for use in $setOnInsert. Exclude fields the same update already writes"""
    defaults = User(_id=None).__dict__
    del defaults["_id"]
    for field in exclude:
        del defaults[field]
    return defaults

def leveled_up(current_xp, current_level):
    new_level = int(current_xp** (1/2.5))
    return new_level > current_level

def board_page_count(guild_id):
    server_boards = BOARDS.find_one({"_id" : guild_id})
    num_boards = len(server_boards["boards"]) if server_boards else 0
    return max(1, ceil(num_boards / 5))

def board_view_description(guild_id, page):
    start = (page - 1) * 5
    server_boards = BOARDS.find_one({"_id" : guild_id})
    boards = server_boards["boards"] if server_boards else []
    num_boards = len(boards)
    if num_boards == 0:
        return "There are no boards yet. Use ➕ to create one."

    desciption = ""
    for i in range(start, start + 5):
        if i >= num_boards:
            break
        
        rank = (i % 5) + 1
        name = boards[i]["name"]
        desciption += f"`{rank}` **{name}**\n"

    return desciption

def board_players(info):
    """Get a board's (user_id, score) pairs, highest score first"""
    # Boards made back when there was a cursor still hold a (-1, -1) placeholder row
    players = [(str(user_id), score) for user_id, score in info["scores"] if str(user_id) != "-1"]
    return sorted(players, key = lambda x : x[1], reverse = True)

def board_info_embed(info, guild_members):

    name = info["name"]
    last_edited_time = info["last_edited_time"]
    last_edited_user = info["last_edited_user"]
    embed = Embed(title = name, color = Color.red())
    description = ""
    id_to_member = {user.id : user for user in guild_members}

    for rank, (user_id, score) in enumerate(board_players(info)):
        prefix = "👑" if rank == 0 else str(rank + 1)
        description += f"`{prefix}` <@{user_id}> - {score}\n"

    if not description:
        description = "Nobody is on this board yet. Use 👤 to add players."

    footer_text = "Last edit by "
    footer_text += id_to_member[last_edited_user].name if last_edited_user in id_to_member else "Unknown"
    embed.description = description
    embed.set_footer(text = footer_text)
    if isinstance(last_edited_time, datetime):
        # Stored in UTC. As the embed's timestamp, Discord shows it in each viewer's own timezone
        embed.timestamp = last_edited_time.replace(tzinfo = timezone.utc)
    return embed


def sort_dict(dictionary):
    sorted_dict = dict(sorted(dictionary.items(), key=lambda item: (item[1], item[0]), reverse=True))
    return sorted_dict

def parse_id(x):
    return int(x[len(x)-18::])

def sort_wl_entries(entries):
    """Order entries the way the watchlist shows them: by status, then alphabetically"""
    return sorted(entries, key = lambda x : (x["status"], x["name"].lower()))

def clamp_wl_page(page, entries):
    """Get the nearest page that exists, as deleting entries can leave the current one past the end"""
    page_count = max(1, ceil(len(entries)/10))
    return min(max(page, 1), page_count)

def generate_wl_page(page, entries):
    if len(entries) == 0:
        return WATCHLIST_EMBED
    entries = sort_wl_entries(entries)
    page = clamp_wl_page(page, entries)
    start = 10 * (page - 1)
    page_count = ceil(len(entries)/10)
    ind = start
    desc = ""
    title = "Watch List (Page {}/{})".format(page, page_count)
    for entry in entries[start:start + 10]:
        name, status, curr, total = entry["name"], entry["status"], entry["curr"], entry["total"]
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







