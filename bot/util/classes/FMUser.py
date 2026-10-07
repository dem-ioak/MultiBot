import os
import requests
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

FM_URL = "https://ws.audioscrobbler.com/2.0/"
FM_TIMEOUT = 10
FM_NOT_FOUND = 6  # LastFM error code for "the user/artist/track you asked for does not exist"
FM_TRANSIENT = (8, 11, 16)  # LastFM error codes that are worth a retry
FM_PAGE_SIZE = 500  # Pages of 1000 are accepted, but fail on LastFM's end about a quarter of the time
FM_PAGE_BATCH = 4  # Library pages fetched at once when scanning a user's top tracks

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


def _first_match(data, mode):
    matches = data.get("results", {}).get(f"{mode}matches")
    if not isinstance(matches, dict):
        return None

    matches = as_list(matches.get(mode))
    return matches[0] if matches else None


@lru_cache(maxsize=512)
def find_track(track, artist=None):
    """Search LastFM for a track, returning its proper (track, artist) names or None"""
    params = {"track": track, "limit": 1}
    if artist:
        params["artist"] = artist

    match = _first_match(fm_request("track.search", **params), "track")
    return (match["name"], match["artist"]) if match else None


@lru_cache(maxsize=512)
def find_artist(artist):
    """Search LastFM for an artist, returning their proper name or None"""
    match = _first_match(fm_request("artist.search", artist=artist, limit=1), "artist")
    return match["name"] if match else None


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

    def get_plays_track(self, artist, track):
        """Get this user's playcount for provided track"""
        try:
            data = fm_request(
                "track.getInfo",
                artist=artist,
                track=track,
                username=self.username,
                autocorrect=1,
            )
        except FMError as e:
            if e.code == FM_NOT_FOUND:
                return 0
            raise

        return int(data["track"].get("userplaycount", 0))

    def get_plays_artist(self, artist, autocorrect=1):
        """Get this user's playcount for provided artist"""
        try:
            data = fm_request(
                "artist.getinfo",
                artist=artist,
                username=self.username,
                autocorrect=autocorrect,
            )
        except FMError as e:
            if e.code == FM_NOT_FOUND:
                return 0
            raise

        return int(data["artist"].get("stats", {}).get("userplaycount", 0))

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
            f"user.gettop{mode}s", user=self.username, period=period, limit=limit
        )
        entries = as_list(data[f"top{mode}s"].get(mode))
        return [
            {
                "name": entry["name"],
                "artist": None if mode == "artist" else entry["artist"]["name"],
                "playcount": int(entry["playcount"]),
            }
            for entry in entries
        ]

    def _top_tracks_page(self, page):
        return fm_request(
            "user.gettoptracks", user=self.username, limit=FM_PAGE_SIZE, page=page
        )["toptracks"]

    def get_top_tracks_artist(self, artist, limit=10):
        """Get this user's top tracks from the provided artist as (track, playcount) pairs"""
        # Knowing the total lets us stop scanning once every play is accounted for,
        # rather than reading the whole library when the artist has under `limit` tracks
        remaining = self.get_plays_artist(artist, autocorrect=0)
        if not remaining:
            return []

        artist = artist.lower()
        result = []

        def collect(data):
            """Add this page's matches, returning whether the scan is finished"""
            nonlocal remaining
            for track in as_list(data.get("track")):
                if track["artist"]["name"].lower() == artist:
                    result.append((track["name"], int(track["playcount"])))
                    remaining -= result[-1][1]
                    # Tracks come back sorted by playcount, so the first `limit` are the top ones
                    if len(result) >= limit or remaining <= 0:
                        return True
            return False

        first = self._top_tracks_page(1)
        if collect(first):
            return result

        total_pages = int(first.get("@attr", {}).get("totalPages", 1))
        with ThreadPoolExecutor(FM_PAGE_BATCH) as pool:
            for start in range(2, total_pages + 1, FM_PAGE_BATCH):
                pages = range(start, min(start + FM_PAGE_BATCH, total_pages + 1))
                if any(collect(data) for data in pool.map(self._top_tracks_page, pages)):
                    break

        return result
