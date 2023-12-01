class FMUser:
    def __init__(self, username):
        self.username = username
        if not self.is_valid():
            raise

    def is_valid(self):
        """Determine whether the provided LastFM username is an active account"""
            
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
        pass

    def get_top_artists(self, time = None):
        """Get this user's top artists over the provided timeframe"""
        pass

    def get_top_albums(self, time = None):
        """Get this user's top albums over the provided timeframe"""
        pass

    def get_top_tracks(self, time = None):
        """Get this user's top trackks over the provided timeframe"""
        pass

    def get_top_tracks_artist(self, artist):
        """Get this user's top tracks from the provided artist"""
        pass

    def get_top_tracks_album(self, album):
        """Get this user's top tracks from the provided album"""
        pass