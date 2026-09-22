"""In-memory fake adapters, one per port, for pipeline tests."""

from __future__ import annotations

from collections import deque

from pydantic_settings import SettingsConfigDict

from mixmansion.categorizers.port import Categorizer
from mixmansion.core.models import (
    Choice,
    Group,
    Grouping,
    Plan,
    ScoredSong,
    SimilarityGraph,
    Song,
    SongPool,
)
from mixmansion.groupers.port import Grouper
from mixmansion.namers.port import PlaylistNamer
from mixmansion.plan_stores.port import PlanStore
from mixmansion.pool_stores.port import PoolStore
from mixmansion.retrievers.port import SongRetriever
from mixmansion.shared.config import AdapterParams
from mixmansion.writers.port import PlaylistNotFound, PlaylistWriter


def tid(n: int) -> str:
    """A well-formed 22-character Spotify id."""
    return f"{n:022d}"


def _song(n: int, letter: str) -> Song:
    return Song(
        id=tid(n),
        isrc=f"ISRC{n}",
        title=f"Song {n}",
        artists=[letter],
        artist_ids=[f"{letter.lower()}1"],
        duration_ms=180000 + n * 10000,
    )


SONGS: list[Song] = [_song(i, letter) for i, letter in enumerate("ABCDEF", start=1)]


class FakeRetriever(SongRetriever):
    name = "fake"

    class Params(AdapterParams):
        model_config = SettingsConfigDict(env_prefix="MIXMANSION_RETRIEVER_FAKE_")
        source: str = "default"

    def __init__(self, songs: dict[str, list[Song]] | None = None):
        self.songs = songs if songs is not None else {"default": SONGS}

    def retrieve(self, params: AdapterParams) -> list[Song]:
        return [s.model_copy(deep=True) for s in self.songs[params.source]]

    def choices(self, field: str, params: dict) -> list[Choice]:
        return [Choice(value=key, label=key) for key in self.songs]


class FakeCategorizer(Categorizer):
    """A categorizer that splits songs by index parity."""

    name = "fake"

    class Params(AdapterParams):
        model_config = SettingsConfigDict(env_prefix="MIXMANSION_CATEGORIZER_FAKE_")
        dimension: str = "fake"

    def similarity(self, songs: list[Song], params: AdapterParams) -> SimilarityGraph:
        edges: dict[tuple[str, str], float] = {}
        labels: dict[str, list[str]] = {}
        clusters: dict[int, list[str]] = {0: [], 1: []}
        for i, song in enumerate(songs):
            cluster = i % 2
            clusters[cluster].append(song.id)
            labels[song.id] = ["even" if cluster == 0 else "odd"]
        for ids in clusters.values():
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    a, b = sorted((ids[i], ids[j]))
                    edges[(a, b)] = 1.0
        return SimilarityGraph(
            dimension=params.dimension,
            edges=edges,
            covered={s.id for s in songs},
            labels=labels,
        )


class FakeGrouper(Grouper):
    name = "fake"

    class Params(AdapterParams):
        model_config = SettingsConfigDict(env_prefix="MIXMANSION_GROUPER_FAKE_")

    def group(
        self,
        songs: list[Song],
        graphs: list[SimilarityGraph],
        weights: dict[str, float],
        params: AdapterParams,
    ) -> Grouping:
        adjacency: dict[str, set[str]] = {s.id: set() for s in songs}
        for graph in graphs:
            for a, b in graph.edges:
                adjacency.setdefault(a, set()).add(b)
                adjacency.setdefault(b, set()).add(a)

        seen: set[str] = set()
        groups: list[Group] = []
        unassigned: list[str] = []
        for song in songs:
            if song.id in seen:
                continue
            if not adjacency.get(song.id):
                seen.add(song.id)
                unassigned.append(song.id)
                continue
            component: list[str] = []
            queue = deque([song.id])
            seen.add(song.id)
            while queue:
                current = queue.popleft()
                component.append(current)
                for neighbor in adjacency.get(current, ()):
                    if neighbor not in seen:
                        seen.add(neighbor)
                        queue.append(neighbor)
            groups.append(Group(songs=[ScoredSong(song_id=sid, score=1.0) for sid in component]))
        return Grouping(groups=groups, unassigned=unassigned)


class FakeNamer(PlaylistNamer):
    name = "fake"

    class Params(AdapterParams):
        model_config = SettingsConfigDict(env_prefix="MIXMANSION_NAMER_FAKE_")

    def name_groups(
        self, groups: list[list[Song]], labels: dict[str, list[str]], params: AdapterParams
    ) -> list[tuple[str, str]]:
        return [(f"Group {i + 1}", f"{len(g)} songs") for i, g in enumerate(groups)]


class FakeWriter(PlaylistWriter):
    name = "fake"

    class Params(AdapterParams):
        model_config = SettingsConfigDict(env_prefix="MIXMANSION_WRITER_FAKE_")

    def __init__(self):
        self.calls: list[tuple] = []
        self.playlists: dict[str, list[str]] = {}
        self._next = 1

    def create(self, name: str, description: str, params: AdapterParams) -> str:
        self.calls.append(("create", name))
        playlist_id = f"pl{self._next}"
        self._next += 1
        self.playlists[playlist_id] = []
        return playlist_id

    def replace_tracks(self, playlist_id: str, track_ids: list[str], params: AdapterParams) -> None:
        if playlist_id not in self.playlists:
            raise PlaylistNotFound(playlist_id)
        self.calls.append(("replace", playlist_id, track_ids))
        self.playlists[playlist_id] = list(track_ids)


class FakePoolStore(PoolStore):
    name = "fake"

    class Params(AdapterParams):
        model_config = SettingsConfigDict(env_prefix="MIXMANSION_POOL_STORE_FAKE_")

    def __init__(self):
        self.pools: dict[str, SongPool] = {}

    def load(self, pool_id: str) -> SongPool:
        pool = self.pools.get(pool_id)
        return pool.model_copy(deep=True) if pool else SongPool()

    def save(self, pool_id: str, pool: SongPool) -> None:
        self.pools[pool_id] = pool.model_copy(deep=True)

    def delete(self, pool_id: str) -> None:
        self.pools.pop(pool_id, None)


class FakePlanStore(PlanStore):
    name = "fake"

    class Params(AdapterParams):
        model_config = SettingsConfigDict(env_prefix="MIXMANSION_PLAN_STORE_FAKE_")

    def __init__(self):
        self.plans: dict[str, Plan] = {}
        self._next = 1

    def load(self, ref: str) -> Plan:
        if ref not in self.plans:
            raise KeyError(ref)
        return self.plans[ref].model_copy(deep=True)

    def save(self, plan: Plan, ref: str | None = None) -> str:
        key = ref or f"plan{self._next}"
        if ref is None:
            self._next += 1
        self.plans[key] = plan.model_copy(deep=True)
        return key
