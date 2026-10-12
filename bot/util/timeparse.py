import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import dateparser

UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
UNIT_NAMES = {
    "s": "s", "sec": "s", "secs": "s", "second": "s", "seconds": "s",
    "m": "m", "min": "m", "mins": "m", "minute": "m", "minutes": "m",
    "h": "h", "hr": "h", "hrs": "h", "hour": "h", "hours": "h",
    "d": "d", "day": "d", "days": "d",
    "w": "w", "wk": "w", "wks": "w", "week": "w", "weeks": "w",
}
DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)\s*([a-z]+)")
DURATION = re.compile(r"(?:\s*\d+(?:\.\d+)?\s*[a-z]+\s*(?:,|and)?)+")

DEFAULT_HOUR = 9  # What a day given without a time ("friday") is taken to mean, rather than midnight
MIDNIGHT_WORDS = ("midnight", "12am", "12 am", "00:00", "0:00")
PAST_WORDS = ("yesterday", "ago", "last ")


def parse_duration(text):
    """Read a length of time such as "10m", "2h30m" or "1 week and 2 days". None if it is not one"""
    text = text.strip().lower()
    if text.startswith("in "):
        text = text[3:]
    if not DURATION.fullmatch(text):
        return None

    seconds = 0
    for amount, unit in DURATION_PART.findall(text):
        if unit not in UNIT_NAMES:
            return None
        seconds += float(amount) * UNIT_SECONDS[UNIT_NAMES[unit]]
    return timedelta(seconds=round(seconds))


def parse_when(text, timezone_name, now=None):
    """Work out the moment a user means, as a stored (UTC) time. None if it cannot be understood.

    Lengths of time are counted from now. Anything else ("tomorrow 9am", "friday 17:00",
    "dec 25 6pm") is read as a time on the clock in the given timezone."""
    now = now or datetime.utcnow()
    duration = parse_duration(text)
    if duration is not None:
        return now + duration

    cleaned = " ".join(text.strip().lower().split())
    if not cleaned or cleaned.replace(" ", "").isdigit():
        return None  # A bare number is not a time, though the parser would happily make one of it

    # Phrasings the parser trips over, put into ones that it does not
    for prefix in ("on ", "at ", "next "):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
    cleaned = cleaned.replace(" at ", " ").replace(" next ", " ").replace("tonight", "today")

    local_zone = ZoneInfo(timezone_name)
    local_now = now.replace(tzinfo=timezone.utc).astimezone(local_zone)
    parsed = dateparser.parse(
        cleaned,
        languages=["en"],
        settings={
            "TIMEZONE": timezone_name,
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
            "RELATIVE_BASE": local_now.replace(tzinfo=None),
        },
    )
    if parsed is None:
        return None

    # Pinned to the user's own timezone, whatever offset the parser settled on
    parsed = parsed.replace(tzinfo=None).replace(tzinfo=local_zone)
    if (parsed.hour, parsed.minute, parsed.second) == (0, 0, 0) and not any(
        word in cleaned for word in MIDNIGHT_WORDS
    ):
        parsed = parsed.replace(hour=DEFAULT_HOUR)

    # "9pm" said at 10pm means tomorrow's
    if parsed <= local_now and not any(word in cleaned for word in PAST_WORDS):
        if local_now - parsed < timedelta(days=1):
            parsed += timedelta(days=1)

    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def next_occurrence(due, repeat, timezone_name, now):
    """Get when a repeating reminder is next due: the same time on the clock, a day or week on.

    Worked out on the clock rather than by adding hours, so it stays at 9am when the clocks change.
    Any occurrences that were missed while offline are skipped over, not sent in a burst."""
    step = timedelta(days=1 if repeat == "daily" else 7)
    local_zone = ZoneInfo(timezone_name)
    local = due.replace(tzinfo=timezone.utc).astimezone(local_zone).replace(tzinfo=None)
    while True:
        local += step
        upcoming = local.replace(tzinfo=local_zone).astimezone(timezone.utc).replace(tzinfo=None)
        if upcoming > now:
            return upcoming
