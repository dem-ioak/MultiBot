import discord
from discord import app_commands
from discord.app_commands import Range
from discord.ext import commands

from util.buttons.voicecommands_buttons import (
    VoicePanel, VoiceError, error_embed, done_embed, owned_channel, target_channel, get_record, claim,
    toggle_lock, toggle_hide, let_in, block, kick, transfer, rename, set_limit, is_locked, is_hidden,
    post_panel, refresh_panel,
)
from util.log_manager import get_logger


class VoiceCommands(commands.Cog):
    vc = app_commands.Group(name="vc", description="Control your voice channel", guild_only=True)

    def __init__(self, client):
        self.client = client
        # Panels posted before a restart keep working
        client.add_view(VoicePanel())

    async def respond(self, interaction, action, *args, needs=None):
        """Run an owner-only action from a slash command"""
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /{interaction.command.qualified_name}")
        try:
            channel, record = await owned_channel(interaction)
            if needs is not None and needs(channel):
                raise VoiceError(needs(channel))
            channel, note = await action(channel, record, *args)
        except VoiceError as error:
            await interaction.response.send_message(embed=error_embed(str(error)), ephemeral=True)
            return
        await interaction.response.send_message(embed=done_embed(note), ephemeral=True)
        await refresh_panel(channel, record)

    @vc.command(description="Stop anyone new from joining your channel")
    async def lock(self, interaction: discord.Interaction):
        await self.respond(interaction, toggle_lock, needs=lambda c: "It's already locked." if is_locked(c) else None)

    @vc.command(description="Let anyone join your channel again")
    async def unlock(self, interaction: discord.Interaction):
        await self.respond(interaction, toggle_lock, needs=lambda c: "It isn't locked." if not is_locked(c) else None)

    @vc.command(description="Hide your channel from everyone you haven't let in")
    async def hide(self, interaction: discord.Interaction):
        await self.respond(interaction, toggle_hide, needs=lambda c: "It's already hidden." if is_hidden(c) else None)

    @vc.command(description="Make your channel visible again")
    async def show(self, interaction: discord.Interaction):
        await self.respond(interaction, toggle_hide, needs=lambda c: "It isn't hidden." if not is_hidden(c) else None)

    @vc.command(description="Let someone in, even when the channel is locked or hidden")
    async def allow(self, interaction: discord.Interaction, user: discord.Member):
        await self.respond(interaction, let_in, [user])

    @vc.command(description="Block someone from your channel, and kick them if they're in it")
    async def block(self, interaction: discord.Interaction, user: discord.Member):
        await self.respond(interaction, block, [user])

    @vc.command(description="Kick someone from the call (they can rejoin unless blocked)")
    async def kick(self, interaction: discord.Interaction, user: discord.Member):
        await self.respond(interaction, kick, [user])

    @vc.command(description="Give your channel to someone else")
    async def transfer(self, interaction: discord.Interaction, user: discord.Member):
        await self.respond(interaction, transfer, user)

    @vc.command(description="Rename your channel")
    async def rename(self, interaction: discord.Interaction, name: Range[str, 1, 100]):
        await self.respond(interaction, rename, name)

    @vc.command(description="Set how many people can join (0 for no limit)")
    async def limit(self, interaction: discord.Interaction, people: Range[int, 0, 99]):
        await self.respond(interaction, set_limit, people)

    @vc.command(description="Take over the channel you're in once its owner has left")
    async def claim(self, interaction: discord.Interaction):
        get_logger(__name__, server=interaction.guild.name, user=interaction.user.name).info("COMMAND_INVOKED: /vc claim")
        try:
            channel, record, note = await claim(interaction)
        except VoiceError as error:
            await interaction.response.send_message(embed=error_embed(str(error)), ephemeral=True)
            return
        await interaction.response.send_message(embed=done_embed(note), ephemeral=True)
        await refresh_panel(channel, record)

    @vc.command(description="Post the control panel in your channel's chat")
    async def panel(self, interaction: discord.Interaction):
        await self.post(interaction)

    @app_commands.command(name="voicecommands", description="Post the control panel in your voice channel's chat")
    @app_commands.guild_only()
    async def voicecommands(self, interaction: discord.Interaction):
        await self.post(interaction)

    async def post(self, interaction):
        get_logger(__name__, server=interaction.guild.name, user=interaction.user.name).info("COMMAND_INVOKED: panel")
        channel = target_channel(interaction)
        try:
            if channel is None:
                raise VoiceError("Join your voice channel first, or use this in its chat.")
            await get_record(channel)
        except VoiceError as error:
            await interaction.response.send_message(embed=error_embed(str(error)), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            message = await post_panel(channel)
        except discord.HTTPException:
            await interaction.followup.send(embed=error_embed("I couldn't post in that channel's chat."), ephemeral=True)
            return
        await interaction.followup.send(embed=done_embed(f"Panel posted: {message.jump_url}"), ephemeral=True)


async def setup(client):
    await client.add_cog(VoiceCommands(client))
