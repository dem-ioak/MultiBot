import discord
from discord import ButtonStyle, Embed, Color, app_commands
from discord.ext import commands
from discord.ui import Button, View, DynamicItem
from pymongo.errors import DuplicateKeyError
import asyncio
import os
import re
import time
from collections import defaultdict

from util.constants import USERS, FORBIDDEN_COMMAND, MY_USER_ID
from util.helper_functions import run_db, user_defaults
from util.log_manager import get_logger

# The only things that need changing for a new year: this, and the two server maps below
WRAPPED_YEAR = 2025
WRAPPED_DIR = f"Wrapped{WRAPPED_YEAR}"

# 2025 was recorded under the bare field name, before the year became part of it
SENT_FIELD = "sent_wrapped" if WRAPPED_YEAR == 2025 else f"sent_wrapped_{WRAPPED_YEAR}"
ANNOUNCED_FIELD = f"announced_wrapped_{WRAPPED_YEAR}"

BOARD_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]

# Display names, for servers that should be called something other than their Discord name
SERVER_NAMES = {
    1118721668970467428: "DA SUPA SERVER",
    529893177524617221: "DAMNIT",
    979199758591733780: "Mars 🩷",
}

SHARE_CHANNELS = {
    1118721668970467428: 1455772947884150894,
    529893177524617221: 1455772146469966001,
    979199758591733780: 1320886452183236678,
}

IMAGE_FILE_NAMES = [
    -1,
    "voice",
    "messages",
    "server_wide",
    "misc_one",
    "misc_two",
    "final"
]

BASE_TEXT = "Here are your {} stats!"
LAST_PAGE = 6
OPEN_COOLDOWN = 60
ANNOUNCE_DELAY = 1
FOOTER_TEXT = f"You can request your wrapped again at any time with /wrapped, once every {OPEN_COOLDOWN} seconds per server."

HEADER_TEXT = [
    -1,
    BASE_TEXT.format("***VOICE CHANNEL***"),
    BASE_TEXT.format("***MESSAGING***"),
    "Here are some general server stats!",
    BASE_TEXT.format("***MISCELLANEOUS***"),
    BASE_TEXT.format("***MISCELLANEOUS***"),
    f"To share your {WRAPPED_YEAR} Wrapped with this server, click the green 📨 button below!"
]

WRAPPED_ALREADY_SENT = Embed(description="Your wrapped has already been sent to this server!", color = Color.red())
NOT_ELIGIBLE = Embed(description=f"Sorry, but you're not eligible for a {WRAPPED_YEAR} Wrapped in any Yeat Bot server. This is because there was not enough activity to create one.",
                     color = Color.red())
NO_LONGER_AVAILABLE = Embed(description="This wrapped is no longer available.", color = Color.red())
SHARE_UNAVAILABLE = Embed(description="Sharing is not set up for this server.", color = Color.red())
SHARE_FAILED = Embed(description="Your wrapped could not be shared right now. Please try again later.", color = Color.red())
DMS_CLOSED = Embed(description="I could not DM you your wrapped. Allow direct messages from server members, then try again.", color = Color.red())

# When each (user, server) wrapped was last delivered, to enforce OPEN_COOLDOWN
last_opened = {}


def get_server_name(client, server_id):
    if server_id in SERVER_NAMES:
        return SERVER_NAMES[server_id]

    server = client.get_guild(server_id)
    return server.name if server else "SERVER"


def get_image_path(server_id, user_id, page):
    return f"{WRAPPED_DIR}/{server_id}/{user_id}/{IMAGE_FILE_NAMES[page]}.png"


def eligibility_check(user_id, server_id):
    return all(
        os.path.exists(get_image_path(server_id, user_id, page))
        for page in range(1, LAST_PAGE + 1)
    )


def get_wrapped_servers():
    """Every server that has wrapped data, sorted to guarantee order is preserved"""
    if not os.path.isdir(WRAPPED_DIR):
        return []
    return sorted(int(name) for name in os.listdir(WRAPPED_DIR) if name.isdigit())


