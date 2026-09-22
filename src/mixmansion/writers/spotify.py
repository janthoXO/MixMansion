"""Spotify writer: creates playlists and replaces the tracks of ones it created."""

import re

from pydantic import Field
from pydantic_settings import SettingsConfigDict
from spotipy import SpotifyException

from mixmansion.shared.config import AdapterParams
from mixmansion.shared.spotify import SpotifyService
from mixmansion.writers.port import PlaylistNotFound, PlaylistWriter

CHUNK = 100


class SpotifyWriter(PlaylistWriter):
    """Creates and fills Spotify playlists. Never touches a playlist it didn't create."""

    name = "spotify"

    class Params(AdapterParams):
        model_config = SettingsConfigDict(env_prefix="MIXMANSION_WRITER_SPOTIFY_")
        public: bool = Field(
            default=False, description="Create public instead of private playlists"
        )
        name_prefix: str = Field(default="", description="Prefix for generated playlist names")

    def __init__(self, spotify: SpotifyService):
        self.spotify = spotify

    def create(self, name: str, description: str, params: Params) -> str:
        clean = re.sub(r"\s+", " ", description).strip()[:300]
        playlist = self.spotify.client.current_user_playlist_create(
            params.name_prefix + name, public=params.public, description=clean
        )
        return playlist["id"]

    def replace_tracks(self, playlist_id: str, track_ids: list[str], params: Params) -> None:
        uris = [f"spotify:track:{i}" for i in track_ids]
        chunks = [uris[i : i + CHUNK] for i in range(0, len(uris), CHUNK)] or [[]]
        try:
            self.spotify.client.playlist_replace_items(playlist_id, chunks[0])
            for chunk in chunks[1:]:
                self.spotify.client.playlist_add_items(playlist_id, chunk)
        except SpotifyException as e:
            if e.http_status == 404:
                raise PlaylistNotFound(playlist_id) from e
            raise
