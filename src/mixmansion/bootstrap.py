"""Composition root: the only module that imports concrete adapters."""

import inspect
from collections.abc import Callable
from typing import Any

from mixmansion.categorizers.genre import GenreCategorizer
from mixmansion.categorizers.mood import MoodCategorizer
from mixmansion.categorizers.port import Categorizer
from mixmansion.core.usecases import MixMansion
from mixmansion.groupers.louvain import LouvainGrouper
from mixmansion.groupers.port import Grouper
from mixmansion.namers.llm import LLMNamer
from mixmansion.namers.port import PlaylistNamer
from mixmansion.plan_stores.port import PlanStore
from mixmansion.plan_stores.yaml_file import YamlFilePlanStore
from mixmansion.pool_stores.file_kv import FileKVPoolStore
from mixmansion.pool_stores.port import PoolStore
from mixmansion.retrievers.playlist import PlaylistRetriever
from mixmansion.retrievers.port import SongRetriever
from mixmansion.retrievers.search import SearchRetriever
from mixmansion.shared.config import AppSettings, ServiceOverrides, load
from mixmansion.shared.llm import LLMService
from mixmansion.shared.spotify import SpotifyService, SpotifySettings
from mixmansion.writers.port import PlaylistWriter
from mixmansion.writers.spotify import SpotifyWriter

RETRIEVERS: dict[str, type[SongRetriever]] = {
    "playlist": PlaylistRetriever,
    "search": SearchRetriever,
}
CATEGORIZERS: dict[str, type[Categorizer]] = {"mood": MoodCategorizer, "genre": GenreCategorizer}
GROUPERS: dict[str, type[Grouper]] = {"louvain": LouvainGrouper}
NAMERS: dict[str, type[PlaylistNamer]] = {"llm": LLMNamer}
WRITERS: dict[str, type[PlaylistWriter]] = {"spotify": SpotifyWriter}
POOL_STORES: dict[str, type[PoolStore]] = {"file_kv": FileKVPoolStore}
PLAN_STORES: dict[str, type[PlanStore]] = {"yaml_file": YamlFilePlanStore}


def registries() -> dict[str, dict[str, type]]:
    return {
        "retriever": RETRIEVERS,
        "categorizer": CATEGORIZERS,
        "grouper": GROUPERS,
        "namer": NAMERS,
        "writer": WRITERS,
        "pool_store": POOL_STORES,
        "plan_store": PLAN_STORES,
    }


def build_app(overrides: ServiceOverrides | None = None) -> MixMansion:
    """Wire the use cases. Values in `overrides` beat env and .env for service settings.

    Adapters declare their dependencies as constructor parameters, named after the keys of
    `services`. Services are built lazily, once, when the first adapter needs them.
    """
    overrides = overrides or ServiceOverrides()
    settings = load(AppSettings)
    services: dict[str, Callable[[], Any]] = {
        "settings": lambda: settings,
        "spotify": lambda: SpotifyService(
            load(SpotifySettings, **overrides.spotify), settings.workspace
        ),
        "llm": lambda: LLMService(settings.workspace),
    }
    built: dict[str, Any] = {}

    def make(cls: type) -> Any:
        kwargs = {}
        for dep in inspect.signature(cls).parameters:
            if dep not in services:
                raise TypeError(f"{cls.__name__} needs unknown service {dep!r}")
            if dep not in built:
                built[dep] = services[dep]()
            kwargs[dep] = built[dep]
        return cls(**kwargs)

    return MixMansion(settings, registries(), make)
