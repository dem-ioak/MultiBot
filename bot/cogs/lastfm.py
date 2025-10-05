import discord
from discord import Embed, Color, guild, app_commands
from discord.ext import commands, tasks
from discord.app_commands import Choice

from util.helper_functions import handle_get_top_call
from util.classes.FMUser import FMUser, convert_title
from util.constants import USERS

import os
from util.log_manager import get_logger
import requests
from dotenv import load_dotenv

load_dotenv()


CHOICES = [
    Choice(name="Weekly", value="7day"),
    Choice(name="Monthly", value="1month"),
    Choice(name="Tri-Monthly", value="3month"),
    Choice(name="Bi-Anually", value="6month"),
    Choice(name="Yearly", value="12month"),
]

NO_USERNAME_EMBED = Embed(
    description="You have not yet set your **Last.FM** username! Use `fm set (username)` to get started.",
    color=Color.red(),
)

SPOTIFY_CLIENT = os.getenv("SPOTIFY_CLIENT")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

def get_spotify_access_token():
    URL = "https://accounts.spotify.com/api/token"
    headers = {
        "Content-Type" : "application/x-www-form-urlencoded"
    }
    
    data = {
        "grant_type" : "client_credentials",
        "client_id" : SPOTIFY_CLIENT,
        "client_secret" : SPOTIFY_CLIENT_SECRET
    }
    
    resp = requests.post(URL, headers = headers, data = data)
    access_token = resp.json()["access_token"]
    return access_token

def search_for_id(album_name, headers, artist = None):
    URL = f"https://api.spotify.com/v1/search?q=\"{album_name}\""
    if artist:
        URL += f"%20artist:\"{artist}\""
        
    URL += "&type=album"
    resp = requests.get(URL, headers=headers)
    id_ = resp.json()["albums"]["items"][0]["id"]
    return id_

def get_album_info(album_id, headers):
    URL = f"https://api.spotify.com/v1/albums/{album_id}"
    resp = requests.get(URL, headers=headers)
    data = resp.json()
    album_name = data["name"]
    artist_name = data["artists"][0]["name"]
    track_items = data["tracks"]["items"]
    tracklist = [track["name"] for track in track_items]
    return (album_name, artist_name, tracklist)

