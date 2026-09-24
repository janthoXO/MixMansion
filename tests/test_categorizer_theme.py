import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import requests
from fakes import cos, tid

from mixmansion.categorizers import lrclib
from mixmansion.categorizers.theme import SongTheme, ThemeCategorizer, ThemeResponse
from mixmansion.core.models import Song
from mixmansion.shared.config import AppSettings
from mixmansion.shared.llm import LLMError

FIXTURES = Path(__file__).parent / "fixtures/lrclib"


def song(n: int, title: str = "One More Time", seconds: int = 320) -> Song:
    return Song(
        id=tid(n),
        title=title,
        artists=["Daft Punk"],
        artist_ids=["a"],
        album="Discovery",
        duration_ms=seconds * 1000,
    )


class FakeSession:
    def __init__(self, get=None, search=None, error=None):
        self.responses = {"get": get, "search": search}
        self.error = error
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append((url.rsplit("/", 1)[1], params))
        if self.error:
            raise self.error
        body = self.responses[url.rsplit("/", 1)[1]]
        return SimpleNamespace(status_code=200 if body is not None else 404, json=lambda: body)


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


# ── LRCLIB ──────────────────────────────────────────────────────────────────


def test_lyrics_exact_match_sends_duration_in_seconds():
    http = FakeSession(get=fixture("get.json"))
    assert lrclib.lyrics(http, song(1)).startswith("One more time")
    kind, params = http.calls[0]
    assert kind == "get" and params["duration"] == 320 and params["album_name"] == "Discovery"


def test_lyrics_search_fallback_picks_closest_duration_with_lyrics():
    http = FakeSession(get=None, search=fixture("search.json"))
    assert lrclib.lyrics(http, song(1, "Aerodynamic", 207)) == "closest with lyrics"
    assert [c[0] for c in http.calls] == ["get", "search"]


def test_instrumental_and_misses_have_no_lyrics():
    assert (
        lrclib.lyrics(FakeSession(get={**fixture("get.json"), "instrumental": True}), song(1))
        is None
    )
    assert lrclib.lyrics(FakeSession(get=None, search=[]), song(1)) is None
    assert lrclib.lyrics(FakeSession(error=requests.ConnectionError("offline")), song(1)) is None


# ── theme vectors ────────────────────────────────────────────────────────────

SAD = "aching heartbreak and the loss of someone loved"
HAPPY = "joyful celebration of love and life"
VECTORS = {
    SAD: [1, 0, 0],
    HAPPY: [0, 1, 0],
}


class FakeLLM:
    def __init__(self, theme_by_title, fail_batches=0, drop=()):
        self.theme_by_title = theme_by_title
        self.fail_batches = fail_batches
        self.drop = set(drop)
        self.requests = []

    def complete_json_many(self, requests_, schema):
        assert schema is ThemeResponse
        out = []
        for system, user in requests_:
            self.requests.append((system, json.loads(user)))
            if self.fail_batches:
                self.fail_batches -= 1
                out.append(LLMError("bad json"))
                continue
            songs = json.loads(user)["songs"]
            dropped = len(songs) > 1 and self.drop
            out.append(
                ThemeResponse(
                    songs=[
                        SongTheme(id=s["id"], theme=self.theme_by_title[s["title"]])
                        for s in songs
                        if not (dropped and s["id"] in self.drop)
                    ]
                )
            )
        return out

    def embed(self, texts):
        return np.array([VECTORS[t.strip()] for t in texts], dtype=float)


def categorizer(llm, monkeypatch, lyrics=None):
    monkeypatch.setattr(lrclib, "lyrics", lambda http, s: (lyrics or {}).get(s.id))
    return ThemeCategorizer(llm, AppSettings())


def test_similar_themes_are_neighbours(monkeypatch):
    songs = [song(i, "sad" if i < 3 else "happy") for i in range(6)]
    lyrics = {s.id: "some lyrics" for s in songs}
    llm = FakeLLM({"sad": SAD, "happy": HAPPY})
    dim = categorizer(llm, monkeypatch, lyrics).vectors(songs, ThemeCategorizer.Params())
    assert cos(dim, tid(0), tid(1)) > 0.9 and cos(dim, tid(3), tid(4)) > 0.9
    assert cos(dim, tid(0), tid(3)) < 0.3
    assert dim.ids == [tid(i) for i in range(6)]
    assert dim.labels[tid(0)] == [SAD]


def test_songs_without_lyrics_are_uncovered_and_never_sent(monkeypatch):
    songs = [song(i, "sad") for i in range(3)]
    lyrics = {tid(0): "some lyrics", tid(1): "more lyrics"}  # tid(2) has none
    llm = FakeLLM({"sad": SAD})
    dim = categorizer(llm, monkeypatch, lyrics).vectors(songs, ThemeCategorizer.Params())
    assert dim.ids == [tid(0), tid(1)]
    sent_ids = {s["id"] for _, user in llm.requests for s in user["songs"]}
    assert tid(2) not in sent_ids


def test_batches_and_lyrics_in_the_request(monkeypatch):
    songs = [song(i, "sad") for i in range(5)]
    lyrics = {s.id: "x" * 50 for s in songs}
    llm = FakeLLM({"sad": SAD})
    params = ThemeCategorizer.Params(batch_size=2, lyrics_max_chars=10)
    categorizer(llm, monkeypatch, lyrics).vectors(songs, params)
    assert [len(user["songs"]) for _, user in llm.requests] == [2, 2, 1]
    first = llm.requests[0][1]["songs"]
    assert first[0]["lyrics"] == "x" * 10
    assert "prompt v" in llm.requests[0][0]


def test_songs_missing_from_a_batch_are_retried_alone(monkeypatch):
    songs = [song(i, "sad") for i in range(3)]
    lyrics = {s.id: "some lyrics" for s in songs}
    llm = FakeLLM({"sad": SAD}, drop={tid(1)})
    dim = categorizer(llm, monkeypatch, lyrics).vectors(songs, ThemeCategorizer.Params())
    assert tid(1) in dim.ids
    assert [len(u["songs"]) for _, u in llm.requests] == [3, 1]


def test_failed_batch_is_retried_then_left_uncovered(monkeypatch, caplog):
    songs = [song(i, "sad") for i in range(2)]
    lyrics = {s.id: "some lyrics" for s in songs}
    llm = FakeLLM({"sad": SAD}, fail_batches=2)  # the batch and the first single retry fail
    dim = categorizer(llm, monkeypatch, lyrics).vectors(songs, ThemeCategorizer.Params())
    assert dim.ids == [tid(1)]
    assert "no theme" in caplog.text


def test_whitespace_is_cleaned_and_empty_description_is_uncovered(monkeypatch):
    songs = [song(i, "sad" if i == 0 else "empty") for i in range(2)]
    lyrics = {s.id: "some lyrics" for s in songs}
    messy = "  " + SAD.replace(" ", "   ") + "  \n"
    llm = FakeLLM({"sad": messy, "empty": "   "})
    dim = categorizer(llm, monkeypatch, lyrics).vectors(songs, ThemeCategorizer.Params())
    assert dim.ids == [tid(0)]
    assert dim.labels[tid(0)] == [SAD]
