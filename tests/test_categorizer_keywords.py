from fakes import cos, tid

from mixmansion.categorizers import lrclib
from mixmansion.categorizers.keywords import KeywordsCategorizer, words
from mixmansion.core.models import Song
from mixmansion.shared.config import AppSettings


def song(n: int) -> Song:
    return Song(
        id=tid(n), title=f"song {n}", artists=["Artist"], artist_ids=["a"], duration_ms=200_000
    )


def categorizer(lyrics, monkeypatch):
    monkeypatch.setattr(lrclib, "lyrics", lambda http, s: lyrics.get(s.id))
    return KeywordsCategorizer(AppSettings())


def test_shared_distinctive_word_makes_neighbours(monkeypatch):
    songs = [song(i) for i in range(4)]
    lyrics = {
        tid(0): "california sunshine forever california",
        tid(1): "under the california sky tonight",
        tid(2): "whiskey and rain in the city",
        tid(3): "another whiskey another rainy night",
    }
    dim = categorizer(lyrics, monkeypatch).vectors(songs, KeywordsCategorizer.Params())
    assert cos(dim, tid(0), tid(1)) > 0
    assert cos(dim, tid(0), tid(2)) == 0


def test_max_df_and_min_df_drop_words(monkeypatch):
    songs = [song(i) for i in range(4)]
    lyrics = {
        tid(0): "midnight love story",
        tid(1): "midnight dance floor",
        tid(2): "midnight train home",
        tid(3): "solo unique word here",
    }
    dim = categorizer(lyrics, monkeypatch).vectors(songs, KeywordsCategorizer.Params())
    all_words = {w for i in dim.ids for w in dim.labels[i]}
    assert "midnight" not in all_words  # in every song with lyrics: max_df
    assert "solo" not in all_words  # in only one song: min_df


def test_uncovered_without_lyrics_or_kept_words(monkeypatch):
    songs = [song(0), song(1), song(2)]
    lyrics = {
        tid(0): "california whiskey midnight",
        tid(2): "a an on it is",
    }  # tid(2): all too short
    dim = categorizer(lyrics, monkeypatch).vectors(songs, KeywordsCategorizer.Params(min_df=1))
    assert tid(0) in dim.ids
    assert tid(1) not in dim.ids  # no lyrics
    assert tid(2) not in dim.ids  # nothing survives tokenization


def test_words_tokenizes():
    text = "[Chorus]\nOh yeah, das Mädchen tanzt 2 or 3 I a on ohh uh lala"
    assert words(text) == ["das", "mädchen", "tanzt"]


def test_repeated_word_counts_sublinearly(monkeypatch):
    songs = [song(0), song(1), song(2)]
    lyrics = {
        tid(0): "midnight " * 6,
        tid(1): "midnight",
        tid(2): "other filler words here",
    }
    dim = categorizer(lyrics, monkeypatch).vectors(
        songs, KeywordsCategorizer.Params(min_df=1, max_df=1.0)
    )
    w0 = dim.vectors[dim.ids.index(tid(0))].sum()
    w1 = dim.vectors[dim.ids.index(tid(1))].sum()
    assert w0 > w1
    assert w0 < 3 * w1


def test_labels_are_top_words_deterministic(monkeypatch):
    songs = [song(0), song(1)]
    lyrics = {
        tid(0): "apple apple apple banana cherry date egg",
        tid(1): "apple banana banana banana cherry date egg",
    }
    dim = categorizer(lyrics, monkeypatch).vectors(
        songs, KeywordsCategorizer.Params(min_df=1, max_df=1.0, labels_per_song=2)
    )
    assert dim.labels[tid(0)] == ["apple", "banana"]
    assert dim.labels[tid(1)] == ["banana", "apple"]
