from util.constants import VC_EVENTS, EVENT_ARCHIVE_DIR, cluster
import os
from datetime import datetime
import json
from bson import ObjectId
from discord import Embed, Color
from util.classes import FMUser
from util.constants import USERS

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
        return

    fm_obj = FMUser(fm_username)

    # Username does not exist
    if not fm_obj.is_valid():
        return
    
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