# General Embed getters
def get_intro(server_name):
    embed = Embed()
    embed.color = Color.random()
    embed.title = f"Welcome to {server_name} WRAPPED {WRAPPED_YEAR}"
    embed.description = f"Dive into your yearly statistics within the server with ***{server_name} WRAPPED {WRAPPED_YEAR}!***\n"
    embed.description += "Explore details about your activity, how you interacted with others, and more.\n\n"
    embed.description += "***Developed*** by **@.demetri**\n***Designed*** by **@jerseyjhonnie**\n***Powered*** by <@762465131736989696>\n\n"
    embed.description += "Hit the ▶️ to get started!"
    embed.set_footer(text=FOOTER_TEXT)
    return embed


def get_cooldown(time_left):
    embed = Embed()
    embed.color = Color.red()
    embed.description = f"You can only request a {WRAPPED_YEAR} Wrapped for a single server once every minute. Please try again in {round(time_left)} seconds."
    return embed


def get_wrapped_sent(server_name):
    embed = Embed()
    embed.color = Color.green()
    embed.description = f"✅ Your {server_name} WRAPPED {WRAPPED_YEAR} has been sent to your DMs!"
    return embed


# Wrapped Stats Embed Getters
def get_stat_image(server_name, text):
    embed = Embed()
    embed.title = f"{server_name} WRAPPED {WRAPPED_YEAR}"
    embed.color = Color.random()
    embed.description = text
    embed.set_footer(text=FOOTER_TEXT)
    return embed


def get_page_view(server_id, page):
    """The buttons under a page of someone's wrapped"""
    view = View(timeout=None)
    view.add_item(WrappedPageButton(server_id, max(page - 1, 0), "◀️", disabled=page == 0))
    view.add_item(WrappedPageButton(server_id, min(page + 1, LAST_PAGE), "▶️", disabled=page == LAST_PAGE))
    if page == LAST_PAGE:
        view.add_item(ShareWrappedButton(server_id))
    return view


# All of the buttons below carry what they need in their custom_id and belong to whoever clicks them,
# so they never time out and keep working after the bot restarts
class WrappedPageButton(DynamicItem[Button], template=r"wrapped:page:(?P<server_id>\d+):(?P<page>\d+)"):
    def __init__(self, server_id, page, emoji, disabled=False):
        super().__init__(
            Button(
                style=ButtonStyle.gray,
                emoji=emoji,
                disabled=disabled,
                custom_id=f"wrapped:page:{server_id}:{page}",
            )
        )
        self.server_id = server_id
        self.page = page

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: Button, match: re.Match):
        return cls(int(match["server_id"]), int(match["page"]), item.emoji)

    async def callback(self, interaction: discord.Interaction):
        server_name = get_server_name(interaction.client, self.server_id)
        view = get_page_view(self.server_id, self.page)
        if self.page == 0:
            await interaction.response.edit_message(
                view=view, embed=get_intro(server_name), attachments=[]
            )
            return

        image_file_path = get_image_path(self.server_id, interaction.user.id, self.page)
        if self.page > LAST_PAGE or not os.path.exists(image_file_path):
            await interaction.response.send_message(embed=NO_LONGER_AVAILABLE, ephemeral=True)
            return

        image_file_name = IMAGE_FILE_NAMES[self.page] + ".png"
        image_file = discord.File(image_file_path, filename=image_file_name)
        embed = get_stat_image(server_name, HEADER_TEXT[self.page])
        embed.set_image(url="attachment://" + image_file_name)
        await interaction.response.edit_message(
            view=view, embed=embed, attachments=[image_file]
        )


