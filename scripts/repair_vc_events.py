"""Find and repair one sided VC events (a join with no leave, a stream end with no start, ...).

    python scripts/repair_vc_events.py                          # report only, changes nothing
    python scripts/repair_vc_events.py --apply                  # back the collection up, then repair it
    python scripts/repair_vc_events.py --complete               # the database holds every event ever recorded
    python scripts/repair_vc_events.py --import-archives DIR    # also bring archived events back in

Events the bot never saw cannot be recovered, so each repair adds the missing half of the pair:

- A session or stream with no end is ended at the end of the (Eastern) day it started on, or just
  before the user's next event if that comes sooner. It is never ended "now": a join from months
  ago that lost its leave would otherwise become a session lasting until today.
- A leave with no join gets a join 1ms before it, and a stream end with no start a start 1ms
  before it. How long those really lasted is unknown, so they are zero length rather than a guess.
- A stream end with no start, right after the user changed channel while streaming, gets its
  start at the moment they joined that channel (the stream carried over, the old bot never
  recorded it restarting).
- A leave whose AFK-ness differs from its join is changed to mirror the join.

Every event that is added carries `synthetic: true` and a `repair` reason, so the analysis can
tell them apart.

Two kinds of one sided events are deliberately left alone:

- Sessions that started within the last day and are still open at the very end of the data.
  Those users may well be in voice right now, and the bot ends them itself.
- A leave / stream end that is the first thing a user does in the data, unless the data is known
  to be complete (`--complete`, or `--import-archives`). The bot used to move events out of the
  database into archive files, so the matching join is most likely sitting in one of those.

Running it again is safe. Ends that an earlier run (or the bot) added are worked out afresh, and
only the ones that come out differently are replaced.

`--import-archives` takes the folder of `N_archive.json` files the bot used to move events into
and puts them back into the database (skipping any already there). The data is then complete, so
sessions split across archives pair up by themselves.
"""
import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta

from bson import ObjectId
from dotenv import load_dotenv
from pymongo import MongoClient, UpdateOne

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bot"))
from util.enums import EventType  # noqa: E402
from util.timeutil import end_of_day, is_stale  # noqa: E402

JOIN_VC, LEAVE_VC = EventType.JOIN_VC.value, EventType.LEAVE_VC.value
JOIN_AFK, LEAVE_AFK = EventType.JOIN_AFK.value, EventType.LEAVE_AFK.value
START_STREAM, END_STREAM = EventType.START_STREAM.value, EventType.END_STREAM.value
JOINS, LEAVES = (JOIN_VC, JOIN_AFK), (LEAVE_VC, LEAVE_AFK)
LEAVE_FOR = {JOIN_VC: LEAVE_VC, JOIN_AFK: LEAVE_AFK}
JOIN_FOR = {LEAVE_VC: JOIN_VC, LEAVE_AFK: JOIN_AFK}

MS = timedelta(milliseconds=1)
# How close a stream end and a channel join must be to count as one move
MOVE_WINDOW = timedelta(seconds=2)
# Ends this script works out. They are redone on every run, so a change to how they are timed reaches old ones too
END_REASONS = ("missing_leave", "missing_stream_end", "stale_open")


def sort_key(event):
    return (event["timestamp"], event["_id"])


def group_by_user(events):
    by_user = {}
    for event in sorted(events, key=sort_key):
        by_user.setdefault((event["guild_id"], event["user_id"]), []).append(event)
    return by_user


def find_superseded(events):
    """Find the added ends that should be worked out again: this script's own, and any the bot
    wrote that run past the end of the day their session / stream started on"""
    superseded = []
    for user_events in group_by_user(events).values():
        session = stream = None
        for event in user_events:
            kind = event["event_type"]
            start = session if kind in LEAVES else stream if kind == END_STREAM else None
            if event.get("repair") in END_REASONS:
                superseded.append(event)
            elif event.get("synthetic") and "repair" not in event and start:
                if event["timestamp"] > end_of_day(start["timestamp"]) + 2 * MS:
                    superseded.append(event)

            if kind in JOINS:
                session = event
            elif kind in LEAVES:
                session = None
            elif kind == START_STREAM:
                stream = event
            elif kind == END_STREAM:
                stream = None
    return superseded


