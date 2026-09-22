"""Last.fm tags. Only the genre categorizer uses Last.fm so far, so its settings live here;
move them to shared/ once another port folder needs Last.fm."""

import logging

import requests
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)

API = "https://ws.audioscrobbler.com/2.0/"


class LastfmSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LASTFM_", env_file=".env", extra="ignore")

    api_key: SecretStr  # https://www.last.fm/api/account/create


class Lastfm:
    def __init__(self, session: requests.Session, settings: LastfmSettings):
        self.session = session
        self.api_key = settings.api_key.get_secret_value()

    def _tags(self, method: str, **params: str) -> dict[str, int]:
        """{tag: count 0–100}; empty when Last.fm doesn't know the track or artist."""
        try:
            r = self.session.get(
                API,
                params={"method": method, "api_key": self.api_key, "format": "json", **params},
                timeout=20,
            )
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            log.debug("Last.fm %s failed: %s", method, e)
            return {}
        tags = (data.get("toptags") or {}).get("tag") or []
        if isinstance(tags, dict):  # a single tag comes back as an object, not a list
            tags = [tags]
        return {t["name"]: int(t.get("count") or 0) for t in tags if t.get("name")}

    def track_tags(self, artist: str, title: str) -> dict[str, int]:
        return self._tags("track.getTopTags", artist=artist, track=title, autocorrect="1")

    def artist_tags(self, artist: str) -> dict[str, int]:
        return self._tags("artist.getTopTags", artist=artist, autocorrect="1")
