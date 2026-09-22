from abc import ABC, abstractmethod
from typing import ClassVar

from mixmansion.core.models import Grouping, SimilarityGraph, Song
from mixmansion.shared.config import AdapterParams


class Grouper(ABC):
    name: ClassVar[str]
    Params: ClassVar[type[AdapterParams]]

    @abstractmethod
    def group(
        self,
        songs: list[Song],
        graphs: list[SimilarityGraph],
        weights: dict[str, float],
        params: AdapterParams,
    ) -> Grouping: ...
