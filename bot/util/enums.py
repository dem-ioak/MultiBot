from enum import Enum

class EventType(Enum):
    JOIN_VC = 0
    LEAVE_VC = 1
    JOIN_AFK = 2
    LEAVE_AFK = 3
    NAME_VC = 4
    START_STREAM = 5
    END_STREAM = 6



class BoardUpdates(Enum):
    INCREMENT_SCORE = 1
    DECREMENT_SCORE = 2
    CREATE_BOARD = 3
    ADD_PLAYER = 4
    REMOVE_PLAYER = 5