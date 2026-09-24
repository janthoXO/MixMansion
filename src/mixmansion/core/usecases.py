"""Use cases: the primary port. Interaction surfaces (CLI, later REST) call only `MixMansion`."""

import inspect
import logging
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from mixmansion.core.models import (
    Choice,
    Group,
    Grouping,
    Plan,
    PlanTrack,
    Playlist,
    ScoredSong,
    Song,
    SongPool,
    SongVectors,
)
from mixmansion.shared.config import AdapterParams, AppSettings, load
from mixmansion.writers.port import PlaylistNotFound

log = logging.getLogger(__name__)

PORTS = ("retriever", "categorizer", "grouper", "namer", "writer", "pool_store", "plan_store")


class MixMansionError(Exception):
    """A user-facing error: shown as one line by the interaction surface."""


class AdapterInfo(BaseModel):
    name: str
    description: str = ""  # first line of the adapter's docstring
    params_schema: dict
    bucketable: bool = False  # categorizers only: can split the pool into buckets


class AdapterSpec(BaseModel):
    name: str
    params: dict = {}


class CategorizerSpec(BaseModel):
    weight: float
    params: dict = {}


class PlanSpec(BaseModel):
    buckets: list[AdapterSpec] | None = None  # default: MIXMANSION_BUCKETS
    categorizers: dict[str, CategorizerSpec] | None = None  # default: MIXMANSION_WEIGHTS
    grouper: AdapterSpec | None = None  # default: MIXMANSION_GROUPER
    namer: AdapterSpec | None = None  # default: MIXMANSION_NAMER


class PoolSummary(BaseModel):
    added: int
    total: int
    duplicates: int  # retrieved songs that were already in the pool


class ApplyResult(BaseModel):
    created: list[str] = []
    updated: list[str] = []
    tracks: dict[str, int] = {}  # playlist name -> track count


