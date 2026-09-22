"""Composition root: the only module that imports concrete adapters."""

from mixmansion.categorizers.port import Categorizer
from mixmansion.groupers.port import Grouper
from mixmansion.namers.port import PlaylistNamer
from mixmansion.plan_stores.port import PlanStore
from mixmansion.pool_stores.port import PoolStore
from mixmansion.retrievers.port import SongRetriever
from mixmansion.writers.port import PlaylistWriter

RETRIEVERS: dict[str, type[SongRetriever]] = {}
CATEGORIZERS: dict[str, type[Categorizer]] = {}
GROUPERS: dict[str, type[Grouper]] = {}
NAMERS: dict[str, type[PlaylistNamer]] = {}
WRITERS: dict[str, type[PlaylistWriter]] = {}
POOL_STORES: dict[str, type[PoolStore]] = {}
PLAN_STORES: dict[str, type[PlanStore]] = {}
