import discord
from discord import Embed, Color, app_commands
from discord.app_commands import Choice
from discord.ext import commands, tasks
import json
import os

import util.dataclasses as DataClasses
from util.log_manager import get_logger
from util.helper_functions import run_db, user_defaults, generate_wl_page
from util.buttons.board_buttons import show_board_list
from util.buttons.watchlist_buttons import WatchListView

from util.constants import (
    WRAPPED,
    SERVERS,
    USERS,
    BANISHED_SUCCESS_MESSAGE,
    BANISHED_FAILURE_MESSAGE,
    MY_USER_ID,
    FORBIDDEN_COMMAND,
    DAMNIT_GUILD,
    WATCHLIST,
    BOARDS,
)
from util.log_messages import *

import logging

logger = logging.getLogger("data")

BANISHED_ROLE_NAME = "Banished"
NO_BANISHED_ROLE = "This server does not have a banished role set up. An admin can create one with `/banish_setup`."
MISSING_PERMISSIONS = (
    "I am not allowed to do that. I need the **Manage Roles** and **Manage Channels** permissions, "
    "and my role has to be above both the banished role and the user's highest role."
)


def get_banished_role(guild):
    """Get this server's banished role, None if it has not been set up or has been deleted"""
    server_data = SERVERS.find_one({"_id": guild.id})
    if server_data is None or server_data["banished_id"] == -1:
        return None
    return guild.get_role(server_data["banished_id"])


def get_banished_channel(guild, user_id):
    """Get the id of the channel a user is banished to, -1 if they are not banished"""
    user_data = USERS.find_one({"_id": {"guild_id": guild.id, "user_id": user_id}})
    return user_data["banished_id"] if user_data else -1


def set_banished_channel(guild, user_id, channel_id):
    USERS.update_one(
        {"_id": {"guild_id": guild.id, "user_id": user_id}},
        {"$set": {"banished_id": channel_id}, "$setOnInsert": user_defaults("banished_id")},
        upsert=True,
    )


async def apply_banishment(member, role, channel_id=-1):
    """Put a member into the banished state: role on, own channel present and visible, out of voice.

    Safe to repeat. It is used both to banish someone and to restore a banishment that has come
    undone, such as when the member left the server and came back. Returns their channel."""
    guild = member.guild
    had_role = role in member.roles
    if not had_role:
        await member.add_roles(role)

    try:
        channel = guild.get_channel(channel_id)
        if channel is None:
            channel = await guild.create_text_channel(
                name=f"{member.name}s corner",
                overwrites={
                    guild.default_role: discord.PermissionOverwrite(view_channel=False),
                    member: discord.PermissionOverwrite(view_channel=True, send_messages=True),
                    # Without this the bot can lose sight of the channel, and be unable to delete it later
                    guild.me: discord.PermissionOverwrite(view_channel=True, manage_channels=True),
                },
            )
        elif channel.overwrites_for(member).view_channel is not True:
            await channel.set_permissions(member, view_channel=True, send_messages=True)
    except discord.HTTPException:
        # Do not leave them with the role but nowhere to go
        if not had_role:
            await member.remove_roles(role)
        raise

    if member.voice:
        await member.move_to(None)

    return channel


