from util.constants import VC_EVENTS, EVENT_ARCHIVE_DIR, cluster
import os
from datetime import datetime
import json
from bson import ObjectId

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

def json_extract(obj, key, val):
    """Recursively fetch values from nested JSON."""
    arr = []

    def extract(obj, arr, key, val):
        """Recursively search for values of key in JSON tree."""
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, (dict, list)):
                    extract(v, arr, key, val)
                elif k == key:
                    try:
                        if obj["artist"]["name"].lower() == val:              
                            arr.append((obj["name"], obj["playcount"]))
                    except KeyError:
                        continue
        elif isinstance(obj, list):
            for item in obj:
                extract(item, arr, key, val)
        return arr

    values = extract(obj, arr, key, val)
    return values





    


