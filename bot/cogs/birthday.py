import discord
from discord.ext import commands, tasks
from discord import Embed, Color, app_commands
from discord.app_commands import Choice

from util.constants import MONTH_DAYS, MONTH_NAMES, INTEGER_CONVERSION_FAIL, USERS, EST_TIME
from util.log_manager import get_logger
from datetime import date
from zoneinfo import available_timezones

MONTH_CHOICES = [Choice(name = MONTH_NAMES[i], value = i) for i in range(1, 13)]
INVALID_DATE = "The date you entered was invalid. Please make sure the the day is valid for the given month, and the year is correct."
INVALID_TIMEZONE = "**{}** is not a timezone I know. Start typing your nearest major city and pick one of the suggestions."
BIRTHDAY_SUCCESS = "Successfully set **{} {}, {}** as your birthday. It will be announced at midnight **{}** time"
TIMEZONE_SUCCESS = "Your birthday will now be announced at midnight **{}** time"
NO_BIRTHDAY = "You have not set a birthday yet. Use `/birthday set` to get started."

ALL_TIMEZONES = sorted(available_timezones())
TIMEZONE_NAMES = {timezone.lower() : timezone for timezone in ALL_TIMEZONES}

# Suggested before the user has typed anything, as only 25 options can be shown at once
COMMON_TIMEZONES = [
    "America/New_York", "America/Chicago", "America/Denver", "America/Phoenix", "America/Los_Angeles",
    "America/Anchorage", "Pacific/Honolulu", "America/Toronto", "America/Mexico_City", "America/Sao_Paulo",
    "Europe/London", "Europe/Paris", "Europe/Berlin", "Europe/Athens", "Europe/Istanbul", "Africa/Lagos",
    "Asia/Dubai", "Asia/Kolkata", "Asia/Manila", "Asia/Shanghai", "Asia/Seoul", "Asia/Tokyo",
    "Australia/Perth", "Australia/Sydney", "Pacific/Auckland",
]

def to_timezone_query(text):
    return text.strip().lower().replace(" ", "_")

def find_timezone(text):
    """Get the proper name of the timezone a user entered, None if there is no such timezone"""
    return TIMEZONE_NAMES.get(to_timezone_query(text))

async def timezone_autocomplete(interaction : discord.Interaction, current : str):
    query = to_timezone_query(current)
    matches = [timezone for timezone in ALL_TIMEZONES if query in timezone.lower()] if query else COMMON_TIMEZONES
    return [Choice(name = timezone, value = timezone) for timezone in matches[:25]]

class Birthday(commands.Cog):
    def __init__(self, client):
        self.client = client
    
    birthday = app_commands.Group(name = "birthday", description = "Commands involving user birthdays")

    @birthday.command(description = "Set your birthday")
    @app_commands.choices(month = MONTH_CHOICES)
    @app_commands.describe(timezone = "Announce it at midnight in this timezone, type your nearest major city (defaults to Eastern)")
    @app_commands.autocomplete(timezone = timezone_autocomplete)
    async def set(self, interaction : discord.Interaction, month : Choice[int], day : str, year : str, timezone : str = None):
        log = get_logger(__name__, server = interaction.guild.name, user = interaction.user.name)
        log.info(f"COMMAND_INVOKED: /birthday set [month={month}, day={day}, year={year}, timezone={timezone}]")
        current_day = date.today()
        month_num = month.value
        if timezone and not find_timezone(timezone):
            await interaction.response.send_message(
                embed=Embed(
                    description=INVALID_TIMEZONE.format(timezone),
                    color = Color.red()))
            return

        try:
            day = int(day)
            year = int(year)
            assert day >= 1 and day <= MONTH_DAYS[month_num]
            assert year < current_day.year
        except ValueError:
            await interaction.response.send_message(embed = INTEGER_CONVERSION_FAIL)
        
        except AssertionError:
            await interaction.response.send_message(
                embed=Embed(
                    description=INVALID_DATE,
                    color = Color.red()))
            
        else:
            birthday_obj = date(
                year = year,
                month = month_num,
                day = day
            )
            birthday_str = str(birthday_obj)
            to_set = {"birthday" : birthday_str}
            if timezone:
                timezone = find_timezone(timezone)
                to_set["timezone"] = timezone
            else:
                # Keep whichever timezone they chose before
                user_data = USERS.find_one({"_id.user_id" : interaction.user.id, "timezone" : {"$ne" : None}})
                timezone = user_data["timezone"] if user_data else EST_TIME.key

            USERS.update_many({"_id.user_id" : interaction.user.id}, {"$set" : to_set})
            await interaction.response.send_message(embed = Embed(
                description=BIRTHDAY_SUCCESS.format(MONTH_NAMES[month_num], day, year, timezone),
                color = Color.blue()
            ))
            log.info(f"Successfully set this user's birthday to : {birthday_str}")

    @birthday.command(description = "Set the timezone your birthday is announced in")
    @app_commands.describe(timezone = "Type your nearest major city and pick one of the suggestions")
    @app_commands.autocomplete(timezone = timezone_autocomplete)
    async def timezone(self, interaction : discord.Interaction, timezone : str):
        log = get_logger(__name__, server = interaction.guild.name, user = interaction.user.name)
        log.info(f"COMMAND_INVOKED: /birthday timezone [timezone={timezone}]")
        timezone_name = find_timezone(timezone)
        if not timezone_name:
            await interaction.response.send_message(
                embed=Embed(
                    description=INVALID_TIMEZONE.format(timezone),
                    color = Color.red()))
            return

        USERS.update_many({"_id.user_id" : interaction.user.id}, {"$set" : {"timezone" : timezone_name}})
        has_birthday = USERS.find_one({"_id.user_id" : interaction.user.id, "birthday" : {"$ne" : None}})
        description = TIMEZONE_SUCCESS.format(timezone_name)
        if not has_birthday:
            description += f"\n{NO_BIRTHDAY}"

        await interaction.response.send_message(embed = Embed(description=description, color = Color.blue()))
        log.info(f"Successfully set this user's timezone to : {timezone_name}")


    @birthday.command(description = "Generate a list of the nearest birthdays")
    async def list(self, interaction : discord.Interaction):
        log = get_logger(__name__, server = interaction.guild.name, user = interaction.user.name)
        log.info("COMMAND_INVOKED: /birthday list")
        
        all_birthdays = USERS.find({"_id.guild_id" : interaction.guild.id, "birthday" : {"$ne" : None}})
        sorted_days = []
        today = date.today()
        embed = Embed(
            title = "Nearest Birthdays",
            color = Color.blue()
        )
        description = ""
        rank = 1
        for user in all_birthdays:
            birthday = user["birthday"]
            user_id = user["_id"]["user_id"]
            year, month, day = [int(i) for i in birthday.split("-")]
            birthday_str = f"{MONTH_NAMES[month]} {day}"
            birthday_obj = date(today.year, month, day)
            if (birthday_obj - today).days < 0:
                birthday_obj = date(today.year + 1, month, day)
            
            sorted_days.append((user_id, birthday_str, (birthday_obj - today).days))

        sorted_days.sort(key = lambda x : x[2])
        for val in sorted_days:
            user_id, _date, days_until = val
            member_obj = interaction.guild.get_member(user_id)
            if member_obj is None:
                continue

            description += f"{rank} **{member_obj.name}** - {_date} ({days_until} days)\n"
            rank += 1
        
        embed.description = description
        embed.set_footer(text = "Set your birthday using /birthday set!")
        await interaction.response.send_message(embed = embed)



            

async def setup(client):
    await client.add_cog(Birthday(client))