import discord
from discord import ButtonStyle, Embed, Color, app_commands
from discord.ext import commands
from discord.ui import Button, View
import os
from collections import defaultdict

from util.constants import USERS, FORBIDDEN_COMMAND

BOARD_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]

SERVER_NAMES = {
    1118721668970467428: "DA SUPA SERVER",
    529893177524617221: "DAMNIT",
    979199758591733780: "Mars 🩷",
}

SHARE_CHANNELS = {
    1118721668970467428: 1320255443641307167,
    529893177524617221: 1319807972842405929,
    979199758591733780: 1320886452183236678,
}

IMAGE_FILE_NAMES = [
    -1,
    "voice",
    "messages",
    "server_wide",
    "misc_one",
    "misc_two",
    "final"
]

BASE_TEXT = "Here are your {} stats!"
LAST_PAGE = 6
WRAPPED_DIR = "Wrapped2024"
FOOTER_TEXT = 'If at any point you get a response of "Interaction Failed", try again, or you can request your wrapped again once every 60 seconds.'

HEADER_TEXT = [
    -1,
    BASE_TEXT.format("***VOICE CHANNEL***"),
    BASE_TEXT.format("***MESSAGING***"),
    "Here are some general server stats!",
    BASE_TEXT.format("***MISCELLANEOUS***"),
    BASE_TEXT.format("***MISCELLANEOUS***"),  
    "To share your 2024 Wrapped with this server, click the green 📨 button below!"
]

WRAPPED_ALREADY_SENT = Embed(description="Your wrapped has already been sent to this server!", color = Color.red())
NOT_ELIGIBLE = Embed(description="Sorry, but you're not eligible for a 2024 Wrapped in any Yeat Bot server. This is because there was not enough activity to create one.",
                     color = Color.red())

def eligibility_check(user_id, server_id):
    user_dir = f"{WRAPPED_DIR}/{server_id}/{user_id}"
    if not os.path.isdir(user_dir):
        return False
    
    if not os.path.exists(f"{user_dir}/final.png"):
        return False

    return True

# General Embed getters
def get_intro(server_id):
    server_name = SERVER_NAMES[server_id]
    embed = Embed()
    embed.color = Color.random()
    embed.title = f"Welcome to {server_name} WRAPPED 2024"
    embed.description = f"Dive into your yearly statistics within the server with ***{server_name} WRAPPED 2024!***\n"
    embed.description += "Explore details about your activity, how you interacted with others, and more.\n\n"
    embed.description += "***Developed*** by **@.demetri**\n***Designed*** by **@jerseyjhonnie**\n***Powered*** by <@762465131736989696>\n\n"
    embed.description += "Hit the ▶️ to get started!"
    embed.set_footer(text=FOOTER_TEXT)
    return embed


def get_cooldown(time_left):
    embed = Embed()
    embed.color = Color.red()
    embed.description = f"You can only request a 2024 Wrapped for a single server once every minute. Please try again in {round(time_left)} seconds."
    return embed


def get_wrapped_sent(server_id):
    server_name = SERVER_NAMES[server_id]
    embed = Embed()
    embed.color = Color.green()
    embed.description = f"✅ Your {server_name} WRAPPED 2024 has been sent to your DMs!"
    return embed


# Wrapped Stats Embed Getters
def get_stat_image(server_id, text):
    server_name = SERVER_NAMES[server_id]
    embed = Embed()
    embed.title = f"{server_name} WRAPPED 2024"
    embed.color = Color.random()
    embed.description = text
    embed.set_footer(text=FOOTER_TEXT)
    return embed


class ShareWrappedButton(Button):
    def __init__(self, author_id, server_id, base_dir, client):
        super().__init__(style=ButtonStyle.green, emoji="📨")
        self.author_id = author_id
        self.server_id = server_id
        self.base_dir = base_dir
        self.client = client

    async def callback(self, interaction: discord.Interaction):
        # channel_id = SHARE_CHANNELS[self.server_id]
        
        primary_key = {"_id" : {"guild_id" : self.server_id, "user_id" : self.author_id}}
        user_data = USERS.find_one(primary_key)
        if user_data["sent_wrapped"]:
            await interaction.response.send_message(embed = WRAPPED_ALREADY_SENT, ephemeral=True)
            return
        
        USERS.update_one(primary_key, {"$set" : {"sent_wrapped" : True}})
        channel_id = SHARE_CHANNELS[self.server_id]
        channel = self.client.get_channel(channel_id)
        server_name = SERVER_NAMES[self.server_id]
        embed = Embed(title = f"{server_name} WRAPPED 2024")
        embed.color = Color.random()
        embed.description = f"{interaction.user.mention} shared their wrapped!"

        image_file_name = IMAGE_FILE_NAMES[LAST_PAGE] + ".png"
        image_file_path = self.base_dir + "/" + image_file_name
        image_file = discord.File(image_file_path, filename=image_file_name)
        embed.set_image(url="attachment://" + image_file_name)
        await channel.send(embed = embed, file = image_file)
        await interaction.response.send_message("✅", ephemeral=True)


