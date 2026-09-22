import json
import logging
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mixmansion import bootstrap
from mixmansion.interfaces import cli as cli_mod
from mixmansion.retrievers.search import SearchRetriever
from mixmansion.retrievers.spotify import search_tracks, tracks

TRACK = json.loads((Path(__file__).parent / "fixtures/spotify/track.json").read_text())


def tid(n: int) -> str:
    return f"{n:022d}"


def track(n: int, **overrides) -> dict:
    return {**TRACK, "id": tid(n), "name": f"Song {n}", **overrides}


class FakeSpotify:
    """Minimal fake of the spotipy client surface the search retriever uses."""

    def __init__(self):
        self.results: list[dict] = []
        self.search_calls: list[tuple[int, int]] = []  # (limit, offset)
        self.tracks_by_id: dict[str, dict] = {}
        self.tracks_called = False  # detect accidental use of the removed batch endpoint

    def search(self, q: str, type: str, limit: int, offset: int) -> dict:
        self.search_calls.append((limit, offset))
        page = self.results[offset : offset + limit]
        next_ = offset + limit < len(self.results)
        return {"tracks": {"items": page, "next": next_ or None}}

    def track(self, track_id: str) -> dict | None:
        return self.tracks_by_id.get(track_id)

    def tracks(self, ids):  # pragma: no cover - must never be called (removed endpoint)
        self.tracks_called = True
        raise AssertionError("batch tracks() endpoint must not be used")


class FakeSpotifyService:
    def __init__(self, client: FakeSpotify):
        self.client = client


def _service_with(sp: FakeSpotify) -> FakeSpotifyService:
    return FakeSpotifyService(sp)


def test_search_tracks_pages_with_offset_capped_at_ten():
    sp = FakeSpotify()
    sp.results = [track(i) for i in range(25)]

    choices = search_tracks(sp, "rainy jazz", limit=25)

    assert len(choices) == 25
    assert sp.search_calls == [(10, 0), (10, 10), (5, 20)]
    assert all(limit <= 10 for limit, _ in sp.search_calls)


def test_search_tracks_stops_early_when_results_run_out():
    sp = FakeSpotify()
    sp.results = [track(i) for i in range(3)]

    choices = search_tracks(sp, "obscure", limit=20)

    assert len(choices) == 3
    assert sp.search_calls == [(10, 0)]


def test_search_tracks_label_format_with_and_without_year():
    sp = FakeSpotify()
    sp.results = [
        track(1, artists=[{"id": "a1", "name": "Daft Punk"}]),
        track(2, album={"name": "Unknown Album", "release_date": ""}),
    ]

    choices = search_tracks(sp, "q", limit=10)

    labels = {c.value: c.label for c in choices}
    assert labels[tid(1)] == "Daft Punk – Song 1 · Discovery (2001)"
    assert labels[tid(2)] == "Daft Punk – Song 2 · Unknown Album"


def test_search_tracks_empty_result():
    sp = FakeSpotify()
    sp.results = []

    assert search_tracks(sp, "nothing here", limit=20) == []


def test_choices_accepts_string_limit():
    sp = FakeSpotify()
    sp.results = [track(i) for i in range(8)]
    retriever = SearchRetriever(_service_with(sp))

    choices = retriever.choices("track_ids", {"query": "jazz", "limit": "5"})

    assert len(choices) == 5


def test_choices_falls_back_to_env_limit(monkeypatch):
    monkeypatch.setenv("MIXMANSION_RETRIEVER_SEARCH_LIMIT", "3")
    sp = FakeSpotify()
    sp.results = [track(i) for i in range(8)]
    retriever = SearchRetriever(_service_with(sp))

    choices = retriever.choices("track_ids", {"query": "jazz"})

    assert len(choices) == 3


def test_choices_empty_search_logs_warning(caplog):
    sp = FakeSpotify()
    sp.results = []
    retriever = SearchRetriever(_service_with(sp))

    with caplog.at_level(logging.WARNING):
        choices = retriever.choices("track_ids", {"query": "nothing here"})

    assert choices == []
    assert 'no songs found for "nothing here"' in caplog.text


def test_choices_other_field_returns_empty():
    sp = FakeSpotify()
    retriever = SearchRetriever(_service_with(sp))
    assert retriever.choices("query", {"query": "jazz"}) == []


def test_retrieve_fetches_each_track_individually_and_sets_source():
    sp = FakeSpotify()
    sp.tracks_by_id[tid(1)] = track(1)
    sp.tracks_by_id[tid(2)] = track(2)
    retriever = SearchRetriever(_service_with(sp))

    songs = retriever.retrieve(
        SearchRetriever.Params(query="rainy day jazz", track_ids=[tid(1), tid(2)])
    )

    assert {s.id for s in songs} == {tid(1), tid(2)}
    assert all(s.sources == ["search:rainy day jazz"] for s in songs)
    assert sp.tracks_called is False


def test_retrieve_parses_uri_and_url_ids():
    sp = FakeSpotify()
    sp.tracks_by_id[tid(1)] = track(1)
    retriever = SearchRetriever(_service_with(sp))

    songs = retriever.retrieve(
        SearchRetriever.Params(
            query="q",
            track_ids=[f"spotify:track:{tid(1)}", f"https://open.spotify.com/track/{tid(1)}"],
        )
    )
    assert len(songs) == 2


def test_retrieve_requires_track_ids():
    sp = FakeSpotify()
    retriever = SearchRetriever(_service_with(sp))
    with pytest.raises(ValueError, match="pick songs first"):
        retriever.retrieve(SearchRetriever.Params(query="q"))


def test_tracks_helper_never_calls_batch_endpoint():
    sp = FakeSpotify()
    sp.tracks_by_id[tid(1)] = track(1)
    songs = tracks(sp, [tid(1)], "search:q")
    assert [s.id for s in songs] == [tid(1)]
    assert sp.tracks_called is False


def test_cli_pool_add_search_help_shows_flags(monkeypatch):
    monkeypatch.setattr(bootstrap, "RETRIEVERS", {"search": SearchRetriever})
    fresh = cli_mod.build_cli()
    runner = CliRunner()
    result = runner.invoke(fresh, ["pool", "add", "search", "--help"])
    assert result.exit_code == 0, result.output
    assert "--query" in result.output
    assert "--limit" in result.output
    assert "--track-ids" in result.output
