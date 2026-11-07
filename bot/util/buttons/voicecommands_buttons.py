"""Controls for join-to-create voice channels: the panel posted in each channel's chat, and the actions behind it
(also used by the /vc slash commands)"""
import time

import discord
from discord import Embed, Color
from discord.ui import Button, View, Modal, TextInput, UserSelect

from util.constants import VCS
from util.helper_functions import run_db

# Discord only lets a channel be renamed twice every 10 minutes, and makes the bot wait out anything more
RENAMES_PER_WINDOW, RENAME_WINDOW = 2, 600
recent_renames = {}  # channel id -> times of its recent renames


class VoiceError(Exception):
    """Something the user did wrong, shown to them as is"""


def error_embed(text):
    return Embed(description=text, color=Color.red())


def done_embed(text):
    return Embed(description=text, color=Color.green())


# Finding the channel and checking who may change it
def target_channel(interaction):
    """The voice channel being controlled: the one whose chat this is in, or else the one the user is in"""
    if isinstance(interaction.channel, discord.VoiceChannel):
        return interaction.channel
    voice = getattr(interaction.user, "voice", None)
    return voice.channel if voice else None


async def get_record(channel):
    record = await run_db(VCS.find_one, {"_id": channel.id})
    if not record or not record.get("owner"):
        raise VoiceError("This isn't a channel made with join to create, so it can't be changed here.")
    return record


async def owned_channel(interaction):
    """The channel and its record, if the user owns it. Raises VoiceError otherwise"""
    channel = target_channel(interaction)
    if channel is None:
        raise VoiceError("Join your voice channel first, or use this in its chat.")
    record = await get_record(channel)
    if record["owner"] != interaction.user.id:
        owner = channel.guild.get_member(record["owner"])
        if owner and owner in channel.members:
            raise VoiceError(f"Only the owner, {owner.mention}, can change this channel.")
        raise VoiceError("Only the owner can change this channel. They've left, so hit **Claim** to take it over.")
    return channel, record


# Changing the channel. Everything goes through channel.edit, which hands back the updated channel,
# so the panel always shows the new state rather than whatever Discord's events have caught up on
async def change_permissions(channel, changes):
    """changes: {member or role: {permission: True / False / None}}"""
    overwrites = dict(channel.overwrites)
    for target, perms in changes.items():
        overwrite = overwrites.get(target, discord.PermissionOverwrite())
        overwrite.update(**perms)
        if overwrite.is_empty():
            overwrites.pop(target, None)
        else:
            overwrites[target] = overwrite
    return await channel.edit(overwrites=overwrites) or channel


def is_locked(channel):
    return channel.overwrites_for(channel.guild.default_role).connect is False


def is_hidden(channel):
    return channel.overwrites_for(channel.guild.default_role).view_channel is False


async def toggle_lock(channel, record):
    """Lock so nobody new can join. Whoever is in the call (and the owner) can still get back in"""
    locking = not is_locked(channel)
    changes = {channel.guild.default_role: {"connect": False if locking else None}}
    if locking:
        keep = [m for m in channel.members if not m.bot] + [channel.guild.me]
        owner = channel.guild.get_member(record["owner"])
        if owner:
            keep.append(owner)
        for member in keep:
            changes[member] = {"connect": True, "view_channel": True}
    return await change_permissions(channel, changes), "Locked. Nobody new can join." if locking else "Unlocked."


async def toggle_hide(channel, record):
    hiding = not is_hidden(channel)
    changes = {channel.guild.default_role: {"view_channel": False if hiding else None}}
    if hiding:
        keep = [m for m in channel.members if not m.bot] + [channel.guild.me]
        owner = channel.guild.get_member(record["owner"])
        if owner:
            keep.append(owner)
        for member in keep:
            changes[member] = {"view_channel": True}
    return await change_permissions(channel, changes), "Hidden from everyone not let in." if hiding else "Visible again."


def split_targets(users, record, skip_owner=True):
    """People an action applies to, and a note about anyone left out"""
    people, skipped = [], []
    for user in users:
        if user.bot or (skip_owner and user.id == record["owner"]):
            skipped.append(user)
        else:
            people.append(user)
    note = f" (skipped {', '.join(u.mention for u in skipped)})" if skipped else ""
    return people, note


async def let_in(channel, record, users):
    people, note = split_targets(users, record, skip_owner=False)
    if not people:
        raise VoiceError("Pick someone to let in.")
    channel = await change_permissions(channel, {m: {"connect": True, "view_channel": True} for m in people})
    return channel, f"Let in {', '.join(m.mention for m in people)}{note}."


async def block(channel, record, users):
    people, note = split_targets(users, record)
    if not people:
        raise VoiceError("You can't block yourself, the owner, or bots.")
    channel = await change_permissions(channel, {m: {"connect": False, "view_channel": False} for m in people})
    for member in people:
        if member in channel.members:
            await member.move_to(None)
    return channel, f"Blocked {', '.join(m.mention for m in people)}{note}."


