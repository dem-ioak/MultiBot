import discord
from discord import Embed, Color, app_commands
from discord.app_commands import Choice
from discord.ext import commands, tasks

from util.constants import SERVERS

class Moderation(commands.Cog):
    def __init__(self, client):
        self.client = client
    
    @app_commands.command(description = "Add to this servers auto roles")
    async def autor(self, interaction : discord.Interaction, role : discord.Role):
        guild_id = interaction.guild.id
        server_data = SERVERS.find_one({"_id" : guild_id})
        SERVERS.update_one(server_data,
            {"$push" : {"auto_roles" : role.id}
        })
        embed = Embed(
            description = "Successfully added {role.mention} to this server's auto roles",
            color = Color.green()
        )
        await interaction.response.send_message(embed = embed)
    
    @app_commands.command()
    @app_commands.describe(channel = "Select a channel")
    @app_commands.choices(channel = [
        Choice(name = "Good Vibes", value = "vibe"),
        Choice(name = "Logs", value = "logs"),
        Choice(name = "Polls", value = "polls")
    ])
    async def set_channel(self, interaction : discord.Interaction, channel : Choice[str]):
        pass
    


async def setup(client):
    await client.add_cog(Moderation(client))