class Moderation(commands.Cog):
    def __init__(self, client):
        self.client = client

    @app_commands.command(description="Add to this servers auto roles")
    async def autor(self, interaction: discord.Interaction, role: discord.Role):
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /autor [role={role}]")
        guild_id = interaction.guild.id
        server_data = SERVERS.find_one({"_id": guild_id})
        SERVERS.update_one(server_data, {"$push": {"auto_roles": role.id}})
        embed = Embed(
            description=f"Successfully added {role.mention} to this server's auto roles",
            color=Color.green(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(description="Set a channel to a specific bot feature")
    @app_commands.choices(
        channel_type=[
            Choice(name="Good Vibes / Birthday", value="vibe_id"),
            Choice(name="Logs", value="logs_id"),
            Choice(name="Polls", value="polls_id"),
            Choice(name="AFK Corner", value="afk_corner"),
            Choice(name="Join to Create", value="join_to_create"),
        ]
    )
    async def set_channel(
        self,
        interaction: discord.Interaction,
        channel_type: Choice[str],
        channel_id: str,
    ):
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /set_channel [channel_type={channel_type}, channel_id={channel_id}]")
        guild_id = interaction.guild.id
        channel_id = int(channel_id)

        choice_value = channel_type.value
        text_channel_ids = set(
            channel.id for channel in interaction.guild.text_channels
        )
        voice_channel_ids = set(
            channel.id for channel in interaction.guild.voice_channels
        )

        voice_categories = ["afk_corner", "join_to_create"]

        invalid_text_channel = (
            choice_value not in voice_categories and channel_id not in text_channel_ids
        )
        invalid_voice_channel = (
            choice_value in voice_categories and channel_id not in voice_channel_ids
        )

        if invalid_text_channel or invalid_voice_channel:
            await interaction.response.send_message(ephemeral=True, content="❌")

        else:
            server_data = SERVERS.find_one({"_id": guild_id})
            SERVERS.update_one(server_data, {"$set": {choice_value: channel_id}})
            await interaction.response.send_message(ephemeral=True, content="✅")

    @app_commands.command(description="Banish a user.")
    @app_commands.guild_only()
    async def banish(
        self, interaction: discord.Interaction, user: discord.Member, reason: str = None
    ):
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /banish [user={user}, reason={reason}]")
        await interaction.response.defer()

        guild = interaction.guild
        embed = Embed(color=Color.red())
        role = get_banished_role(guild)
        channel_id = get_banished_channel(guild, user.id)
        if role is None:
            embed.description = NO_BANISHED_ROLE
        elif user.bot:
            embed.description = "Bots cannot be banished."
        else:
            try:
                # Also ran for someone already banished, which repairs a missing role or channel
                channel = await apply_banishment(user, role, channel_id)
            except discord.Forbidden:
                embed.description = MISSING_PERMISSIONS
            else:
                set_banished_channel(guild, user.id, channel.id)
                if channel_id != -1:
                    embed.description = f"{user.mention} is already banished, to {channel.mention}."
                else:
                    embed.description = BANISHED_SUCCESS_MESSAGE.format(
                        user.mention, reason or "No reason given"
                    )
                    embed.color = Color.green()

        await interaction.followup.send(embed=embed)

    @app_commands.command(description="Unbanish a currently banished user.")
    @app_commands.guild_only()
    async def unbanish(self, interaction: discord.Interaction, user: discord.User):
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /unbanish [user={user}]")
        guild = interaction.guild
        banished_users = USERS.find({"_id.guild_id": guild.id, "banished_id": {"$ne": -1}})
        invalid_channels = [i["banished_id"] for i in banished_users]
        if interaction.channel.id in invalid_channels:
            await interaction.response.send_message(
                embed=Embed(
                    description="Please do not try to unbanish someone in a banished channel.",
                    color=Color.red(),
                )
            )
            return

        await interaction.response.defer()
        channel_id = get_banished_channel(guild, user.id)
        if channel_id == -1:
            await interaction.followup.send(
                embed=Embed(
                    description=f"Could not unbanish {user.mention}, as they are not currently banished",
                    color=Color.red(),
                )
            )
            return

        # Cleared first. Taking the role off below is otherwise seen as somebody removing it by hand
        set_banished_channel(guild, user.id, -1)
        try:
            # Whoever left the server has no role to take off, and the role or channel may be long gone
            role = get_banished_role(guild)
            member = guild.get_member(user.id)
            if member and role and role in member.roles:
                await member.remove_roles(role)

            channel = guild.get_channel(channel_id)
            if channel:
                await channel.delete()
        except discord.Forbidden:
            set_banished_channel(guild, user.id, channel_id)
            await interaction.followup.send(
                embed=Embed(description=MISSING_PERMISSIONS, color=Color.red())
            )
            return

        await interaction.followup.send(
            embed=Embed(
                description=f"Successfully unbanished {user.mention}",
                color=Color.green(),
            )
        )

    @app_commands.command(description="Create (or choose) the role given to banished users")
    @app_commands.describe(role="Use this existing role instead of creating one")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def banish_setup(self, interaction: discord.Interaction, role: discord.Role = None):
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /banish_setup [role={role}]")
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        try:
            role = role or get_banished_role(guild)
            if role is None:
                role = await guild.create_role(name=BANISHED_ROLE_NAME, reason="Banished role setup")

            # Hide everything from the role. Channels that follow their category are left to keep doing so
            hidden = 0
            channels = list(guild.categories) + [
                channel
                for channel in guild.channels
                if not isinstance(channel, discord.CategoryChannel) and not channel.permissions_synced
            ]
            for channel in channels:
                if channel.overwrites_for(role).view_channel is not False:
                    await channel.set_permissions(role, view_channel=False)
                    hidden += 1
        except discord.Forbidden:
            await interaction.followup.send(
                embed=Embed(description=MISSING_PERMISSIONS, color=Color.red())
            )
            return

        SERVERS.update_one({"_id": guild.id}, {"$set": {"banished_id": role.id}})
        await interaction.followup.send(
            embed=Embed(
                description=(
                    f"{role.mention} is now this server's banished role, and was hidden from {hidden} channels / categories.\n"
                    "Keep my role above it, and note that anyone with **Administrator** can still see everything."
                ),
                color=Color.green(),
            )
        )

    # Keeping banishments intact
    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Leaving the server takes the banished role off, so put it back on whoever returns"""
        channel_id = get_banished_channel(member.guild, member.id)
        role = get_banished_role(member.guild)
        if channel_id == -1 or role is None:
            return

        log = get_logger(__name__, server=member.guild.name, user=member.name)
        channel = await apply_banishment(member, role, channel_id)
        set_banished_channel(member.guild, member.id, channel.id)
        log.info("A banished user rejoined the server, their banishment has been restored")

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        """Somebody taking the banished role off by hand is an unbanish, so finish the job"""
        role = get_banished_role(after.guild) if len(before.roles) > len(after.roles) else None
        if role is None or role not in before.roles or role in after.roles:
            return

        channel_id = get_banished_channel(after.guild, after.id)
        if channel_id == -1:
            return

        log = get_logger(__name__, server=after.guild.name, user=after.name)
        set_banished_channel(after.guild, after.id, -1)
        channel = after.guild.get_channel(channel_id)
        if channel:
            await channel.delete()
        log.info("The banished role was removed by hand, this user has been unbanished")

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        """Hide new channels from banished users, unless something already does"""
        role = get_banished_role(channel.guild)
        if role is None:
            return

        hidden_from_everyone = channel.overwrites_for(channel.guild.default_role).view_channel is False
        if hidden_from_everyone or channel.overwrites_for(role).view_channel is False:
            return

        try:
            await channel.set_permissions(role, view_channel=False)
        except discord.NotFound:
            pass  # Temporary voice channels can be gone again by now

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        result = SERVERS.update_one(
            {"_id": role.guild.id, "banished_id": role.id}, {"$set": {"banished_id": -1}}
        )
        if result.modified_count:
            get_logger(__name__, server=role.guild.name).warning(
                "The banished role was deleted. Nobody can be banished until /banish_setup is ran again"
            )

    @app_commands.command(description="Initialize features channel")
    async def init_features(self, interaction: discord.Interaction):
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /init_features")
        if interaction.user.id != MY_USER_ID:
            await interaction.response.send_message(embed=FORBIDDEN_COMMAND)
            return

        # Answered first, as posting and filling in two messages can take longer than Discord waits
        await interaction.response.defer(ephemeral=True)
        placeholder_embed = Embed(
            description="This is a placeholder, which will be overrided momentarily.",
            color=Color.random(),
        )
        server_id = interaction.guild.id
        channel_id = interaction.channel.id
        board_placeholder = await interaction.channel.send(embed=placeholder_embed)
        watchlist_placeholder = await interaction.channel.send(embed=placeholder_embed)

        # Upserts, so this also works in a server whose boards / watchlist data was never created
        WATCHLIST.update_one(
            {"_id" : server_id},
            {
                "$set" : {"message_id" : watchlist_placeholder.id, "channel_id" : channel_id, "current_page" : 1},
                "$setOnInsert" : {"entries" : []},
            },
            upsert=True)
        BOARDS.update_one(
            {"_id" : server_id},
            {
                "$set" : {"message_id" : board_placeholder.id, "channel_id" : channel_id},
                "$setOnInsert" : {"boards" : []},
            },
            upsert=True)

        # Filled in right away, rather than the next time the bot starts up
        await show_board_list(board_placeholder, server_id, interaction.user.id)
        entries = WATCHLIST.find_one({"_id" : server_id})["entries"]
        await watchlist_placeholder.edit(
            embed=generate_wl_page(1, entries), view=WatchListView(self.client, watchlist_placeholder))
        await interaction.followup.send("✅", ephemeral=True)

    @app_commands.command(name = "compile", description = "Output a file consisting of a  user_id -> username map")
    @app_commands.default_permissions(administrator=True)
    async def compile_names(self, interaction : discord.Interaction):
        if interaction.user.id != MY_USER_ID:
            await interaction.response.send_message(
                embed = FORBIDDEN_COMMAND,
                ephemeral = True
            )
            return

        # Downloading every avatar takes far longer than Discord waits for a response
        await interaction.response.defer(ephemeral = True)
        export_dir = os.getenv("WRAPPED_EXPORT_DIR", "wrapped_export")

        async def save_image(asset, path):
            try:
                await asset.with_format("png").save(path)
            except (discord.HTTPException, ValueError):
                pass

        username_map = {}
        for guild in self.client.guilds:
            guild_dir = os.path.join(export_dir, str(guild.id))
            os.makedirs(guild_dir, exist_ok=True)

            if guild.icon:
                await save_image(guild.icon, os.path.join(guild_dir, "icon.png"))

            users = {user.id : user for user in guild.members}

            # People who have left still show up in the stats of those who stayed
            tracked_ids = await run_db(USERS.distinct, "_id.user_id", {"_id.guild_id" : guild.id})
            for user_id in tracked_ids:
                if user_id not in users:
                    try:
                        users[user_id] = await self.client.fetch_user(user_id)
                    except discord.HTTPException:
                        continue

            for user in users.values():
                if user.bot:
                    continue

                user_dir = os.path.join(guild_dir, str(user.id))
                os.makedirs(user_dir, exist_ok=True)
                await save_image(user.display_avatar, os.path.join(user_dir, "avatar.png"))
                username_map[user.id] = user.name

        with open(os.path.join(export_dir, "user_map.json"), "w") as f:
            json.dump(username_map, f)

        await interaction.followup.send(f"✅ Exported to `{os.path.abspath(export_dir)}`", ephemeral = True)


async def setup(client):
    await client.add_cog(Moderation(client))
