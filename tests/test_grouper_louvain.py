import numpy as np
import pytest
from fakes import tid

from mixmansion.core.models import Song, SongVectors
from mixmansion.groupers.louvain import LouvainGrouper, _merge_small, cosine, fuse

E = np.eye(4)  # one axis per cluster


def songs(n: int) -> list[Song]:
    return [
        Song(id=tid(i), title=f"s{i}", artists=["a"], artist_ids=["a"], duration_ms=1)
        for i in range(n)
    ]


def dim(vectors: dict[str, list[float]], dimension="genre") -> SongVectors:
    return SongVectors(
        dimension=dimension, ids=list(vectors), vectors=np.array(list(vectors.values()))
    )


def clusters(ids: list[str], *sizes: int) -> dict[str, list[float]]:
    """The first sizes[0] songs point along axis 0, the next sizes[1] along axis 1, ..."""
    axes = [c for c, size in enumerate(sizes) for _ in range(size)]
    return {i: list(E[c]) for i, c in zip(ids, axes, strict=False)}


def run(pool, dims, weights=None, **params):
    grouper = LouvainGrouper()
    return grouper.group(pool, dims, weights or {"genre": 1.0}, LouvainGrouper.Params(**params))


def membership(grouping) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for i, g in enumerate(grouping.groups):
        for s in g.songs:
            out.setdefault(s.song_id, []).append(i)
    return out


def test_bridge_song_lands_in_both_groups():
    pool = songs(16)
    ids = [s.id for s in pool]
    bridge = ids[15]
    vectors = clusters(ids, 5, 5, 5) | {bridge: [1, 1, 0, 0]}  # between the first two
    grouping = run(pool, [dim(vectors)], min_size=2)
    assert len(grouping.groups) == 3
    member = membership(grouping)
    assert len(member[bridge]) == 2
    assert all(len(member[x]) == 1 for x in ids[:15])
    for g in grouping.groups:
        scores = [s.score for s in g.songs]
        assert scores == sorted(scores, reverse=True)


def test_fusion_is_the_exact_weighted_mean_of_every_pair():
    """Not only each dimension's nearest neighbours: every pair counts."""
    rng = np.random.default_rng(0)
    ids = [tid(i) for i in range(20)]
    a, b = rng.normal(size=(20, 8)), rng.normal(size=(20, 3))
    genre = SongVectors(dimension="genre", ids=ids, vectors=a)
    mood = SongVectors(dimension="mood", ids=ids, vectors=b)
    expected = (3 * cosine(a) + 1 * cosine(b)) / 4
    np.fill_diagonal(expected, 0)
    fused = fuse(ids, [genre, mood], {"genre": 3, "mood": 1})
    assert fused == pytest.approx(expected, abs=1e-6)


def test_fusion_renormalizes_over_covering_dimensions():
    x, y, z, w = ids = [tid(i) for i in range(4)]
    genre = dim({x: [1, 0], y: [1, 0], z: [0, 1], w: [0, 1]})
    mood = dim({x: [1, 0], z: [1, 0], w: [0, 1]}, dimension="mood")  # y has no lyrics
    fused = fuse(ids, [genre, mood], {"genre": 0.5, "mood": 0.5})
    assert fused[0, 1] == pytest.approx(1.0)  # x, y: only genre covers both
    assert fused[0, 2] == pytest.approx(0.5)  # x, z: genre 0, mood 1
    # an uncovered song still groups by the other dimension
    grouping = run(songs(4), [genre, mood], {"genre": 0.5, "mood": 0.5}, min_size=1)
    assert y in membership(grouping)


def test_zero_weight_dimension_is_ignored():
    x, y, z, w = ids = [tid(i) for i in range(4)]
    genre = dim({x: [1, 0], y: [1, 0], z: [0, 1], w: [0, 1]})
    mood = dim({x: [1, 0], z: [1, 0], w: [0, 1]}, dimension="mood")
    assert fuse(ids, [genre, mood], {"genre": 1, "mood": 0})[0, 2] == 0


def test_centering_calibrates_dimensions():
    """Embeddings that are all alike (cosine ~0.9) must not drown out a sparse dimension."""
    ids = [tid(i) for i in range(4)]
    alike = dim(dict(zip(ids, [[1, 0.1, 0], [1, 0.12, 0], [1, 0, 0.1], [1, 0, 0.12]], strict=True)))
    assert fuse(ids, [alike], {"genre": 1})[0, 2] < 0.1
    assert fuse(ids, [alike], {"genre": 1})[0, 1] > 0.9


def test_small_communities_merge_into_the_most_similar_one():
    fused = np.zeros((9, 9))
    fused[np.ix_([7, 8], [0, 1, 2])] = fused[np.ix_([0, 1, 2], [7, 8])] = 0.6
    fused[np.ix_([7, 8], [3, 4, 5, 6])] = fused[np.ix_([3, 4, 5, 6], [7, 8])] = 0.2
    merged = _merge_small(fused, [[3, 4, 5, 6], [0, 1, 2], [7, 8]], min_size=3)
    assert merged == [[3, 4, 5, 6], [0, 1, 2, 7, 8]]  # the more similar, not the larger
    # unconnected: goes to the largest
    assert _merge_small(np.zeros((9, 9)), [[0, 1, 2], [3, 4, 5, 6], [7, 8]], 3)[1] == [
        3,
        4,
        5,
        6,
        7,
        8,
    ]


def test_small_communities_are_merged_end_to_end():
    pool = songs(12)
    ids = [s.id for s in pool]
    grouping = run(pool, [dim(clusters(ids, 8, 4))], min_size=5, tau=0)
    assert len(grouping.groups) == 1
    assert {s.song_id for s in grouping.groups[0].songs} == set(ids)
    assert len(run(pool, [dim(clusters(ids, 8, 4))], min_size=2, tau=0).groups) == 2


def test_uncovered_songs_are_unassigned_and_every_song_is_placed():
    pool = songs(8)
    ids = [s.id for s in pool]
    grouping = run(pool, [dim(clusters(ids[:6], 3, 3))], min_size=2)
    assert grouping.unassigned == ids[6:]  # no dimension covers them
    placed = set(membership(grouping)) | set(grouping.unassigned)
    assert placed == set(ids)


def test_max_memberships_caps_groups_per_song():
    pool = songs(21)
    ids = [s.id for s in pool]
    hub = ids[20]
    vectors = clusters(ids, 5, 5, 5, 5) | {hub: [1, 1, 1, 0]}
    grouping = run(pool, [dim(vectors)], min_size=2, tau=1.0, max_memberships=2)
    assert len(membership(grouping)[hub]) == 2


def test_no_data_at_all():
    pool = songs(3)
    grouping = run(pool, [SongVectors.empty("genre")])
    assert grouping.groups == [] and grouping.unassigned == [s.id for s in pool]


def test_same_input_same_output():
    pool = songs(30)
    vectors = dict(
        zip([s.id for s in pool], np.random.default_rng(1).normal(size=(30, 5)), strict=True)
    )
    dims = [dim({k: list(v) for k, v in vectors.items()})]
    first = run(pool, dims, min_size=3)
    assert first == run(pool, dims, min_size=3)
