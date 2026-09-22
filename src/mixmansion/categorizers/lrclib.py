"""Lyrics from LRCLIB (https://lrclib.net), free and without an API key."""

import logging

import requests

from mixmansion.core.models import Song

log = logging.getLogger(__name__)

BASE = "https://lrclib.net/api"


def lyrics(session: requests.Session, song: Song) -> str | None:
    """Plain lyrics, or None for instrumentals and songs LRCLIB doesn't know.

    `/get` matches the duration within ±2 s; if that misses, `/search` and take the
    result with the closest duration among those that have lyrics or are instrumental.
    """
    seconds = round(song.duration_ms / 1000)
    query = {"artist_name": song.artists[0] if song.artists else "", "track_name": song.title}
    try:
        r = session.get(
            f"{BASE}/get",
            params=query
            | {"duration": seconds}
            | ({"album_name": song.album} if song.album else {}),
            timeout=20,
        )
        match = r.json() if r.status_code == 200 else None
        if not match:
            r = session.get(f"{BASE}/search", params=query, timeout=20)
            results = r.json() if r.status_code == 200 else []
            useful = [x for x in results if x.get("plainLyrics") or x.get("instrumental")]
            match = min(useful, key=lambda x: abs((x.get("duration") or 0) - seconds), default=None)
    except (requests.RequestException, ValueError) as e:
        log.debug("LRCLIB lookup failed for %s – %s: %s", query["artist_name"], song.title, e)
        return None
    if not match or match.get("instrumental"):
        return None
    return (match.get("plainLyrics") or "").strip() or None