class ShareWrappedButton(DynamicItem[Button], template=r"wrapped:share:(?P<server_id>\d+)"):
    def __init__(self, server_id):
        super().__init__(
            Button(style=ButtonStyle.green, emoji="📨", custom_id=f"wrapped:share:{server_id}")
        )
        self.server_id = server_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: Button, match: re.Match):
        return cls(int(match["server_id"]))

    async def callback(self, interaction: discord.Interaction):
        log = get_logger(__name__, user=interaction.user.name)
        await interaction.response.defer(ephemeral=True)

        image_file_name = IMAGE_FILE_NAMES[LAST_PAGE] + ".png"
        image_file_path = get_image_path(self.server_id, interaction.user.id, LAST_PAGE)
        channel = interaction.client.get_channel(SHARE_CHANNELS.get(self.server_id))
        if channel is None:
            await interaction.followup.send(embed=SHARE_UNAVAILABLE, ephemeral=True)
            return
        if not os.path.exists(image_file_path):
            await interaction.followup.send(embed=NO_LONGER_AVAILABLE, ephemeral=True)
            return

        # Claim the share in one step, so two quick clicks cannot both get through.
        # If it was already claimed nothing matches, and the upsert collides with the existing user
        primary_key = {"guild_id": self.server_id, "user_id": interaction.user.id}
        try:
            await run_db(
                USERS.update_one,
                {"_id": primary_key, SENT_FIELD: {"$ne": True}},
                {"$set": {SENT_FIELD: True}, "$setOnInsert": user_defaults()},
                upsert=True,
            )
        except DuplicateKeyError:
            await interaction.followup.send(embed=WRAPPED_ALREADY_SENT, ephemeral=True)
            return

        server_name = get_server_name(interaction.client, self.server_id)
        embed = Embed(title = f"{server_name} WRAPPED {WRAPPED_YEAR}")
        embed.color = Color.random()
        embed.description = f"{interaction.user.mention} shared their wrapped!"
        image_file = discord.File(image_file_path, filename=image_file_name)
        embed.set_image(url="attachment://" + image_file_name)
        try:
            await channel.send(embed = embed, file = image_file)
        except discord.HTTPException:
            # Nothing was posted, so give them their share back
            log.exception(f"Failed to share wrapped to server {self.server_id}")
            await run_db(USERS.update_one, {"_id": primary_key}, {"$set": {SENT_FIELD: False}})
            await interaction.followup.send(embed=SHARE_FAILED, ephemeral=True)
            return

        await interaction.followup.send("✅", ephemeral=True)


class WrappedViewButton(DynamicItem[Button], template=r"wrapped:open:(?P<server_id>\d+)"):
    def __init__(self, server_id, emoji):
        super().__init__(
            Button(style=ButtonStyle.gray, emoji=emoji, custom_id=f"wrapped:open:{server_id}")
        )
        self.server_id = server_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: Button, match: re.Match):
        return cls(int(match["server_id"]), item.emoji)

    async def callback(self, interaction: discord.Interaction):
        log = get_logger(__name__, user=interaction.user.name)
        user_id = interaction.user.id
        key = (user_id, self.server_id)
        time_left = last_opened.get(key, 0) + OPEN_COOLDOWN - time.monotonic()
        if time_left > 0:
            await interaction.response.send_message(
                embed=get_cooldown(time_left), ephemeral=True
            )
            return

        if not eligibility_check(user_id, self.server_id):
            await interaction.response.send_message(embed=NO_LONGER_AVAILABLE, ephemeral=True)
            return

        # Answer first, sending the DM can take longer than Discord waits for a response
        await interaction.response.defer(ephemeral=True)
        server_name = get_server_name(interaction.client, self.server_id)
        log.info(f"Delivering {server_name} WRAPPED {WRAPPED_YEAR}")
        try:
            await interaction.user.send(
                embed=get_intro(server_name), view=get_page_view(self.server_id, 0)
            )
        except discord.Forbidden:
            await interaction.followup.send(embed=DMS_CLOSED, ephemeral=True)
            return

        last_opened[key] = time.monotonic()
        await interaction.followup.send(embed=get_wrapped_sent(server_name), ephemeral=True)