def plan_repairs(events, complete):
    """Walk every user's events in order, returning (events to insert, type changes, what was left alone).

    `complete` says whether these are all the events there ever were. If not, a leave or stream end
    with nothing before it is assumed to have its other half in an archive."""
    inserts, retypes, still_open = [], [], Counter()
    data_end = max((event["timestamp"] for event in events), default=None)

    for (guild_id, user_id), user_events in group_by_user(events).items():
        session = stream = last_end = last_seen = None
        seen_session = seen_stream = complete

        def add(event_type, channel_id, timestamp, reason):
            inserts.append(
                {
                    "guild_id": guild_id,
                    "user_id": user_id,
                    "timestamp": timestamp,
                    "event_type": event_type,
                    "channel_id": channel_id,
                    "synthetic": True,
                    "repair": reason,
                }
            )

        def end_unrecorded(closing, before=None):
            """End what was left open, as (event type, the event that opened it, reason) in order.

            Each is ended at the end of the day it started on. That is brought forward to fit before
            the user's next event (`before`), and pushed back past the last time they were seen"""
            previous = None
            for i, (event_type, opened, reason) in enumerate(closing):
                timestamp = max(end_of_day(opened["timestamp"]), last_seen + MS)
                if before:
                    timestamp = max(last_seen, min(timestamp, before - (len(closing) - i) * MS))
                if previous and timestamp <= previous:
                    timestamp = previous + MS
                add(event_type, opened["channel_id"], timestamp, reason)
                previous = timestamp

        for event in user_events:
            now, kind, channel_id = event["timestamp"], event["event_type"], event["channel_id"]
            closing = []  # Ends that are missing for what was open before this event

            if kind == START_STREAM and stream:
                closing.append((END_STREAM, stream, "missing_stream_end"))
                stream = None
            elif kind == END_STREAM and stream and stream["channel_id"] != channel_id:
                closing.append((END_STREAM, stream, "missing_stream_end"))
                stream = None
            elif kind in JOINS and session:
                if stream:
                    closing.append((END_STREAM, stream, "missing_stream_end"))
                    stream = None
                closing.append((LEAVE_FOR[session["event_type"]], session, "missing_leave"))
                session = None
            elif kind in LEAVES:
                if stream:
                    closing.append((END_STREAM, stream, "missing_stream_end"))
                    stream = None
                if session and session["channel_id"] != channel_id:
                    closing.append((LEAVE_FOR[session["event_type"]], session, "missing_leave"))
                    session = None

            end_unrecorded(closing, before=now)

            if kind in JOINS:
                session = event
            elif kind in LEAVES:
                if session is None and not seen_session:
                    still_open["before_data"] += 1
                elif session is None:
                    add(JOIN_FOR[kind], channel_id, now - MS, "missing_join")
                elif LEAVE_FOR[session["event_type"]] != kind:
                    retypes.append((event["_id"], LEAVE_FOR[session["event_type"]]))
                session = None
            elif kind == START_STREAM:
                stream = event
            elif kind == END_STREAM:
                if stream is None and not seen_stream:
                    still_open["before_data"] += 1
                elif stream is None:
                    carried_over = (
                        session is not None
                        and session["channel_id"] == channel_id
                        and last_end is not None
                        and last_end["channel_id"] != channel_id
                        and abs(session["timestamp"] - last_end["timestamp"]) <= MOVE_WINDOW
                    )
                    if carried_over:
                        add(START_STREAM, channel_id, session["timestamp"] + MS, "stream_carried_over")
                    else:
                        add(START_STREAM, channel_id, now - MS, "missing_stream_start")
                stream = None
                last_end = event

            seen_session = seen_session or kind in JOINS or kind in LEAVES
            seen_stream = seen_stream or kind in (START_STREAM, END_STREAM)
            last_seen = now

        # Whatever is open at the very end may simply still be going on, unless it started too
        # long ago for that. Then its end was lost like any other, there was just nothing after it
        closing = []
        if stream and is_stale(stream["timestamp"], data_end):
            closing.append((END_STREAM, stream, "stale_open"))
            stream = None
        if session and is_stale(session["timestamp"], data_end):
            closing.append((LEAVE_FOR[session["event_type"]], session, "stale_open"))
            session = None
        end_unrecorded(closing)

        still_open["sessions"] += session is not None
        still_open["streams"] += stream is not None

    return inserts, retypes, still_open


def signature(event):
    return (event["guild_id"], event["user_id"], event["event_type"], event["channel_id"], event["timestamp"])


def plan(events, complete):
    """Work out what has to change: (events to remove, events to insert, type changes, what was left alone)"""
    superseded = find_superseded(events)
    superseded_ids = {event["_id"] for event in superseded}
    inserts, retypes, still_open = plan_repairs(
        [event for event in events if event["_id"] not in superseded_ids], complete
    )

    # An end that comes out exactly as it already is stays as it is
    existing = Counter(signature(event) for event in superseded)
    planned = Counter(signature(event) for event in inserts)
    removals = [event for event in superseded if signature(event) not in planned]
    inserts = [event for event in inserts if signature(event) not in existing]
    return removals, inserts, retypes, still_open


