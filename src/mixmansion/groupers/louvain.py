"""Louvain grouper: fuse the weighted dimensions, find communities, soft-assign songs to them."""

import networkx as nx
import numpy as np
from pydantic import Field
from pydantic_settings import SettingsConfigDict

from mixmansion.core.models import Group, Grouping, ScoredSong, Song, SongVectors
from mixmansion.groupers.port import Grouper
from mixmansion.shared.config import AdapterParams


def cosine(vectors: np.ndarray) -> np.ndarray:
    """Pairwise cosine similarity of mean-centered rows, clipped to [0, 1].

    Centering calibrates the dimensions against each other: raw text embeddings sit around
    0.8 for any pair while sparse genre vectors sit near 0, so without it equal weights would
    not mean equal influence. Afterwards 0 means "no more alike than the pool's average".
    """
    centered = vectors - vectors.mean(axis=0)
    norms = np.linalg.norm(centered, axis=1, keepdims=True)
    unit = centered / np.where(norms == 0, 1, norms)
    return np.clip(unit @ unit.T, 0.0, 1.0)


def fuse(ids: list[str], dimensions: list[SongVectors], weights: dict[str, float]) -> np.ndarray:
    """Dense songs × songs similarity: the weighted mean over only the dimensions covering both.

    A song without data in one dimension (e.g. no lyrics) is grouped by the others instead of
    pushed away. Pairs no dimension covers, and the diagonal, are 0.
    """
    # ponytail: dense n×n float32 matrices, fine up to ~10k songs; go sparse/ANN beyond that
    index = {song_id: i for i, song_id in enumerate(ids)}
    num = np.zeros((len(ids), len(ids)), dtype=np.float32)
    den = np.zeros_like(num)
    for dim in dimensions:
        w = weights.get(dim.dimension, 0)
        if w <= 0 or not dim.ids:
            continue
        rows = [index[i] for i in dim.ids]
        num[np.ix_(rows, rows)] += w * cosine(dim.vectors)
        den[np.ix_(rows, rows)] += w
    fused = np.divide(num, den, out=np.zeros_like(num), where=den > 0)
    np.fill_diagonal(fused, 0.0)
    return fused


class LouvainGrouper(Grouper):
    name = "louvain"

    class Params(AdapterParams):
        model_config = SettingsConfigDict(env_prefix="MIXMANSION_GROUPER_LOUVAIN_")
        k: int = Field(15, ge=1, description="Neighbours kept per song for community detection")
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
        dimensions: list[SongVectors],
        weights: dict[str, float],
        params: Params,
    ) -> Grouping:
        ids = [s.id for s in songs]
        fused = fuse(ids, dimensions, weights)

        g = nx.Graph()
        g.add_nodes_from(range(len(ids)))
        for i, row in enumerate(fused):
            g.add_weighted_edges_from(
                (i, int(j), float(row[j])) for j in np.argsort(-row)[: params.k] if row[j] > 0
            )
        connected = [n for n in g if g.degree(n) > 0]
        unassigned = [ids[n] for n in g if g.degree(n) == 0]
        if not connected:
            return Grouping(groups=[], unassigned=unassigned)

        found = nx.community.louvain_communities(
            g.subgraph(connected), weight="weight", resolution=params.resolution, seed=params.seed
        )
        communities = sorted((sorted(c) for c in found), key=lambda c: (-len(c), c))
        communities = _merge_small(fused, communities, params.min_size)

        # affinity of a song to a community: mean similarity to its other members
        members = np.zeros((len(ids), len(communities)))
        for c, community in enumerate(communities):
            members[community, c] = 1
        sizes = members.sum(axis=0) - members  # a song doesn't count towards itself
        affinity = np.divide(fused @ members, sizes, out=np.zeros_like(members), where=sizes > 0)

        groups: list[list[ScoredSong]] = [[] for _ in communities]
        for song in connected:
            best = affinity[song].max()
            ranked = sorted(range(len(communities)), key=lambda c: (-affinity[song, c], c))
            for c in ranked[: params.max_memberships]:
                if affinity[song, c] > 0 and affinity[song, c] >= (1 - params.tau) * best:
                    groups[c].append(ScoredSong(song_id=ids[song], score=float(affinity[song, c])))

        return Grouping(
            groups=[
                Group(songs=sorted(m, key=lambda s: (-s.score, s.song_id))) for m in groups if m
            ],
            unassigned=unassigned,
        )


def _merge_small(fused: np.ndarray, communities: list[list[int]], min_size: int) -> list[list[int]]:
    """Fold the smallest undersized community into the one it's most similar to, repeatedly."""
    communities = [list(c) for c in communities]
    while len(communities) > 1:
        small = min(range(len(communities)), key=lambda i: (len(communities[i]), i))
        if len(communities[small]) >= min_size:
            break
        others = [i for i in range(len(communities)) if i != small]
        # mean similarity per pair; ties go to the largest community
        target = max(
            others,
            key=lambda i: (
                fused[np.ix_(communities[small], communities[i])].mean(),
                len(communities[i]),
                -i,
            ),
        )
        communities[target] += communities[small]
        del communities[small]
    return communities