class Lastfm(commands.Cog):
    def __init__(self, client):
        self.client = client

    lastfm = app_commands.Group(
        name="lastfm", description="Commands utilizing LastFM Functionality"
    )

    @lastfm.command(
        name="topartists", description="Generate a list of any users Top Artists."
    )
    @app_commands.choices(time=CHOICES)
    async def topartists(
        self,
        interaction: discord.Interaction,
        target: discord.Member = None,
        time: Choice[str] = None,
    ):
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /lastfm topartists [target={target}, time={time}]")
        await interaction.response.defer()
        embed = handle_get_top_call(interaction, target, time, "artists")
        await interaction.followup.send(embed=embed)

    @lastfm.command(
        name="topalbums", description="Generate a list of any users Top Albums."
    )
    @app_commands.choices(time=CHOICES)
    async def topalbums(
        self,
        interaction: discord.Interaction,
        target: discord.Member = None,
        time: Choice[str] = None,
    ):
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /lastfm topalbums [target={target}, time={time}]")
        await interaction.response.defer()
        embed = handle_get_top_call(interaction, target, time, "albums")
        await interaction.followup.send(embed=embed)

    @lastfm.command(
        name="toptracks", description="Generate a list of any users Top Tracks."
    )
    @app_commands.choices(time=CHOICES)
    async def toptracks(
        self,
        interaction: discord.Interaction,
        target: discord.Member = None,
        time: Choice[str] = None,
    ):
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /lastfm toptracks [target={target}, time={time}]")
        await interaction.response.defer()
        embed = handle_get_top_call(interaction, target, time, "tracks")
        await interaction.followup.send(embed=embed)

    # TODO: This
    @lastfm.command(
        name="nowplaying", description="Display what song you are currently streaming."
    )
    async def nowplaying(
        self, interaction: discord.Interaction, target: discord.Member = None
    ):
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /lastfm nowplaying [target={target}]")
        guild_id = interaction.guild.id
        target = target if target else interaction.user
        user_id = target.id if target else interaction.user.id
        primary_key = {"guild_id": guild_id, "user_id": user_id}
        user_data = USERS.find_one({"_id": primary_key})
        fm_username = user_data["last_fm"]
        if not fm_username:
            await interaction.response.send_message(embed=NO_USERNAME_EMBED)
            return

        fm_user = FMUser(fm_username)
        name, artist, img, album = fm_user.get_np().values()
        playcount = fm_user.get_plays_track(artist, name)
        embed = Embed()
        embed.color = Color.red()
        embed.add_field(name="Track", value=name, inline=True)
        embed.add_field(name="Artist", value=artist, inline=True)
        embed.set_footer(text="Album: {} - Playcount: {}".format(album, playcount))
        embed.set_thumbnail(url=img)
        embed.set_author(name=target.name, icon_url=target.avatar.url)
        await interaction.response.send_message(embed=embed)

    @lastfm.command(
        name="toptracksartist",
        description="Generate your most played songs from a given artist",
    )
    async def toptracksartist(
        self,
        interaction: discord.Interaction,
        target: discord.Member = None,
        artist: str = None,
    ):
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /lastfm toptracksartist [target={target}, artist={artist}]")
        embed = Embed(color=Color.red())
        guild_id = interaction.guild.id
        target = target if target else interaction.user
        user_id = target.id if target else interaction.user.id
        primary_key = {"guild_id": guild_id, "user_id": user_id}
        user_data = USERS.find_one({"_id": primary_key})
        fm_username = user_data["last_fm"]
        if not fm_username:
            await interaction.response.send_message(embed=NO_USERNAME_EMBED)
            return

        await interaction.response.defer()
        res = ""
        fm_user = FMUser(fm_username)
        artist = artist if artist else fm_user.get_np()["artist"]
        data = fm_user.get_top_tracks_artist(artist)
        for i, val in enumerate(data):
            res += f"`{i + 1}` **{val[0]}** - {val[1]} plays\n"

        embed.description = res if res else "No listening records for this artist"
        embed.set_author(name=target.name, icon_url=target.avatar.url)
        await interaction.followup.send(embed=embed)

    @lastfm.command(name="set", description="Set your lastfm username")
    async def set(self, interaction: discord.Interaction, username: str):
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /lastfm set [username={username}]")
        fm_obj = FMUser(username)
        guild_id = interaction.guild.id
        user_id = interaction.user.id
        if not fm_obj.is_valid():
            embed = Embed(
                color=Color.red(),
                description=f"**{username}** is not a valid username according to LastFM. Use /lastfm set to set your username.",
            )
        else:
            primary_key = {"guild_id": guild_id, "user_id": user_id}
            USERS.update_one({"_id": primary_key}, {"$set": {"last_fm": username}})
            embed = Embed(
                color=Color.green(),
                description=f"Successfully set your LastFM username to **{username}**",
            )

        await interaction.response.send_message(embed=embed)

    @lastfm.command(
        name="compareartist",
        description="Compare every user's playcount of a given artist",
    )
    async def compareartist(self, interaction: discord.Interaction):
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /lastfm compareartist")
        guild_id = interaction.guild.id
        user_id = interaction.user.id
        primary_key = {"guild_id": guild_id, "user_id": user_id}
        user_data = USERS.find_one({"_id": primary_key})
        user_fm = user_data["last_fm"]
        if not user_fm:
            await interaction.response.send_message(embed=NO_USERNAME_EMBED)
            return

        await interaction.response.defer()
        user_fm_obj = FMUser(user_fm)
        now_playing = user_fm_obj.get_np()
        artist = now_playing["artist"]

        fm_users = USERS.find(
            {"_id.guild_id": guild_id, "last_fm": {"$ne": None}}
        )  # All users IN THIS SERVER, with a FM set
        entries = []
        for user in fm_users:
            user_id = user["_id"]["user_id"]
            user_obj = self.client.get_user(user_id)

            if not user_obj:
                continue

            fm_username = user["last_fm"]
            fm_obj = FMUser(fm_username)
            playcount = int(fm_obj.get_plays_artist(artist))
            entries.append((user_obj.name, playcount))

        entries.sort(key=lambda x: x[1], reverse=True)
        description = str()
        for i, (username, playcount) in enumerate(entries):
            description += f"`{i + 1}` **{username}** - {playcount} Plays\n"

        embed = Embed(
            title=f"Top Listeners for {artist}",
            description=description,
            color=Color.red(),
        )
        await interaction.followup.send(embed=embed)

    @lastfm.command(
        name="comparetrack",
        description="Compare every user's playcount of a given track",
    )
    async def comparetrack(
        self, interaction: discord.Interaction, target: discord.Member = None
    ):
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /lastfm comparetrack [target={target}]")
        guild_id = interaction.guild.id
        user_id = interaction.user.id
        primary_key = {"guild_id": guild_id, "user_id": user_id}
        user_data = USERS.find_one({"_id": primary_key})
        user_fm = user_data["last_fm"]
        if not user_fm:
            await interaction.response.send_message(embed=NO_USERNAME_EMBED)
            return

        await interaction.response.defer()
        user_fm_obj = FMUser(user_fm)
        now_playing = user_fm_obj.get_np()
        artist, track = now_playing["artist"], now_playing["song_name"]

        fm_users = USERS.find(
            {"_id.guild_id": guild_id, "last_fm": {"$ne": None}}
        )  # All users IN THIS SERVER, with a FM set
        entries = []
        for user in fm_users:
            user_id = user["_id"]["user_id"]
            user_obj = self.client.get_user(user_id)

            if not user_obj:
                continue

            fm_username = user["last_fm"]
            fm_obj = FMUser(fm_username)
            playcount = int(fm_obj.get_plays_track(artist, track))
            entries.append((user_obj.name, playcount))

        entries.sort(key=lambda x: x[1], reverse=True)
        description = str()
        for i, (username, playcount) in enumerate(entries):
            description += f"`{i + 1}` **{username}** - {playcount} Plays\n"

        embed = Embed(
            title=f"Top Listeners for {track} by {artist}",
            description=description,
            color=Color.red(),
        )
        await interaction.followup.send(embed=embed)

    @lastfm.command(
        name="playsalbum", description="Get the playcount for every song on an album"
    )
    async def playsalbum(
        self,
        interaction: discord.Interaction,
        target: discord.Member = None,
        album: str = None,
        artist: str = None
    ):
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /lastfm playsalbum [target={target}, album={album}, artist={artist}]")
        guild_id = interaction.guild.id
        target = target if target else interaction.user
        user_id = target.id
        primary_key = {"guild_id": guild_id, "user_id": user_id}
        user_data = USERS.find_one({"_id": primary_key})
        user_fm = user_data["last_fm"]
        if not user_fm:
            await interaction.response.send_message(embed=NO_USERNAME_EMBED)
            return

        await interaction.response.defer()
        
        user_fm_obj = FMUser(user_fm)
        access_token = get_spotify_access_token()
        headers = {"Authorization" : f"Bearer {access_token}"}
        now_playing = user_fm_obj.get_np()
        if not album and not artist:
            album = now_playing["album"]
            artist = now_playing["artist"]
        
        album_id = search_for_id(album_name=album, headers=headers, artist=artist)
        album_name, artist_name, tracklist = get_album_info(album_id, headers)
        
        playcounts = []
        for track_name in tracklist:
            playcount = user_fm_obj.get_plays_track(artist_name, track_name)
            playcounts.append((track_name, int(playcount)))
        
        playcounts.sort(key = lambda x : x[1], reverse=True)
        description = ""
        for i, (track_name, playcount) in enumerate(playcounts):
            description += f"`{i+1}` **{track_name}** - {playcount} plays\n"
        
        embed = Embed()
        embed.title = f"Plays for songs on {album_name} by {artist_name}"
        embed.description = description
        embed.color = Color.red()
        embed.set_author(name=target.name, icon_url=target.avatar.url)
        await interaction.followup.send(embed=embed)
        
async def setup(client):
    await client.add_cog(Lastfm(client))
