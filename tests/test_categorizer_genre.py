from types import SimpleNamespace

import pytest
import spotipy
from fakes import tid
from requests_cache import CachedSession

from mixmansion.categorizers.genre import GenreCategorizer, normalize
from mixmansion.categorizers.lastfm import Lastfm, LastfmSettings
from mixmansion.core.models import Song
from mixmansion.shared.config import AppSettings, ConfigError

ROCK = {"a_rock": ["rock", "classic rock"], "b_rock": ["rock", "hard-rock"]}
TECHNO = {"c_techno": ["techno", "minimal techno"], "d_techno": ["techno"]}


def song(n: int, artist: str, title: str = "t") -> Song:
    return Song(id=tid(n), title=title, artists=[artist], artist_ids=[artist], duration_ms=1)


class FakeSpotify:
    def __init__(self, genres):
        self.genres = genres
        self.calls = []

    def artist(self, artist_id):
        self.calls.append(artist_id)
        if artist_id not in self.genres:
            raise spotipy.SpotifyException(404, -1, "not found")
        return {"id": artist_id, "genres": self.genres[artist_id]}


class FakeLastfmSession:
    """Answers track.getTopTags / artist.getTopTags from dicts, like the JSON API."""

    def __init__(self, track=None, artist=None):
        self.track, self.artist = track or {}, artist or {}
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append(params)
        if params["method"] == "track.getTopTags":
            tags = self.track.get((params["artist"], params["track"]), [])
        else:
            tags = self.artist.get(params["artist"], [])
        if len(tags) == 1:
            tag = {"name": tags[0][0], "count": tags[0][1]}  # Last.fm returns a lone object
        else:
            tag = [{"name": n, "count": c} for n, c in tags]
        body = {"toptags": {"tag": tag}} if tags else {"error": 6, "message": "not found"}
        return SimpleNamespace(status_code=200, json=lambda: body)


def categorizer(monkeypatch, spotify_genres, lastfm=None):
    monkeypatch.setenv("LASTFM_API_KEY", "key")
    cat = GenreCategorizer(SimpleNamespace(client=FakeSpotify(spotify_genres)), AppSettings())
    cat.http = lastfm or FakeLastfmSession()
    return cat


def test_rock_neighbours_rock_not_techno(monkeypatch):
    songs = [song(1, "a_rock"), song(2, "b_rock"), song(3, "c_techno"), song(4, "d_techno")]
    graph = categorizer(monkeypatch, ROCK | TECHNO).similarity(
        songs, GenreCategorizer.Params(lastfm_weight=0)
    )
    assert (tid(1), tid(2)) in graph.edges and (tid(3), tid(4)) in graph.edges
    assert (tid(1), tid(3)) not in graph.edges and (tid(2), tid(4)) not in graph.edges
    assert graph.labels[tid(2)] == ["rock", "hard rock"]  # normalized, weight order


def test_songs_without_data_are_uncovered(monkeypatch):
    songs = [song(1, "a_rock"), song(2, "b_rock"), song(3, "unknown")]
    graph = categorizer(monkeypatch, ROCK).similarity(songs, GenreCategorizer.Params())
    assert graph.covered == {tid(1), tid(2)}
    assert all(tid(3) not in pair for pair in graph.edges)


def test_lastfm_tags_with_min_count_stoplist_and_artist_fallback(monkeypatch):
    lastfm = FakeLastfmSession(
        track={("x", "t"): [("Shoegaze", 100), ("seen live", 90), ("x", 80), ("noise", 5)]},
        artist={"y": [("shoegaze", 60)]},
    )
    songs = [song(1, "x"), song(2, "y")]
    params = GenreCategorizer.Params(spotify_weight=0, lastfm_min_count=10)
    graph = categorizer(monkeypatch, {}, lastfm).similarity(songs, params)
    assert graph.labels == {tid(1): ["shoegaze"], tid(2): ["shoegaze"]}
    assert graph.edges == {(tid(1), tid(2)): pytest.approx(1.0)}
    methods = [c["method"] for c in lastfm.calls]
    assert methods.count("artist.getTopTags") == 1  # only for the track Last.fm didn't know
    assert all(c["autocorrect"] == "1" for c in lastfm.calls)


def test_weights_combine_both_sources(monkeypatch):
    lastfm = FakeLastfmSession(track={("a_rock", "t"): [("grunge", 100)]})
    graph = categorizer(monkeypatch, ROCK, lastfm).similarity(
        [song(1, "a_rock")], GenreCategorizer.Params(spotify_weight=1, lastfm_weight=3)
    )
    assert graph.labels[tid(1)][0] == "grunge"  # 3 × 1.0 beats the Spotify genres (1.0)


def test_artists_fetched_once_each(monkeypatch):
    cat = categorizer(monkeypatch, ROCK)
    songs = [song(1, "a_rock"), song(2, "a_rock"), song(3, "b_rock")]
    cat.similarity(songs, GenreCategorizer.Params(lastfm_weight=0))
    assert sorted(cat.spotify.client.calls) == ["a_rock", "b_rock"]


def test_missing_lastfm_key_names_the_variable(monkeypatch):
    cat = categorizer(monkeypatch, ROCK)
    monkeypatch.delenv("LASTFM_API_KEY")
    with pytest.raises(ConfigError, match="LASTFM_API_KEY"):
        cat.similarity([song(1, "a_rock")], GenreCategorizer.Params())


def test_normalize():
    assert normalize("Hip-Hop") == normalize("hip_hop") == normalize(" hip  hop ") == "hip hop"


def test_uses_the_shared_cached_session(tmp_path):
    cat = GenreCategorizer(SimpleNamespace(client=None), AppSettings(workspace=tmp_path))
    assert isinstance(cat.http, CachedSession)
    assert cat.http.settings.ignored_parameters  # api_key is never stored in the cache
    assert "api_key" in cat.http.settings.ignored_parameters


def test_lastfm_client_handles_a_single_tag_object():
    http = FakeLastfmSession(artist={"z": [("dream pop", 70)]})
    assert Lastfm(http, LastfmSettings(api_key="k")).artist_tags("z") == {"dream pop": 70}