class MixMansion:
    def __init__(
        self,
        settings: AppSettings,
        adapters: dict[str, dict[str, type]],
        make: Callable[[type], Any],
    ):
        """`adapters` maps port -> adapter name -> class; `make` instantiates a class with its
        dependencies (see `bootstrap.build_app`)."""
        self.settings = settings
        self._adapters = adapters
        self._make = make

    # ── adapters ────────────────────────────────────────────────────────────

    def _port(self, port: str) -> dict[str, type]:
        if port not in self._adapters:
            raise MixMansionError(f"unknown port {port!r}, expected one of {', '.join(PORTS)}")
        return self._adapters[port]

    def _cls(self, port: str, name: str) -> type:
        adapters = self._port(port)
        if name not in adapters:
            known = ", ".join(adapters) or "none registered"
            raise MixMansionError(f"unknown {port} {name!r} (available: {known})")
        return adapters[name]

    def _adapter(self, port: str, name: str, params: dict) -> tuple[Any, AdapterParams]:
        cls = self._cls(port, name)
        return self._make(cls), load(cls.Params, **params)

    def list_adapters(self, port: str) -> list[AdapterInfo]:
        return [
            AdapterInfo(
                name=name,
                description=(inspect.getdoc(cls) or "").split("\n")[0],
                params_schema=cls.Params.model_json_schema(),
                bucketable=getattr(cls, "bucketable", False),
            )
            for name, cls in self._port(port).items()
        ]

    def adapter_choices(self, port: str, adapter: str, field: str, params: dict) -> list[Choice]:
        instance = self._make(self._cls(port, adapter))
        return instance.choices(field, params) if hasattr(instance, "choices") else []

    # ── pool ────────────────────────────────────────────────────────────────

    def _pool_store(self):
        return self._adapter("pool_store", self.settings.pool_store, {})[0]

    def add_to_pool(self, pool_id: str, retriever: str, params: dict) -> PoolSummary:
        adapter, p = self._adapter("retriever", retriever, params)
        songs = adapter.retrieve(p)
        store = self._pool_store()
        pool = store.load(pool_id)
        added = pool.add(songs)
        store.save(pool_id, pool)
        return PoolSummary(added=added, total=len(pool.songs), duplicates=len(songs) - added)

    def get_pool(self, pool_id: str) -> SongPool:
        return self._pool_store().load(pool_id)

    def clear_pool(self, pool_id: str) -> None:
        self._pool_store().delete(pool_id)

    # ── plan ────────────────────────────────────────────────────────────────

    def _plan_store(self):
        return self._adapter("plan_store", self.settings.plan_store, {})[0]

    def build_plan(self, pool_id: str, spec: PlanSpec, ref: str | None = None) -> str:
        pool = self.get_pool(pool_id)
        if not pool.songs:
            raise MixMansionError(f"pool {pool_id!r} is empty, add songs first")
        songs = pool.songs
        by_id = {s.id: s for s in songs}

        buckets = (
            spec.buckets
            if spec.buckets is not None
            else [AdapterSpec(name=n) for n in self.settings.buckets]
        )
        categorizers = (
            spec.categorizers
            if spec.categorizers is not None
            else {name: CategorizerSpec(weight=w) for name, w in self.settings.weights.items()}
        )
        labels: dict[str, list[str]] = {}

        def categorize(name: str, params: dict) -> tuple[SongVectors, dict]:
            adapter, p = self._adapter("categorizer", name, params)
            dim = adapter.vectors(songs, p)
            for song_id, tags in dim.labels.items():
                labels[song_id] = labels.get(song_id, []) + [
                    t for t in tags if t not in labels.get(song_id, [])
                ]
            return dim, p.model_dump(mode="json")

        splits, split_used = [], {}
        for b in buckets:
            if not getattr(self._cls("categorizer", b.name), "bucketable", False):
                raise MixMansionError(f"categorizer {b.name!r} can't split the pool into buckets")
            dim, split_used[b.name] = categorize(b.name, b.params)
            splits.append(dim)

        dimensions, weights, used = [], {}, {}
        for name, cat in categorizers.items():
            # within a bucket every song shares the bucket's value, so weighing it adds nothing
            if cat.weight <= 0 or name in split_used:
                continue
            dim, used[name] = categorize(name, cat.params)
            dimensions.append(dim)
            weights[dim.dimension] = cat.weight
        if not dimensions and not splits:
            raise MixMansionError("pick a bucket or a categorizer with a weight > 0")

        # songs without a value for a bucket categorizer share the bucket `None`
        bucketed: dict[tuple, list[Song]] = {}
        for s in songs:
            key = tuple((d.labels.get(s.id) or [None])[0] for d in splits)
            bucketed.setdefault(key, []).append(s)

        g_spec = spec.grouper or AdapterSpec(name=self.settings.grouper)
        grouper, g_params = self._adapter("grouper", g_spec.name, g_spec.params)
        grouping = Grouping(groups=[])
        for bucket in bucketed.values():
            if dimensions:
                ids = {s.id for s in bucket}
                part = grouper.group(bucket, [d.subset(ids) for d in dimensions], weights, g_params)
            else:  # buckets only: each bucket is one playlist
                part = Grouping(
                    groups=[Group(songs=[ScoredSong(song_id=s.id, score=1.0) for s in bucket])]
                )
            grouping.groups += part.groups
            grouping.unassigned += part.unassigned

        n_spec = spec.namer or AdapterSpec(name=self.settings.namer)
        namer, n_params = self._adapter("namer", n_spec.name, n_spec.params)
        groups = [[by_id[s.song_id] for s in g.songs] for g in grouping.groups]
        names = namer.name_groups(groups, labels, n_params)

        def track(song: Song, score: float | None = None) -> PlanTrack:
            return PlanTrack(
                id=song.id,
                artist=", ".join(song.artists),
                title=song.title,
                score=None if score is None else round(score, 3),
                tags=labels.get(song.id, []),
            )

        plan = Plan(
            generated={
                "pool_id": pool_id,
                "pool_size": len(songs),
                "buckets": split_used,
                "weights": weights,
                "categorizers": used,
                "grouper": {g_spec.name: g_params.model_dump(mode="json")},
                "namer": {n_spec.name: n_params.model_dump(mode="json")},
            },
            playlists=[
                Playlist(
                    name=name,
                    description=description[:300],
                    tracks=[track(by_id[s.song_id], s.score) for s in group.songs],
                )
                for (name, description), group in zip(names, grouping.groups, strict=True)
            ],
            unassigned=[track(by_id[i]) for i in grouping.unassigned],
        )

        missing = set(by_id) - _placed(plan)
        if missing:
            raise RuntimeError(
                f"invariant broken: grouper {g_spec.name!r} dropped {len(missing)} songs"
            )
        return self._plan_store().save(plan, ref)

    def get_plan(self, ref: str) -> Plan:
        plan = self._plan_store().load(ref)
        self._warn_orphans(plan)
        return plan

    def _warn_orphans(self, plan: Plan) -> None:
        """Warn when a song from the generated pool is now in no playlist."""
        pool_id = plan.generated.get("pool_id")
        if not pool_id:
            return
        placed = {t.id for p in plan.playlists for t in p.tracks}
        orphans = [s for s in self.get_pool(pool_id).songs if s.id not in placed]
        if orphans:
            log.warning(
                "%d pool songs are in no playlist, e.g. %s",
                len(orphans),
                ", ".join(f"{s.artists[0]} – {s.title}" for s in orphans[:3]),
            )

    def apply_plan(self, ref: str) -> ApplyResult:
        store = self._plan_store()
        plan = store.load(ref)
        if not plan.approved:
            raise MixMansionError("plan is not approved, set `approved: true` in it first")
        writer, p = self._adapter("writer", self.settings.writer, {})
        result = ApplyResult()
        for playlist in plan.playlists:
            ids = [t.id for t in playlist.tracks]
            if playlist.spotify_id:
                try:
                    writer.replace_tracks(playlist.spotify_id, ids, p)
                    result.updated.append(playlist.name)
                    result.tracks[playlist.name] = len(ids)
                    continue
                except PlaylistNotFound:
                    log.info("playlist %r was deleted, recreating it", playlist.name)
            playlist.spotify_id = writer.create(playlist.name, playlist.description, p)
            store.save(plan, ref)  # right away, so a crash never leads to duplicates
            writer.replace_tracks(playlist.spotify_id, ids, p)
            result.created.append(playlist.name)
            result.tracks[playlist.name] = len(ids)
        return result


def _placed(plan: Plan) -> set[str]:
    return {t.id for p in plan.playlists for t in p.tracks} | {t.id for t in plan.unassigned}
