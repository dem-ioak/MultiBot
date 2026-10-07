import discord
from discord import Embed, Color, app_commands
from discord.ext import commands
from discord.app_commands import Choice
from discord.ui import View

from util.classes.FMUser import FMUser, FMError, find_artist, find_track, artist_variants
from util.constants import USERS
from util.helper_functions import user_defaults
from util.log_manager import get_logger

import asyncio
import os
import requests
from math import ceil
import time as time_module

CHOICES = [
    Choice(name="Overall", value="overall"),
    Choice(name="Weekly", value="7day"),
    Choice(name="Monthly", value="1month"),
    Choice(name="Tri-Monthly", value="3month"),
    Choice(name="Bi-Annually", value="6month"),
    Choice(name="Yearly", value="12month"),
]

SPOTIFY_TIMEOUT = 10
EMBED_DESCRIPTION_LIMIT = 4096
MAX_CONCURRENT_REQUESTS = 5

PAGE_SIZE = 10
PAGE_TIMEOUT = 300  # Seconds the arrows under a list keep working after they were last used
TOP_LIST_SIZE = 100  # How far topartists / topalbums / toptracks can be paged, 10 pages
ARTIST_TRACKS_SIZE = 50  # How far toptracksartist can be paged, 5 pages


def error_embed(description):
    return Embed(description=description, color=Color.red())


def no_username_embed(member, invoker):
    if member == invoker:
        return error_embed(
            "You have not yet set your **LastFM** username! Use `/lastfm set` to get started."
        )
    return error_embed(
        f"**{member.name}** has not yet set their **LastFM** username! They can use `/lastfm set` to get started."
    )


def ranked_lines(entries, unit="plays"):
    """Build numbered lines out of (name, playcount) pairs"""
    return [
        f"`{i + 1}` **{name}** - {playcount} {unit}"
        for i, (name, playcount) in enumerate(entries)
    ]


def ranked_description(entries, unit="plays"):
    """Build a numbered embed description out of (name, playcount) pairs"""
    return "\n".join(ranked_lines(entries, unit))[:EMBED_DESCRIPTION_LIMIT]


class PageView(View):
    """Arrows to flip through a list that is too long for one embed, `PAGE_SIZE` lines at a time.

    Only the first page is fetched to begin with, as most lists are never flipped through.
    `load_more(count)` is called when a later page is asked for, and returns the first `count`
    lines of the list along with whether that is the whole list."""

    def __init__(self, embed, lines, invoker_id, note=None, load_more=None, complete=True, total=None):
        super().__init__(timeout=PAGE_TIMEOUT)
        self.embed = embed
        self.lines = lines
        self.invoker_id = invoker_id
        self.note = note
        self.load_more = load_more
        self.complete = complete or load_more is None
        self.total = total  # How long the full list is, if that is known before it is loaded
        self.page = 0
        self.message = None
        self.loading = asyncio.Lock()
        self.show_page()

    @property
    def page_count(self):
        """How many pages there are, None while that cannot be known without loading the rest"""
        if self.complete:
            return max(1, ceil(len(self.lines) / PAGE_SIZE))
        return ceil(self.total / PAGE_SIZE) if self.total else None

    def show_page(self):
        start = self.page * PAGE_SIZE
        self.embed.description = "\n".join(self.lines[start:start + PAGE_SIZE])
        footer = [self.note] if self.note else []
        if self.page_count != 1:
            footer.append(f"Page {self.page + 1}" + (f"/{self.page_count}" if self.page_count else ""))
        # Set every time, as a list found to end on its first page should stop saying "Page 1"
        self.embed.set_footer(text=" • ".join(footer) or None)
        self.previous_page.disabled = self.page == 0
        self.next_page.disabled = self.page_count is not None and self.page >= self.page_count - 1

    async def send(self, interaction):
        # A list that fits on one page has no use for arrows
        if self.page_count == 1:
            await interaction.followup.send(embed=self.embed)
        else:
            self.message = await interaction.followup.send(embed=self.embed, view=self)

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id == self.invoker_id:
            return True
        await interaction.response.send_message(
            embed=error_embed("Only the person who ran this command can flip its pages."),
            ephemeral=True,
        )
        return False

    async def flip(self, interaction, change):
        # Answered straight away, fetching more of the list can take longer than Discord waits
        await interaction.response.defer()
        async with self.loading:
            page = max(self.page + change, 0)
            needed = (page + 1) * PAGE_SIZE
            if len(self.lines) < needed and not self.complete:
                try:
                    self.lines, self.complete = await self.load_more(needed)
                except FMError as error:
                    # The page being shown is left as it was, so the arrow can simply be pressed again
                    await interaction.followup.send(
                        embed=error_embed(f"LastFM returned an error: {error}"), ephemeral=True
                    )
                    return

            # Stay on the last page there is, if the list turned out shorter than expected
            self.page = min(page, max(1, ceil(len(self.lines) / PAGE_SIZE)) - 1)
            self.show_page()
            await interaction.edit_original_response(embed=self.embed, view=self)

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.gray)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.flip(interaction, -1)

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.gray)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.flip(interaction, 1)

    async def on_timeout(self):
        # Grey the arrows out, so it is clear they have stopped working
        self.previous_page.disabled = self.next_page.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass  # The message was deleted in the meantime


