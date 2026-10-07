import discord
from discord import ButtonStyle, Embed, Color
from discord.ui import Button, View, DynamicItem
from datetime import datetime, timedelta
import re

from util.constants import POLL_REQUESTS
from util.log_manager import get_logger

# Polls in this server have to be approved first, unless they come from someone with the HEAD_HONCHO role
OG_DAMNIT = 970888799569715260 # Channel that approval requests are sent to
DAMNIT_ID = 529893177524617221
HEAD_HONCHO = 577005652820361236
POLL_APPROVERS = [167836423699824640, 341051335170326540, 739618992393682974]

# Limits Discord puts on its polls
MAX_ANSWERS = 10
MAX_QUESTION_LENGTH = 300
MAX_ANSWER_LENGTH = 55

CUSTOM_EMOJI = re.compile(r"<a?:\w+:\d+>")
KEYCAP_EMOJI = re.compile(r"[0-9#*]️?⃣")
EMOJI_RANGES = [(0x203C, 0x2BFF), (0x1F000, 0x1FAFF), (0xE0020, 0xE007F)]
EMOJI_JOINERS = "‍️"


def is_emoji(text):
    if CUSTOM_EMOJI.fullmatch(text) or KEYCAP_EMOJI.fullmatch(text):
        return True
    return len(text) > 0 and all(
        char in EMOJI_JOINERS or any(low <= ord(char) <= high for low, high in EMOJI_RANGES)
        for char in text
    )


def parse_answer(option):
    """Split an option such as "🍕 Pizza" into its text and its (optional) leading emoji"""
    option = option.strip()
    first, _, rest = option.partition(" ")
    if rest.strip() and is_emoji(first):
        return {"text": rest.strip(), "emoji": first}
    return {"text": option, "emoji": None}


def build_poll(request, emojis=True):
    poll = discord.Poll(
        question=request["question"],
        duration=timedelta(hours=request["hours"]),
        multiple=request["multiple"],
    )
    for answer in request["answers"]:
        if answer["emoji"] and emojis:
            poll.add_answer(text=answer["text"], emoji=discord.PartialEmoji.from_str(answer["emoji"]))
        elif answer["emoji"]:
            # Kept as part of the text instead, cut down to what an answer can hold
            poll.add_answer(text=f"{answer['emoji']} {answer['text']}"[:MAX_ANSWER_LENGTH])
        else:
            poll.add_answer(text=answer["text"])
    return poll


async def send_poll(channel, request):
    """Post a poll to the polls channel, followed by the ping announcing it"""
    try:
        message = await channel.send(poll=build_poll(request))
    except discord.HTTPException:
        if not any(answer["emoji"] for answer in request["answers"]):
            raise
        # Most likely an emoji from a server the bot is not in, which Discord will not accept
        message = await channel.send(poll=build_poll(request, emojis=False))

    await channel.send(
        f"@everyone New poll from <@{request['author_id']}>!",
        allowed_mentions=discord.AllowedMentions(everyone=True, users=False),
    )
    return message


def request_embed(request, outcome=None):
    embed = Embed(title="Poll Request", color=Color.random())
    embed.add_field(name="Question", value=request["question"], inline=False)
    embed.add_field(name="Author", value=f"<@{request['author_id']}>")
    embed.add_field(name="Duration", value=f"{request['hours']} hours")
    embed.add_field(name="Votes", value="Multiple" if request["multiple"] else "One each")
    embed.add_field(
        name="Options",
        value="\n".join(
            f"{answer['emoji'] or '•'} {answer['text']}" for answer in request["answers"]
        ),
        inline=False,
    )
    if outcome:
        embed.description = outcome
    return embed


async def request_approval(interaction, request):
    """Send a poll to the approvers. It is stored, so it can still be approved after a restart"""
    channel = interaction.guild.get_channel(OG_DAMNIT)
    view = View(timeout=None)
    view.add_item(PollDecisionButton("approve"))
    view.add_item(PollDecisionButton("deny"))
    message = await channel.send(embed=request_embed(request), view=view)
    POLL_REQUESTS.insert_one({**request, "_id": message.id, "created_at": datetime.utcnow()})
    await channel.send("@everyone")


class PollDecisionButton(DynamicItem[Button], template=r"poll:(?P<decision>approve|deny)"):
    """Approve / Deny under a poll request. Which request it is comes from the message it sits on"""

    def __init__(self, decision):
        approve = decision == "approve"
        super().__init__(
            Button(
                label="Approve" if approve else "Deny",
                emoji="✅" if approve else "❌",
                style=ButtonStyle.green if approve else ButtonStyle.red,
                custom_id=f"poll:{decision}",
            )
        )
        self.decision = decision

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: Button, match: re.Match):
        return cls(match["decision"])

    async def callback(self, interaction: discord.Interaction):
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        if interaction.user.id not in POLL_APPROVERS:
            await interaction.response.send_message(
                embed=Embed(description="You are not able to approve or deny polls.", color=Color.red()),
                ephemeral=True,
            )
            return

        # Taken out in one step, so two approvers pressing at once cannot both act on it
        request = POLL_REQUESTS.find_one_and_delete({"_id": interaction.message.id})
        if request is None:
            await interaction.response.edit_message(view=None)
            return

        await interaction.response.defer()
        author = interaction.guild.get_member(request["author_id"])
        if self.decision == "deny":
            outcome = f"❌ Denied by {interaction.user.mention}"
            notice = f"Your poll **{request['question']}** has been denied."
        else:
            polls = interaction.guild.get_channel(request["channel_id"])
            try:
                await send_poll(polls, request)
            except (discord.HTTPException, AttributeError):
                # Put back, so it can be approved again once the polls channel is sorted out
                log.exception("Failed to send an approved poll")
                POLL_REQUESTS.insert_one(request)
                await interaction.followup.send(
                    embed=Embed(
                        description="That poll could not be sent. Check that the polls channel still exists and that I can post in it.",
                        color=Color.red(),
                    ),
                    ephemeral=True,
                )
                return
            outcome = f"✅ Approved by {interaction.user.mention}"
            notice = f"Your poll **{request['question']}** has been approved and sent to {polls.mention}."

        log.info(f"POLL_{self.decision.upper()}: {request['question']}")
        await interaction.message.edit(embed=request_embed(request, outcome), view=None)
        if author:
            try:
                await author.send(embed=Embed(description=notice))
            except discord.HTTPException:
                pass  # Their DMs are closed, nothing more to be done
