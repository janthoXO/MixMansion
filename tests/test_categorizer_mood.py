import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import requests
from fakes import cos, tid

from mixmansion.categorizers import lrclib
from mixmansion.categorizers.mood import MoodCategorizer, SongTags, TagResponse, normalize
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


# ── mood vectors ─────────────────────────────────────────────────────────────

SAD = ["melancholic", "rainy", "heartbreak"]
HAPPY = ["euphoric", "sunny", "party"]
VECTORS = {
    "melancholic": [1, 0, 0],
    "rainy": [0.9, 0.1, 0],
    "heartbreak": [0.8, 0, 0.2],
    "euphoric": [0, 1, 0],
    "sunny": [0.1, 0.9, 0],
    "party": [0, 0.8, 0.2],
}


class FakeLLM:
    def __init__(self, tags_by_title, fail_batches=0, drop=()):
        self.tags_by_title = tags_by_title
        self.fail_batches = fail_batches
        self.drop = set(drop)
        self.requests = []

    def complete_json_many(self, requests_, schema):
        assert schema is TagResponse
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
                TagResponse(
                    songs=[
                        SongTags(id=s["id"], tags=self.tags_by_title[s["title"]])
                        for s in songs
                        if not (dropped and s["id"] in self.drop)
                    ]
                )
            )
        return out

    def embed(self, texts):
        return np.array([VECTORS[t.strip().lower()] for t in texts], dtype=float)


def categorizer(llm, monkeypatch, lyrics=None):
    monkeypatch.setattr(lrclib, "lyrics", lambda http, s: (lyrics or {}).get(s.id))
    return MoodCategorizer(llm, AppSettings())


def test_similar_tags_are_neighbours(monkeypatch):
    songs = [song(i, "sad" if i < 3 else "happy") for i in range(6)]
    llm = FakeLLM({"sad": SAD, "happy": HAPPY})
    dim = categorizer(llm, monkeypatch).vectors(songs, MoodCategorizer.Params())
    assert cos(dim, tid(0), tid(1)) > 0.9 and cos(dim, tid(3), tid(4)) > 0.9
    assert cos(dim, tid(0), tid(3)) < 0.3
    assert dim.ids == [tid(i) for i in range(6)]
    assert dim.labels[tid(0)] == SAD


def test_batches_and_lyrics_in_the_request(monkeypatch):
    songs = [song(i, "sad") for i in range(5)]
    llm = FakeLLM({"sad": SAD})
    lyrics = {tid(0): "x" * 50}
    params = MoodCategorizer.Params(batch_size=2, lyrics_max_chars=10, tags_per_song=6)
    categorizer(llm, monkeypatch, lyrics).vectors(songs, params)
    assert [len(user["songs"]) for _, user in llm.requests] == [2, 2, 1]
    first = llm.requests[0][1]["songs"]
    assert first[0]["lyrics"] == "x" * 10 and first[1]["lyrics"] is None
    assert "about 6" in llm.requests[0][0] and "prompt v" in llm.requests[0][0]


def test_songs_missing_from_a_batch_are_retried_alone(monkeypatch):
    songs = [song(i, "sad") for i in range(3)]
    llm = FakeLLM({"sad": SAD}, drop={tid(1)})
    dim = categorizer(llm, monkeypatch).vectors(songs, MoodCategorizer.Params())
    assert tid(1) in dim.ids
    assert [len(u["songs"]) for _, u in llm.requests] == [3, 1]


def test_failed_batch_is_retried_then_left_uncovered(monkeypatch, caplog):
    songs = [song(i, "sad") for i in range(2)]
    llm = FakeLLM({"sad": SAD}, fail_batches=2)  # the batch and the first single retry fail
    dim = categorizer(llm, monkeypatch).vectors(songs, MoodCategorizer.Params())
    assert dim.ids == [tid(1)]
    assert "no mood tags" in caplog.text


def test_each_unique_tag_is_embedded_once(monkeypatch):
    songs = [song(i, "sad") for i in range(4)]
    llm = FakeLLM({"sad": [" Melancholic", "rainy", "melancholic"]})
    embedded = []
    real = llm.embed
    llm.embed = lambda texts: embedded.append(list(texts)) or real(texts)
    dim = categorizer(llm, monkeypatch).vectors(songs, MoodCategorizer.Params())
    assert embedded == [["melancholic", "rainy"]]
    assert dim.labels[tid(0)] == ["melancholic", "rainy"]


def test_normalize():
    assert normalize(["  Late   Night ", "late night", "", "Sad"]) == ["late night", "sad"]