spotify_session = requests.Session()
spotify_token = {"value": None, "expires_at": 0}


def get_spotify_access_token():
    """Get a Spotify access token, reusing the current one until it is about to expire"""
    if spotify_token["value"] and time_module.monotonic() < spotify_token["expires_at"]:
        return spotify_token["value"]

    resp = spotify_session.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "client_credentials",
            "client_id": os.getenv("SPOTIFY_CLIENT"),
            "client_secret": os.getenv("SPOTIFY_CLIENT_SECRET"),
        },
        timeout=SPOTIFY_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    spotify_token["value"] = data["access_token"]
    spotify_token["expires_at"] = time_module.monotonic() + data.get("expires_in", 3600) - 60
    return spotify_token["value"]


def search_for_id(album_name, headers, artist=None):
    query = f'"{album_name}"'
    if artist:
        query += f' artist:"{artist}"'

    resp = spotify_session.get(
        "https://api.spotify.com/v1/search",
        headers=headers,
        params={"q": query, "type": "album", "limit": 1},
        timeout=SPOTIFY_TIMEOUT,
    )
    resp.raise_for_status()
    items = [item for item in resp.json()["albums"]["items"] if item]
    return items[0]["id"] if items else None


def get_album_info(album_name, artist=None):
    """Look an album up on Spotify, returning (album name, artist name, tracklist) or None"""
    headers = {"Authorization": f"Bearer {get_spotify_access_token()}"}
    album_id = search_for_id(album_name, headers, artist)
    if not album_id:
        return None

    resp = spotify_session.get(
        f"https://api.spotify.com/v1/albums/{album_id}",
        headers=headers,
        timeout=SPOTIFY_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    tracklist = [track["name"] for track in data["tracks"]["items"]]
    return (data["name"], data["artists"][0]["name"], tracklist)


class Lastfm(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.request_limit = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    lastfm = app_commands.Group(
        name="lastfm",
        description="Commands utilizing LastFM Functionality",
        guild_only=True,
    )

    async def cog_app_command_error(self, interaction: discord.Interaction, error):
        # Logging is left to the global handler in main.py, this only tells the user
        error = getattr(error, "original", error)
        if isinstance(error, FMError):
            embed = error_embed(f"LastFM returned an error: {error}")
        else:
            embed = error_embed("Something went wrong while running this command.")

        if interaction.response.is_done():
            await interaction.followup.send(embed=embed)
        else:
            await interaction.response.send_message(embed=embed)

    async def run_blocking(self, func, *args):
        """Run a blocking API call off of the event loop"""
        async with self.request_limit:
            return await asyncio.to_thread(func, *args)

    async def get_fm_user(self, interaction, member):
        """Get the FMUser of a member. If they have none set, respond saying so and return None"""
        primary_key = {"guild_id": interaction.guild.id, "user_id": member.id}
        user_data = USERS.find_one({"_id": primary_key})
        fm_username = user_data.get("last_fm") if user_data else None
        if not fm_username:
            await interaction.followup.send(
                embed=no_username_embed(member, interaction.user)
            )
            return None

        return FMUser(fm_username)

    async def get_now_playing(self, interaction, member):
        """Get what a member is playing. If that is not possible, respond saying so and return None"""
        fm_user = await self.get_fm_user(interaction, member)
        if not fm_user:
            return None

        now_playing = await self.run_blocking(fm_user.get_np)
        if not now_playing:
            await interaction.followup.send(
                embed=error_embed(f"**{member.name}** has not listened to anything yet.")
            )

        return now_playing

    async def resolve_artist(self, interaction, member, artist):
        """Use the given artist if there is one, otherwise whoever the member is playing"""
        if not artist:
            now_playing = await self.get_now_playing(interaction, member)
            return now_playing["artist"] if now_playing else None

        found = await self.run_blocking(find_artist, artist)
        if not found:
            await interaction.followup.send(
                embed=error_embed(f"Could not find an artist named **{artist}**.")
            )

        return found

    async def send_top(self, interaction, target, time, mode):
        """Handle logic for all forms of top(artists/albums/tracks)"""
        await interaction.response.defer()
        target = target or interaction.user
        fm_user = await self.get_fm_user(interaction, target)
        if not fm_user:
            return

        period = time.value if time else "overall"
        loaded = 0
        total = None

        async def load(count):
            """Get the first `count` lines of the list, and whether that is all there is to show"""
            nonlocal loaded, total
            # Fetched in growing steps, so paging through the whole list takes a handful of requests
            loaded = min(TOP_LIST_SIZE, max(count, loaded * 2))
            entries, total = await self.run_blocking(fm_user.get_top, mode, period, loaded)
            lines = []
            for i, entry in enumerate(entries):
                line = f"`{i + 1}` **{entry['name']}**"
                if entry["artist"]:
                    line += f" by **{entry['artist']}**"
                lines.append(line + f" ({entry['playcount']} plays)")
            return lines, len(entries) < loaded or loaded >= TOP_LIST_SIZE

        lines, complete = await load(PAGE_SIZE)
        embed = Embed(
            color=Color.red(),
            title=f"Top {mode.title()}s ({time.name if time else 'Overall'})",
        )
        embed.set_author(name=target.name, icon_url=target.display_avatar.url)
        await PageView(
            embed,
            lines or ["No listening records for this timeframe"],
            interaction.user.id,
            load_more=load,
            complete=complete,
            total=min(total, TOP_LIST_SIZE),
        ).send(interaction)

    async def get_spellings_note(self, artist):
        """Describe which other spellings of an artist ("Giveon" for "GIVĒON") are being counted, if any.

        Looking them up here also means the many lookups that follow all find them already known"""
        variants = await self.run_blocking(artist_variants, artist)
        if len(variants) == 1:
            return None
        return "Also counting plays under: " + ", ".join(variants[1:])

    async def send_comparison(self, interaction, title, get_plays, note=None):
        """Rank every user in this server with a LastFM set by `get_plays(fm_user)`"""

        async def fetch(member, fm_username):
            try:
                return member.name, await self.run_blocking(get_plays, FMUser(fm_username))
            except FMError:
                # Most likely a username that no longer exists, leave them out
                return None

        fm_users = USERS.find(
            {"_id.guild_id": interaction.guild.id, "last_fm": {"$ne": None}},
            {"last_fm": 1},
        )  # All users IN THIS SERVER, with a FM set
        fetches = []
        for user in fm_users:
            member = interaction.guild.get_member(user["_id"]["user_id"])
            if member and user["last_fm"]:
                fetches.append(fetch(member, user["last_fm"]))

        entries = [entry for entry in await asyncio.gather(*fetches) if entry]
        entries.sort(key=lambda x: x[1], reverse=True)

        embed = Embed(
            title=title,
            description=ranked_description(entries, "Plays") or "Nobody has listened to this",
            color=Color.red(),
        )
        if note:
            embed.set_footer(text=note)
        await interaction.followup.send(embed=embed)

    @lastfm.command(
        name="topartists", description="Generate a list of any users Top Artists."
    )
    @app_commands.describe(
        target="Whose top artists to show (defaults to you)",
        time="Timeframe to look at (defaults to overall)",
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
        await self.send_top(interaction, target, time, "artist")

    @lastfm.command(
        name="topalbums", description="Generate a list of any users Top Albums."
    )
    @app_commands.describe(
        target="Whose top albums to show (defaults to you)",
        time="Timeframe to look at (defaults to overall)",
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
        await self.send_top(interaction, target, time, "album")

    @lastfm.command(
        name="toptracks", description="Generate a list of any users Top Tracks."
    )
    @app_commands.describe(
        target="Whose top tracks to show (defaults to you)",
        time="Timeframe to look at (defaults to overall)",
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
        await self.send_top(interaction, target, time, "track")

    @lastfm.command(
        name="nowplaying", description="Display what song you are currently streaming."
    )
    @app_commands.describe(target="Whose current song to show (defaults to you)")
    async def nowplaying(
        self, interaction: discord.Interaction, target: discord.Member = None
    ):
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /lastfm nowplaying [target={target}]")
        await interaction.response.defer()
        target = target or interaction.user
        fm_user = await self.get_fm_user(interaction, target)
        if not fm_user:
            return

        now_playing = await self.run_blocking(fm_user.get_np)
        if not now_playing:
            await interaction.followup.send(
                embed=error_embed(f"**{target.name}** has not listened to anything yet.")
            )
            return

        name, artist, img, album = now_playing.values()
        playcount = await self.run_blocking(fm_user.get_plays_track, artist, name)
        embed = Embed()
        embed.color = Color.red()
        embed.add_field(name="Track", value=name, inline=True)
        embed.add_field(name="Artist", value=artist, inline=True)
        embed.set_footer(text="Album: {} - Playcount: {}".format(album or "N/A", playcount))
        if img:
            embed.set_thumbnail(url=img)
        embed.set_author(name=target.name, icon_url=target.display_avatar.url)
        await interaction.followup.send(embed=embed)

    @lastfm.command(
        name="toptracksartist",
        description="Generate your most played songs from a given artist",
    )
    @app_commands.describe(
        target="Whose most played songs to show (defaults to you)",
        artist="Artist to look at (defaults to the one currently playing)",
    )
    async def toptracksartist(
        self,
        interaction: discord.Interaction,
        target: discord.Member = None,
        artist: str = None,
    ):
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /lastfm toptracksartist [target={target}, artist={artist}]")
        await interaction.response.defer()
        target = target or interaction.user
        fm_user = await self.get_fm_user(interaction, target)
        if not fm_user:
            return

        artist = await self.resolve_artist(interaction, target, artist)
        if not artist:
            return

        note = await self.get_spellings_note(artist)
        async def load(count):
            """Get the artist's top `count` tracks as lines, and whether that is all there is to show"""
            count = min(count, ARTIST_TRACKS_SIZE)
            data, complete = await self.run_blocking(fm_user.get_top_tracks_artist, artist, count)
            return ranked_lines(data), complete or count >= ARTIST_TRACKS_SIZE

        lines, complete = await load(PAGE_SIZE)
        embed = Embed(color=Color.red(), title=f"Top Tracks by {artist}")
        embed.set_author(name=target.name, icon_url=target.display_avatar.url)
        await PageView(
            embed,
            lines or ["No listening records for this artist"],
            interaction.user.id,
            note,
            load_more=load,
            complete=complete,
        ).send(interaction)

    @lastfm.command(name="set", description="Set your lastfm username")
    @app_commands.describe(username="Your LastFM username")
    async def set(self, interaction: discord.Interaction, username: str):
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /lastfm set [username={username}]")
        await interaction.response.defer()
        username = username.strip()
        fm_obj = FMUser(username)
        if not await self.run_blocking(fm_obj.is_valid):
            embed = error_embed(
                f"**{username}** is not a valid username according to LastFM. Use /lastfm set to set your username."
            )
        else:
            primary_key = {"guild_id": interaction.guild.id, "user_id": interaction.user.id}
            # Users are normally created by the events cog, but make sure the name is never silently dropped
            USERS.update_one(
                {"_id": primary_key},
                {"$set": {"last_fm": username}, "$setOnInsert": user_defaults("last_fm")},
                upsert=True,
            )
            embed = Embed(
                color=Color.green(),
                description=f"Successfully set your LastFM username to **{username}**",
            )

        await interaction.followup.send(embed=embed)

    @lastfm.command(
        name="compareartist",
        description="Compare every user's playcount of a given artist",
    )
    @app_commands.describe(
        target="Use the artist this user is currently playing (defaults to you)",
        artist="Artist to compare (defaults to the one currently playing)",
    )
    async def compareartist(
        self,
        interaction: discord.Interaction,
        target: discord.Member = None,
        artist: str = None,
    ):
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /lastfm compareartist [target={target}, artist={artist}]")
        await interaction.response.defer()
        artist = await self.resolve_artist(interaction, target or interaction.user, artist)
        if not artist:
            return

        await self.send_comparison(
            interaction,
            f"Top Listeners for {artist}",
            lambda fm_user: fm_user.get_plays_artist(artist),
            await self.get_spellings_note(artist),
        )

    @lastfm.command(
        name="comparetrack",
        description="Compare every user's playcount of a given track",
    )
    @app_commands.describe(
        target="Use the track this user is currently playing (defaults to you)",
        track="Track to compare (defaults to the one currently playing)",
        artist="Artist of the track, to narrow down the search",
    )
    async def comparetrack(
        self,
        interaction: discord.Interaction,
        target: discord.Member = None,
        track: str = None,
        artist: str = None,
    ):
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /lastfm comparetrack [target={target}, track={track}, artist={artist}]")
        await interaction.response.defer()
        if track:
            found = await self.run_blocking(find_track, track, artist)
            if not found:
                searched = f"**{track}** by **{artist}**" if artist else f"**{track}**"
                await interaction.followup.send(
                    embed=error_embed(f"Could not find a track named {searched}.")
                )
                return
            track, artist = found
        elif artist:
            await interaction.followup.send(
                embed=error_embed("You need to specify a `track` along with the `artist`.")
            )
            return
        else:
            now_playing = await self.get_now_playing(interaction, target or interaction.user)
            if not now_playing:
                return
            artist, track = now_playing["artist"], now_playing["song_name"]

        await self.send_comparison(
            interaction,
            f"Top Listeners for {track} by {artist}",
            lambda fm_user: fm_user.get_plays_track(artist, track),
            await self.get_spellings_note(artist),
        )

    @lastfm.command(
        name="playsalbum", description="Get the playcount for every song on an album"
    )
    @app_commands.describe(
        target="Whose playcounts to show (defaults to you)",
        album="Album to look at (defaults to the one currently playing)",
        artist="Artist of the album, to narrow down the search",
    )
    async def playsalbum(
        self,
        interaction: discord.Interaction,
        target: discord.Member = None,
        album: str = None,
        artist: str = None,
    ):
        log = get_logger(__name__, server=interaction.guild.name, user=interaction.user.name)
        log.info(f"COMMAND_INVOKED: /lastfm playsalbum [target={target}, album={album}, artist={artist}]")
        await interaction.response.defer()
        target = target or interaction.user
        user_fm_obj = await self.get_fm_user(interaction, target)
        if not user_fm_obj:
            return

        if not album and artist:
            await interaction.followup.send(
                embed=error_embed("You need to specify an `album` along with the `artist`.")
            )
            return

        if not album:
            now_playing = await self.run_blocking(user_fm_obj.get_np)
            if not now_playing or not now_playing["album"]:
                await interaction.followup.send(
                    embed=error_embed(
                        f"Could not tell what album **{target.name}** is listening to. Try specifying an `album`."
                    )
                )
                return
            album, artist = now_playing["album"], now_playing["artist"]

        album_info = await self.run_blocking(get_album_info, album, artist)
        if not album_info:
            searched = f"**{album}** by **{artist}**" if artist else f"**{album}**"
            await interaction.followup.send(
                embed=error_embed(f"Could not find an album named {searched}.")
            )
            return

        album_name, artist_name, tracklist = album_info
        note = await self.get_spellings_note(artist_name)
        counts = await asyncio.gather(
            *(
                self.run_blocking(user_fm_obj.get_plays_track, artist_name, track_name)
                for track_name in tracklist
            )
        )
        playcounts = sorted(zip(tracklist, counts), key=lambda x: x[1], reverse=True)

        embed = Embed()
        embed.title = f"Plays for songs on {album_name} by {artist_name}"
        if note:
            embed.set_footer(text=note)
        embed.description = ranked_description(playcounts)
        embed.color = Color.red()
        embed.set_author(name=target.name, icon_url=target.display_avatar.url)
        await interaction.followup.send(embed=embed)


async def setup(client):
    await client.add_cog(Lastfm(client))
