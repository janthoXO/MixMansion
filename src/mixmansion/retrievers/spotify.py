"""Spotify-backed retriever helpers, built on `shared.spotify`. Never construct a client here."""

import logging

import spotipy

from mixmansion.core.models import Choice, Song
from mixmansion.shared.spotify import paginate, to_song

log = logging.getLogger(__name__)


def user_playlists(sp: spotipy.Spotify) -> list[Choice]:
    """Playlists the user owns or collaborates on (the only ones whose contents can be read)."""
    me = sp.current_user()["id"]
    readable: list[Choice] = []
    hidden = 0
    for p in paginate(sp, sp.current_user_playlists(limit=50)):
        if p["owner"]["id"] != me and not p.get("collaborative"):
            hidden += 1
            continue
        count = (p.get("items") or p.get("tracks") or {}).get("total")
        owner = p["owner"].get("display_name") or p["owner"]["id"]
        readable.append(Choice(value=p["id"], label=f"{p['name']} · {count} tracks · {owner}"))
    if hidden:
        log.info(
            "hid %d followed playlist(s): contents aren't readable for playlists you don't "
            "own or collaborate on",
            hidden,
        )
    return readable


def playlist_tracks(sp: spotipy.Spotify, playlist_id: str) -> tuple[str, list[Song], int]:
    """Every song of one playlist. Returns (name, songs, number of items skipped)."""
    name = sp.playlist(playlist_id, fields="name")["name"]
    songs: list[Song] = []
    skipped = 0
    for entry in paginate(sp, sp.playlist_items(playlist_id, additional_types=("track",))):
        song = to_song(entry.get("item") or entry.get("track"), f"playlist:{name}")
        if song is None:
            skipped += 1
            continue
        songs.append(song)
    if skipped:
        log.warning(
            '"%s": skipped %d items (local files, episodes or unavailable tracks)', name, skipped
        )
    return name, songs, skipped


def search_tracks(sp: spotipy.Spotify, query: str, limit: int) -> list[Choice]:
    """Search tracks for `query`, up to `limit` hits. Spotify caps `limit` per request (dev-mode
    apps currently get at most 10), so this pages with `offset` until `limit` is reached."""
    choices: list[Choice] = []
    offset = 0
    while offset < limit:
        page_size = min(10, limit - offset)
        page = sp.search(q=query, type="track", limit=page_size, offset=offset)["tracks"]
        items = page.get("items") or []
        for track in items:
            song = to_song(track, "search")
            if song is None:
                continue
            artists = ", ".join(song.artists)
            year = f" ({song.release_year})" if song.release_year else ""
            choices.append(
                Choice(value=song.id, label=f"{artists} – {song.title} · {song.album}{year}")
            )
        offset += page_size
        if len(items) < page_size or not page.get("next"):
            break
    return choices[:limit]


def tracks(sp: spotipy.Spotify, ids: list[str], source: str) -> list[Song]:
    """Fetch tracks one at a time (the batch `GET /tracks?ids=` endpoint was removed)."""
    songs: list[Song] = []
    for track_id in ids:
        song = to_song(sp.track(track_id), source)
        if song is not None:
            songs.append(song)
    return songs
