from itertools import combinations

from fakes import tid

from mixmansion.core.models import SimilarityGraph, Song
from mixmansion.groupers.louvain import LouvainGrouper, fuse


def songs(n: int) -> list[Song]:
    return [
        Song(id=tid(i), title=f"s{i}", artists=["a"], artist_ids=["a"], duration_ms=1)
        for i in range(n)
    ]


def clique(ids: list[str], w: float = 1.0) -> dict[tuple[str, str], float]:
    return {tuple(sorted(p)): w for p in combinations(ids, 2)}


def graph(edges, covered, dimension="genre") -> SimilarityGraph:
    return SimilarityGraph(dimension=dimension, edges=edges, covered=set(covered))


def run(pool, graphs, weights=None, **params):
    grouper = LouvainGrouper()
    return grouper.group(pool, graphs, weights or {"genre": 1.0}, LouvainGrouper.Params(**params))


def membership(grouping) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for i, g in enumerate(grouping.groups):
        for s in g.songs:
            out.setdefault(s.song_id, []).append(i)
    return out


def test_bridge_song_lands_in_both_groups():
    pool = songs(11)
    ids = [s.id for s in pool]
    a, b, bridge = ids[:5], ids[5:10], ids[10]
    edges = clique(a) | clique(b) | {tuple(sorted((bridge, x))): 0.5 for x in a + b}
    grouping = run(pool, [graph(edges, ids)], min_size=2)
    assert len(grouping.groups) == 2
    member = membership(grouping)
    assert len(member[bridge]) == 2
    assert all(len(member[x]) == 1 for x in a + b)
    for g in grouping.groups:
        scores = [s.score for s in g.songs]
        assert scores == sorted(scores, reverse=True)


def test_fusion_renormalizes_over_covering_dimensions():
    x, y, z = tid(1), tid(2), tid(3)
    genre = graph({(x, y): 0.8, (x, z): 0.4}, {x, y, z})
    mood = graph({(x, z): 1.0}, {x, z}, dimension="mood")  # y has no lyrics: uncovered
    fused = fuse([genre, mood], {"genre": 0.5, "mood": 0.5})
    assert fused[(x, y)] == 0.8  # only genre covers both
    assert fused[(x, z)] == 0.7  # mean of 0.4 and 1.0
    # an uncovered song still groups by the other dimension
    grouping = run(songs(4)[1:], [genre, mood], {"genre": 0.5, "mood": 0.5}, min_size=1)
    assert y in membership(grouping)


def test_missing_edge_in_covering_dimension_counts_as_zero():
    x, y = tid(1), tid(2)
    genre = graph({(x, y): 1.0}, {x, y})
    mood = graph({}, {x, y}, dimension="mood")
    assert fuse([genre, mood], {"genre": 1, "mood": 1}) == {(x, y): 0.5}


def test_zero_weight_dimension_is_ignored():
    x, y = tid(1), tid(2)
    genre = graph({(x, y): 1.0}, {x, y})
    mood = graph({}, {x, y}, dimension="mood")
    assert fuse([genre, mood], {"genre": 1, "mood": 0}) == {(x, y): 1.0}


def test_small_communities_are_merged():
    pool = songs(12)
    ids = [s.id for s in pool]
    edges = clique(ids[:8]) | clique(ids[8:], 1.0) | {(ids[0], ids[8]): 0.2}
    grouping = run(pool, [graph(edges, ids)], min_size=5, tau=0)
    assert len(grouping.groups) == 1
    assert {s.song_id for s in grouping.groups[0].songs} == set(ids)
    # with a low min_size both communities survive
    assert len(run(pool, [graph(edges, ids)], min_size=2, tau=0).groups) == 2


def test_isolated_songs_are_unassigned_and_every_song_is_placed():
    pool = songs(8)
    ids = [s.id for s in pool]
    edges = clique(ids[:3]) | clique(ids[3:6])
    grouping = run(pool, [graph(edges, ids)], min_size=2)
    assert grouping.unassigned == ids[6:]
    placed = set(membership(grouping)) | set(grouping.unassigned)
    assert placed == set(ids)


def test_max_memberships_caps_groups_per_song():
    pool = songs(16)
    ids = [s.id for s in pool]
    hub = ids[15]
    edges = clique(ids[:5]) | clique(ids[5:10]) | clique(ids[10:15])
    edges |= {tuple(sorted((hub, x))): 0.5 for x in ids[:15]}
    grouping = run(pool, [graph(edges, ids)], min_size=2, tau=1.0, max_memberships=2)
    assert len(membership(grouping)[hub]) == 2


def test_no_edges_at_all():
    pool = songs(3)
    grouping = run(pool, [graph({}, [s.id for s in pool])])
    assert grouping.groups == [] and grouping.unassigned == [s.id for s in pool]


def test_same_input_same_output():
    pool = songs(30)
    ids = [s.id for s in pool]
    edges = {
        tuple(sorted((ids[i], ids[j]))): ((i * 7 + j * 3) % 10) / 10
        for i in range(30)
        for j in range(i + 1, 30)
        if (i + j) % 3 == 0
    }
    edges = {k: v for k, v in edges.items() if v > 0}
    first = run(pool, [graph(edges, ids)], min_size=3)
    assert first == run(pool, [graph(edges, ids)], min_size=3)
