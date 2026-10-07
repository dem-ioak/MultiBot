import asyncio
import discord
import random
import util.dataclasses as DataClasses

from datetime import datetime, time, date, timedelta
from discord import Embed, Color, Status, Streaming, DMChannel
from discord.ext import commands, tasks
from pymongo import ReturnDocument, UpdateOne
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from util.constants import *
from util.enums import EventType
from util.helper_functions import leveled_up, run_db, user_defaults
from util.timeutil import unrecorded_end, is_stale
from util.log_messages import *
from util.constants import SERVERS, BOARDS, WATCHLIST, WATCHLIST_EMBED
from util.helper_functions import board_view_description, generate_wl_page
from util.buttons.board_buttons import BoardView, BoardListView, shown_board
from util.buttons.watchlist_buttons import WatchListView
from util.log_manager import get_logger

GIF_HOSTS = ("tenor.com", "giphy.com", "klipy.com")
SETUP_TIMEOUT = 120  # Seconds each startup step for wrapped tracking is given before it is abandoned
JOIN_EVENTS = (EventType.JOIN_VC, EventType.JOIN_AFK)
SESSION_EVENTS = (*JOIN_EVENTS, EventType.LEAVE_VC, EventType.LEAVE_AFK)
STREAM_EVENTS = (EventType.START_STREAM, EventType.END_STREAM)


def is_gif(attachment):
    return attachment.content_type == "image/gif" or attachment.filename.lower().endswith(".gif")


def message_stats(message):
    """Build the wrapped increments for a message, as (lifetime totals, daily bucket, bucket date)"""
    content = message.content
    images = [a for a in message.attachments if (a.content_type or "").startswith("image/")]
    gif_uploads = sum(1 for a in images if is_gif(a))
    gif_links = int(any(host in content.lower() for host in GIF_HOSTS))

    # `raw_mentions` only holds pings typed into the message, which leaves out reply pings
    typed = set(message.raw_mentions)
    pinged = [
        user.id
        for user in message.mentions
        if user.id in typed and not user.bot and user.id != message.author.id
    ]

    stats = {
        "everyone_pings": int(message.mention_everyone and "@everyone" in content),
        "image_count": len(images) - gif_uploads,
        "gif_count": gif_uploads + gif_links,
        "word_count": len(content.split()),
        "message_count": 1,
    }
    for user_id in pinged:
        stats[f"user_pings.{user_id}"] = 1

    now = datetime.now(EST_TIME)
    lifetime = {**stats, "user_pings.count": int(len(pinged) > 0)}
    daily = {
        **stats,
        "ping_count": int(len(pinged) > 0),
        f"hours.{now.hour}": 1,
        f"channels.{message.channel.id}": 1,
    }
    return lifetime, daily, now.strftime("%Y-%m-%d")


def load_open_sessions():
    """Find every VC session / stream that has a start event but no end event yet"""

    def last_events(event_types):
        return VC_EVENTS.aggregate(
            [
                {"$match": {"event_type": {"$in": [e.value for e in event_types]}}},
                {"$sort": {"timestamp": 1, "_id": 1}},
                {
                    "$group": {
                        "_id": {"guild_id": "$guild_id", "user_id": "$user_id"},
                        "last": {"$last": "$$ROOT"},
                    }
                },
            ],
            allowDiskUse=True,
        )

    open_vc, open_streams = {}, {}
    for entry in last_events(SESSION_EVENTS):
        event = entry["last"]
        if EventType(event["event_type"]) in JOIN_EVENTS:
            open_vc[(event["guild_id"], event["user_id"])] = event

    for entry in last_events(STREAM_EVENTS):
        event = entry["last"]
        if event["event_type"] == EventType.START_STREAM.value:
            open_streams[(event["guild_id"], event["user_id"])] = event

    return open_vc, open_streams


def to_documents(events):
    documents = [dict(event.__dict__) for event in events]
    for document in documents:
        document["event_type"] = document["event_type"].value
    return documents


