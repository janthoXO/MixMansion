"""Spotify client, OAuth and helpers shared by every Spotify-based connector.

The env values are defaults; `bootstrap.build_app` builds `SpotifySettings(**overrides.spotify)`,
so values from the interaction surface win. Nothing here reads the environment at import time.
"""

import logging
import re
from collections.abc import Iterator
from functools import cached_property
from pathlib import Path
from typing import Literal

import spotipy
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from spotipy.cache_handler import CacheFileHandler

from mixmansion.core.models import Song

log = logging.getLogger(__name__)

SCOPES = (
    "playlist-read-private playlist-read-collaborative "
    "playlist-modify-private playlist-modify-public"
)


class SpotifySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SPOTIFY_", env_file=".env", extra="ignore")

    client_id: str
    client_secret: SecretStr
    redirect_uri: str = "http://127.0.0.1:8888/callback"
    token_cache: Path | None = None  # default: <workspace>/spotify_token_<client_id>.json


class SpotifyService:
    """One authenticated client per app, handed to adapters through their constructor."""

    def __init__(self, settings: SpotifySettings, workspace: Path):
        self.settings = settings
        self.token_cache = (
            settings.token_cache or workspace / f"spotify_token_{settings.client_id}.json"
        )

    @cached_property
    def client(self) -> spotipy.Spotify:
        """Created on first use. The first login opens the browser; tokens refresh on their own."""
        self.token_cache.parent.mkdir(parents=True, exist_ok=True)
        auth = spotipy.SpotifyOAuth(
            client_id=self.settings.client_id,
            client_secret=self.settings.client_secret.get_secret_value(),
            redirect_uri=self.settings.redirect_uri,
            scope=SCOPES,
            cache_handler=CacheFileHandler(cache_path=str(self.token_cache)),
        )
        # spotipy retries 429s (honouring Retry-After) and 5xx with backoff
        return spotipy.Spotify(auth_manager=auth, requests_timeout=30, retries=5, status_retries=5)


def paginate(sp: spotipy.Spotify, page: dict | None) -> Iterator[dict]:
    """Yield every item of a paging object, following `next` (page sizes vary per endpoint)."""
    while page:
        yield from page.get("items") or []
        page = sp.next(page) if page.get("next") else None


def to_song(track: dict | None, source: str) -> Song | None:
    """Map a Spotify track object to a Song. None for local files, episodes and missing tracks."""
    if not track or track.get("is_local") or track.get("type", "track") != "track":
        return None
    if not track.get("id"):
        return None
    album = track.get("album") or {}
    year = (album.get("release_date") or "")[:4]
    artists = track.get("artists") or []
    return Song(
        id=track["id"],
        isrc=(track.get("external_ids") or {}).get("isrc"),
        title=track.get("name") or "",
        artists=[a["name"] for a in artists if a.get("name")],
        artist_ids=[a["id"] for a in artists if a.get("id")],
        album=album.get("name"),
        release_year=int(year) if year.isdigit() else None,
        duration_ms=track.get("duration_ms") or 0,
        sources=[source],
    )


def parse_id(value: str, kind: Literal["track", "playlist"]) -> str:
    """Bare id, `spotify:<kind>:<id>` or `https://open.spotify.com/[intl-xx/]<kind>/<id>?si=…`."""
    m = re.fullmatch(
        rf"(?:spotify:{kind}:|https?://open\.spotify\.com/(?:intl-[\w-]+/)?{kind}/)?"
        r"([0-9A-Za-z]{22})(?:\?.*)?",
        value.strip(),
    )
    if not m:
        raise ValueError(f"not a Spotify {kind} id, URI or URL: {value!r}")
    return m.group(1)
