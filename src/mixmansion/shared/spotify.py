"""Spotify settings shared by every Spotify-based connector.

The env values are defaults; `bootstrap.build_app` builds `SpotifySettings(**overrides.spotify)`,
so values from the interaction surface win.
"""

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class SpotifySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SPOTIFY_", env_file=".env", extra="ignore")

    client_id: str
    client_secret: SecretStr
    redirect_uri: str = "http://127.0.0.1:8888/callback"
    token_cache: Path | None = None  # default: <workspace>/spotify_token_<client_id>.json