class Wrapped(commands.Cog):
    def __init__(self, client):
        self.client = client

    @app_commands.command(description="Deliver Wrapped")
    async def wrapped(self, interaction: discord.Interaction):
        # This can be ran from DMs, where there is no server
        server_name = interaction.guild.name if interaction.guild else "DM"
        log = get_logger(__name__, server=server_name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /wrapped")

        user_id = interaction.user.id
        embed = Embed()
        embed.title = f"DISCORD WRAPPED {WRAPPED_YEAR}"
        embed_text = f"Which of the following servers would you like to view your {WRAPPED_YEAR} WRAPPED for?\n"

        valid_wrapped = [
            server_id
            for server_id in get_wrapped_servers()
            if eligibility_check(user_id, server_id)
        ][: len(BOARD_EMOJIS)]

        view = View(timeout=None)
        for i, server_id in enumerate(valid_wrapped):
            embed_text += f"{BOARD_EMOJIS[i]} **{get_server_name(self.client, server_id)}**\n"
            view.add_item(WrappedViewButton(server_id, BOARD_EMOJIS[i]))

        embed.color = Color.gold()
        embed.description = embed_text
        log.info(f"Valid wrapped: {valid_wrapped}")
        if valid_wrapped:
            await interaction.response.send_message(
                embed=embed,
                view=view,
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(embed = NOT_ELIGIBLE)

    @app_commands.command(description="Announce that Wrapped is available")
    @app_commands.default_permissions(administrator=True)
    async def announce(self, interaction: discord.Interaction):
        server_name = interaction.guild.name if interaction.guild else "DM"
        log = get_logger(__name__, server=server_name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /announce")
        if interaction.user.id != MY_USER_ID:
            await interaction.response.send_message(embed = FORBIDDEN_COMMAND, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        eligibility_map = defaultdict(list)
        for server in self.client.guilds:
            for user in server.members:
                if eligibility_check(user.id, server.id):
                    eligibility_map[user.id].append(server.id)

        # Anyone told by an earlier (possibly interrupted) run is not told again
        already_announced = set(
            await run_db(USERS.distinct, "_id.user_id", {ANNOUNCED_FIELD: True})
        )

        success = 0
        failed_users = []
        for user_id, server_ids in eligibility_map.items():
            if user_id in already_announced:
                continue

            user_obj = self.client.get_user(user_id)
            if user_obj is None:
                failed_users.append(str(user_id))
                continue

            embed = Embed(title = f"DISCORD WRAPPED {WRAPPED_YEAR}", color = Color.random())
            embed.description = f"Happy New Years Eve! 🍾 Your Discord Wrapped for {WRAPPED_YEAR} is available in the following servers:\n\n"

            for server_id in server_ids:
                embed.description += f"**{get_server_name(self.client, server_id)}**\n"

            embed.description += "\nTo access, run the Yeat Bot command **/wrapped** either here or in any one of these servers."
            embed.set_footer(text = "If you have any issues accessing, DM or ping @.demetri.")
            try:
                await user_obj.send(embed = embed)
            except discord.HTTPException:
                failed_users.append(user_obj.name)
            else:
                success += 1
                await run_db(
                    USERS.update_many,
                    {"_id.user_id": user_id},
                    {"$set": {ANNOUNCED_FIELD: True}},
                )

            # Sending DMs back to back is what gets a bot flagged for spam
            await asyncio.sleep(ANNOUNCE_DELAY)

        skipped = len(already_announced & eligibility_map.keys())
        response_text = f"Delivered with {success} successes and {len(failed_users)} failures."
        if skipped:
            response_text += f"\n{skipped} users were skipped, as they had already been told."
        if failed_users:
            response_text += "\n\nFailed to send to:\n"
            for username in failed_users:
                response_text += f"{username}\n"

        await interaction.followup.send(embed = Embed(description=response_text, color = Color.green()), ephemeral=True)


async def setup(client):
    client.add_dynamic_items(WrappedPageButton, ShareWrappedButton, WrappedViewButton)
    await client.add_cog(Wrapped(client))
