import discord
from discord import Embed, Color, app_commands
from discord.app_commands import Choice, Range
from discord.ext import commands, tasks
from bson import ObjectId
from bson.errors import InvalidId
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from util.constants import REMINDERS, USERS, EST_TIME
from util.helper_functions import run_db
from util.log_manager import get_logger
from util.timeparse import parse_when, next_occurrence

MAX_REMINDERS = 25  # Pending per user. Also the most that a list or a set of suggestions can show
MAX_MESSAGE_LENGTH = 500
MIN_DELAY = timedelta(minutes=1)
MAX_DELAY = timedelta(days=365 * 5)
CHECK_SECONDS = 30
LATE_AFTER = timedelta(minutes=2)  # Past this, a reminder says that it is late (the bot was offline)
RETRY_DELAY = timedelta(minutes=1)
MAX_ATTEMPTS = 5

WHERE_CHOICES = [Choice(name="Here", value="here"), Choice(name="DM", value="dm")]
REPEAT_CHOICES = [Choice(name="Daily", value="daily"), Choice(name="Weekly", value="weekly")]

BAD_TIME = (
    "I could not tell when you mean. Try a length of time (`10m`, `2h30m`, `3 days`) "
    "or a time on the clock (`9pm`, `tomorrow 9am`, `friday 17:00`, `dec 25 6pm`)."
)
TOO_SOON = "That is in the past, or less than a minute away."
TOO_FAR = "That is more than 5 years away."
TOO_MANY = f"You already have {MAX_REMINDERS} reminders waiting. Cancel one with `/reminder cancel` first."
NOT_FOUND = "That reminder is not one of yours, or has already gone off. Pick one of the suggestions."


def error_embed(description):
    return Embed(description=description, color=Color.red())


def timestamp(moment, style="F"):
    """Turn a stored (UTC) time into a Discord timestamp, shown in each viewer's own timezone"""
    return f"<t:{int(moment.replace(tzinfo=timezone.utc).timestamp())}:{style}>"


def get_timezone(user_id):
    """Get the timezone a user picked for their birthday, which their reminders are read in too"""
    user_data = USERS.find_one({"_id.user_id": user_id, "timezone": {"$ne": None}}, {"timezone": 1})
    return user_data["timezone"] if user_data else EST_TIME.key


def get_reminders(user_id):
    return list(REMINDERS.find({"user_id": user_id}).sort("due", 1).limit(MAX_REMINDERS))


def describe(reminder):
    repeat = f" 🔁 {reminder['repeat']}" if reminder.get("repeat") else ""
    return f"{timestamp(reminder['due'], 'f')} ({timestamp(reminder['due'], 'R')}){repeat}\n{reminder['message']}"


