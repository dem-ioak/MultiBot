import discord
from discord import SelectOption, Embed, Color
from discord.ext import commands
from discord.ui import Button, View, TextInput, Modal, Select

from util.constants import VCS
from util.helper_functions import parse_id

USER_NOT_CONNECTED = Embed(description="You must be connected to a vc to use voice commands.", color =  Color.red())
NOT_OWNER = Embed(description="You are not the owner of this vc!", color = Color.red())
INVALID_VC = Embed(description="You cannot use voice commands to this voice channel.", color = Color.red())
OWNER_STILL_PRESENT = "{} is still connected to this vc, so it cannot be claimed"

def checks(inter, claim = None):
    user = inter.user
    if not user.voice:
        return (0, None)
        
    channel_id = user.voice.channel.id
    user_id = user.id
    data = VCS.find_one({"_id" : channel_id})
    if data is None or data["owner"] is None:
        return (-1, None)
    
    owner_id = data["owner"]
    if not claim:
        return (1, None) if user_id == owner_id else (2, owner_id)
    
    return (3, owner_id) if owner_id in [i.id for i in inter.user.voice.channel.members] else (1, None)

# ALL VOICE COMMAND FEATURES
class VoiceView(View):
    def __init__(self, client):
        super().__init__(timeout=None)
        buttons = [
            UserDropDownButton("Hide", "hide", client),
            UserDropDownButton("Unhide", "unhide", client),
            UserDropDownButton("Allow", "allow", client),
            UserDropDownButton("Deny", "deny", client),
            RenameButton(),
            ToggleLock(),
            ClaimVC(client)
        ]
        for i in buttons: self.add_item(i)

class UserDropDownButton(Button):
    def __init__(self, name, mode, client):
        super().__init__(label=name, style = discord.ButtonStyle.blurple, custom_id=name)
        self.mode = mode
        self.embed = Embed(description="👤 Select the user you want to perform this action on")
        self.client = client
    
    async def callback(self, interaction):
        check_result, owner_id = checks(interaction)
        if check_result == 1:
            guild = interaction.user.guild
            view = View()
            view.add_item(UserDropDown(guild, self.mode, self.client))
            await interaction.response.send_message(
                embed=self.embed, 
                view=view,
                ephemeral=True)

        elif check_result == 0:
            await interaction.response.send_message(embed=USER_NOT_CONNECTED, ephemeral=True)
        elif check_result == 2:
            await interaction.response.send_message(embed=NOT_OWNER, ephemeral=True)
        elif check_result == -1:
            await interaction.response.send_message(embed=INVALID_VC, ephemeral=True)
        else:
            owner_obj = self.client.get_user(owner_id)
            if owner_obj is None:
                pass
            await interaction.response.send_message(
                embed= Embed(
                    description=OWNER_STILL_PRESENT.format(owner_obj.mention),
                    color = Color.red()),
                ephemeral=True)


class UserDropDown(Select):
    def __init__(self, guild, mode, client):
        super().__init__(placeholder="Select a member of this server", min_values=1, max_values=len(guild.members))
        self.options = []
        self.mode = mode
        self.client = client
        for i in guild.members:
            self.options.append(SelectOption(label="👤 {} - {}".format(i.name, i.id)))
    
        
    async def callback(self, interaction):
        outputs = {
            "hide" : "Successfully hid your vc from {}.",
            "unhide" : "Successfully unhid your vc from {}.",
            "deny" : "Successfully denied {} from joining your vc.",
            "allow" : "Successfully allowed {} to join your vc."
        }
        members = []

        for target in self.values:
            user_id = parse_id(target)
            member = self.client.get_user(user_id)
            members.append(member)
            channel = interaction.user.voice.channel
            perms = channel.overwrites_for(member)
            desc = None
            if self.mode == "hide": 
                perms.view_channel = False

            elif self.mode == "unhide": 
                perms.view_channel = None

            elif self.mode == "deny": 
                perms.connect = False

            elif self.mode == "allow":
                perms.connect = True
                perms.view_channel = True

            await channel.set_permissions(member, overwrite=perms)
            
        desc = outputs[self.mode].format(" ".join([i.mention for i in members]))
        embed = Embed(description=desc, color= Color.green())
        await interaction.response.send_message(embed=embed, ephemeral=True)