async def kick(channel, record, users):
    people, note = split_targets(users, record)
    inside = [m for m in people if m in channel.members]
    if not inside:
        raise VoiceError("None of them are in the call.")
    for member in inside:
        await member.move_to(None)
    return channel, f"Kicked {', '.join(m.mention for m in inside)}{note}. They can rejoin unless you block them."


async def transfer(channel, record, user):
    if user.bot or user.id == record["owner"]:
        raise VoiceError("Pick someone else to hand the channel to.")
    await run_db(VCS.update_one, {"_id": channel.id}, {"$set": {"owner": user.id}})
    record["owner"] = user.id
    channel = await change_permissions(channel, {user: {"connect": True, "view_channel": True}})
    return channel, f"{user.mention} owns this channel now."


async def claim(interaction):
    """Take over a channel whose owner has left"""
    channel = target_channel(interaction)
    if channel is None or interaction.user not in channel.members:
        raise VoiceError("You have to be in the call to claim it.")
    record = await get_record(channel)
    if record["owner"] == interaction.user.id:
        raise VoiceError("You already own this channel.")
    owner = channel.guild.get_member(record["owner"])
    if owner and owner in channel.members:
        raise VoiceError(f"{owner.mention} is still in the call, so it can't be claimed.")
    await run_db(VCS.update_one, {"_id": channel.id}, {"$set": {"owner": interaction.user.id}})
    record["owner"] = interaction.user.id
    return channel, record, "You own this channel now."


def check_rename(channel):
    now = time.monotonic()
    recent = [t for t in recent_renames.get(channel.id, []) if now - t < RENAME_WINDOW]
    recent_renames[channel.id] = recent
    if len(recent) >= RENAMES_PER_WINDOW:
        wait = int(RENAME_WINDOW - (now - recent[0])) // 60 + 1
        raise VoiceError(f"Discord only allows 2 renames every 10 minutes. Try again in about {wait} minute{'s' if wait != 1 else ''}.")


async def rename(channel, record, name):
    check_rename(channel)
    channel = await channel.edit(name=name) or channel
    recent_renames[channel.id].append(time.monotonic())
    return channel, f"Renamed to **{name}**."


async def set_limit(channel, record, limit):
    channel = await channel.edit(user_limit=limit) or channel
    return channel, "No user limit." if limit == 0 else f"Limit set to {limit}."


# The panel
def status_embed(channel, record):
    owner = channel.guild.get_member(record["owner"])
    locked, hidden = is_locked(channel), is_hidden(channel)
    allowed, blocked = [], []
    for target, overwrite in channel.overwrites.items():
        if not isinstance(target, discord.Member) or target.bot or target.id == record["owner"]:
            continue
        if overwrite.connect is False:
            blocked.append(target.mention)
        elif overwrite.connect is True:
            allowed.append(target.mention)

    embed = Embed(title=f"🔊 {channel.name}", color=Color.red() if locked else Color.blurple())
    embed.add_field(name="Owner", value=f"👑 {owner.mention}" if owner else "👑 Nobody (hit Claim)", inline=True)
    embed.add_field(name="Access", value="🔒 Locked" if locked else "🔓 Open", inline=True)
    embed.add_field(name="Visibility", value="👻 Hidden" if hidden else "👀 Visible", inline=True)
    embed.add_field(name="Limit", value=f"👥 {channel.user_limit}" if channel.user_limit else "👥 None", inline=True)
    if allowed:
        embed.add_field(name="✅ Let in", value=" ".join(allowed)[:1024], inline=False)
    if blocked:
        embed.add_field(name="⛔ Blocked", value=" ".join(blocked)[:1024], inline=False)
    embed.set_footer(text="Only the owner can use these. If the owner leaves, anyone in the call can claim it. Also: /vc")
    return embed


def panel(channel, record):
    return {"embed": status_embed(channel, record), "view": VoicePanel(is_locked(channel), is_hidden(channel))}


async def refresh_panel(channel, record):
    """Bring the panel in the channel's chat up to date after a change made from anywhere"""
    message_id = record.get("panel_message")
    if not message_id:
        return
    try:
        await channel.get_partial_message(message_id).edit(**panel(channel, record))
    except discord.HTTPException:
        pass  # Deleted by someone, or the channel is gone


async def post_panel(channel):
    """Post a fresh panel in the channel's chat and remember it"""
    record = await get_record(channel)
    message = await channel.send(**panel(channel, record))
    old = record.get("panel_message")
    await run_db(VCS.update_one, {"_id": channel.id}, {"$set": {"panel_message": message.id}})
    if old:
        try:
            await channel.get_partial_message(old).delete()
        except discord.HTTPException:
            pass
    return message


