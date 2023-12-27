import discord
from discord.ext import commands, tasks
from discord import Embed, Color, app_commands
import random
from time import sleep
from util.constants import INTEGER_CONVERSION_FAIL


class Fun(commands.Cog):
    def __init__(self, client):
        self.client = client

    @app_commands.command(description="Flip a coin.")
    async def flip(self, interaction : discord.Interaction):
        choice = random.choice(["Heads", "Tails"])
        embed = Embed(
            color = Color.green(),
            description = f"{choice} wins!"
        )
        embed.set_author(
            name = interaction.user.name, 
            icon_url=interaction.user.avatar.url)
        await interaction.response.send_message(embed=embed)
    
    @app_commands.command(description="Make a randomized decision.")
    async def decide(self, interaction : discord.Interaction, args : str):
        first, second = Embed(), Embed()
        first.color = second.color = Color.dark_orange()
        options = args.split() if "-" not in args else args.split("-")
        choice = random.choice(options)
        
        emoji = discord.utils.get(self.client.emojis, name="loading")
        first.description = "{} Loading Randomized Choice...".format(emoji)
        second.description = "**{}** was chosen!".format(choice)

        name = interaction.user.name
        icon_url = interaction.user.avatar.url
        
        first.set_author(
            name = name, 
            icon_url = icon_url)
        
        second.set_author(
            name = name,
            icon_url = icon_url
        )

        await interaction.response.send_message(embed=first)
        sleep(3)
        message = await interaction.original_response()
        await message.edit(embed=second)
    
    @app_commands.command(description="Chose a random number between x-y.")
    async def rng(self, interaction : discord.Interaction, first : str, last : str):
        try:
            first = int(first)
            last = int(last)
            assert first > float("-inf")
            assert last < float("inf")
        except ValueError:
            await interaction.response.send_message(embed = INTEGER_CONVERSION_FAIL)
            return
        except AssertionError:
            await interaction.response.send_message(
                embed = Embed(
                    description = "The bounds you set exceed the minimum or maximum values I can provide. Please chose a smaller range.",
                    color = Color.red()
                )
            )
            return
        
        choice = random.randint(first, last)
        embed = Embed(
                description = f"The number **{choice}** was chosen!",
                color = Color.green() 
            )
        embed.set_author(name = interaction.user.name, icon_url= interaction.user.avatar.url)
        embed.set_footer(text = f"Range provided : {first} - {last}")
        await interaction.response.send_message(
            embed = embed
        )

async def setup(client):
    await client.add_cog(Fun(client))