class Events(commands.Cog):
    def __init__(self, client):
        self.client = client
        self._cd = commands.CooldownMapping.from_cooldown(
            1, 60.0, commands.BucketType.member
        )

        # Sessions that have been started but not ended, keyed by (guild_id, user_id).
        # These guarantee every JOIN / START_STREAM we write gets exactly one matching end event
        self.open_vc = {}  # -> (channel_id, channel_name, EventType of the join, time of the join)
        self.open_streams = {}  # -> (channel_id, channel_name, time it started)
        self.voice_lock = asyncio.Lock()
        self.connected = False

        self.birthday.start()
        self.vibes.start()
        if TRACKING_ENABLED:
            self.heartbeat.start()
            self.tracking_report.start()

    def get_ratelimit(self, message: discord.Message):
        """Returns ratelimit remaining after a message was sent"""
        if message.author.id != self.client.user.id:
            bucket = self._cd.get_bucket(message)
            return bucket.update_rate_limit()

    @commands.Cog.listener()
    async def on_ready(self):
        log = get_logger(__name__)
        log.info(f"SETUP: Ready in {len(self.client.guilds)} servers, tracking {'on' if TRACKING_ENABLED else 'off'}")

        # The boards / watchlist come first. Their buttons do nothing until this has ran, so it
        # should never be left waiting on (or be stopped by) anything else that happens at startup
        await self.refresh_features()

        if TRACKING_ENABLED:
            try:
                await asyncio.wait_for(self.backfill_members(), SETUP_TIMEOUT)
                await asyncio.wait_for(self.reconcile_voice(), SETUP_TIMEOUT)
            except Exception:
                self.client.listener_errors += 1
                log.exception("SETUP: Failed to prepare wrapped tracking")
        self.connected = True
        log.info("SETUP: Finished")

    async def refresh_features(self):
        """Redraw each server's boards and watchlist messages, which is what makes their buttons work"""
        for server in self.client.guilds:
            log = get_logger(__name__, server=server.name)
            log.info("SETUP: Refreshing Watchlist / Leaderboards")
            server_id = server.id
            board_data = BOARDS.find_one({"_id": server_id})
            watchlist_data = WATCHLIST.find_one({"_id": server_id})

            try:
                if board_data and "message_id" in board_data and "channel_id" in board_data:
                    board_channel_id = board_data["channel_id"]
                    board_message_id = board_data["message_id"]

                    board_channel = self.client.get_channel(board_channel_id)
                    board_message = await board_channel.fetch_message(board_message_id)
                    embed = Embed(
                        title="Server Boards",
                        description=board_view_description(server_id, 1),
                        color=Color.red(),
                    )
                    view = BoardListView(server_id, None, board_message)
                    shown_board.pop(board_message.id, None)
                    await board_message.edit(embed=embed, view=view)
                    log.info("SETUP: Successfully Refreshed BOARDS")

                if watchlist_data and "message_id" in watchlist_data and "channel_id" in watchlist_data:
                    watchlist_channel_id = watchlist_data["channel_id"]
                    watchlist_message_id = watchlist_data["message_id"]

                    watchlist_channel = self.client.get_channel(watchlist_channel_id)
                    watchlist_message = await watchlist_channel.fetch_message(
                        watchlist_message_id
                    )
                    entries = watchlist_data["entries"]
                    # Drawn on the stored page, which is the one the arrows will move on from
                    embed = generate_wl_page(watchlist_data.get("current_page", 1), entries)
                    view = WatchListView(self, watchlist_message)
                    await watchlist_message.edit(embed=embed, view=view)
                    log.info("SETUP: Successfully Refreshed WATCHLIST")
            except Exception:
                # One server's missing message should not stop the others from refreshing
                log.exception("SETUP: Failed to refresh Watchlist / Leaderboards")

    @commands.Cog.listener()
    async def on_resumed(self):
        self.connected = True

    @commands.Cog.listener()
    async def on_disconnect(self):
        self.connected = False

    # Wrapped tracking upkeep
    async def backfill_members(self):
        """Create data for anyone who joined while the bot was not around to see it"""
        user_ops, wrapped_ops = [], []
        for server in self.client.guilds:
            for user in server.members:
                if user.bot:
                    continue
                primary_key = {"guild_id": server.id, "user_id": user.id}
                wrapped_defaults = DataClasses.WrappedUser(
                    _id=primary_key, user_pings={"count": 0}
                ).__dict__
                del wrapped_defaults["_id"]
                user_ops.append(
                    UpdateOne({"_id": primary_key}, {"$setOnInsert": user_defaults()}, upsert=True)
                )
                wrapped_ops.append(
                    UpdateOne({"_id": primary_key}, {"$setOnInsert": wrapped_defaults}, upsert=True)
                )

        if user_ops:
            users = await run_db(USERS.bulk_write, user_ops, ordered=False)
            wrapped = await run_db(WRAPPED.bulk_write, wrapped_ops, ordered=False)
            get_logger(__name__).info(
                f"SETUP: Backfilled {users.upserted_count} users and {wrapped.upserted_count} wrapped users"
            )

    async def reconcile_voice(self):
        """Bring the stored VC events in line with who is actually in voice right now.

        Anything that happened while we were offline was never seen, so sessions that are open
        in the data but over in reality are closed, and anyone sitting in voice without an open
        session gets one started now.

        A session whose end was missed is ended at the last moment we know we were online, but
        never later than the end of the day it started on. Without that, a join from months ago
        that never got its leave would turn into a session lasting until today."""
        log = get_logger(__name__)
        async with self.voice_lock:
            now = datetime.utcnow()
            await run_db(
                VC_EVENTS.create_index,
                [("guild_id", 1), ("user_id", 1), ("timestamp", 1)],
            )
            await run_db(WRAPPED_DAILY.create_index, [("_id.guild_id", 1), ("_id.date", 1)])

            heartbeat = await run_db(BOT_STATE.find_one, {"_id": "heartbeat"})
            last_event = await run_db(VC_EVENTS.find_one, sort=[("timestamp", -1)])
            seen = [doc["timestamp"] for doc in (heartbeat, last_event) if doc]
            last_alive = min(max(seen), now) if seen else now

            stored_vc, stored_streams = await run_db(load_open_sessions)

            # Who is actually in voice
            current_vc, current_streams = {}, {}
            for server in self.client.guilds:
                server_data = await run_db(SERVERS.find_one, {"_id": server.id})
                if server_data is None:
                    continue
                for channel in server.voice_channels:
                    if channel.id == server_data["join_to_create"]:
                        continue
                    is_afk = channel.id == server_data["afk_corner"]
                    for member in channel.members:
                        if member.bot:
                            continue
                        key = (server.id, member.id)
                        current_vc[key] = (
                            channel.id,
                            channel.name,
                            EventType.JOIN_AFK if is_afk else EventType.JOIN_VC,
                            now,
                        )
                        if member.voice and member.voice.self_stream:
                            current_streams[key] = (channel.id, channel.name, now)

            events = []

            def add(key, timestamp, event_type, channel_id, channel_name):
                events.append(
                    DataClasses.VCEvent(
                        key[0], key[1], timestamp, event_type, channel_id, channel_name, True
                    )
                )

            def still_going(key, event, current):
                # Being in the same channel only means the same session if it began recently enough.
                # Somebody sat in a channel they also joined last month has simply come back to it
                in_place = key in current and current[key][0] == event["channel_id"]
                return in_place and not is_stale(event["timestamp"], last_alive)

            def ended(event):
                return unrecorded_end(event["timestamp"], last_alive)

            self.open_vc, self.open_streams = {}, {}
            for key, event in stored_streams.items():
                in_same_vc = key in stored_vc and still_going(key, stored_vc[key], current_vc)
                if in_same_vc and still_going(key, event, current_streams):
                    self.open_streams[key] = (*current_streams[key][:2], event["timestamp"])
                else:
                    end = ended(event)
                    if key in stored_vc and not in_same_vc:
                        # A stream cannot outlast the session it was part of
                        end = max(event["timestamp"], min(end, ended(stored_vc[key])))
                    add(
                        key,
                        end,
                        EventType.END_STREAM,
                        event["channel_id"],
                        event.get("channel_name"),
                    )

            for key, event in stored_vc.items():
                if still_going(key, event, current_vc):
                    self.open_vc[key] = (
                        event["channel_id"],
                        current_vc[key][1],
                        EventType(event["event_type"]),
                        event["timestamp"],
                    )
                else:
                    was_afk = event["event_type"] == EventType.JOIN_AFK.value
                    add(
                        key,
                        # 1ms after the stream end above, so that sorting by time keeps their order
                        ended(event) + timedelta(milliseconds=1),
                        EventType.LEAVE_AFK if was_afk else EventType.LEAVE_VC,
                        event["channel_id"],
                        event.get("channel_name"),
                    )

            # Strictly after every closing event above
            now = max(now, last_alive + timedelta(milliseconds=2))
            for key, session in current_vc.items():
                if key not in self.open_vc:
                    add(key, now, session[2], session[0], session[1])
                    self.open_vc[key] = session

            for key, stream in current_streams.items():
                if key not in self.open_streams:
                    add(key, now + timedelta(milliseconds=1), EventType.START_STREAM, stream[0], stream[1])
                    self.open_streams[key] = stream

            if events:
                await run_db(VC_EVENTS.insert_many, to_documents(events))
            log.info(
                f"SETUP: Reconciled voice state with {len(events)} events, "
                f"{len(self.open_vc)} users in voice and {len(self.open_streams)} streaming"
            )

    @tasks.loop(seconds=60)
    async def heartbeat(self):
        """Record that we are online, so a restart knows when events stopped being seen"""
        if not self.connected:
            return
        try:
            await run_db(
                BOT_STATE.update_one,
                {"_id": "heartbeat"},
                {"$set": {"timestamp": datetime.utcnow()}},
                upsert=True,
            )
        except Exception:
            # An unhandled error would stop the loop for good
            get_logger(__name__).exception("HEARTBEAT: Failed to write")

    @tasks.loop(time=MIDNIGHT)
    async def tracking_report(self):
        """DM the owner how much was tracked today, so a silent failure is noticed within a day"""
        try:
            log = get_logger(__name__)
            since = datetime.utcnow() - timedelta(days=1)
            yesterday = (datetime.now(EST_TIME) - timedelta(days=1)).strftime("%Y-%m-%d")
            voice_events = await run_db(VC_EVENTS.count_documents, {"timestamp": {"$gte": since}})
            buckets = await run_db(
                lambda: list(WRAPPED_DAILY.find({"_id.date": yesterday}, {"message_count": 1}))
            )
            messages = sum(bucket.get("message_count", 0) for bucket in buckets)
            errors, self.client.listener_errors = self.client.listener_errors, 0

            embed = Embed(
                title=f"Wrapped tracking for {yesterday}",
                description=(
                    f"**{messages}** messages from **{len(buckets)}** users\n"
                    f"**{voice_events}** voice events in the last 24 hours\n"
                    f"**{errors}** listener errors (see the log)"
                ),
                color=Color.red() if errors else Color.green(),
            )
            log.info(f"TRACKING_REPORT: messages={messages}, voice_events={voice_events}, errors={errors}")
            try:
                owner = await self.client.fetch_user(MY_USER_ID)
                await owner.send(embed=embed)
            except discord.HTTPException:
                log.exception("TRACKING_REPORT: Could not DM the report")
        except Exception:
            # An unhandled error would stop the loop for good
            get_logger(__name__).exception("TRACKING_REPORT: Failed")

    @tracking_report.before_loop
    async def before_tracking_report(self):
        await self.client.wait_until_ready()

    # User / Bot Joining & Leaving
    @commands.Cog.listener()
    async def on_guild_join(self, server):
        """Handle DB Operations when bot joins a server"""
        log = get_logger(__name__, server=server.name)
        log.info("SERVER_SETUP: Bot has been added to a new server, setting up data")
        guild_id = server.id
        guild_name = server.name

        server_data = SERVERS.find_one({"_id": guild_id})
        curr_time = datetime.utcnow()

        # Add server if it does not exist
        if server_data is None:

            server_obj = DataClasses.Server(_id=guild_id, auto_roles=[], vibe_gifs=[])

            SERVERS.insert_one(server_obj.__dict__)
            BOARDS.insert_one({"_id": guild_id, "boards": []})
            WATCHLIST.update_one(
                {"_id": guild_id},
                {"$set": {"entries": [], "current_page": 1}},
                upsert=True,
            )

        # Add each user in the server to data
        for user in server.members:
            if not user.bot:
                primary_key = {"guild_id": guild_id, "user_id": user.id}
                user_data = USERS.find_one({"_id": primary_key})
                wrapped_data = WRAPPED.find_one({"_id": primary_key})
                if user_data is None:
                    user_obj = DataClasses.User(_id=primary_key)
                    USERS.insert_one(user_obj.__dict__)

                if wrapped_data is None:
                    wrapped_obj = DataClasses.WrappedUser(
                        _id=primary_key, user_pings={"count": 0}
                    )
                    WRAPPED.insert_one(wrapped_obj.__dict__)

        # Add every text channel in the server to data
        for t_channel in server.text_channels:
            t_channel_data = TEXT_CHANNELS.find_one({"_id": t_channel.id})
            if t_channel_data is not None:
                continue

            t_channel_obj = DataClasses.TChannel(_id=t_channel.id, created_at=curr_time)

            TEXT_CHANNELS.insert_one(t_channel_obj.__dict__)

        for v_channel in server.voice_channels:
            v_channel_data = VCS.find_one({"_id": v_channel.id})
            if v_channel_data is not None:
                continue

            v_channel_obj = DataClasses.VChannel(
                _id=v_channel.id, owner=None, created_by=None, created_at=curr_time
            )
            VCS.insert_one(v_channel_obj.__dict__)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Handle DB Operations when a user joins a server the bot is in"""
        guild_id = member.guild.id
        guild_name = member.guild.name
        user_id = member.id
        
        log = get_logger(__name__, server=guild_name, user=member.name)
        log.info("New user joined this server, setting up their data")

        primary_key = {"guild_id": guild_id, "user_id": user_id}
        server_data = SERVERS.find_one({"_id": guild_id})
        user_data = USERS.find_one({"_id": primary_key})

        # Add user if does not already exist in data
        if user_data is None and not member.bot:
            user_obj = DataClasses.User(_id=primary_key)
            USERS.insert_one(user_obj.__dict__)
            log.info("Successfully inserted new user's data into the database")

        wrapped_data = WRAPPED.find_one({"_id": primary_key})
        if wrapped_data is None and not member.bot:
            wrapped_user_obj = DataClasses.WrappedUser(
                _id=primary_key, user_pings={"count": 0}
            )
            WRAPPED.insert_one(wrapped_user_obj.__dict__)
            log.info("Successfully inserted new user's WRAPPED data into the database")

        if server_data is None:
            return

        # Give user all `auto_roles` for this server
        for auto_role in server_data["auto_roles"]:
            role = discord.utils.get(member.guild.roles, id=auto_role)
            try:
                await member.add_roles(role)
            except Exception as e:
                log.error("Failed to add auto role to user. The role has likely been deleted.")
                continue

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        guild_id = member.guild.id
        guild_name = member.guild.name
        user_id = member.id
        log = get_logger(__name__, server=guild_name, user=member.name)
        log.info("This user has left the server.")

    # Voice State
    async def record_voice_events(self, member, before, after, server_data, curr_time):
        """Write the wrapped events for a voice state update"""
        log = get_logger(__name__, server=member.guild.name, user=member.name)
        create_id = server_data["join_to_create"]
        afk_corner = server_data["afk_corner"]
        key = (member.guild.id, member.id)

        # The join to create channel is only ever passed through, so it is not tracked
        old = before.channel if before.channel and before.channel.id != create_id else None
        new = after.channel if after.channel and after.channel.id != create_id else None
        moved = (old.id if old else None) != (new.id if new else None)
        streaming = bool(new and after.self_stream)

        async with self.voice_lock:
            events = []
            previous = (self.open_vc.get(key), self.open_streams.get(key))

            def add(event_type, channel_id, channel_name, synthetic=False, timestamp=None):
                # Events of one update are spaced 1ms apart so that sorting by time keeps their order
                timestamp = timestamp or curr_time + timedelta(milliseconds=len(events))
                if events and timestamp <= events[-1].timestamp:
                    timestamp = events[-1].timestamp + timedelta(milliseconds=1)
                events.append(
                    DataClasses.VCEvent(
                        key[0], key[1], timestamp, event_type, channel_id, channel_name, synthetic
                    )
                )
                log.info(event_type.name)

            # The open session is not the one they are leaving now, so its own leave was never seen.
            # When that happened is unknown, so it is ended no later than the end of the day it began
            missed_end = None
            if moved and key in self.open_vc and (not old or old.id != self.open_vc[key][0]):
                missed_end = unrecorded_end(self.open_vc[key][3], curr_time)

            # A stream ends when it is stopped, and also whenever the user changes channel
            if key in self.open_streams and (moved or not streaming):
                channel_id, channel_name, started = self.open_streams.pop(key)
                if missed_end:
                    add(EventType.END_STREAM, channel_id, channel_name, True, max(started, missed_end))
                else:
                    add(EventType.END_STREAM, channel_id, channel_name)

            if moved:
                if key in self.open_vc:
                    channel_id, channel_name, join_type, joined = self.open_vc.pop(key)
                    # The leave always mirrors its join, even if the AFK channel was changed since
                    leave_type = (
                        EventType.LEAVE_AFK
                        if join_type == EventType.JOIN_AFK
                        else EventType.LEAVE_VC
                    )
                    if missed_end:
                        add(leave_type, channel_id, channel_name, True, missed_end + timedelta(milliseconds=1))
                    else:
                        add(leave_type, channel_id, channel_name)
                elif old:
                    log.warning("Left a VC they were never seen joining, no event written")

            if new and key not in self.open_vc:
                join_type = EventType.JOIN_AFK if new.id == afk_corner else EventType.JOIN_VC
                # If they did not just move here, we missed the join and are only now catching up
                add(join_type, new.id, new.name, not moved)
                self.open_vc[key] = (new.id, new.name, join_type, curr_time)

            if streaming and key not in self.open_streams:
                add(EventType.START_STREAM, new.id, new.name)
                self.open_streams[key] = (new.id, new.name, curr_time)

            if events:
                try:
                    await run_db(VC_EVENTS.insert_many, to_documents(events))
                except Exception:
                    # Nothing was stored, so forget it happened rather than end a session later
                    # that the data never saw begin
                    for state, value in zip((self.open_vc, self.open_streams), previous):
                        if value is None:
                            state.pop(key, None)
                        else:
                            state[key] = value
                    raise

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        curr_time = datetime.utcnow()
        guild_id = member.guild.id
        guild_name = member.guild.name
        user_id = member.id
        log = get_logger(__name__, server=guild_name, user=member.name)
        server_data = await run_db(SERVERS.find_one, {"_id": guild_id})
        if server_data is None:
            return

        create_id = server_data["join_to_create"]

        created_vc = after.channel and after.channel.id == create_id
        changed_vc = before.channel != after.channel

        # Wrapped Stuff. Written before any channel management, so a failed Discord call cannot lose it
        if TRACKING_ENABLED and not member.bot:
            try:
                await self.record_voice_events(member, before, after, server_data, curr_time)
            except Exception:
                self.client.listener_errors += 1
                log.exception("Failed to record voice events")

        # Delete VC if empty after leaving
        if changed_vc and before.channel and before.channel.id != create_id:
            vc_data = await run_db(VCS.find_one, {"_id": before.channel.id})
            if vc_data is not None and vc_data["owner"]:
                if len(before.channel.members) == 0:
                    try:
                        await before.channel.delete()
                    except discord.NotFound:
                        pass  # Somebody else leaving at the same moment already deleted it
                    await run_db(
                        VCS.update_one,
                        {"_id": vc_data["_id"]},
                        {"$set": {"deleted_at": curr_time}},
                    )
                    seconds = int((curr_time - vc_data["created_at"]).total_seconds())
                    hours, remainder = divmod(seconds, 3600)
                    minutes, seconds = divmod(remainder, 60)
                    duration_str = f"{hours}h {minutes}m {seconds}s" if hours else f"{minutes}m {seconds}s"
                    log.info(f"Voice channel concluded after {duration_str}")

        # Create a new VC for anyone joining the join to create channel
        if changed_vc and created_vc and not member.bot:
            created_channel = await member.guild.create_voice_channel(
                name=f"{member.name}'s VC", category=after.channel.category
            )
            channel_data = DataClasses.VChannel(
                _id=created_channel.id,
                owner=user_id,
                created_by=user_id,
                created_at=curr_time,
            )
            await run_db(VCS.insert_one, channel_data.__dict__)
            log.info("CREATED_VC")
            try:
                await member.move_to(created_channel)
            except discord.HTTPException:
                # They left before they could be moved, so nobody will ever empty this channel
                await created_channel.delete()
                await run_db(
                    VCS.update_one,
                    {"_id": created_channel.id},
                    {"$set": {"deleted_at": datetime.utcnow()}},
                )

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.guild is None:
            return

        guild_id = message.guild.id
        user_id = message.author.id
        primary_key = {"guild_id": guild_id, "user_id": user_id}
        server_data = await run_db(SERVERS.find_one, {"_id": guild_id})

        # Wrapped Stuff. Keyed on _id with an upsert so no message is dropped, whoever sent it
        if TRACKING_ENABLED:
            lifetime, daily, today = message_stats(message)
            await asyncio.gather(
                run_db(WRAPPED.update_one, {"_id": primary_key}, {"$inc": lifetime}, upsert=True),
                run_db(
                    WRAPPED_DAILY.update_one,
                    {"_id": {**primary_key, "date": today}},
                    {"$inc": daily},
                    upsert=True,
                ),
            )

        # Update level if server has levels enabled
        if server_data and server_data["levels"]:
            rate_limit = self.get_ratelimit(message)
            if not rate_limit:
                user_data = await run_db(
                    USERS.find_one_and_update,
                    {"_id": primary_key},
                    {
                        "$inc": {"xp": random.randint(5, 15)},
                        "$setOnInsert": user_defaults("xp"),
                    },
                    upsert=True,
                    return_document=ReturnDocument.AFTER,
                )
                current_level = user_data["level"]
                if leveled_up(user_data["xp"], current_level):
                    current_level += 1
                    await run_db(
                        USERS.update_one,
                        {"_id": primary_key},
                        {"$set": {"level": current_level}},
                    )
                    await message.channel.send(
                        embed=Embed(
                            title=LEVEL_UP.format(current_level), color=Color.green()
                        )
                    )
                    await message.channel.send(message.author.mention)

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        if message.guild is None:
            return
        log = get_logger(__name__, server=message.guild.name, user=message.author.name, channel=message.channel.name)
        try:
            assert message.author != self.client.user
            assert not message.content.startswith(".")
            assert len(message.attachments) == 0
        except AssertionError:
            return

        guild_id = message.guild.id
        user_id = message.author.id

        channel_id = message.channel.id
        server_data = SERVERS.find_one({"_id": guild_id})
        channel_data = TEXT_CHANNELS.find_one({"_id": channel_id})
        member = message.author
        if channel_data is not None:
            TEXT_CHANNELS.update_one(
                channel_data,
                {
                    "$set": {
                        "deleted_author": user_id,
                        "deleted_content": message.content,
                    }
                },
            )
            log.info("Message has been deleted, this text channel's snipe has been updated")
            logs = server_data["logs_id"]
            if logs != -1:
                pass

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if before.guild is None:
            return
        log = get_logger(__name__, server=before.guild.name, user=before.author.name, channel=before.channel.name)
        try:
            assert before.author != self.client.user
            assert "https://" not in before.content
        except AssertionError:
            return

        guild_id = before.guild.id
        user_id = before.author.id
        channel_id = before.channel.id
        server_data = SERVERS.find_one({"_id": guild_id})
        channel_data = TEXT_CHANNELS.find_one({"_id": channel_id})
        member = before.author
        if channel_data is not None:
            TEXT_CHANNELS.update_one(
                channel_data,
                {"$set": {"edited_author": user_id, "edited_content": before.content}},
            )

            log.info("Message has been edited, this text channel's snipe has been updated")
            logs = server_data["logs_id"]
            if logs != -1:
                pass

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        log = get_logger(__name__, server=channel.guild.name, channel=channel.name)
        is_text = isinstance(channel, discord.TextChannel)
        title = "TEXT_CHANNEL" if is_text else "VOICE_CHANNEL"
        log.info(f"{title}_DELETE")

        curr_time = datetime.utcnow()
        collection = TEXT_CHANNELS if is_text else VCS
        channel_data = collection.find_one({"_id": channel.id})
        if channel_data is not None:
            collection.update_one({"_id": channel.id}, {"$set": {"deleted_at": curr_time}})
            log.info("Succesfully updated this channel's deletion date")

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        curr_time = datetime.utcnow()
        log = get_logger(__name__, server=channel.guild.name, channel=channel.name)
        is_text = isinstance(channel, discord.TextChannel)
        title = "TEXT_CHANNEL" if is_text else "VOICE_CHANNEL"
        log.info(f"{title}_CREATE")
        if is_text:
            channel_data_obj = DataClasses.TChannel(
                _id=channel.id, created_at=curr_time
            )

            TEXT_CHANNELS.insert_one(channel_data_obj.__dict__)

        else:
            vc_data = VCS.find_one({"_id": channel.id})

            # If VC was not created using join to create
            if "'s VC" not in channel.name:
                vc_data_obj = DataClasses.VChannel(
                    _id=channel.id, owner=None, created_by=None, created_at=curr_time
                )
                VCS.insert_one(vc_data_obj.__dict__)
        log.info("Succesfully created database entry for this channel")


    @tasks.loop(time=VIBE_TIME)
    async def vibes(self):
        for guild in self.client.guilds:
            log = get_logger(__name__, server=guild.name)
            server_data = SERVERS.find_one({"_id": guild.id})
            channel_id = server_data["vibe_id"]
            gifs = server_data["vibe_gifs"]
            if server_data["vibes"] and channel_id != -1:
                channel = self.client.get_channel(channel_id)
                result = random.randint(1, 100)
                log.info(f"GOOD_VIBES_RESULT : {result}")

                # Bad Vibes 1
                if result <= 5:
                    await channel.send(BAD_VIBES_GIF)
                    await channel.send(BAD_VIBES_MESSAGE)

                # Magnificent Vibes
                elif result <= 10:
                    await channel.send(MAGNIFICENT_VIBES_GIF)
                    await channel.send(MAGNIFICENT_VIBES_MESSAGE)

                # Good Vibes
                elif result <= 99:
                    gif = (
                        GOOD_VIBES_DEFAULT_GIF
                        if len(gifs) == 0
                        else random.choice(gifs)
                    )
                    await channel.send(gif)
                    await channel.send(GOOD_VIBES_MESSAGE)
                    
                # Chopped Chin Vibes
                else:
                    await channel.send(CC_VIBES_GIF)
                    await channel.send(CC_VIBES_MESSAGE)

    @tasks.loop(time=BIRTHDAY_CHECK_TIMES)
    async def birthday(self):
        """Announce each birthday as it turns midnight in that user's own timezone"""
        now = datetime.now(ZoneInfo("UTC"))
        for guild in self.client.guilds:
            log = get_logger(__name__, server=guild.name)
            try:
                server_data = SERVERS.find_one({"_id": guild.id})
                all_birthdays = USERS.find(
                    {"_id.guild_id": guild.id, "birthday": {"$ne": None}}
                )

                embed = Embed(title="🎂 Today's Birthdays", color=Color.orange())
                embed.set_footer(text="Set your birthday using /birthday set!")

                for user in all_birthdays:
                    try:
                        user_timezone = ZoneInfo(user.get("timezone") or EST_TIME.key)
                    except (ZoneInfoNotFoundError, ValueError):
                        user_timezone = EST_TIME

                    local = now.astimezone(user_timezone)
                    if local.hour != 0 or local.minute >= BIRTHDAY_CHECK_MINUTES:
                        continue

                    year, month, day = [int(i) for i in user["birthday"].split("-")]
                    if local.month == month and local.day == day:
                        member_obj = self.client.get_user(user["_id"]["user_id"])
                        if member_obj is None:
                            continue

                        embed.add_field(
                            name=member_obj.name,
                            value=f"{local.year - year} Years Old 🎉",
                            inline=False,
                        )

                if not embed.fields:
                    continue

                channel = self.client.get_channel(server_data["vibe_id"]) if server_data else None

                # Birthday channel could not be found, skipping
                if not channel:
                    log.info("Birthday's exist in this server, but there is no channel to announce to.")
                    continue

                await channel.send(embed=embed)
                await channel.send("@everyone THERE'S BIRTHDAY(s) TODAY!!!!!")
                log.info("Succesfully announced today's birthdays")
            except Exception:
                # An unhandled error would stop the loop for good, and skip the other servers
                log.exception("Failed to announce birthdays")


async def setup(client):
    await client.add_cog(Events(client))
