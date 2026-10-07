import discord
from discord import Embed, Color, app_commands
from discord.app_commands import Choice, Range
from discord.ext import commands

from util.constants import TEXT_CHANNELS, SERVERS, POLL_FAIL
from util.log_messages import SNIPE_FAIL
from util.buttons.poll_buttons import (
    PollDecisionButton, parse_answer, send_poll, request_approval,
    DAMNIT_ID, HEAD_HONCHO, OG_DAMNIT, MAX_QUESTION_LENGTH, MAX_ANSWER_LENGTH,
)
from util.log_manager import get_logger

NO_SNIPE = Embed(description="There is nothing to snipe in this channel", color = Color.red())

POLL_DURATIONS = [
    Choice(name = "1 hour", value = 1),
    Choice(name = "4 hours", value = 4),
    Choice(name = "8 hours", value = 8),
    Choice(name = "1 day", value = 24),
    Choice(name = "3 days", value = 72),
    Choice(name = "1 week", value = 168),
]
POLL_OPTION = "An option, which can start with an emoji (\"🍕 Pizza\")"


class Chat(commands.Cog):
    def __init__(self, client):
        self.client = client
    
    @app_commands.command(description = "Create a poll for the server")
    @app_commands.describe(
        question = "What the poll is asking",
        option1 = POLL_OPTION, option2 = POLL_OPTION, option3 = POLL_OPTION, option4 = POLL_OPTION,
        option5 = POLL_OPTION, option6 = POLL_OPTION, option7 = POLL_OPTION, option8 = POLL_OPTION,
        option9 = POLL_OPTION, option10 = POLL_OPTION,
        duration = "How long the poll stays open (defaults to 1 day)",
        multiple = "Whether people can vote for more than one option (defaults to no)",
    )
    @app_commands.choices(duration = POLL_DURATIONS)
    @app_commands.guild_only()
    async def poll(
        self,
        interaction : discord.Interaction,
        question : Range[str, 1, MAX_QUESTION_LENGTH],
        option1 : str,
        option2 : str,
        option3 : str = None,
        option4 : str = None,
        option5 : str = None,
        option6 : str = None,
        option7 : str = None,
        option8 : str = None,
        option9 : str = None,
        option10 : str = None,
        duration : Choice[int] = None,
        multiple : bool = False,
    ):

        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        options = [option1, option2, option3, option4, option5, option6, option7, option8, option9, option10]
        options = [option for option in options if option and option.strip()]
        log.info(f"COMMAND_INVOKED: /poll [question={question}, options={options}, duration={duration}, multiple={multiple}]")

        guild = interaction.guild
        server_data = SERVERS.find_one({"_id" : guild.id})
        channel = guild.get_channel(server_data["polls_id"]) if server_data else None
        problem = None
        answers = [parse_answer(option) for option in options]
        texts = [answer["text"].lower() for answer in answers]
        needs_approval = guild.id == DAMNIT_ID and HEAD_HONCHO not in [role.id for role in interaction.user.roles]

        # Everything that can stop a poll is checked up front, before anybody else is asked to look at it
        if channel is None:
            problem = POLL_FAIL
        elif len(answers) < 2:
            problem = "A poll needs at least two options."
        elif any(len(answer["text"]) > MAX_ANSWER_LENGTH for answer in answers):
            problem = f"Options can be at most {MAX_ANSWER_LENGTH} characters long."
        elif len(set(texts)) != len(texts):
            problem = "Two of your options are the same."
        elif needs_approval and guild.get_channel(OG_DAMNIT) is None:
            problem = "Polls in this server need approving, but the channel requests go to no longer exists."

        if problem:
            await interaction.response.send_message(
                embed = Embed(description = problem, color = Color.red()), ephemeral = True)
            return

        request = {
            "guild_id" : guild.id,
            "channel_id" : channel.id,
            "author_id" : interaction.user.id,
            "question" : question,
            "answers" : answers,
            "hours" : duration.value if duration else 24,
            "multiple" : multiple,
        }
        await interaction.response.defer(ephemeral = True)
        if needs_approval:
            await request_approval(interaction, request)
            description = "Your poll has been sent for approval. You will get a DM once it is approved or denied."
        else:
            await send_poll(channel, request)
            description = "Successfully sent your poll to {}".format(channel.mention)

        await interaction.followup.send(
            embed = Embed(description = description, color = Color.green()), ephemeral = True)

    @app_commands.command(description="Display any users avatar.")
    async def avatar(self, interaction : discord.Interaction, user : discord.Member = None):
        
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /avatar [user={user.name if user else interaction.user.name}]")
        
        embed = Embed(color = Color.random())
        if user:
            embed.set_image(url=user.avatar.url)
            embed.set_footer(text = "{}'s Avatar".format(user.name))
        else:
            embed.set_image(url = interaction.user.avatar.url)
            embed.set_footer(text = "{}'s Avatar".format(interaction.user.name))
        
        await interaction.response.send_message(embed=embed)

    @app_commands.command(description="Display any users server avatar (default if None)")
    async def savatar(self, interaction : discord.Interaction, user : discord.Member = None):
        
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /savatar [user={user.name if user else interaction.user.name}]")
        
        embed = Embed(color = Color.random())
        if user:
            embed.set_image(url=user.display_avatar.url)
            embed.set_footer(text = "{}'s Avatar".format(user.name))
        else:
            embed.set_image(url = interaction.user.display_avatar.url)
            embed.set_footer(text = "{}'s Avatar".format(interaction.user.name))
        
        await interaction.response.send_message(embed=embed)
    
    @app_commands.command(description="Display the most recently deleted message in a channel")
    async def s(self, interaction : discord.Interaction):
        
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /s")
        channel_id = interaction.channel.id
        guild_id = interaction.guild.id
        guild_name = interaction.guild.name
        tc_data = TEXT_CHANNELS.find_one({"_id" : channel_id})


        # Should never happen, log if does
        if tc_data is None:
            return 
        
        
        content = tc_data["deleted_content"]
        author = tc_data["deleted_author"]
        user = self.client.get_user(author)
        if author == -1 or user is None:
            await interaction.response.send_message(embed = NO_SNIPE, ephemeral = True)
            return

        embed = Embed(
            title = "Sniped Deleted Message",
            description = content,
            color = Color.dark_magenta()
        )
        embed.set_author(name = user.name, icon_url = user.avatar.url)
        await interaction.response.send_message(embed = embed)

    @app_commands.command(description="Display the most recently edited message in a channel")
    async def es(self, interaction : discord.Interaction):
        
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /es")
        channel_id = interaction.channel.id
        guild_id = interaction.guild.id
        guild_name = interaction.guild.name
        tc_data = TEXT_CHANNELS.find_one({"_id" : channel_id})

        # Should never happen, log if does
        if tc_data is None:
            return 
        
        
        content = tc_data["edited_content"]
        author = tc_data["edited_author"]
        user = self.client.get_user(author)
        if author == -1 or user is None:
            await interaction.response.send_message(embed = NO_SNIPE, ephemeral = True)
            return

        embed = Embed(
            title = "Sniped Edited Message",
            description = content,
            color = Color.dark_magenta()
        )
        embed.set_author(name = user.name, icon_url = user.avatar.url)
        await interaction.response.send_message(embed = embed)

async def setup(client):
    client.add_dynamic_items(PollDecisionButton)
    await client.add_cog(Chat(client))






