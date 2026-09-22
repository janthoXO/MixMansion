"""Tests for SongPool.add dedup/merge behavior."""

from mixmansion.core.models import Song, SongPool


def make_song(**overrides):
    defaults = dict(
        id="t1",
        isrc="ISRC1",
        title="Song",
        artists=["A"],
        artist_ids=["a1"],
        duration_ms=180000,
        sources=["playlist:Chill"],
    )
    defaults.update(overrides)
    return Song(**defaults)


def test_add_new_songs():
    pool = SongPool()
    added = pool.add([make_song(id="t1"), make_song(id="t2", isrc="ISRC2")])
    assert added == 2
    assert len(pool.songs) == 2


def test_add_same_id_dedupes_and_merges_sources():
    pool = SongPool()
    pool.add([make_song(id="t1", sources=["playlist:Chill"])])
    added = pool.add([make_song(id="t1", sources=["search:rainy jazz"])])
    assert added == 0
    assert len(pool.songs) == 1
    assert pool.songs[0].sources == ["playlist:Chill", "search:rainy jazz"]


def test_add_same_source_not_duplicated():
    pool = SongPool()
    pool.add([make_song(id="t1", sources=["playlist:Chill"])])
    pool.add([make_song(id="t1", sources=["playlist:Chill"])])
    assert pool.songs[0].sources == ["playlist:Chill"]


def test_add_same_isrc_different_id_dedupes():
    # e.g. a single vs its album version share an ISRC but have different Spotify ids
    pool = SongPool()
    pool.add([make_song(id="single1", isrc="ISRC1", sources=["a"])])
    added = pool.add([make_song(id="album1", isrc="ISRC1", sources=["b"])])
    assert added == 0
    assert len(pool.songs) == 1
    assert pool.songs[0].id == "single1"  # first one wins
    assert pool.songs[0].sources == ["a", "b"]


def test_add_no_isrc_dedupes_by_id():
    pool = SongPool()
    pool.add([make_song(id="t1", isrc=None, sources=["a"])])
    added = pool.add([make_song(id="t1", isrc=None, sources=["b"])])
    assert added == 0
    assert len(pool.songs) == 1
    assert pool.songs[0].sources == ["a", "b"]


def test_add_no_isrc_different_ids_both_added():
    pool = SongPool()
    added = pool.add([make_song(id="t1", isrc=None), make_song(id="t2", isrc=None)])
    assert added == 2


def test_add_dedupes_within_single_call():
    pool = SongPool()
    added = pool.add(
        [
            make_song(id="t1", isrc="ISRC1", sources=["a"]),
            make_song(id="t1", isrc="ISRC1", sources=["b"]),
        ]
    )
    assert added == 1
    assert len(pool.songs) == 1
    assert pool.songs[0].sources == ["a", "b"]


def test_add_does_not_mutate_caller_song_on_later_merge():
    pool = SongPool()
    original = make_song(id="t1", sources=["a"])
    pool.add([original])
    pool.add([make_song(id="t1", sources=["b"])])
    # the pool's stored copy gained "b", but the caller's original object must not
    assert original.sources == ["a"]
    assert pool.songs[0].sources == ["a", "b"]
