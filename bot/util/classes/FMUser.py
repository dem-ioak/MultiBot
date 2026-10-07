import os
import requests
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

FM_URL = "https://ws.audioscrobbler.com/2.0/"
FM_TIMEOUT = 10
FM_NOT_FOUND = 6  # LastFM error code for "the user/artist/track you asked for does not exist"
FM_TRANSIENT = (8, 11, 16)  # LastFM error codes that are worth a retry
FM_PAGE_SIZE = 500  # Pages of 1000 are accepted, but fail on LastFM's end about a quarter of the time
FM_PAGE_BATCH = 4  # Library pages fetched at once when scanning a user's top tracks

# LastFM keeps every spelling of an artist ("Giveon", "GIVĒON") as its own artist, with its own plays.
# These decide which spellings are counted together. Each one costs an extra request per lookup, so
# the long tail of misspellings almost nobody has listened to is left out
MAX_VARIANTS = 4
MIN_VARIANT_SHARE = 0.005  # Of the listeners of the most popular spelling
TOP_FETCH_FACTOR = 5  # How much further than asked a top list is read, to find entries to merge

# Shared so every call reuses the same connections instead of opening a new one
session = requests.Session()


class FMError(Exception):
    """Raised when LastFM cannot be reached, or responds with an error"""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def fm_request(method, retry=True, **params):
    """Make a call to the LastFM API, returning the parsed JSON response"""
    query = dict(params, method=method, api_key=os.getenv("LAST_FM_KEY"), format="json")
    try:
        data = session.get(FM_URL, params=query, timeout=FM_TIMEOUT).json()
    except (requests.RequestException, ValueError) as e:
        raise FMError("Could not reach LastFM") from e

    if "error" in data:
        if retry and data["error"] in FM_TRANSIENT:
            return fm_request(method, retry=False, **params)
        raise FMError(data.get("message", "Unknown LastFM error"), data["error"])

    return data


def as_list(value):
    """LastFM returns a bare object instead of a list when there is only one result"""
    if not value:
        return []
    return value if isinstance(value, list) else [value]


def normalize(name):
    """Reduce a name to what is left once accents and capitalisation are ignored ("GIVĒON" -> "giveon")"""
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(char for char in decomposed if not unicodedata.combining(char)).casefold().strip()


def _matches(data, mode):
    matches = data.get("results", {}).get(f"{mode}matches")
    if not isinstance(matches, dict):
        return []

    return as_list(matches.get(mode))


@lru_cache(maxsize=512)
def find_track(track, artist=None):
    """Search LastFM for a track, returning its proper (track, artist) names or None"""
    params = {"track": track, "limit": 1}
    if artist:
        params["artist"] = artist

    matches = _matches(fm_request("track.search", **params), "track")
    return (matches[0]["name"], matches[0]["artist"]) if matches else None


@lru_cache(maxsize=512)
def find_artist(artist):
    """Search LastFM for an artist, returning their proper name or None"""
    matches = _matches(fm_request("artist.search", artist=artist, limit=1), "artist")
    return matches[0]["name"] if matches else None


@lru_cache(maxsize=512)
def artist_variants(artist):
    """Get every spelling of an artist worth counting, the given one first.

    Spellings are the same artist when they only differ by accents or capitalisation, which is
    what a rename like "Giveon" -> "GIVĒON" comes down to."""
    target = normalize(artist)
    matches = _matches(fm_request("artist.search", artist=artist, limit=30), "artist")
    matches = [match for match in matches if normalize(match["name"]) == target]
    most_listeners = max((int(match["listeners"]) for match in matches), default=0)

    # LastFM itself ignores capitalisation, so spellings that only differ by it are one artist there
    variants = {artist.lower(): artist}
    for match in sorted(matches, key=lambda match: int(match["listeners"]), reverse=True):
        popular_enough = int(match["listeners"]) >= most_listeners * MIN_VARIANT_SHARE
        if popular_enough and len(variants) < MAX_VARIANTS:
            variants.setdefault(match["name"].lower(), match["name"])

    return tuple(variants.values())