class ServerWrappedView(View):
    def __init__(self, author_id, server_id, page, client):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.server_id = server_id
        self.base_dir = f"{WRAPPED_DIR}/{server_id}/{author_id}"
        self.page = page
        self.client = client
        self.init_view()

    async def update_page(self, interaction: discord.Interaction, page_delta: int):
        new_page = self.page + page_delta
        if new_page < 0 or new_page > LAST_PAGE:
            return

        new_view = ServerWrappedView(
            self.author_id, self.server_id, new_page, self.client
        )
        if new_page == 0:
            await interaction.response.edit_message(
                view=new_view, embed=get_intro(self.server_id), attachments=[]
            )

        else:
            if new_page == LAST_PAGE:
                new_view.add_item(
                    ShareWrappedButton(
                        self.author_id, self.server_id, self.base_dir, self.client
                    )
                )

            image_file_name = IMAGE_FILE_NAMES[new_page] + ".png"
            image_file_path = self.base_dir + "/" + image_file_name
            image_file = discord.File(image_file_path, filename=image_file_name)
            embed = get_stat_image(self.server_id, HEADER_TEXT[new_page])
            embed.set_image(url="attachment://" + image_file_name)
            await interaction.response.edit_message(
                view=new_view, embed=embed, attachments=[image_file]
            )

    def init_view(self):
        left_button = Button(style=ButtonStyle.gray, emoji="◀️")
        right_button = Button(style=ButtonStyle.gray, emoji="▶️")
        left_button.disabled = self.page == 0
        right_button.disabled = self.page == LAST_PAGE
        left_button.callback = lambda interaction: self.update_page(interaction, -1)
        right_button.callback = lambda interaction: self.update_page(interaction, 1)
        self.add_item(left_button)
        self.add_item(right_button)


class WrappedViewButton(Button):
    def __init__(self, index, server_id, author_id, client):
        super().__init__(style=ButtonStyle.gray, emoji=BOARD_EMOJIS[index])
        self.index = index
        self.server_id = server_id
        self.author_id = author_id
        self.cd_mapping = commands.CooldownMapping.from_cooldown(
            1, 60, commands.BucketType.member
        )
        self.client = client

    async def callback(self, interaction: discord.Interaction):
        bucket = self.cd_mapping.get_bucket(interaction.message)
        retry_after = bucket.update_rate_limit()
        if retry_after:
            await interaction.response.send_message(
                embed=get_cooldown(retry_after), ephemeral=True
            )
        else:
            print(
                f"Delivering {SERVER_NAMES[self.server_id]} WRAPPED 2024 to {self.author_id}"
            )
            view = ServerWrappedView(self.author_id, self.server_id, 0, self.client)
            await interaction.user.send(embed=get_intro(self.server_id), view=view)
            await interaction.response.send_message(
                embed=get_wrapped_sent(self.server_id), ephemeral=True
            )


class WrappedListView(View):
    def __init__(self, author_id, valid_wrapped, client):
        super().__init__(timeout=None)
        self.clear_items()
        self.valid_wrapped = valid_wrapped
        self.author_id = author_id
        self.client = client
        self.init_view()

    def init_view(self):
        for i, server_id in enumerate(self.valid_wrapped):
            to_add = WrappedViewButton(
                index=i,
                server_id=server_id,
                author_id=self.author_id,
                client=self.client,
            )
            self.add_item(to_add)


class Wrapped(commands.Cog):
    def __init__(self, client):
        self.client = client

    @app_commands.command(description="Deliver Wrapped")
    async def wrapped(self, interaction: discord.Interaction):

        user_id = interaction.user.id
        server_ids = list(
            sorted(int(id_) for id_ in os.listdir(WRAPPED_DIR))
        )  # Sort to guarantee order is preserved
        valid_wrapped = []
        embed = Embed()
        embed.title = "DISCORD WRAPPED 2024"
        embed_text = "Which of the following servers would you like to view your 2024 WRAPPED for?\n"

        for server_id in server_ids:
            if eligibility_check(user_id, server_id):
                valid_wrapped.append(server_id)

        for i, server_id in enumerate(valid_wrapped):
            embed_text += f"{BOARD_EMOJIS[i]} **{SERVER_NAMES[server_id]}**\n"

        embed.color = Color.gold()
        embed.description = embed_text
        
        if valid_wrapped:
            await interaction.response.send_message(
                embed=embed,
                view=WrappedListView(user_id, valid_wrapped, self.client),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(embed = NOT_ELIGIBLE)
    
    @app_commands.command(description="Deliver Wrapped")
    async def announce(self, interaction: discord.Interaction):
        if interaction.user.id != 739618992393682974:
            await interaction.response.send_message(embed = FORBIDDEN_COMMAND, ephemeral=True)
            return
        
        await interaction.response.defer()
        eligibility_map = defaultdict(list)
        eligible_users = set()
        for server in self.client.guilds:
            for user in server.members:
                if eligibility_check(user.id, server.id):
                    eligibility_map[user.id].append(server.id)
                    eligible_users.add(user.id)
        
        success = 0
        fail = 0
        failed_users = []
        for user_id in eligible_users:
            
            user_obj = self.client.get_user(user_id)
            embed = Embed(title = "DISCORD WRAPPED 2024", color = Color.random())
            embed.description = "Merry Christmas Eve! 🌲 Your Discord Wrapped for 2024 is available in the following servers:\n\n"
            
            for server_id in eligibility_map[user_id]:
                embed.description += f"**{SERVER_NAMES[server_id]}**\n"
            
            embed.description += "\nTo access, run the Yeat Bot command **/wrapped** either here or in any one of these servers."
            embed.set_footer(text = "If you have any issues accessing, DM or ping @.demetri.")
            try:    
                success += 1
                await user_obj.send(embed = embed)
            except:
                fail += 1
                failed_users.append[user_obj.name]
        
        response_text = f"Delivered with {success} successes and {fail} failures."
        if fail != 0:
            response_text += "\n\nFailed to send to:\n"
            for username in failed_users:
                response_text += f"{username}\n"
                
        await interaction.followup.send(embed = Embed(description=response_text, color = Color.green()), ephemeral=True)
        
            
        
        
        
        
        
        


async def setup(client):
    await client.add_cog(Wrapped(client))
