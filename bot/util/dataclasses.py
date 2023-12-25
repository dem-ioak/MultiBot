from dataclasses import dataclass
from datetime import datetime

from util.enums import EventType

@dataclass
class User:
    # Server Members
    _id : dict
    xp : int = 0
    level : int = 1
    birthday : datetime = None
    banished_id : int = -1
    is_warned : bool = False
    last_fm : str = None
    last_active : datetime = None


@dataclass
class WrappedUser:
    # Data for Wrapped
    _id : dict
    user_pings : dict

    message_count : int = 0
    word_count : int = 0
    image_count : int = 0
    gif_count : int = 0
    everyone_pings : int = 0

    # polls_voted_on : list
    # vc_names : list
    # vcs_created : int = 0
    # vcs_joined : int = 0
    # stream_count : int = 0
    # time_spent_vc : float = 0
    # time_spent_afk : float = 0
    # time_spent_streaming : float = 0
    # afk_count : int = 0
    # current_vc : bool = False
    # current_afk : bool = False
    # current_stream : bool = False

@dataclass
class VChannel:
    _id : int
    owner : int
    denied : list
    allowed : list
    hidden : list
    is_locked : bool

    created_at : datetime
    deleted_at : datetime = None
    
@dataclass
class TChannel:
    _id : int
    created_at : datetime
    deleted_author : int = -1
    deleted_content : str = ""
    edited_author : int = -1
    edited_content : str = ""
    deleted_at : datetime = None

@dataclass
class Server:
    _id : int
    auto_roles : list
    vibe_gifs : list
    banished_id : int = -1

    vibe_id : int = -1
    logs_id : int = -1
    polls_id : int = -1
    boards_id : int = -1
    join_to_create : int = -1
    afk_corner : int = -1

    vibes : bool = True
    auto_chop : bool = False
    levels : bool = True

@dataclass
class WatchList:
    _id : int
    message_id : int
    channel_id : int
    entries : list
    current_page : int

@dataclass
class WatchListEntry:
    name : str
    status : int = 1
    curr : int = None
    total : int = None

@dataclass
class VCEvent:
    guild_id : int
    user_id : int
    timestamp : datetime
    event_type : EventType
    channel_id : int
