"""End-to-end pipeline tests: pool -> plan -> hand-edit -> apply, against the fakes."""

import logging

import numpy as np
import pytest
from fakes import (
    SONGS,
    FakeCategorizer,
    FakeGrouper,
    FakePlanStore,
    FakeRetriever,
    FakeWriter,
    tid,
)
from pydantic import ValidationError
from pydantic_settings import SettingsConfigDict

from mixmansion.core.models import PlanTrack, Playlist, SongVectors
from mixmansion.core.usecases import (
    AdapterSpec,
    ApplyResult,
    CategorizerSpec,
    MixMansionError,
    PlanSpec,
    PoolSummary,
)
from mixmansion.shared.config import AdapterParams, ConfigError

# The fake categorizer clusters by even/odd index: songs 1,3,5 in one group, 2,4,6 in the other.
GROUP_A = {tid(1), tid(3), tid(5)}
GROUP_B = {tid(2), tid(4), tid(6)}


def test_pool_add_dedupes(fake_app):
    app, _ = fake_app
    summary = app.add_to_pool("default", "fake", {})
    assert summary == PoolSummary(added=6, total=6, duplicates=0)
    summary2 = app.add_to_pool("default", "fake", {})
    assert summary2 == PoolSummary(added=0, total=6, duplicates=6)


def test_end_to_end_build_edit_apply(fake_app):
    app, instances = fake_app
    app.add_to_pool("default", "fake", {})

    ref = app.build_plan("default", PlanSpec())
    plan = app.get_plan(ref)

    assert [p.name for p in plan.playlists] == ["Group 1", "Group 2"]
    ids_by_group = [{t.id for t in p.tracks} for p in plan.playlists]
    assert set(ids_by_group[0]) in (GROUP_A, GROUP_B)
    assert set(ids_by_group[0]) | set(ids_by_group[1]) == GROUP_A | GROUP_B

    for p in plan.playlists:
        for t in p.tracks:
            assert t.artist and t.title and t.score is not None and t.tags

    gen = plan.generated
    assert gen["pool_id"] == "default"
    assert gen["pool_size"] == 6
    assert gen["weights"] == {"fake": 1.0}
    assert gen["categorizers"] == {"fake": {"dimension": "fake"}}
    assert gen["grouper"] == {"fake": {}}
    assert gen["namer"] == {"fake": {}}

    # hand-edit: move a track between playlists, remove one, add a new one via URI, approve.
    moved = plan.playlists[0].tracks.pop()
    plan.playlists[1].tracks.append(moved)
    plan.playlists[0].tracks.pop()
    new_track = PlanTrack(id=f"spotify:track:{tid(7)}")
    assert new_track.id == tid(7)  # normalized to the bare id
    plan.playlists[1].tracks.append(new_track)
    plan.approved = True

    store = instances[FakePlanStore]
    store.save(plan, ref)

    result = app.apply_plan(ref)

    writer = instances[FakeWriter]
    ids0 = [t.id for t in plan.playlists[0].tracks]
    ids1 = [t.id for t in plan.playlists[1].tracks]
    assert writer.calls == [
        ("create", "Group 1"),
        ("replace", "pl1", ids0),
        ("create", "Group 2"),
        ("replace", "pl2", ids1),
    ]

    saved = store.load(ref)
    assert [p.spotify_id for p in saved.playlists] == ["pl1", "pl2"]
    assert result == ApplyResult(
        created=["Group 1", "Group 2"],
        updated=[],
        tracks={"Group 1": len(ids0), "Group 2": len(ids1)},
    )


def _built_plan(app, instances):
    """Build, approve and return a ref for a plan ready to apply."""
    app.add_to_pool("default", "fake", {})
    ref = app.build_plan("default", PlanSpec())
    store = instances[FakePlanStore]
    plan = store.plans[ref]
    plan.approved = True
    return ref


def test_apply_twice_only_replaces(fake_app):
    app, instances = fake_app
    ref = _built_plan(app, instances)

    app.apply_plan(ref)
    writer = instances[FakeWriter]
    calls_before = len(writer.calls)

    result = app.apply_plan(ref)

    new_calls = writer.calls[calls_before:]
    assert all(c[0] == "replace" for c in new_calls)
    assert result.created == []
    assert set(result.updated) == {"Group 1", "Group 2"}


