import discord
from discord import Embed, Color, app_commands
from discord.ext import commands
from datetime import datetime
from io import BytesIO
from pymongo.errors import DuplicateKeyError
from PIL import Image
import anthropic
import base64
import json
import os

from util.constants import RATINGS_67, SIXTY_SEVEN_THREAD_ID, EST_TIME
from util.helper_functions import run_db
from util.log_manager import get_logger

MODEL = "claude-haiku-5-5"
MAX_TOKENS = 2048

# Spending guards on top of the account's own prepaid credit. A rating costs well under a cent,
# so these only matter if someone tries to burn through the credit on purpose
RATINGS_PER_USER_PER_DAY = 10
RATINGS_PER_DAY = 100

MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
# Images are shrunk to this many pixels on the long side before they are sent, which keeps each call cheap
MAX_IMAGE_SIDE = 1024

SYSTEM_PROMPT = """You judge entries in a Discord thread where friends post photos and screenshots of the number 67 they find out in the world.

Score the picture from 1 to 10 on:
- How clean the 67 is: clearly a 67, easy to spot, not a stretch.
- How surprising the find is: an unexpected place beats an obvious one.
- Whether it is out in the wild: a real find in the real world beats something staged or made on purpose for the thread.

Give a yellow card to a 67 that was bound to show up, because it is not a real find:
- Anything in a numbered sequence: house and door numbers, highway exits, lockers, seats, rooms, page numbers, scoreboard frames, list positions.
- Counters and readings that pass through 67 on their way somewhere: battery percentage, temperature, miles to empty, timers, clocks, progress bars, step counts.
A yellow-carded 67 scores 3 or less.

If there is no 67 in the picture, or you cannot find one, say so: is_67 is false and the score is 1.
Keep the verdict to one or two short sentences, in a casual tone."""

RATING_SCHEMA = {
    "type": "object",
    "properties": {
        "is_67": {"type": "boolean"},
        "where": {"type": "string", "description": "Where the 67 is in the picture, in a few words"},
        "card": {"type": "string", "enum": ["none", "yellow"]},
        "score": {"type": "integer", "description": "1 to 10"},
        "verdict": {"type": "string"},
    },
    "required": ["is_67", "where", "card", "score", "verdict"],
    "additionalProperties": False,
}


def failure(description):
    return Embed(description=description, color=Color.red())


def find_image(message):
    for attachment in message.attachments:
        if (attachment.content_type or "").startswith("image/"):
            return attachment
    return None


def prepare_image(data):
    """Shrink the image and turn it into a JPEG, which every format Discord accepts can become"""
    image = Image.open(BytesIO(data))
    image.seek(0)  # The first frame of an animated image
    image = image.convert("RGB")
    image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    out = BytesIO()
    image.save(out, format="JPEG", quality=85)
    return base64.standard_b64encode(out.getvalue()).decode("utf-8")


def rating_embed(message, rating, requested_by):
    if not rating["is_67"]:
        color, title = Color.dark_grey(), "No 67 found"
    elif rating["card"] == "yellow":
        color, title = Color.gold(), f"🟨 Yellow card - {rating['score']}/10"
    else:
        color, title = Color.green(), f"67 rating: {rating['score']}/10"
    embed = Embed(title=title, description=rating["verdict"], color=color)
    if rating["is_67"] and rating["where"]:
        embed.add_field(name="The 67", value=rating["where"], inline=False)
    embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
    embed.set_footer(text=f"Rated by Claude Haiku for {requested_by}")
    return embed


