"""The Spotify calls the categorizers need, built on shared/spotify.py."""

import logging
from concurrent.futures import ThreadPoolExecutor

import spotipy

log = logging.getLogger(__name__)


def artist_genres(sp: spotipy.Spotify, artist_ids: list[str]) -> dict[str, list[str]]:
    """Genres per artist. One request per artist: dev-mode apps can't batch (GET /artists)."""

    def one(artist_id: str) -> list[str]:
        try:
            return sp.artist(artist_id).get("genres") or []
        except spotipy.SpotifyException as e:
            log.debug("no genres for artist %s: %s", artist_id, e)
            return []

    ids = list(dict.fromkeys(artist_ids))
    with ThreadPoolExecutor(max_workers=4) as pool:
        return dict(zip(ids, pool.map(one, ids), strict=True))
