from dataclasses import dataclass
from datetime import datetime
from enum import Enum

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
    _id : int
    user_pings : dict
    vc_names : list
    polls_voted_on : list
    message_count : int = 0
    word_count : int = 0
    image_count : int = 0
    gif_count : int = 0
    everyone_pings : int = 0

    vcs_created : int = 0
    vcs_joined : int = 0
    stream_count : int = 0
    time_spent_vc : float = 0
    time_spent_afk : float = 0
    time_spent_streaming : float = 0
    afk_count : int = 0
    current_vc : bool = False
    current_afk : bool = False
    current_stream : bool = False

@dataclass
class VChannel:
    _id : int
    owner : int
    denied : list
    allowed : list
    hidden : list
    is_locked : bool

@dataclass
class TChannel:
    _id : int
    deleted_author : int = -1
    deleted_content : str = ""
    edited_author : int = -1
    edited_content : str = ""

@dataclass
class Server:
    _id : int
    auto_roles : list
    vibe_gifs : list
    toc : dict
    banished_id : int = -1

    vibe_id : int = -1
    logs_id : int = -1
    polls_id : int = -1
    boards_id : int = -1
    vc_commands_id : int = -1

    vc_setup : bool = False
    vc_create_id : int = -1
    vc_category_id : int = -1
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




