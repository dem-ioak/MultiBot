from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

# Kept free of any other imports from the bot, so that scripts can use it without starting a database connection
EASTERN = ZoneInfo("America/New_York")

# A session this long with no end in sight is taken to have lost its end, rather than still be going
MAX_SESSION_HOURS = 24


def end_of_day(moment):
    """Get the last moment of the (Eastern) day a stored UTC time falls on, also as a stored UTC time.

    This is when a VC session or stream whose end was never recorded is taken to have ended: the
    real end is unknown, but it is far more likely to be that same day than whenever it was noticed."""
    local = moment.replace(tzinfo=timezone.utc).astimezone(EASTERN)
    last = local.replace(hour=23, minute=59, second=59, microsecond=999000)
    return last.astimezone(timezone.utc).replace(tzinfo=None)


def unrecorded_end(start, known_by):
    """When to end something that started at `start`, given that it was certainly over by `known_by`"""
    return max(start, min(end_of_day(start), known_by))


def is_stale(start, now):
    return now - start > timedelta(hours=MAX_SESSION_HOURS)