def test_apply_recreates_deleted_playlist(fake_app):
    app, instances = fake_app
    ref = _built_plan(app, instances)
    app.apply_plan(ref)

    writer = instances[FakeWriter]
    del writer.playlists["pl1"]

    result = app.apply_plan(ref)

    saved = instances[FakePlanStore].plans[ref]
    spotify_ids = {p.name: p.spotify_id for p in saved.playlists}
    assert spotify_ids["Group 2"] == "pl2"  # untouched
    recreated = [p for p in saved.playlists if p.spotify_id == "pl3"]
    assert len(recreated) == 1
    assert "Group 2" in result.updated
    assert recreated[0].name in result.created


def test_invariant_violation_raises(fake_app):
    app, instances = fake_app
    app.add_to_pool("default", "fake", {})

    class DroppingGrouper(FakeGrouper):
        name = "dropping"

        def group(self, songs, dimensions, weights, params):
            grouping = super().group(songs, dimensions, weights, params)
            grouping.groups[0].songs.pop()  # silently drops a song
            return grouping

    app._adapters["grouper"]["dropping"] = DroppingGrouper
    instances[DroppingGrouper] = DroppingGrouper()

    spec = PlanSpec(grouper=AdapterSpec(name="dropping"))
    with pytest.raises(RuntimeError, match="invariant"):
        app.build_plan("default", spec)


def test_every_song_placed_or_unassigned(fake_app):
    app, instances = fake_app
    app.add_to_pool("default", "fake", {})

    class SparseCategorizer(FakeCategorizer):
        name = "sparse"

        def vectors(self, songs, params):
            return super().vectors(songs[:-1], params)  # the last song is uncovered

    app._adapters["categorizer"]["sparse"] = SparseCategorizer
    instances[SparseCategorizer] = SparseCategorizer()

    spec = PlanSpec(categorizers={"sparse": CategorizerSpec(weight=1.0)})
    ref = app.build_plan("default", spec)
    plan = app.get_plan(ref)

    assert plan.unassigned  # the isolated song has nowhere to go
    placed = {t.id for p in plan.playlists for t in p.tracks} | {t.id for t in plan.unassigned}
    assert placed == {s.id for s in SONGS}


def test_apply_unapproved_plan_refused(fake_app):
    app, instances = fake_app
    ref = _built_plan(app, instances)
    instances[FakePlanStore].plans[ref].approved = False

    with pytest.raises(MixMansionError):
        app.apply_plan(ref)


def test_build_plan_empty_pool(fake_app):
    app, _ = fake_app
    with pytest.raises(MixMansionError):
        app.build_plan("default", PlanSpec())


def test_unknown_adapter_lists_available(fake_app):
    app, _ = fake_app
    app.add_to_pool("default", "fake", {})
    spec = PlanSpec(grouper=AdapterSpec(name="nope"))
    with pytest.raises(MixMansionError, match="fake"):
        app.build_plan("default", spec)


def test_list_adapters_and_choices(fake_app):
    app, _ = fake_app
    infos = app.list_adapters("retriever")
    assert [i.name for i in infos] == ["fake"]
    assert "properties" in infos[0].params_schema

    choices = app.adapter_choices("retriever", "fake", "source", {})
    assert [c.value for c in choices] == ["default"]


def test_missing_required_param_is_config_error(fake_app):
    app, instances = fake_app

    class RequiredRetriever(FakeRetriever):
        name = "required"

        class Params(AdapterParams):
            model_config = SettingsConfigDict(env_prefix="MIXMANSION_RETRIEVER_REQUIRED_")
            query: str

    app._adapters["retriever"]["required"] = RequiredRetriever
    instances[RequiredRetriever] = RequiredRetriever()

    with pytest.raises(ConfigError, match="MIXMANSION_RETRIEVER_REQUIRED_QUERY"):
        app.add_to_pool("default", "required", {})


# ── plan model validation ─────────────────────────────────────────────────────


def test_track_id_normalization():
    bare = tid(1)
    assert PlanTrack(id=bare).id == bare
    assert PlanTrack(id=f"spotify:track:{bare}").id == bare
    assert PlanTrack(id=f"https://open.spotify.com/track/{bare}?si=abc123").id == bare
    assert PlanTrack(id=f"https://open.spotify.com/intl-de/track/{bare}").id == bare


def test_invalid_track_id_rejected():
    with pytest.raises(ValidationError):
        PlanTrack(id="not-a-valid-id")


