"""Smoke tests for the in-memory fakes."""

import pytest
from fakes import (
    SONGS,
    FakeCategorizer,
    FakeGrouper,
    FakeNamer,
    FakePlanStore,
    FakePoolStore,
    FakeRetriever,
    FakeWriter,
    tid,
)

from mixmansion.core.models import Plan, Playlist, SongPool
from mixmansion.writers.port import PlaylistNotFound


def test_fake_retriever():
    retriever = FakeRetriever()
    params = FakeRetriever.Params()
    songs = retriever.retrieve(params)
    assert [s.id for s in songs] == [s.id for s in SONGS]
    assert songs[0] is not SONGS[0]  # deep copy
    choices = retriever.choices("source", {})
    assert [c.value for c in choices] == ["default"]


def test_fake_categorizer():
    categorizer = FakeCategorizer()
    params = FakeCategorizer.Params()
    dim = categorizer.vectors(SONGS, params)
    assert dim.ids == [s.id for s in SONGS]
    assert dim.labels[tid(1)] == ["even"]
    assert dim.labels[tid(2)] == ["odd"]
    assert dim.vectors[0] @ dim.vectors[2] > 0  # both even-indexed
    assert dim.vectors[0] @ dim.vectors[1] == 0  # different clusters


def test_fake_grouper():
    grouper = FakeGrouper()
    params = FakeGrouper.Params()
    categorizer = FakeCategorizer()
    dim = categorizer.vectors(SONGS, FakeCategorizer.Params())
    grouping = grouper.group(SONGS, [dim], {}, params)
    assert len(grouping.groups) == 2
    assert not grouping.unassigned
    ids = {s.song_id for group in grouping.groups for s in group.songs}
    assert ids == {s.id for s in SONGS}


def test_fake_grouper_unassigned():
    grouper = FakeGrouper()
    grouping = grouper.group(SONGS, [], {}, FakeGrouper.Params())
    assert grouping.groups == []
    assert set(grouping.unassigned) == {s.id for s in SONGS}


def test_fake_namer():
    namer = FakeNamer()
    result = namer.name_groups([SONGS[:2], SONGS[2:]], {}, FakeNamer.Params())
    assert result == [("Group 1", "2 songs"), ("Group 2", "4 songs")]


def test_fake_writer():
    writer = FakeWriter()
    params = FakeWriter.Params()
    playlist_id = writer.create("My Mix", "desc", params)
    assert playlist_id == "pl1"
    writer.replace_tracks(playlist_id, ["t1", "t2"], params)
    assert writer.playlists[playlist_id] == ["t1", "t2"]
    assert writer.calls == [("create", "My Mix"), ("replace", "pl1", ["t1", "t2"])]
    with pytest.raises(PlaylistNotFound):
        writer.replace_tracks("nope", ["t1"], params)


def test_fake_pool_store():
    store = FakePoolStore()
    assert store.load("missing") == SongPool()
    pool = SongPool(songs=[SONGS[0]])
    store.save("p1", pool)
    loaded = store.load("p1")
    assert loaded == pool
    loaded.songs.append(SONGS[1])
    assert store.load("p1").songs == [SONGS[0]]  # deep copy, unaffected
    store.delete("p1")
    assert store.load("p1") == SongPool()
    store.delete("missing")  # no error


def test_fake_plan_store():
    store = FakePlanStore()
    plan = Plan(playlists=[Playlist(name="A", tracks=[])])
    ref = store.save(plan)
    assert ref == "plan1"
    assert store.load(ref) == plan
    ref2 = store.save(plan, "custom")
    assert ref2 == "custom"
    with pytest.raises(KeyError):
        store.load("nope")
