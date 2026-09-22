import logging

import spotipy
from pydantic import Field
from pydantic_settings import SettingsConfigDict

from mixmansion.core.models import Choice, Song
from mixmansion.retrievers.port import SongRetriever
from mixmansion.retrievers.spotify import playlist_tracks, user_playlists
from mixmansion.shared.config import AdapterParams
from mixmansion.shared.spotify import SpotifyService, parse_id

log = logging.getLogger(__name__)


class PlaylistRetriever(SongRetriever):
    """Add every song of one or more of your playlists."""

    name = "playlist"

    class Params(AdapterParams):
        model_config = SettingsConfigDict(env_prefix="MIXMANSION_RETRIEVER_PLAYLIST_")
        playlist_ids: list[str] = Field(
            description="Playlist IDs, spotify:playlist: URIs or open.spotify.com URLs"
        )

    def __init__(self, spotify: SpotifyService):
        self.spotify = spotify

    def choices(self, field: str, params: dict) -> list[Choice]:
        if field != "playlist_ids":
            return []
        return user_playlists(self.spotify.client)

    def retrieve(self, params: Params) -> list[Song]:
        sp = self.spotify.client
        songs: list[Song] = []
        for raw in params.playlist_ids:
            playlist_id = parse_id(raw, "playlist")
            try:
                _, tracks, _ = playlist_tracks(sp, playlist_id)
            except spotipy.SpotifyException as e:
                log.warning("playlist %s can't be read, skipping: %s", playlist_id, e)
                continue
            songs.extend(tracks)
        return songs