def test_duplicate_track_ids_rejected():
    with pytest.raises(ValidationError, match="twice"):
        Playlist(name="A", tracks=[PlanTrack(id=tid(1)), PlanTrack(id=tid(1))])


def test_blank_playlist_name_rejected():
    with pytest.raises(ValidationError):
        Playlist(name="   ", tracks=[])


def test_long_description_rejected():
    with pytest.raises(ValidationError):
        Playlist(name="A", description="x" * 301, tracks=[])


# ── orphan warning ─────────────────────────────────────────────────────────────


def test_get_plan_warns_on_orphaned_songs(fake_app, caplog):
    app, instances = fake_app
    app.add_to_pool("default", "fake", {})
    ref = app.build_plan("default", PlanSpec())

    plan = instances[FakePlanStore].plans[ref]
    orphan_id = plan.playlists[0].tracks[0].id
    plan.playlists[0].tracks.pop(0)
    assert all(t.id != orphan_id for p in plan.playlists for t in p.tracks)
    assert all(t.id != orphan_id for t in plan.unassigned)

    with caplog.at_level(logging.WARNING):
        app.get_plan(ref)
    assert "no playlist" in caplog.text


# ── buckets ──────────────────────────────────────────────────────────────────
# FakeBucketCategorizer (registered as "bucket" in fake_app) buckets songs 1,2 -> "x",
# 3,4 -> "y", and leaves 5,6 uncovered (their own bucket, key None).


class SameCategorizer(FakeCategorizer):
    """Gives every song the same vector, so the fake grouper connects them all."""

    name = "same"

    def vectors(self, songs, params):
        return SongVectors(
            dimension="same", ids=[s.id for s in songs], vectors=np.ones((len(songs), 2)), labels={}
        )


def test_buckets_keep_songs_from_different_buckets_apart(fake_app):
    app, instances = fake_app
    app.add_to_pool("default", "fake", {})
    app._adapters["categorizer"]["same"] = SameCategorizer
    instances[SameCategorizer] = SameCategorizer()

    spec = PlanSpec(
        buckets=[AdapterSpec(name="bucket")], categorizers={"same": CategorizerSpec(weight=1.0)}
    )
    ref = app.build_plan("default", spec)
    plan = app.get_plan(ref)

    bucket_x, bucket_y, bucket_none = {tid(1), tid(2)}, {tid(3), tid(4)}, {tid(5), tid(6)}
    playlist_ids = {frozenset(t.id for t in p.tracks) for p in plan.playlists}
    assert playlist_ids == {frozenset(bucket_x), frozenset(bucket_y), frozenset(bucket_none)}
    assert not plan.unassigned  # the uncovered songs still land in their own bucket


def test_buckets_only_makes_one_playlist_per_bucket(fake_app):
    app, instances = fake_app
    app.add_to_pool("default", "fake", {})

    spec = PlanSpec(buckets=[AdapterSpec(name="bucket")], categorizers={})
    ref = app.build_plan("default", spec)
    plan = app.get_plan(ref)

    assert len(plan.playlists) == 3
    playlist_ids = {frozenset(t.id for t in p.tracks) for p in plan.playlists}
    assert playlist_ids == {
        frozenset({tid(1), tid(2)}),
        frozenset({tid(3), tid(4)}),
        frozenset({tid(5), tid(6)}),
    }
    assert plan.generated["buckets"] == {"bucket": {}}


def test_non_bucketable_categorizer_as_bucket_rejected(fake_app):
    app, _instances = fake_app
    app.add_to_pool("default", "fake", {})

    spec = PlanSpec(buckets=[AdapterSpec(name="fake")])
    with pytest.raises(MixMansionError, match="bucket"):
        app.build_plan("default", spec)


def test_categorizer_used_as_both_bucket_and_weight_is_dropped_from_weights(fake_app):
    app, _instances = fake_app
    app.add_to_pool("default", "fake", {})

    spec = PlanSpec(
        buckets=[AdapterSpec(name="bucket")],
        categorizers={"bucket": CategorizerSpec(weight=1.0), "fake": CategorizerSpec(weight=1.0)},
    )
    ref = app.build_plan("default", spec)
    plan = app.get_plan(ref)

    assert "bucket" not in plan.generated["weights"]
    assert "fake" in plan.generated["weights"]