class Rate67(commands.Cog):
    def __init__(self, client):
        self.client = client
        key = os.getenv("ANTHROPIC_API_KEY")
        # Without a key the command still shows up, but turns everyone away before anything is spent
        self.claude = anthropic.AsyncAnthropic(api_key=key, max_retries=1) if key else None
        self.menu = app_commands.ContextMenu(name="Rate 67", callback=self.rate)
        self.client.tree.add_command(self.menu)

    async def cog_unload(self):
        self.client.tree.remove_command(self.menu.name, type=self.menu.type)

    async def ask_claude(self, image_data):
        response = await self.claude.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": RATING_SCHEMA}},
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_data}},
                    {"type": "text", "text": "Rate this 67."},
                ],
            }],
        )
        if response.stop_reason != "end_turn":
            return None
        rating = json.loads(next(block.text for block in response.content if block.type == "text"))
        rating["score"] = max(1, min(10, int(rating["score"])))
        return rating

    async def rate(self, interaction: discord.Interaction, message: discord.Message):
        log = get_logger(__name__, server=interaction.guild.name if interaction.guild else None, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: Rate 67 [message={message.id}]")

        # Every check that can turn someone away happens before anything is spent
        if message.channel.id != SIXTY_SEVEN_THREAD_ID:
            await interaction.response.send_message(embed=failure(f"67s can only be rated in <#{SIXTY_SEVEN_THREAD_ID}>."), ephemeral=True)
            return
        attachment = find_image(message)
        if attachment is None or message.author.bot:
            await interaction.response.send_message(embed=failure("Only messages with an image can be rated."), ephemeral=True)
            return

        earlier = await run_db(RATINGS_67.find_one, {"_id": message.id})
        if earlier:
            if earlier["status"] == "done":
                # Already rated, so the saved rating is shown again for free
                await interaction.response.send_message(embed=rating_embed(message, earlier["rating"], earlier["requested_by_name"]))
            elif earlier["status"] == "refused":
                await interaction.response.send_message(embed=failure("Claude wouldn't rate that one."), ephemeral=True)
            else:
                await interaction.response.send_message(embed=failure("That 67 is already being rated."), ephemeral=True)
            return

        if attachment.size > MAX_ATTACHMENT_BYTES:
            await interaction.response.send_message(embed=failure("That image is too big to rate."), ephemeral=True)
            return
        if self.claude is None:
            await interaction.response.send_message(embed=failure("67 rating is not set up yet."), ephemeral=True)
            return

        day = datetime.now(EST_TIME).strftime("%Y-%m-%d")
        mine = await run_db(RATINGS_67.count_documents, {"day": day, "requested_by": interaction.user.id})
        if mine >= RATINGS_PER_USER_PER_DAY:
            await interaction.response.send_message(embed=failure(f"You've used your {RATINGS_PER_USER_PER_DAY} ratings for today."), ephemeral=True)
            return
        if await run_db(RATINGS_67.count_documents, {"day": day}) >= RATINGS_PER_DAY:
            await interaction.response.send_message(embed=failure("The server is out of 67 ratings for today."), ephemeral=True)
            return

        # Claiming the message first means two people clicking at once only pay for one rating
        try:
            await run_db(RATINGS_67.insert_one, {
                "_id": message.id, "status": "pending", "day": day,
                "requested_by": interaction.user.id, "requested_by_name": interaction.user.display_name,
                "author_id": message.author.id, "requested_at": datetime.utcnow(),
            })
        except DuplicateKeyError:
            await interaction.response.send_message(embed=failure("That 67 is already being rated."), ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        try:
            image_data = prepare_image(await attachment.read())
        except Exception:
            log.exception("RATE_67: Could not read the image")
            await run_db(RATINGS_67.delete_one, {"_id": message.id})
            await interaction.followup.send(embed=failure("Couldn't open that image."))
            return

        try:
            rating = await self.ask_claude(image_data)
        except anthropic.EnforcedSpendLimitReachedError:
            log.error("RATE_67: Spend limit reached")
            await run_db(RATINGS_67.delete_one, {"_id": message.id})
            await interaction.followup.send(embed=failure("67 rating is out of credit."))
            return
        except anthropic.APIError:
            # Nothing usable came back, so the claim is let go and the 67 can be rated again later
            log.exception("RATE_67: Claude request failed")
            await run_db(RATINGS_67.delete_one, {"_id": message.id})
            await interaction.followup.send(embed=failure("Rating failed, try again in a bit."))
            return

        if rating is None:
            # A refusal is kept, so the same picture is not sent again
            await run_db(RATINGS_67.update_one, {"_id": message.id}, {"$set": {"status": "refused"}})
            await interaction.followup.send(embed=failure("Claude wouldn't rate that one."))
            return

        await run_db(RATINGS_67.update_one, {"_id": message.id}, {"$set": {"status": "done", "rating": rating}})
        log.info(f"RATE_67: Rated {rating['score']}/10, card {rating['card']}")
        await interaction.followup.send(embed=rating_embed(message, rating, interaction.user.display_name))


async def setup(client):
    await client.add_cog(Rate67(client))
