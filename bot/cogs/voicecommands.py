import discord
from discord import Embed, app_commands, Color
from discord.ext import commands, tasks

from util.buttons.voicecommands_buttons import VoiceView
from util.log_manager import get_logger

VOICE_COMMANDS_EMBED = Embed(
    title="Voice Channel Commands", 
    description="Use the below buttons to configure your voice channel. These buttons will only function if the user who clicks them is A. Connected to a VC and B. The owner of that VC.",
    color = Color.blue())

class VoiceCommands(commands.Cog):
    def __init__(self, client):
        self.client = client

    @app_commands.command(name = "voicecommands", description = "Load interface to modify your voice channel")
    async def voicecommands(self, interaction : discord.Interaction):
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info("COMMAND_INVOKED: /voicecommands")
        
        await interaction.response.send_message(embed = VOICE_COMMANDS_EMBED, view = VoiceView(self.client), ephemeral = True)

async def setup(client):
    await client.add_cog(VoiceCommands(client))