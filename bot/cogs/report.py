import discord
from discord import Embed, Color, app_commands
from discord.app_commands import Range
from discord.ext import commands
from collections import Counter
from datetime import datetime, timedelta, timezone

from util.constants import USERS, WRAPPED, WRAPPED_DAILY, VC_EVENTS, MY_USER_ID, FORBIDDEN_COMMAND
from util.enums import EventType
from util.helper_functions import run_db
from util.log_manager import get_logger

JOINS = (EventType.JOIN_VC.value, EventType.JOIN_AFK.value)
LEAVES = (EventType.LEAVE_VC.value, EventType.LEAVE_AFK.value)
TOP_COUNT = 3


def format_duration(seconds):
    hours, remainder = divmod(int(seconds), 3600)
    minutes = remainder // 60
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


def format_time(moment, style="f"):
    """Turn a stored (UTC) time into a Discord timestamp, shown in each viewer's own timezone"""
    return f"<t:{int(moment.replace(tzinfo=timezone.utc).timestamp())}:{style}>"


def summarize_voice(events, now, in_voice):
    """Pair up a user's VC events into time spent in voice, AFK and streaming.

    A session still open at the end only counts (up until `now`) if the user really is in voice,
    otherwise its leave was never recorded and how long it lasted is anyone's guess."""
    summary = {
        "voice": 0, "afk": 0, "stream": 0, "sessions": 0, "streams": 0, "longest": 0,
        "channels": Counter(), "names": {}, "first": None, "last": None,
    }
    session = stream = None

    def close_session(end):
        seconds = (end - session["timestamp"]).total_seconds()
        if session["event_type"] == EventType.JOIN_AFK.value:
            summary["afk"] += seconds
        else:
            summary["voice"] += seconds
            summary["sessions"] += 1
            summary["longest"] = max(summary["longest"], seconds)
            summary["channels"][session["channel_id"]] += seconds

    for event in sorted(events, key=lambda event: (event["timestamp"], event["_id"])):
        kind = event["event_type"]
        summary["first"] = summary["first"] or event["timestamp"]
        summary["last"] = event["timestamp"]
        if event.get("channel_name"):
            summary["names"][event["channel_id"]] = event["channel_name"]

        if kind in JOINS:
            session = event
        elif kind in LEAVES:
            if session:
                close_session(event["timestamp"])
            session = None
        elif kind == EventType.START_STREAM.value:
            stream = event
        elif kind == EventType.END_STREAM.value:
            if stream:
                summary["stream"] += (event["timestamp"] - stream["timestamp"]).total_seconds()
                summary["streams"] += 1
            stream = None

    if in_voice and session:
        close_session(now)
        if stream:
            summary["stream"] += (now - stream["timestamp"]).total_seconds()
            summary["streams"] += 1

    return summary


def ranked(counter, label, count=TOP_COUNT):
    """Format the highest entries of a counter, one per line"""
    return "\n".join(f"{label(key)} - {value}" for key, value in counter.most_common(count))