async def run(interaction, action, *args):
    """Run an owner-only action from the panel, then show the result"""
    try:
        channel, record = await owned_channel(interaction)
        channel, note = await action(channel, record, *args)
    except VoiceError as error:
        await interaction.response.send_message(embed=error_embed(str(error)), ephemeral=True)
        return
    await show_result(interaction, channel, record, note)


async def show_result(interaction, channel, record, note):
    on_panel = interaction.message is not None and interaction.message.id == record.get("panel_message")
    if interaction.message is not None and not on_panel:
        # A private copy of the panel (from /voicecommands): update it in place
        await interaction.response.edit_message(**panel(channel, record))
        await interaction.followup.send(embed=done_embed(note), ephemeral=True)
    else:
        await interaction.response.send_message(embed=done_embed(note), ephemeral=True)
    await refresh_panel(channel, record)


class VoicePanel(View):
    """Persistent: the custom ids stay the same, so panels keep working after the bot restarts"""

    def __init__(self, locked=False, hidden=False):
        super().__init__(timeout=None)
        self.lock.label, self.lock.emoji = ("Unlock", "🔓") if locked else ("Lock", "🔒")
        self.hide.label, self.hide.emoji = ("Show", "👀") if hidden else ("Hide", "👻")

    @discord.ui.button(label="Lock", emoji="🔒", style=discord.ButtonStyle.secondary, custom_id="vc:lock", row=0)
    async def lock(self, interaction, button):
        await run(interaction, toggle_lock)

    @discord.ui.button(label="Hide", emoji="👻", style=discord.ButtonStyle.secondary, custom_id="vc:hide", row=0)
    async def hide(self, interaction, button):
        await run(interaction, toggle_hide)

    @discord.ui.button(label="Rename", emoji="✏️", style=discord.ButtonStyle.secondary, custom_id="vc:rename", row=0)
    async def rename_button(self, interaction, button):
        try:
            channel, _ = await owned_channel(interaction)
            check_rename(channel)
        except VoiceError as error:
            await interaction.response.send_message(embed=error_embed(str(error)), ephemeral=True)
            return
        await interaction.response.send_modal(RenameModal(channel.name))

    @discord.ui.button(label="Limit", emoji="👥", style=discord.ButtonStyle.secondary, custom_id="vc:limit", row=0)
    async def limit(self, interaction, button):
        try:
            channel, _ = await owned_channel(interaction)
        except VoiceError as error:
            await interaction.response.send_message(embed=error_embed(str(error)), ephemeral=True)
            return
        await interaction.response.send_modal(LimitModal(channel.user_limit))

    @discord.ui.button(label="Claim", emoji="👑", style=discord.ButtonStyle.success, custom_id="vc:claim", row=0)
    async def claim_button(self, interaction, button):
        try:
            channel, record, note = await claim(interaction)
        except VoiceError as error:
            await interaction.response.send_message(embed=error_embed(str(error)), ephemeral=True)
            return
        await show_result(interaction, channel, record, note)

    @discord.ui.select(cls=UserSelect, placeholder="✅ Let people in", min_values=1, max_values=10, custom_id="vc:allow", row=1)
    async def allow(self, interaction, select):
        await run(interaction, let_in, select.values)

    @discord.ui.select(cls=UserSelect, placeholder="⛔ Block people (kicks them too)", min_values=1, max_values=10, custom_id="vc:block", row=2)
    async def block_select(self, interaction, select):
        await run(interaction, block, select.values)

    @discord.ui.select(cls=UserSelect, placeholder="👢 Kick from the call", min_values=1, max_values=10, custom_id="vc:kick", row=3)
    async def kick_select(self, interaction, select):
        await run(interaction, kick, select.values)

    @discord.ui.select(cls=UserSelect, placeholder="👑 Give the channel to someone", min_values=1, max_values=1, custom_id="vc:transfer", row=4)
    async def transfer_select(self, interaction, select):
        await run(interaction, transfer, select.values[0])


class RenameModal(Modal, title="Rename your channel"):
    def __init__(self, current):
        super().__init__()
        self.name = TextInput(label="New name", default=current, min_length=1, max_length=100)
        self.add_item(self.name)

    async def on_submit(self, interaction):
        await run(interaction, rename, self.name.value.strip())


class LimitModal(Modal, title="User limit"):
    def __init__(self, current):
        super().__init__()
        self.limit = TextInput(label="How many people? 0 for no limit", default=str(current or 0), min_length=1, max_length=2)
        self.add_item(self.limit)

    async def on_submit(self, interaction):
        value = self.limit.value.strip()
        if not value.isdigit():
            await interaction.response.send_message(embed=error_embed("The limit has to be a number from 0 to 99."), ephemeral=True)
            return
        await run(interaction, set_limit, int(value))
