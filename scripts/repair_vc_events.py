"""Find and repair one sided VC events (a join with no leave, a stream end with no start, ...).

    python scripts/repair_vc_events.py                          # report only, changes nothing
    python scripts/repair_vc_events.py --apply                  # back the collection up, then repair it
    python scripts/repair_vc_events.py --import-archives DIR    # also bring archived events back in

Events the bot never saw cannot be recovered, so each repair adds the missing half of the pair:

- A session or stream with no end is closed 1ms after the last moment the user was seen in it.
- A leave with no join gets a join 1ms before it. Both are zero length rather than a guess.
- A stream end with no start, right after the user changed channel while streaming, gets its
  start at the moment they joined that channel (the stream carried over, the old bot never
  recorded it restarting).
- A leave whose AFK-ness differs from its join is changed to mirror the join.

Every event that is added carries `synthetic: true` and a `repair` reason, so the analysis can
tell them apart.

Two kinds of one sided events are deliberately left alone:

- Sessions still open at the very end of the data. Those users may well be in voice right now,
  and the bot closes them itself when it starts up.
- A leave / stream end that is the first thing a user does in the data. The bot used to move
  events out of the database into archive files, so the matching join is most likely sitting in
  one of those rather than missing. Adding another would create a double join once they are merged.

`--import-archives` takes the folder of `N_archive.json` files the bot used to move events into
and puts them back into the database (skipping any already there). The data is then complete, so
sessions split across archives pair up by themselves and the second kind above is repaired too.
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

JOIN_VC, LEAVE_VC = EventType.JOIN_VC.value, EventType.LEAVE_VC.value
JOIN_AFK, LEAVE_AFK = EventType.JOIN_AFK.value, EventType.LEAVE_AFK.value
START_STREAM, END_STREAM = EventType.START_STREAM.value, EventType.END_STREAM.value
JOINS, LEAVES = (JOIN_VC, JOIN_AFK), (LEAVE_VC, LEAVE_AFK)
LEAVE_FOR = {JOIN_VC: LEAVE_VC, JOIN_AFK: LEAVE_AFK}
JOIN_FOR = {LEAVE_VC: JOIN_VC, LEAVE_AFK: JOIN_AFK}

MS = timedelta(milliseconds=1)
# How close a stream end and a channel join must be to count as one move
MOVE_WINDOW = timedelta(seconds=2)


def sort_key(event):
    return (event["timestamp"], event["_id"])


def plan_repairs(events, complete):
    """Walk every user's events in order, returning (events to insert, type changes, what was left alone).

    `complete` says whether these are all the events there ever were. If not, a leave or stream end
    with nothing before it is assumed to have its other half in an archive."""
    by_user = {}
    for event in sorted(events, key=sort_key):
        by_user.setdefault((event["guild_id"], event["user_id"]), []).append(event)

    inserts, retypes, still_open = [], [], Counter()

    for (guild_id, user_id), user_events in by_user.items():
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

        for event in user_events:
            now, kind, channel_id = event["timestamp"], event["event_type"], event["channel_id"]
            closing = []  # Ends that are missing for what was open before this event

            if kind == START_STREAM and stream:
                closing.append((END_STREAM, stream["channel_id"], "missing_stream_end"))
                stream = None
            elif kind == END_STREAM and stream and stream["channel_id"] != channel_id:
                closing.append((END_STREAM, stream["channel_id"], "missing_stream_end"))
                stream = None
            elif kind in JOINS and session:
                if stream:
                    closing.append((END_STREAM, stream["channel_id"], "missing_stream_end"))
                    stream = None
                closing.append((LEAVE_FOR[session["event_type"]], session["channel_id"], "missing_leave"))
                session = None
            elif kind in LEAVES:
                if stream:
                    closing.append((END_STREAM, stream["channel_id"], "missing_stream_end"))
                    stream = None
                if session and session["channel_id"] != channel_id:
                    closing.append((LEAVE_FOR[session["event_type"]], session["channel_id"], "missing_leave"))
                    session = None

            # Closed just after the user was last seen, but never later than the current event
            for i, (event_type, closed_channel, reason) in enumerate(closing, start=1):
                add(event_type, closed_channel, max(last_seen, min(last_seen + i * MS, now - MS)), reason)

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

        still_open["sessions"] += session is not None
        still_open["streams"] += stream is not None

    return inserts, retypes, still_open


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


def describe(inserts, retypes, still_open):
    reasons = Counter(event["repair"] for event in inserts)
    for reason, count in sorted(reasons.items()):
        print(f"  {count:>5}  {reason}")
    print(f"  {len(retypes):>5}  leave changed to mirror its join")
    print(
        f"  (left alone: {still_open['sessions']} sessions and {still_open['streams']} streams "
        "still open at the end of the data)"
    )
    if still_open["before_data"]:
        print(
            f"  (left alone: {still_open['before_data']} leaves / stream ends whose other half is "
            "from before the data begins. Use --import-archives to pair these up)"
        )


def main():
    parser = argparse.ArgumentParser(description="Repair one sided VC events")
    parser.add_argument("--apply", action="store_true", help="write the repairs (default is to only report)")
    parser.add_argument("--import-archives", metavar="DIR", help="folder of N_archive.json files to bring back in")
    parser.add_argument("--verbose", action="store_true", help="list every event that would be added")
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

    # Without the archives, the database only holds the events since it was last emptied
    complete = bool(args.import_archives)
    inserts, retypes, still_open = plan_repairs(events + archived, complete)
    print("Repairs needed:")
    describe(inserts, retypes, still_open)
    if args.verbose:
        for event in sorted(inserts, key=lambda e: e["timestamp"]):
            print(
                f"    + {event['timestamp']}  user {event['user_id']}  "
                f"{EventType(event['event_type']).name:<12}  channel {event['channel_id']}  ({event['repair']})"
            )

    if not args.apply:
        print("\nNothing was changed. Run again with --apply to write these.")
        return
    if not (inserts or retypes or archived):
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
    if inserts:
        collection.insert_many(inserts)
    if retypes:
        collection.bulk_write(
            [UpdateOne({"_id": _id}, {"$set": {"event_type": kind}}) for _id, kind in retypes]
        )
    print(f"Wrote {len(archived)} archived events, {len(inserts)} new events and {len(retypes)} type changes")

    # Prove the result is clean, rather than assume it
    inserts, retypes, still_open = plan_repairs(list(collection.find()), complete)
    if inserts or retypes:
        print("Some events are STILL one sided after the repair:")
        describe(inserts, retypes, still_open)
        sys.exit(1)
    print("Verified: nothing repairable is left one sided.")
    print(f"To undo, replace `vcevents` with `{backup}`.")


if __name__ == "__main__":
    main()
