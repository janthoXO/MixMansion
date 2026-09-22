import logging

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from mixmansion.core.models import Choice, Song
from mixmansion.retrievers.port import SongRetriever
from mixmansion.retrievers.spotify import search_tracks, tracks
from mixmansion.shared.config import AdapterParams
from mixmansion.shared.spotify import SpotifyService, parse_id

log = logging.getLogger(__name__)


class SearchRetriever(SongRetriever):
    """Search Spotify and pick songs to add."""

    name = "search"

    class Params(AdapterParams):
        model_config = SettingsConfigDict(env_prefix="MIXMANSION_RETRIEVER_SEARCH_")
        query: str = Field(description="Free-text Spotify search, e.g. 'rainy day jazz'")
        limit: int = Field(20, ge=1, le=100, description="Max number of search results to show")
        track_ids: list[str] | None = Field(
            None,
            description="IDs, spotify:track: URIs or open.spotify.com URLs of the songs to add",
        )

    def __init__(self, spotify: SpotifyService):
        self.spotify = spotify

    def choices(self, field: str, params: dict) -> list[Choice]:
        if field != "track_ids":
            return []
        p = self.Params(**{k: v for k, v in params.items() if k != "track_ids"})
        results = search_tracks(self.spotify.client, p.query, p.limit)
        if not results:
            log.warning('no songs found for "%s"', p.query)
        return results

    def retrieve(self, params: Params) -> list[Song]:
        if not params.track_ids:
            raise ValueError("pick songs first: pass --track-ids or run interactively")
        ids = [parse_id(v, "track") for v in params.track_ids]
        return tracks(self.spotify.client, ids, f"search:{params.query}")
