from fakes import tid

from mixmansion.categorizers import lrclib
from mixmansion.categorizers.language import LanguageCategorizer
from mixmansion.core.models import Song
from mixmansion.shared.config import AppSettings

GERMAN = "Ich liebe dich, mein Schatz, du bist wunderbar heute Abend im Mondschein"
ENGLISH = "I love you my darling you are wonderful tonight under the moonlight"
SPANISH = "Te quiero mucho mi amor eres maravillosa esta noche bajo la luna"


def song(n: int) -> Song:
    return Song(
        id=tid(n), title=f"song {n}", artists=["Artist"], artist_ids=["a"], duration_ms=200_000
    )


def categorizer(lyrics, monkeypatch):
    monkeypatch.setattr(lrclib, "lyrics", lambda http, s: lyrics.get(s.id))
    return LanguageCategorizer(AppSettings())


def test_languages_detected_and_one_hot(monkeypatch):
    songs = [song(0), song(1), song(2), song(3)]
    lyrics = {tid(0): GERMAN, tid(1): ENGLISH, tid(2): SPANISH}  # tid(3): no lyrics
    dim = categorizer(lyrics, monkeypatch).vectors(songs, LanguageCategorizer.Params())

    assert dim.ids == [tid(0), tid(1), tid(2)]
    assert tid(3) not in dim.ids
    assert dim.labels[tid(0)] == ["german"]
    assert dim.labels[tid(1)] == ["english"]
    assert dim.labels[tid(2)] == ["spanish"]

    assert dim.vectors.shape == (3, 3)
    for row in dim.vectors:
        assert row.sum() == 1  # one-hot


def test_min_probability_cutoff_leaves_song_uncovered(monkeypatch):
    songs = [song(0)]
    lyrics = {tid(0): GERMAN}
    dim = categorizer(lyrics, monkeypatch).vectors(
        songs, LanguageCategorizer.Params(min_probability=0.999_999)
    )
    assert dim.ids == []


def test_no_lyrics_at_all_returns_empty(monkeypatch):
    dim = categorizer({}, monkeypatch).vectors([song(0)], LanguageCategorizer.Params())
    assert dim.ids == []
    assert dim.vectors.shape == (0, 0)


def test_bucketable():
    assert LanguageCategorizer.bucketable is True
