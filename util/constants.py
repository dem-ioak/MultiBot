from util.mongo_connection import MongoDBConnection
from discord import Embed, Color
from zoneinfo import ZoneInfo
from datetime import datetime, time

# MongoDB Stuff
mongo_connection = MongoDBConnection()
cluster = mongo_connection.get_cluster("discord")
SERVERS = cluster["servers"]
USERS = cluster["users"]
TEXT_CHANNELS = cluster["textchannels"]
VCS = cluster["voicechannels"]
WRAPPED = cluster["wrapped"]

MY_USER_ID = 739618992393682974

# Bot Messages
LEVEL_UP = ":tada: Congratulations! You just ranked up to level {}!"
JOINED_SERVER = "{} joined, there are now **{}** members in this server"
POLL_FAIL = "Failed to complete poll request. This is because this guild's polls channel has not yet been setup"
BANISHED_SUCCESS_MESSAGE = "{} has been sent to their own text channel, and all other channels in the server have been hidden.\nReason: {}"
BANISHED_FAILURE_MESSAGE = "{} could not be banished. This is either because they are already banished, or this server's Banished role has not been setup"
BAD_VIBES_MESSAGE = "DAILY BAD VIBES THAT IMMEDIATELY DISSIPATE ANY GOOD VIBES @everyone HAVE A HORRIBLE DAY"
BAD_VIBES_GIF = "https://tenor.com/view/satan-behemoth-darklord-satanic-satanism-gif-8384915"
GOOD_VIBES_MESSAGE = "Daily good vibes that immediately dissipate any bad vibes @everyone have a great day"
GOOD_VIBES_DEFAULT_GIF = "https://tenor.com/view/harry-button-6woodhalllane-buns-twerk-twerkygay-gif-18241756"
MAGNIFICENT_VIBES_GIF = GOOD_VIBES_DEFAULT_GIF
MAGNIFICENT_VIBES_MESSAGE = "DAILY ABSOLUTE MAGNIFICENT VIBES @everyone HAVE A GREAT DAY"
REGULAR_VIBES_MESSAGE = "Daily regular vibes that do not dissipate anything @everyone have a mediocre day"
REGULAR_VIBES_DEFAULT_MESSAGE = "https://tenor.com/view/elmo-shrug-idk-i-dont-know-who-knows-gif-17348455"
OWNER_STILL_PRESENT = "{} is still connected to this vc, so it cannot be claimed"

# Arrays
VALID_CHANNELS = ["vibe", "logs", "polls", "boards"]
VALID_FEATURES = ["vibes", "auto_chop", "levels"]
MONTH_DAYS = [-1, 31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
MONTH_NAMES = [
    "N/A", "January", "February", "March", "April", "May",
    "June", "July", "August", "September", "October", "November", "December" ]

WL_EMOJIS = [-1, "❌", "⏱️", "✅"]
DIRECTIONS = {"left" : "◀", "right" : "▶"}

# Embeds
OUT_OF_BOUNDS = Embed(description="❌ Cannot flip to the requested page. This is because the page is out of bounds.")
WATCHLIST_EMBED = Embed(title="WatchList")

VOICE_COMMANDS_EMBED = Embed(
    title="Voice Channel Commands", 
    description="Use the below buttons to configure your voice channel. These buttons will only function if the user who clicks them is A. Connected to a VC and B. The owner of that VC.",
    color = Color.blue())

WATCHLIST_EMBED.set_footer(text="Use the provided buttons below to make edits to existing watch list entries, or to add and remove listings.")
SUCCESSFUL_ADD = Embed(description="Successfully added the entry to the watchlist.", color = Color.green())
FAILED_ADD = Embed(description="Failed to add that entry. Please make sure the curent season is less than the total, and all season fields are numbers", color = Color.red())
SUCCESSFUL_EDIT = Embed(description="Successfully edited/deleted this entry of the watchlist.", color = Color.yellow())
FORBIDDEN_COMMAND = Embed(description="You are not allowed to use this command!", color = Color.red())
USER_NOT_CONNECTED = Embed(description="You must be connected to a vc to use voice commands.", color =  Color.red())
NOT_OWNER = Embed(description="You are not the owner of this vc!", color = Color.red())
INVALID_VC = Embed(description="You cannot use voice commands to this voice channel.", color = Color.red())

NO_USERNAME_EMBED = Embed(
    description="You have not yet set your **Last.FM** username! Use `fm set (username)` to get started.",
    color = Color.red())

INVALID_TIMEFRAME_EMBED = Embed(
    description="**The timeframe you entered is invalid. Here is a list of valid timeframe inputs.**",
    color = Color.red()
)
INVALID_TIMEFRAME_EMBED.add_field(name="1 Week", value=" - ".join(["7", "7d", "7day", "1w"]), inline=False)
INVALID_TIMEFRAME_EMBED.add_field(name = "1 Month", value=" - ".join(["1month", "30d", "30", "1m"]), inline=False)
INVALID_TIMEFRAME_EMBED.add_field(name = "3 Months", value=" - ".join(["3month", "90d", "90", "3m"]), inline=False)
INVALID_TIMEFRAME_EMBED.add_field(name = "6 Months", value=" - ".join(["6month", "180d", "180", "6m"]), inline=False)
INVALID_TIMEFRAME_EMBED.add_field(name = "1 year", value=" - ".join(["12month", "365d", "1year", "12m"]), inline=False)

INTEGER_CONVERSION_FAIL =Embed(
                    description="One of your fields contained a non-integer value. Please only use numbers",
                    color = Color.red()
                )

# TimeZones
EST_TIME = ZoneInfo("America/New_York")
VIBE_TIME = time(hour=13, minute = 0, second = 0, microsecond = 0, tzinfo = EST_TIME)
MIDNIGHT = time(hour=0, minute = 0, second = 0, microsecond = 0, tzinfo = EST_TIME)

# Rutgers Stuff
OPEN_SECTION_URL = "https://sis.rutgers.edu/soc/api/openSections.json?year=2023&term=9&campus=NB"
COURSES_URL = "https://sis.rutgers.edu/soc/api/courses.json?year=2023&term=9&campus=NB"
REGISTER_BASE_URL = "https://sims.rutgers.edu/webreg/editSchedule.htm?login=cas&semesterSelection=92023&indexList={}"
RU_LOGO = r"https://secoora.org/wp-content/uploads/2020/04/rutgers-logo-png-1454920.png"