class RenameButton(Button):
    def __init__(self):
        super().__init__(label="Rename", style = discord.ButtonStyle.blurple, custom_id="Rename")
        self.cd_mapping = commands.CooldownMapping.from_cooldown(1, 120, commands.BucketType.member)
    
    async def callback(self, interaction):
        bucket = self.cd_mapping.get_bucket(interaction.message)
        retry_after = bucket.update_rate_limit()
        channel = interaction.user.voice.channel
        if retry_after:
            await interaction.response.send_message("You can only rename your vc every 5 minutes. Please wait {} until trying again".format(round(retry_after)), ephemeral=True)
        else:
            check_result, owner_id = checks(interaction)
            if check_result == 1:
                await interaction.response.send_modal(RenameModal(channel))
            elif check_result == 0:
                await interaction.response.send_message(embed=USER_NOT_CONNECTED, ephemeral=True)
            elif check_result == 2:
                await interaction.response.send_message(embed=NOT_OWNER, ephemeral=True)
            elif check_result == -1:
                await interaction.response.send_message(embed=INVALID_VC, ephemeral=True)
            else:
                owner_obj = self.client.get_user(owner_id)
                if owner_obj is None:
                    pass
                await interaction.response.send_message(
                embed= Embed(
                    description=OWNER_STILL_PRESENT.format(owner_obj.mention),
                    color = Color.red()),
                ephemeral=True)

class RenameModal(Modal):
    def __init__(self, channel):
        super().__init__(title="VC Rename")
        self.inp = TextInput(label="What would you like to name this vc?", min_length=1, max_length=32)
        self.channel = channel
        self.add_item(self.inp)
    
    async def on_submit(self, interaction):
        name = self.inp.value
        embed = Embed(description="✅ Successfully renamed your vc to **{}**".format(name), color = Color.green())
        await self.channel.edit(name=name)
        await interaction.response.send_message(embed=embed, ephemeral=True)

class ToggleLock(Button):
    def __init__(self):
        super().__init__(label="Toggle Lock", style = discord.ButtonStyle.blurple, custom_id="lock")
    
    async def callback(self, interaction):
        check_result, owner_id = checks(interaction)
        if check_result == 1:
            user = interaction.user
            perms = user.voice.channel.overwrites_for(user.guild.default_role)
            embed = Embed(description="✅ Successfully **{}** your vc".format("unlocked" if perms.connect is not None else "locked"), color = Color.green())
            if perms.connect is None: 
                perms.connect = False
            else: 
                perms.connect = None

            await user.voice.channel.set_permissions(user.guild.default_role, overwrite=perms)
            await interaction.response.send_message(embed=embed, ephemeral=True)

        elif check_result == 0:
            await interaction.response.send_message(embed=USER_NOT_CONNECTED, ephemeral=True)
        elif check_result == 2:
            await interaction.response.send_message(embed=NOT_OWNER, ephemeral=True)
        elif check_result == -1:
            await interaction.response.send_message(embed=INVALID_VC, ephemeral=True)
        else:
            owner_obj = self.client.get_user(owner_id)
            if owner_obj is None:
                pass
            await interaction.response.send_message(
                embed= Embed(
                    description=OWNER_STILL_PRESENT.format(owner_obj.mention),
                    color = Color.red()),
                ephemeral=True)

class ClaimVC(Button):
    def __init__(self, client):
        super().__init__(label="Claim", style = discord.ButtonStyle.blurple, custom_id="claim")
        self.client = client
    
    async def callback(self, interaction):
        check_result, owner_id = checks(interaction, True)
        if check_result == 1:
            user = interaction.user
            vc = VCS.find_one({"_id" : interaction.user.voice.channel.id})
            embed = Embed()
            embed.description = "✅ Succssfully claimed this vc. You can now use all of the provided commands."
            VCS.update_one(vc, {"$set" : {"owner" : user.id}})
            await  interaction.response.send_message(embed=embed, ephemeral = True)
        
        elif check_result == 0:
            await interaction.response.send_message(embed=USER_NOT_CONNECTED, ephemeral=True)
        elif check_result == 2:
            await interaction.response.send_message(embed=NOT_OWNER, ephemeral=True)
        elif check_result == -1:
            await interaction.response.send_message(embed=INVALID_VC, ephemeral=True)
        else:
            owner_obj = self.client.get_user(owner_id)
            if owner_obj is None:
                pass
            await interaction.response.send_message(
                embed= Embed(
                    description=OWNER_STILL_PRESENT.format(owner_obj.mention),
                    color = Color.red()),
                ephemeral=True)