class Report(commands.Cog):
    def __init__(self, client):
        self.client = client

    @app_commands.command(description="Generate a report of everything tracked about a user")
    @app_commands.describe(
        target="The user to report on",
        days="Only look at this many days back (messages by day and voice only, defaults to everything)",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def report(
        self,
        interaction: discord.Interaction,
        target: discord.User,
        days: Range[int, 1, 3650] = None,
    ):
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /report [target={target}, days={days}]")
        if interaction.user.id != MY_USER_ID:
            await interaction.response.send_message(embed=FORBIDDEN_COMMAND, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        member = guild.get_member(target.id)
        now = datetime.utcnow()
        since = now - timedelta(days=days) if days else None
        primary_key = {"guild_id": guild.id, "user_id": target.id}

        user_data = await run_db(USERS.find_one, {"_id": primary_key}) or {}
        wrapped_data = await run_db(WRAPPED.find_one, {"_id": primary_key}) or {}

        embed = Embed(
            title=f"Report on {target.name}",
            description=f"{target.mention} in **{guild.name}**, " + (f"over the last **{days}** days" if days else "all time"),
            color=Color.blurple(),
        )
        embed.set_thumbnail(url=target.display_avatar.url)

        # Who they are
        about = f"Account created {format_time(target.created_at.replace(tzinfo=None), 'D')}\n"
        if member and member.joined_at:
            about += f"Joined {format_time(member.joined_at.astimezone(timezone.utc).replace(tzinfo=None), 'D')}\n"
        elif not member:
            about += "No longer in this server\n"
        about += f"Level **{user_data.get('level', 1)}** ({user_data.get('xp', 0)} xp)\n"
        if user_data.get("birthday"):
            about += f"Birthday {user_data['birthday']} ({user_data.get('timezone') or 'Eastern'})\n"
        if user_data.get("last_fm"):
            about += f"LastFM **{user_data['last_fm']}**\n"
        if user_data.get("banished_id", -1) != -1:
            about += f"Currently **banished** to <#{user_data['banished_id']}>\n"
        embed.add_field(name="About", value=about, inline=False)

        # Messages. These totals have no dates on them, so they are always all time
        if wrapped_data.get("message_count"):
            messages = wrapped_data["message_count"]
            embed.add_field(
                name="Messages (all time)",
                value=(
                    f"**{messages}** messages, **{wrapped_data.get('word_count', 0)}** words "
                    f"({wrapped_data.get('word_count', 0) / messages:.1f} per message)\n"
                    f"**{wrapped_data.get('image_count', 0)}** images, **{wrapped_data.get('gif_count', 0)}** gifs, "
                    f"**{wrapped_data.get('everyone_pings', 0)}** everyone pings"
                ),
                inline=False,
            )

        pings = Counter(wrapped_data.get("user_pings") or {})
        pings.pop("count", None)
        if pings:
            embed.add_field(name="Pings most", value=ranked(pings, lambda user_id: f"<@{user_id}>"))

        pinged_by = Counter()
        field = f"user_pings.{target.id}"
        others = await run_db(
            lambda: list(WRAPPED.find({"_id.guild_id": guild.id, field: {"$gt": 0}}, {field: 1}))
        )
        for other in others:
            pinged_by[other["_id"]["user_id"]] = other["user_pings"][str(target.id)]
        if pinged_by:
            embed.add_field(name="Pinged most by", value=ranked(pinged_by, lambda user_id: f"<@{user_id}>"))

        # Messages by day, which only exist from when the daily tracking began
        daily_filter = {"_id.guild_id": guild.id, "_id.user_id": target.id}
        if since:
            daily_filter["_id.date"] = {"$gte": (since - timedelta(days=1)).strftime("%Y-%m-%d")}
        buckets = await run_db(lambda: list(WRAPPED_DAILY.find(daily_filter)))
        if buckets:
            by_day = Counter({bucket["_id"]["date"]: bucket.get("message_count", 0) for bucket in buckets})
            hours, channels = Counter(), Counter()
            for bucket in buckets:
                hours.update({int(hour): count for hour, count in bucket.get("hours", {}).items()})
                channels.update(bucket.get("channels", {}))
            busiest_day, busiest_count = by_day.most_common(1)[0]
            busiest_hour = hours.most_common(1)[0][0] if hours else None
            value = (
                f"**{sum(by_day.values())}** messages over **{len(by_day)}** active days "
                f"(since {min(by_day)})\n"
                f"Busiest day {busiest_day} with **{busiest_count}**\n"
            )
            if busiest_hour is not None:
                value += f"Most active around **{busiest_hour % 12 or 12} {'AM' if busiest_hour < 12 else 'PM'}** Eastern\n"
            embed.add_field(name="Messages by day", value=value, inline=False)
            if channels:
                embed.add_field(
                    name="Top text channels",
                    value=ranked(channels, lambda channel_id: f"<#{channel_id}>"),
                    inline=False,
                )

        # Voice
        event_filter = dict(primary_key)
        if since:
            event_filter["timestamp"] = {"$gte": since}
        events = await run_db(lambda: list(VC_EVENTS.find(event_filter)))
        in_voice = bool(member and member.voice and member.voice.channel)
        voice = summarize_voice(events, now, in_voice)
        if events:
            value = (
                f"**{format_duration(voice['voice'])}** in voice over **{voice['sessions']}** sessions "
                f"(longest {format_duration(voice['longest'])})\n"
                f"**{format_duration(voice['stream'])}** streaming over **{voice['streams']}** streams\n"
                f"**{format_duration(voice['afk'])}** in the AFK channel\n"
                f"Last seen in voice {format_time(voice['last'], 'R')}" + (" (in voice now)" if in_voice else "") + "\n"
                f"Voice data starts {format_time(voice['first'], 'D')}"
            )
            embed.add_field(name="Voice", value=value, inline=False)

            def channel_label(channel_id):
                # Temporary voice channels are deleted once empty, so a mention would show as unknown
                if guild.get_channel(channel_id):
                    return f"<#{channel_id}>"
                return voice["names"].get(channel_id, "Deleted channel")

            top_channels = [
                f"{channel_label(channel_id)} - {format_duration(seconds)}"
                for channel_id, seconds in voice["channels"].most_common(TOP_COUNT)
            ]
            if top_channels:
                embed.add_field(name="Top voice channels", value="\n".join(top_channels), inline=False)

        if len(embed.fields) == 1:
            embed.add_field(name="Activity", value="Nothing has been tracked for this user yet.", inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(client):
    await client.add_cog(Report(client))
