import discord
import random
import util.dataclasses as DataClasses

from datetime import datetime, time, date
from discord import Embed, Color, Status, Streaming, DMChannel
from discord.ext import commands, tasks

from util.constants import SERVERS, USERS, TEXT_CHANNELS, VCS, WRAPPED, VC_EVENTS, LEVEL_UP
from util.enums import EventType
from util.helper_functions import leveled_up
from util.log_messages import *

import logging

event_logger = logging.getLogger("events")
data_logger = logging.getLogger("data")

class Events(commands.Cog):
    def __init__(self, client):
        self.client = client
        self._cd = commands.CooldownMapping.from_cooldown(1, 60.0, commands.BucketType.member)
    
    def get_ratelimit(self, message : discord.Message):
        """Returns ratelimit remaining after a message was sent"""
        if message.author.id != self.client.user.id:
            bucket  = self._cd.get_bucket(message)
            return bucket.update_rate_limit()
    
    
    # User / Bot Joining & Leaving
    @commands.Cog.listener()
    async def on_guild_join(self, server):
        """Handle DB Operations when bot joins a server"""
        guild_id = server.id
        event_logger.info(GUILD_JOIN.format(guild_id))

        server_data = SERVERS.find_one({"_id" : guild_id})   
        curr_time = datetime.utcnow()    

        # Add server if it does not exist
        if server_data is None:
        
            server_obj = DataClasses.Server(
                _id = guild_id,
                auto_roles = [],
                vibe_gifs = []
            )
            SERVERS.insert_one(server_obj.__dict__)
            data_logger.info(GUILD_DATA_ADD.format(guild_id))
        
        # Add each user in the server to data
        for user in server.members:
            if not user.bot:
                primary_key = {"guild_id" : guild_id, "user_id"  : user.id} 
                user_data = USERS.find_one({ "_id" : primary_key })
                wrapped_data = WRAPPED.find_one({"_id" : primary_key})
                if user_data is  None:
                    user_obj = DataClasses.User(_id = primary_key)
                    USERS.insert_one(user_obj.__dict__)
                    data_logger.info(USER_DATA_ADD.format(user.id, guild_id))

                if wrapped_data is None:
                    wrapped_obj = DataClasses.WrappedUser(_id = primary_key, user_pings= {"count" : 0})
                    WRAPPED.insert_one(wrapped_obj.__dict__)
                    data_logger.info(WRAPPED_DATA_ADD.format(user.id, guild_id))
        
        # Add every text channel in the server to data
        for t_channel in server.text_channels:
            t_channel_data = TEXT_CHANNELS.find_one({"_id" : t_channel.id})
            if t_channel_data is not None:
                continue

            t_channel_obj = DataClasses.TChannel(
                _id = t_channel.id,
                created_at = curr_time)
            TEXT_CHANNELS.insert_one(t_channel_obj.__dict__)
            data_logger.info(TCHANNEL_DATA_ADD.format(t_channel.id, guild_id))
    
    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Handle DB Operations when a user joins a server the bot is in"""
        guild_id = member.guild.id
        user_id = member.id
        event_logger.info(USER_JOIN_SERVER.format(user_id, guild_id))

        primary_key = {"guild_id" : guild_id, "user_id" : user_id}
        server_data = SERVERS.find_one({"_id" : guild_id})
        user_data = USERS.find_one({"_id" : primary_key})
        
        # Add user if does not already exist in data
        if user_data is None:
            user_obj = DataClasses.User(_id = primary_key)
            USERS.insert_one(user_obj.__dict__)
            data_logger.info(USER_DATA_ADD.format(user_id, guild_id))

        # Log join event if server has a `logs` channel
        if server_data["logs"] != -1:
            pass

        # Give user all `auto_roles` for this server
        for auto_role in server_data["auto_roles"]:
            role = discord.utils.get(member.guild.roles, id = auto_role)
            try:
                await member.add_roles(role)
            except Exception as e:
                continue

        wrapped_data = WRAPPED.find_one({"_id" : primary_key})
        if wrapped_data is None:
            wrapped_user_obj = DataClasses.WrappedUser(
                _id = primary_key,
                user_pings = {"count" : 0}
            )
            WRAPPED.insert_one(wrapped_user_obj.__dict__)
            data_logger.info(WRAPPED_DATA_ADD.format(user_id, guild_id))
    
    @commands.Cog.listener()
    async def on_member_remove(self, member):
        guild_id = member.guild.id
        user_id = member.id
        event_logger.info(USER_LEFT_SERVER.format(user_id, guild_id))
        pass

    # Voice State
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        guild_id = member.guild_id
        user_id = member.id
        server_data = SERVERS.find_one({"_id" : guild_id})

        create_id = server_data["vc_create_id"]
        category_id = server_data["vc_category_id"]

        created_vc = after.channel and after.channel.id == create_id
        changed_vc = before.channel != after.channel

        curr_time = datetime.utcnow()

        # Add channel to data if it is a new vc
        if created_vc:
            category = discord.utils.get(member.guild.categories, id = category_id)
            created_channel = await member.guild.create_voice_channel(
                name = f"{member.name}'s VC",
                category = category
            )
            await member.move_to(created_channel)
            channel_data = DataClasses.VChannel(
                _id = created_channel.id,
                owner = user_id,
                denied = [],
                allowed = [],
                hidden = [],
                is_locked = False,
                deleted_at = None,
                created_at = curr_time
            )
            VCS.insert_one(channel_data.__dict__)

        # Delete VC if empty after leaving
        if before.channel:
            vc_data = VCS.find_one({"_id" : before.channel.id})
            if vc_data is not None:
                if before.channel.id != create_id:
                    if len(before.channel.members) == 0:
                        await before.channel.delete()
                        VCS.update_one(
                            vc_data, 
                            {"$set" : {"deleted_at" : curr_time}})
        
        # Wrapped Stuff
        afk_corner = -1
        events = []

        # Start a stream
        if not before.self_stream and after.self_stream:
            events.append(
                DataClasses.VCEvent(
                    guild_id, 
                    user_id,
                    curr_time, 
                    EventType.START_STREAM, 
                    after.channel.id))
        
        # End a stream
        if before.self_stream and (after.channel != before.channel or not after.self_stream):
            events.append(
                DataClasses.VCEvent(
                    guild_id, 
                    user_id,
                    curr_time, 
                    EventType.END_STREAM, 
                    (after.channel.id if after.channel else None)))


        # The event that triggered this method was a movement between VCs (or leaving  / joining)
        if changed_vc:
            # If you left a VC
            if before.channel:
                events.append(
                    DataClasses.VCEvent(
                        guild_id,
                        user_id,
                        curr_time,
                        (EventType.LEAVE_VC if before.channel.id != afk_corner else EventType.LEAVE_AFK),
                        before.channel.id
                    )
                )
            
            # If you joined a VC
            if after.channel:
                events.append(
                    DataClasses.VCEvent(
                        guild_id,
                        user_id,
                        curr_time,
                        (EventType.JOIN_VC if after.channel.id != afk_corner else EventType.JOIN_AFK),
                        after.channel.id
                    )
                )
        
        documents = [event.__dict__ for event in events]
        VC_EVENTS.insert_many(documents)
    
    @commands.Cog.listener()
    async def on_message(self, message):
        try:
            assert message.author != self.client.user
            assert not message.author.bot
            assert not isinstance(message.channel, DMChannel)
        except AssertionError:
            return
        
        guild_id = message.guild.id
        user_id = message.author.id
        primary_key = {"guild_id" : guild_id, "user_id" : user_id}
        user_data = USERS.find_one({"_id" : primary_key})
        wrapped_data = WRAPPED.find_one({"_id" : primary_key})  
        server_data = SERVERS.find_one({"_id" : guild_id})
        
        if user_data is None:
            return

        # Update level if server has levels enabled
        if server_data["levels"]:
            rate_limit = self.get_ratelimit(message)
            if not rate_limit:
                updated_xp = user_data["xp"] + random.randint(5, 15)
                current_level = user_data["level"]
                if leveled_up(updated_xp, current_level):
                    current_level += 1
                    await message.channel.send(embed = Embed(
                        title = LEVEL_UP.format(current_level),
                        color = Color.green()
                    ))
                    await message.channel.send(message.author.mention)
                
                USERS.update_one(
                    user_data,
                    {"$set" : {
                        "xp" : updated_xp,
                        "level" : current_level
                    }}
                )
        
        # Wrapped Stuff
        content = message.content
        words = len(content.split(" "))
        pings_everyone = int(message.mention_everyone)
        has_image = int(len(message.attachments) > 0)
        has_embed = int("tenor.com" in content)
        mentions = [target.id for target in message.mentions]
        toInc = {
                "everyone_pings" : pings_everyone,
                "image_count" : has_image,
                "gif_count" : has_embed,
                "word_count" : words,
                "message_count" : 1,
                "user_pings.count" : int(len(mentions) > 0)
            } 
        
        for target in mentions:
            key = f"user_pings.{target}"
            toInc[key] = 1
        
        WRAPPED.update_one(wrapped_data, {"$inc" : toInc})
    
    @commands.Cog.listener()
    async def on_message_delete(self, message):
        try:
            assert message.author != self.client.user
            assert not message.content.startswith(".")
            assert len(message.attachments) == 0
        except AssertionError:
            return
        
        guild_id = message.guild.id
        user_id = message.author.id

        channel_id = message.channel.id
        server_data = SERVERS.find_one({"_id" : guild_id})
        channel_data = TEXT_CHANNELS.find_one({"_id" : channel_id})
        member = message.author
        if channel_data is not None:
            TEXT_CHANNELS.update_one(
                channel_data,
                {"$set" : {
                    "deleted_author" : user_id,
                    "deleted_content" : message.content
                }})
        
            logs = server_data["logs_id"]
            if logs != -1:
                pass

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        try:
            assert before.author != self.client.user
            assert "https://" not in before.content
        except AssertionError:
            return

        guild_id = before.guild.id
        user_id = before.author.id
        channel_id = before.channel.id
        server_data = SERVERS.find_one({"_id" : guild_id})
        channel_data = TEXT_CHANNELS.find_one({"_id" : channel_id})
        member = before.author     
        if channel_data is not None:
            TEXT_CHANNELS.update_one(
                channel_data,
                {"$set" : {
                    "edited_author" : user_id,
                    "edited_content" : before.content
                }}
            )

            logs = server_data["logs_id"]
            if logs != -1:
                pass
    
    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        event_logger.log(CHANNEL_DELETE.format(channel.id, channel.guild.id))
        curr_time = datetime.utcnow()
        collection = TEXT_CHANNELS if isinstance(channel, discord.TextChannel) else VCS
        channel_data = collection.find_one({"_id" : channel.id})
        if channel_data is not None:
            collection.update_one(
                    channel_data,
                    {"$set" : {
                        "deleted_at" : curr_time
                    }}
                )
    
    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        event_logger.log(CHANNEL_CREATE.format(channel.id, channel.guild.id))
        curr_time = datetime.utcnow()
        if isinstance(channel, discord.TextChannel):
            channel_data_obj = DataClasses.TChannel(
                _id = channel.id,
                created_at = curr_time)
            
            TEXT_CHANNELS.insert_one(channel_data_obj.__dict__)
            # TODO: Banished stuff


async def setup(client):
    await client.add_cog(Events(client))





            

                    
