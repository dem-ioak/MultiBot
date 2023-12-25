import os
import requests
from util.helper_functions import json_extract

LAST_FM_KEY = os.getenv("LAST_FM_KEY")
FM_BASE = "https://ws.audioscrobbler.com/2.0/?method="
FM_PARAMS = "&user={}&api_key={}&format=json"
FM_TOP_PARAMS = "&user={}&api_key={}&format=json&period={}"

class FMUser:
    def __init__(self, username):
        self.username = username

    def is_valid(self):
        """Determine whether the provided LastFM username is an active account"""
        try:
            method = "user.getinfo"
            URL = FM_BASE + method + FM_PARAMS.format(self.username, LAST_FM_KEY)
            source = requests.get(URL).json()
            data = source["user"]
            return True
        except KeyError:
            return False
    
            
    def get_plays_track(self, track):
        """Get this user's playcount for provided track"""
        pass

    def get_plays_artist(self, artist):
        """Get this user's playcount for provided artist"""
        pass
        
    def get_plays_album(self, album):
        """Get this user's playcount for provided album"""
        pass

    def get_np(self):
        """Get the track this user is currently playing"""
        method = "user.getrecenttracks"
        URL = FM_BASE + method + FM_PARAMS.format(self.username, LAST_FM_KEY)
        source = requests.get(URL).json()
        data = source["recenttracks"]["track"][0]

        name = data["name"]
        artist = data["artist"]["#text"]
        img = data["image"][-1]["#text"]
        album = data['album']['#text']
        return {
            "song_name" : name,
            "artist" : artist,
            "image" : img,
            "album" : album
        }

    def _get_top_util(self, mode, time = None):
        method = f"user.gettop{mode}s"
        URL = FM_BASE + method + FM_TOP_PARAMS.format(self.username, LAST_FM_KEY, time)
        source = requests.get(URL).json()
        info = source[f"top{mode}s"][mode]
        count = 1
        dct = {}
        for entry in info[:10]:
            a = entry["name"]
            playcount = entry["playcount"]
            key = str(count)
            dct[key] = {
                mode : a,
                "playcount" : playcount
            }
            count += 1

        return dct
    
    def _construct_string(self, data, is_artists = False):
        result = ""
        n = len(data.keys())
        for i in range(n):
            key = str(i + 1)
            if is_artists:
                a, b = data[key].values()
                result += f"`{key}` **{a}** ({b} plays)\n"
            else:
                a, b, c = data[key].values()
                result += f"`{key}` **{a}** by **{b}** ({c} plays)\n"
                
        return result
        

    def get_top_artists(self, time = None):
        """Get this user's top artists over the provided timeframe"""
        data = self._get_top_util("artist", time)
        return self._construct_string(data, True)

    def get_top_albums(self, time = None):
        """Get this user's top albums over the provided timeframe"""
        data = self._get_top_util("album", time)
        return self._construct_string(data)

    def get_top_tracks(self, time = None):
        """Get this user's top trackks over the provided timeframe"""
        data = self._get_top_util("track", time)
        return self._construct_string(data)

    def get_top_tracks_artist(self, artist):
        """Get this user's top tracks from the provided artist"""
        method = "user.gettoptracks"
        URL = FM_BASE + method + FM_PARAMS.format(self.username, LAST_FM_KEY) + "&limit=200&page="
        artist = artist.lower()
        result = []
        curr_page = 0
        capacity = False
        while not capacity:
            resp = requests.get(URL + str(curr_page))
            if resp.status_code != 200:
                break

            data = resp.json()
            for j in json_extract(data, "name", artist):
                result.append(j)
                if len(result) == 10:
                    capacity = True
                    break

        return result

    def get_top_tracks_album(self, album):
        """Get this user's top tracks from the provided album"""
        pass