class Reminders(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.deliver.start()

    def cog_unload(self):
        self.deliver.cancel()

    reminder = app_commands.Group(name="reminder", description="Have the bot remind you of something later")

    @reminder.command(name="set", description="Set a reminder for yourself")
    @app_commands.describe(
        when="How long from now (10m, 2h30m, 3 days) or a time (9pm, tomorrow 9am, friday 17:00, dec 25 6pm)",
        message="What to remind you of",
        where="Where to remind you (defaults to this channel)",
        repeat="Keep reminding you at this time (defaults to just the once)",
    )
    @app_commands.choices(where=WHERE_CHOICES, repeat=REPEAT_CHOICES)
    async def set(
        self,
        interaction: discord.Interaction,
        when: str,
        message: Range[str, 1, MAX_MESSAGE_LENGTH],
        where: Choice[str] = None,
        repeat: Choice[str] = None,
    ):
        server_name = interaction.guild.name if interaction.guild else "DM"
        log = get_logger(__name__, server=server_name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /reminder set [when={when}, where={where}, repeat={repeat}]")

        user_id = interaction.user.id
        now = datetime.utcnow()
        timezone_name = await run_db(get_timezone, user_id)
        due = parse_when(when, timezone_name, now)

        problem = None
        if due is None:
            problem = BAD_TIME
        elif due - now < MIN_DELAY:
            problem = TOO_SOON
        elif due - now > MAX_DELAY:
            problem = TOO_FAR
        elif await run_db(REMINDERS.count_documents, {"user_id": user_id}) >= MAX_REMINDERS:
            problem = TOO_MANY
        if problem:
            await interaction.response.send_message(embed=error_embed(problem), ephemeral=True)
            return

        # Outside of a server there is no channel to post in, so it can only be a DM
        in_dm = interaction.guild is None or (where is not None and where.value == "dm")
        reminder = {
            "user_id": user_id,
            "guild_id": interaction.guild.id if interaction.guild else None,
            "channel_id": None if in_dm else interaction.channel.id,
            "message": message,
            "due": due,
            "created_at": now,
            "repeat": repeat.value if repeat else None,
            "timezone": timezone_name,
        }
        await run_db(REMINDERS.insert_one, reminder)

        # Shown back as a timestamp, so a time that was misread is plain to see straight away
        description = f"⏰ I will remind you {timestamp(due)} ({timestamp(due, 'R')})"
        description += " by DM" if in_dm else " in this channel"
        if repeat:
            description += f", and {repeat.value} after that"
        embed = Embed(description=f"{description}.\n\n{message}", color=Color.green())
        embed.set_footer(text=f"Times you type are read as {timezone_name}. Change it with /birthday timezone")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @reminder.command(name="list", description="Show the reminders you have waiting")
    async def list(self, interaction: discord.Interaction):
        server_name = interaction.guild.name if interaction.guild else "DM"
        log = get_logger(__name__, server=server_name, user=interaction.user.name)
        log.info("COMMAND_INVOKED: /reminder list")

        reminders = await run_db(get_reminders, interaction.user.id)
        embed = Embed(title="Your reminders", color=Color.blue())
        if not reminders:
            embed.description = "You have no reminders waiting. Set one with `/reminder set`."
        for i, reminder in enumerate(reminders):
            place = f"<#{reminder['channel_id']}>" if reminder["channel_id"] else "DM"
            embed.add_field(name=f"{i + 1}. {place}", value=describe(reminder)[:1024], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @reminder.command(name="cancel", description="Cancel one of your reminders")
    @app_commands.describe(reminder="Start typing to search your reminders")
    async def cancel(self, interaction: discord.Interaction, reminder: str):
        server_name = interaction.guild.name if interaction.guild else "DM"
        log = get_logger(__name__, server=server_name, user=interaction.user.name)
        log.info("COMMAND_INVOKED: /reminder cancel")

        try:
            # Matched on the owner as well, nobody can cancel a reminder that is not theirs
            removed = await run_db(
                REMINDERS.find_one_and_delete,
                {"_id": ObjectId(reminder), "user_id": interaction.user.id},
            )
        except InvalidId:
            removed = None

        if removed is None:
            await interaction.response.send_message(embed=error_embed(NOT_FOUND), ephemeral=True)
            return

        await interaction.response.send_message(
            embed=Embed(description=f"✅ Cancelled your reminder:\n\n{describe(removed)}", color=Color.green()),
            ephemeral=True,
        )

    @cancel.autocomplete("reminder")
    async def cancel_autocomplete(self, interaction: discord.Interaction, current: str):
        reminders = await run_db(get_reminders, interaction.user.id)
        local_zone = ZoneInfo(await run_db(get_timezone, interaction.user.id))
        query = current.strip().lower()
        choices = []
        for reminder in reminders:
            # Suggestions are plain text, so the time is written out in the user's own timezone
            local = reminder["due"].replace(tzinfo=timezone.utc).astimezone(local_zone)
            name = f"{local:%b %d, %I:%M %p} - {reminder['message']}"
            if query in name.lower():
                choices.append(Choice(name=name[:100], value=str(reminder["_id"])))
        return choices

    # Delivery
    async def send_reminder(self, reminder, now):
        """Deliver a reminder to its channel, or by DM if that is where it goes or the channel is no use"""
        embed = Embed(description=reminder["message"], color=Color.gold())
        late = now - reminder["due"] > LATE_AFTER
        footer = f"Reminder set by you{' • late, I was offline when it was due' if late else ''}"
        if reminder.get("repeat"):
            footer += f" • repeats {reminder['repeat']}, stop it with /reminder cancel"
        embed.set_footer(text=footer)
        embed.timestamp = reminder["created_at"].replace(tzinfo=timezone.utc)

        channel = self.client.get_channel(reminder["channel_id"]) if reminder["channel_id"] else None
        if channel:
            try:
                # Only they are pinged, whatever mentions the reminder itself has in it
                await channel.send(
                    f"⏰ <@{reminder['user_id']}>",
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions(
                        everyone=False, roles=False, users=[discord.Object(reminder["user_id"])]
                    ),
                )
                return
            except discord.Forbidden:
                pass  # Not allowed to post there any more, a DM it is

        user = self.client.get_user(reminder["user_id"]) or await self.client.fetch_user(reminder["user_id"])
        await user.send("⏰ Reminder", embed=embed)

    @tasks.loop(seconds=CHECK_SECONDS)
    async def deliver(self):
        log = get_logger(__name__)
        try:
            while True:
                now = datetime.utcnow()
                # Taken out in one step, so no other check (or other copy of the bot) can send it too
                reminder = await run_db(
                    REMINDERS.find_one_and_delete, {"due": {"$lte": now}}, sort=[("due", 1)]
                )
                if reminder is None:
                    break

                # A repeating reminder goes straight back in for its next time, before anything can go wrong
                if reminder.get("repeat"):
                    upcoming = dict(reminder, attempts=0)
                    del upcoming["_id"]
                    upcoming["due"] = next_occurrence(
                        reminder["due"], reminder["repeat"], reminder.get("timezone") or EST_TIME.key, now
                    )
                    await run_db(REMINDERS.insert_one, upcoming)

                try:
                    await self.send_reminder(reminder, now)
                    log.info(f"REMINDER_SENT: to user_id={reminder['user_id']}")
                except discord.Forbidden:
                    log.warning(f"REMINDER_DROPPED: user_id={reminder['user_id']} cannot be reached")
                except discord.HTTPException:
                    # Discord was having a moment. Try again shortly, a few times, rather than lose it
                    attempts = reminder.get("attempts", 0) + 1
                    log.exception(f"REMINDER_FAILED: attempt {attempts} for user_id={reminder['user_id']}")
                    if attempts < MAX_ATTEMPTS and not reminder.get("repeat"):
                        retry = dict(reminder, attempts=attempts, due=now + RETRY_DELAY)
                        del retry["_id"]
                        await run_db(REMINDERS.insert_one, retry)
        except Exception:
            # An unhandled error would stop the loop for good
            log.exception("REMINDER_LOOP: Failed")

    @deliver.before_loop
    async def before_deliver(self):
        await self.client.wait_until_ready()
        await run_db(REMINDERS.create_index, [("due", 1)])
        await run_db(REMINDERS.create_index, [("user_id", 1), ("due", 1)])


async def setup(client):
    await client.add_cog(Reminders(client))
