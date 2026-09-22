"""Composition root: the only module that imports concrete adapters."""

import inspect
from collections.abc import Callable
from typing import Any

from mixmansion.categorizers.port import Categorizer
from mixmansion.core.usecases import MixMansion
from mixmansion.groupers.port import Grouper
from mixmansion.namers.port import PlaylistNamer
from mixmansion.plan_stores.port import PlanStore
from mixmansion.plan_stores.yaml_file import YamlFilePlanStore
from mixmansion.pool_stores.file_kv import FileKVPoolStore
from mixmansion.pool_stores.port import PoolStore
from mixmansion.retrievers.port import SongRetriever
from mixmansion.shared.config import AppSettings, ServiceOverrides, load
from mixmansion.shared.spotify import SpotifySettings
from mixmansion.writers.port import PlaylistWriter

RETRIEVERS: dict[str, type[SongRetriever]] = {}
CATEGORIZERS: dict[str, type[Categorizer]] = {}
GROUPERS: dict[str, type[Grouper]] = {}
NAMERS: dict[str, type[PlaylistNamer]] = {}
WRITERS: dict[str, type[PlaylistWriter]] = {}
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
        "spotify_settings": lambda: load(SpotifySettings, **overrides.spotify),
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
