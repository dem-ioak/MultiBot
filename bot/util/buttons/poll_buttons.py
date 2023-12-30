import discord
from discord import SelectOption, Embed, Color
from discord.ext import commands
from discord.ui import Button, View, TextInput, Modal, Select
import asyncio

OG_DAMNIT = 970888799569715260
DAMNIT_ID = 529893177524617221
HEAD_HONCHO = 577005652820361236


class PollModal(Modal):
    def __init__(self, client, polls):
        super().__init__(title="Poll Creation")
        self.client = client
        self.polls = polls
        self.fields = [
            TextInput(
                label="What's the question for this poll?", 
                min_length=1, 
                max_length=100, 
                required=True)
        ]
        self.add_item(self.fields[0])
    
    async def on_submit(self, interaction):
        question = self.fields[0].value
        await interaction.response.defer()
        message = await interaction.channel.send(embed=Embed(
            description="Please react to this message with the emojis you want for this poll. When you're done, click the ✔. (Emojis must be from a server the bot is in)"
        ))
        await message.add_reaction("✔")
        emojis = []
        while True:
            try:
                reaction, user = await self.client.wait_for(
                    "reaction_add", 
                    check = lambda reaction, user: user == interaction.user,
                    timeout = 60.0
                    )
            
            except asyncio.TimeoutError:
                break
            else:
                if reaction.emoji == "✔":
                    break
                emojis.append(reaction.emoji)

        if len(emojis) == 0:
            await interaction.followup.send(embed=Embed("Failed to send your poll because you did not add any emojis."))
            return
        
        if interaction.guild.id == DAMNIT_ID and HEAD_HONCHO not in [i.id for i in interaction.user.roles]:
            og_damnit = interaction.guild.get_channel(OG_DAMNIT)
            request_embed = Embed(title="Poll Request", color = Color.random())
            request_embed.add_field(name="Question", value = question, inline = False)
            request_embed.add_field(name="Author", value = interaction.user.mention)
            request_embed.add_field(name="Emojis", value = "-".join([str(i) for i in emojis]))
            request = await og_damnit.send(embed=request_embed)
            await og_damnit.send("@everyone")
            emotes = ['✅', '❌']
            for i in emotes:
                try:
                    await request.add_reaction(i)
                except Exception as e:
                    print(e)
            while True:
                try:
                    reaction, user = await self.client.wait_for(
                        "reaction_add", 
                        check = lambda reaction, user: reaction.emoji in emotes and user.id in [167836423699824640, 341051335170326540, 739618992393682974],
                        timeout = 60.0
                        )
                
                except asyncio.TimeoutError:
                    await request.edit(embed=Embed(
                        description="Poll Timed out before someone could approve or deny it.",
                        color = Color.red()
                    ))
                    await interaction.followup.send(embed=Embed(
                        description="Your poll timed out before someone could approve/deny it.", ephemeral=False))
                    break
                else:
                    if reaction.emoji == "✅":
                        await self.send_poll(interaction, question, emojis, True)
                        break
                    else:
                        await interaction.followup.send(embed=Embed(
                            description= "Your poll has been denied.", ephemeral=False))
                        break

        else:
            await self.send_poll(interaction, question, emojis)
    
    async def send_poll(self, interaction, question, emojis, followup=False):
        embed = Embed(
                    description="**NEW POLL**: {}".format(question),
                    color = Color.random()
                )
        embed.set_footer(text="Poll created by {}".format(interaction.user.name))
        message = await self.polls.send(embed=embed)
        for i in emojis:
            try:
                await message.add_reaction(i)
            except Exception as e:
                await message.delete()
                await interaction.response.send_message(embed=Embed(
                    description="Failed to finalize your poll. This is because your emoji could not be added as a reaction",
                    color = Color.red()),
                    ephemeral=True)
                return
        await self.polls.send("@everyone")
        embed = Embed(
                    description="Successfully sent your poll to {}".format(self.polls.mention),
                    color = Color.green())
        if not followup:
            await interaction.followup.send(embed=embed,
                        ephemeral=True)
        else:
            await interaction.followup.send(embed=embed,
                    ephemeral=True)