def load_archives(directory):
    """Read archived events back into the shape they had in the database"""
    events = []
    for filename in sorted(os.listdir(directory)):
        if not filename.endswith(".json"):
            continue
        with open(os.path.join(directory, filename)) as f:
            for event in json.load(f):
                event["_id"] = ObjectId(event["_id"])
                event["timestamp"] = datetime.fromisoformat(event["timestamp"])
                events.append(event)
    return events


def describe(removals, inserts, retypes, still_open):
    reasons = Counter(event["repair"] for event in inserts)
    for reason, count in sorted(reasons.items()):
        print(f"  {count:>5}  {reason}")
    print(f"  {len(retypes):>5}  leave changed to mirror its join")
    if removals:
        print(f"  {len(removals):>5}  earlier ends removed, to be replaced by the ones above")
    print(
        f"  (left alone: {still_open['sessions']} sessions and {still_open['streams']} streams "
        "that started in the last day and are still open)"
    )
    if still_open["before_data"]:
        print(
            f"  (left alone: {still_open['before_data']} leaves / stream ends whose other half is "
            "from before the data begins. Use --complete if the database holds everything)"
        )


def main():
    parser = argparse.ArgumentParser(description="Repair one sided VC events")
    parser.add_argument("--apply", action="store_true", help="write the repairs (default is to only report)")
    parser.add_argument("--complete", action="store_true", help="the database holds every event ever recorded")
    parser.add_argument("--import-archives", metavar="DIR", help="folder of N_archive.json files to bring back in")
    parser.add_argument("--verbose", action="store_true", help="list every event that would be added or removed")
    args = parser.parse_args()

    load_dotenv(os.path.join(ROOT, ".env"))
    database = MongoClient(os.getenv("DATABASE_URL"))["discord"]
    collection = database["vcevents"]

    events = list(collection.find())
    print(f"{len(events)} events in the database")

    archived = []
    if args.import_archives:
        known = {event["_id"] for event in events}
        archived = [e for e in load_archives(args.import_archives) if e["_id"] not in known]
        archived = list({event["_id"]: event for event in archived}.values())
        print(f"{len(archived)} archived events to bring back in")

    # Without the archives, the database may only hold the events since it was last emptied
    complete = args.complete or bool(args.import_archives)
    removals, inserts, retypes, still_open = plan(events + archived, complete)
    print("Repairs needed:")
    describe(removals, inserts, retypes, still_open)
    if args.verbose:
        changes = [("-", event) for event in removals] + [("+", event) for event in inserts]
        for sign, event in sorted(changes, key=lambda change: (change[1]["user_id"], change[1]["timestamp"])):
            print(
                f"    {sign} {event['timestamp']}  user {event['user_id']}  "
                f"{EventType(event['event_type']).name:<12}  channel {event['channel_id']}  "
                f"({event.get('repair', 'written by the bot')})"
            )

    if not args.apply:
        print("\nNothing was changed. Run again with --apply to write these.")
        return
    if not (removals or inserts or retypes or archived):
        print("\nNothing to do.")
        return

    backup = f"vcevents_backup_{datetime.utcnow():%Y%m%d_%H%M%S}"
    collection.aggregate([{"$match": {}}, {"$out": backup}])
    copied, original = database[backup].count_documents({}), collection.count_documents({})
    if copied < len(events):
        sys.exit(f"Backup {backup} holds {copied} events, expected at least {len(events)}. Nothing was changed.")
    print(f"\nBacked up {copied} events to `{backup}` (collection now holds {original})")

    if archived:
        collection.insert_many(archived, ordered=False)
    if removals:
        collection.delete_many({"_id": {"$in": [event["_id"] for event in removals]}})
    if inserts:
        collection.insert_many(inserts)
    if retypes:
        collection.bulk_write(
            [UpdateOne({"_id": _id}, {"$set": {"event_type": kind}}) for _id, kind in retypes]
        )
    print(
        f"Wrote {len(archived)} archived events, removed {len(removals)}, "
        f"added {len(inserts)} and made {len(retypes)} type changes"
    )

    # Prove the result is clean, rather than assume it
    removals, inserts, retypes, still_open = plan(list(collection.find()), complete)
    if removals or inserts or retypes:
        print("Some events are STILL not right after the repair:")
        describe(removals, inserts, retypes, still_open)
        sys.exit(1)
    print("Verified: nothing repairable is left one sided.")
    print(f"To undo, replace `vcevents` with `{backup}`.")


if __name__ == "__main__":
    main()
