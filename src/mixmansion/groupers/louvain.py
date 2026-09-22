"""Louvain grouper: fuse the weighted graphs, find communities, soft-assign songs to them."""

from collections import defaultdict

import networkx as nx
from pydantic import Field
from pydantic_settings import SettingsConfigDict

from mixmansion.core.models import Group, Grouping, ScoredSong, SimilarityGraph, Song
from mixmansion.groupers.port import Grouper
from mixmansion.shared.config import AdapterParams

Pair = tuple[str, str]


def fuse(graphs: list[SimilarityGraph], weights: dict[str, float]) -> dict[Pair, float]:
    """Weighted mean per pair over only the dimensions that cover both songs.

    A missing edge in a dimension that covers both songs counts as 0, so a song without
    data in one dimension (e.g. no lyrics) is grouped by the others instead of pushed away.
    """
    graphs = [g for g in graphs if weights.get(g.dimension, 0) > 0]
    fused = {}
    for a, b in set().union(*(g.edges for g in graphs)):
        num = den = 0.0
        for g in graphs:
            if a in g.covered and b in g.covered:
                w = weights[g.dimension]
                num += w * g.edges.get((a, b), 0.0)
                den += w
        if num > 0:
            fused[(a, b)] = num / den
    return fused


class LouvainGrouper(Grouper):
    name = "louvain"

    class Params(AdapterParams):
        model_config = SettingsConfigDict(env_prefix="MIXMANSION_GROUPER_LOUVAIN_")
        resolution: float = Field(1.0, gt=0, description="Higher means more, smaller groups")
        min_size: int = Field(15, ge=1, description="Communities smaller than this get merged")
        tau: float = Field(
            0.05, ge=0, le=1, description="How close a second fit must be to join a second group"
        )
        max_memberships: int = Field(2, ge=1, description="Maximum number of groups per song")
        seed: int = Field(42, description="Makes runs repeatable")

    def group(
        self,
        songs: list[Song],
        graphs: list[SimilarityGraph],
        weights: dict[str, float],
        params: Params,
    ) -> Grouping:
        g = nx.Graph()
        g.add_nodes_from(s.id for s in songs)
        g.add_weighted_edges_from((a, b, w) for (a, b), w in fuse(graphs, weights).items())
        connected = [n for n in g if g.degree(n) > 0]
        unassigned = [n for n in g if g.degree(n) == 0]
        if not connected:
            return Grouping(groups=[], unassigned=unassigned)

        found = nx.community.louvain_communities(
            g.subgraph(connected), weight="weight", resolution=params.resolution, seed=params.seed
        )
        communities = sorted((sorted(c) for c in found), key=lambda c: (-len(c), c))
        communities = _merge_small(g, communities, params.min_size)

        index = {n: i for i, c in enumerate(communities) for n in c}
        groups: list[list[ScoredSong]] = [[] for _ in communities]
        for song in connected:
            totals: dict[int, float] = defaultdict(float)
            for neighbour, data in g[song].items():
                totals[index[neighbour]] += data["weight"]
            affinity = {
                i: total / (len(communities[i]) - (index[song] == i)) for i, total in totals.items()
            }
            best = max(affinity.values())
            ranked = sorted(affinity, key=lambda i: (-affinity[i], i))
            for i in ranked[: params.max_memberships]:
                if affinity[i] >= (1 - params.tau) * best:
                    groups[i].append(ScoredSong(song_id=song, score=affinity[i]))

        return Grouping(
            groups=[
                Group(songs=sorted(members, key=lambda s: (-s.score, s.song_id)))
                for members in groups
                if members
            ],
            unassigned=unassigned,
        )


def _merge_small(g: nx.Graph, communities: list[list[str]], min_size: int) -> list[list[str]]:
    """Fold the smallest undersized community into the one it's most connected to, repeatedly."""
    communities = [list(c) for c in communities]
    while len(communities) > 1:
        small = min(range(len(communities)), key=lambda i: (len(communities[i]), i))
        if len(communities[small]) >= min_size:
            break
        members = set(communities[small])
        links: dict[int, float] = defaultdict(float)
        index = {n: i for i, c in enumerate(communities) for n in c}
        for n in members:
            for neighbour, data in g[n].items():
                if neighbour not in members:
                    links[index[neighbour]] += data["weight"]
        others = [i for i in range(len(communities)) if i != small]
        # mean weight per pair; unconnected communities go to the largest one
        target = max(
            others,
            key=lambda i: (links[i] / len(communities[i]), len(communities[i]), -i),
        )
        communities[target] += communities[small]
        del communities[small]
    return communities