class FMUser:
    def __init__(self, username):
        self.username = username

    def is_valid(self):
        """Determine whether the provided LastFM username is an active account"""
        try:
            fm_request("user.getinfo", user=self.username)
            return True
        except FMError as e:
            if e.code == FM_NOT_FOUND:
                return False
            raise

    def _plays_track(self, artist, track, autocorrect):
        try:
            data = fm_request(
                "track.getInfo",
                artist=artist,
                track=track,
                username=self.username,
                autocorrect=autocorrect,
            )
        except FMError as e:
            if e.code == FM_NOT_FOUND:
                return 0
            raise

        return int(data["track"].get("userplaycount", 0))

    def get_plays_track(self, artist, track):
        """Get this user's playcount for provided track, across every spelling of the artist"""
        plays = sum(self._plays_track(variant, track, 0) for variant in artist_variants(artist))

        # Nothing under the exact names, so let LastFM have a go at correcting a misspelt title
        return plays or self._plays_track(artist, track, 1)

    def _plays_artist(self, artist):
        try:
            data = fm_request(
                "artist.getinfo", artist=artist, username=self.username, autocorrect=0
            )
        except FMError as e:
            if e.code == FM_NOT_FOUND:
                return 0
            raise

        return int(data["artist"].get("stats", {}).get("userplaycount", 0))

    def get_plays_artist(self, artist):
        """Get this user's playcount for provided artist, across every spelling of their name"""
        return sum(self._plays_artist(variant) for variant in artist_variants(artist))

    def get_np(self):
        """Get the track this user is currently playing (or last played), None if they have no scrobbles"""
        data = fm_request("user.getrecenttracks", user=self.username, limit=1)
        tracks = as_list(data["recenttracks"].get("track"))
        if not tracks:
            return None

        track = tracks[0]
        images = track.get("image") or [{}]
        return {
            "song_name": track["name"],
            "artist": track["artist"]["#text"],
            "image": images[-1].get("#text", ""),
            "album": track["album"]["#text"],
        }

    def get_top(self, mode, period="overall", limit=10):
        """Get this user's top `mode` ("artist", "album" or "track") over the provided timeframe"""
        data = fm_request(
            f"user.gettop{mode}s",
            user=self.username,
            period=period,
            limit=limit * TOP_FETCH_FACTOR,
        )

        # The same artist under two spellings shows up as two entries, which are added together.
        # They come back most played first, so the name kept is the one the user listens to most
        merged = {}
        for entry in as_list(data[f"top{mode}s"].get(mode)):
            artist = None if mode == "artist" else entry["artist"]["name"]
            key = (normalize(entry["name"]), normalize(artist or ""))
            if key in merged:
                merged[key]["playcount"] += int(entry["playcount"])
            else:
                merged[key] = {
                    "name": entry["name"],
                    "artist": artist,
                    "playcount": int(entry["playcount"]),
                }

        entries = sorted(merged.values(), key=lambda entry: entry["playcount"], reverse=True)
        return entries[:limit]

    def _top_tracks_page(self, page):
        return fm_request(
            "user.gettoptracks", user=self.username, limit=FM_PAGE_SIZE, page=page
        )["toptracks"]

    def get_top_tracks_artist(self, artist, limit=10):
        """Get this user's top tracks from the provided artist as (track, playcount) pairs"""
        variants = artist_variants(artist)
        names = set(variant.lower() for variant in variants)

        # Knowing the total lets us stop scanning once every play is accounted for,
        # rather than reading the whole library when the artist has under `limit` tracks
        remaining = self.get_plays_artist(artist)
        if not remaining:
            return []

        # A track played under two spellings of the artist is two rows in the library, added together here
        totals = {}  # normalized track name -> [name, playcount]

        def collect(data):
            """Add this page's matches, returning whether the scan is finished"""
            nonlocal remaining
            tracks = as_list(data.get("track"))
            for track in tracks:
                if track["artist"]["name"].lower() in names:
                    plays = int(track["playcount"])
                    totals.setdefault(normalize(track["name"]), [track["name"], 0])[1] += plays
                    remaining -= plays

            if remaining <= 0 or not tracks:
                return True
            if len(totals) < limit:
                return False

            # Rows come back most played first, so nothing further on has more plays than the last
            # row seen. We are done once neither a brand new track, nor one of the tracks outside
            # the top `limit`, could still collect enough from the other spellings to break in
            ranked = sorted((plays for name, plays in totals.values()), reverse=True)
            lowest = int(tracks[-1]["playcount"])
            runner_up = ranked[limit] if len(ranked) > limit else 0
            return ranked[limit - 1] >= max(len(variants) * lowest, runner_up + (len(variants) - 1) * lowest)

        first = self._top_tracks_page(1)
        if not collect(first):
            total_pages = int(first.get("@attr", {}).get("totalPages", 1))
            with ThreadPoolExecutor(FM_PAGE_BATCH) as pool:
                for start in range(2, total_pages + 1, FM_PAGE_BATCH):
                    pages = range(start, min(start + FM_PAGE_BATCH, total_pages + 1))
                    if any(collect(data) for data in pool.map(self._top_tracks_page, pages)):
                        break

        result = sorted(totals.values(), key=lambda track: track[1], reverse=True)[:limit]
        if len(variants) > 1 and remaining > 0:
            # The scan stopped once the top tracks were settled, but one of them may still have a few
            # plays under another spelling further down the library. Ask for those tracks directly
            with ThreadPoolExecutor(FM_PAGE_BATCH) as pool:
                exact = pool.map(lambda track: self.get_plays_track(artist, track[0]), result)
                result = [[name, max(plays, count)] for (name, plays), count in zip(result, exact)]
            result.sort(key=lambda track: track[1], reverse=True)

        return [(name, plays) for name, plays in result]
