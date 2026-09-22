import json
import logging
from pathlib import Path

import pytest
import spotipy

from mixmansion.retrievers.playlist import PlaylistRetriever
from mixmansion.retrievers.spotify import playlist_tracks, user_playlists

TRACK = json.loads((Path(__file__).parent / "fixtures/spotify/track.json").read_text())


def tid(n: int) -> str:
    return f"{n:022d}"


def track(n: int, **overrides) -> dict:
    return {**TRACK, "id": tid(n), "name": f"Song {n}", **overrides}


class FakeSpotify:
    """Minimal fake of the spotipy client surface the playlist retriever uses."""

    def __init__(self, me_id: str = "me"):
        self.me_id = me_id
        self.playlists_page: dict = {"items": [], "next": None}
        self.names: dict[str, str] = {}
        self.item_pages: dict[str, list[dict]] = {}
        self.unreadable: set[str] = set()

    def current_user(self) -> dict:
        return {"id": self.me_id}

    def current_user_playlists(self, limit: int = 50) -> dict:
        return self.playlists_page

    def playlist(self, playlist_id: str, fields: str | None = None) -> dict:
        if playlist_id in self.unreadable:
            raise spotipy.SpotifyException(403, -1, "forbidden")
        return {"name": self.names[playlist_id]}

    def playlist_items(self, playlist_id: str, additional_types=None) -> dict:
        if playlist_id in self.unreadable:
            raise spotipy.SpotifyException(403, -1, "forbidden")
        return self.item_pages[playlist_id][0]

    def next(self, page: dict) -> dict | None:
        pages = self.item_pages[page["_pl"]]
        idx = page["_idx"] + 1
        return pages[idx] if idx < len(pages) else None


def paged_items(playlist_id: str, entries: list[dict], page_size: int) -> list[dict]:
    pages = [entries[i : i + page_size] for i in range(0, len(entries), page_size)] or [[]]
    return [
        {"items": chunk, "next": i + 1 < len(pages) or None, "_pl": playlist_id, "_idx": i}
        for i, chunk in enumerate(pages)
    ]


class FakeSpotifyService:
    """Stand-in for shared.spotify.SpotifyService: just a `.client` attribute."""

    def __init__(self, client: FakeSpotify):
        self.client = client


def test_user_playlists_only_lists_owned_and_collaborative(caplog):
    sp = FakeSpotify(me_id="me")
    sp.playlists_page = {
        "items": [
            {
                "id": "owned",
                "name": "Chill",
                "owner": {"id": "me", "display_name": "Me"},
                "items": {"total": 12},
            },
            {
                "id": "collab",
                "name": "Party",
                "owner": {"id": "friend", "display_name": "Friend"},
                "collaborative": True,
                "tracks": {"total": 30},
            },
            {
                "id": "followed",
                "name": "Editorial",
                "owner": {"id": "spotify"},
                "collaborative": False,
                "items": {"total": 50},
            },
        ],
        "next": None,
    }
    with caplog.at_level(logging.INFO):
        choices = user_playlists(sp)
    values = {c.value: c.label for c in choices}
    assert values == {
        "owned": "Chill · 12 tracks · Me",
        "collab": "Party · 30 tracks · Friend",
    }
    assert "hid 1" in caplog.text


def test_playlist_tracks_paginates_and_handles_item_and_track_keys(caplog):
    sp = FakeSpotify()
    sp.names["pl"] = "Big One"
    entries = [{"item": track(i)} for i in range(100)]
    entries += [{"track": track(i)} for i in range(100, 200)]
    entries += [{"item": track(i)} for i in range(200, 205)]
    sp.item_pages["pl"] = paged_items("pl", entries, page_size=100)

    with caplog.at_level(logging.WARNING):
        name, songs, skipped = playlist_tracks(sp, "pl")

    assert name == "Big One"
    assert skipped == 0
    assert len(songs) == 205
    assert {s.id for s in songs} == {tid(i) for i in range(205)}
    assert all(s.sources == ["playlist:Big One"] for s in songs)


def test_playlist_tracks_skips_local_episodes_and_nulls_and_logs(caplog):
    sp = FakeSpotify()
    sp.names["pl"] = "Chill"
    entries = [
        {"item": track(1)},
        {"item": {**track(2), "is_local": True}},
        {"item": {**track(3), "type": "episode"}},
        {"item": None},
    ]
    sp.item_pages["pl"] = paged_items("pl", entries, page_size=10)

    with caplog.at_level(logging.WARNING):
        name, songs, skipped = playlist_tracks(sp, "pl")

    assert skipped == 3
    assert len(songs) == 1
    assert '"Chill": skipped 3 items' in caplog.text


def _service_with(sp: FakeSpotify) -> FakeSpotifyService:
    return FakeSpotifyService(sp)


def test_retriever_choices_delegates_to_user_playlists():
    sp = FakeSpotify(me_id="me")
    sp.playlists_page = {
        "items": [
            {"id": "owned", "name": "Mine", "owner": {"id": "me"}, "items": {"total": 1}},
        ],
        "next": None,
    }
    retriever = PlaylistRetriever(_service_with(sp))
    choices = retriever.choices("playlist_ids", {})
    assert [c.value for c in choices] == ["owned"]
    assert retriever.choices("other_field", {}) == []


PL1, PL2, PL_OK, PL_BAD = (f"pl{n:020d}" for n in range(1, 5))


def test_retrieve_concats_songs_across_playlists_and_sets_sources():
    sp = FakeSpotify()
    sp.names[PL1] = "Chill"
    sp.names[PL2] = "Focus"
    sp.item_pages[PL1] = paged_items(PL1, [{"item": track(1)}], page_size=10)
    sp.item_pages[PL2] = paged_items(PL2, [{"item": track(2)}], page_size=10)

    retriever = PlaylistRetriever(_service_with(sp))
    songs = retriever.retrieve(PlaylistRetriever.Params(playlist_ids=[PL1, PL2]))

    assert {s.id for s in songs} == {tid(1), tid(2)}
    sources = {s.id: s.sources for s in songs}
    assert sources[tid(1)] == ["playlist:Chill"]
    assert sources[tid(2)] == ["playlist:Focus"]


def test_retrieve_skips_unreadable_playlist_and_continues(caplog):
    sp = FakeSpotify()
    sp.names[PL_OK] = "Chill"
    sp.item_pages[PL_OK] = paged_items(PL_OK, [{"item": track(1)}], page_size=10)
    sp.unreadable.add(PL_BAD)

    retriever = PlaylistRetriever(_service_with(sp))
    with caplog.at_level(logging.WARNING):
        songs = retriever.retrieve(PlaylistRetriever.Params(playlist_ids=[PL_BAD, PL_OK]))

    assert [s.id for s in songs] == [tid(1)]
    assert PL_BAD in caplog.text


def test_retrieve_parses_uri_and_url_ids():
    sp = FakeSpotify()
    sp.names[tid(1)] = "Chill"
    sp.item_pages[tid(1)] = paged_items(tid(1), [{"item": track(1)}], page_size=10)

    retriever = PlaylistRetriever(_service_with(sp))
    songs = retriever.retrieve(
        PlaylistRetriever.Params(
            playlist_ids=[
                f"spotify:playlist:{tid(1)}",
                f"https://open.spotify.com/playlist/{tid(1)}",
            ]
        )
    )
    assert len(songs) == 2


def test_retrieve_rejects_invalid_id():
    sp = FakeSpotify()
    retriever = PlaylistRetriever(_service_with(sp))
    with pytest.raises(ValueError):
        retriever.retrieve(PlaylistRetriever.Params(playlist_ids=["not-an-id"]))
