import discord
from discord.ext import commands, tasks
from discord import Embed, Color, app_commands
from discord.app_commands import Choice

from util.constants import MONTH_DAYS, MONTH_NAMES, INTEGER_CONVERSION_FAIL, USERS
from util.log_manager import get_logger
from datetime import date

MONTH_CHOICES = [Choice(name = MONTH_NAMES[i], value = i) for i in range(1, 13)]
INVALID_DATE = "The date you entered was invalid. Please make sure the the day is valid for the given month, and the year is correct."
BIRTHDAY_SUCCESS = "Successfully set **{} {}, {}** as your birthday"

class Birthday(commands.Cog):
    def __init__(self, client):
        self.client = client
    
    birthday = app_commands.Group(name = "birthday", description = "Commands involving user birthdays")

    @birthday.command(description = "Set your birthday")
    @app_commands.choices(month = MONTH_CHOICES)
    async def set(self, interaction : discord.Interaction, month : Choice[int], day : str, year : str):
        log = get_logger(__name__, server = interaction.guild.name, user = interaction.user.name)
        current_day = date.today()
        month_num = month.value
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
            USERS.update_many({"_id.user_id" : interaction.user.id}, {"$set" : {"birthday" : birthday_str}})
            await interaction.response.send_message(embed = Embed(
                description=BIRTHDAY_SUCCESS.format(MONTH_NAMES[month_num], day, year),
                color = Color.blue()
            ))
            log.info(f"Successfully set this user's birthday to : {birthday_str}")


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