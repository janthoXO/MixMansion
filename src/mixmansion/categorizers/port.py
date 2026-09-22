from abc import ABC, abstractmethod
from typing import ClassVar

from mixmansion.core.models import SimilarityGraph, Song
from mixmansion.shared.config import AdapterParams


class Categorizer(ABC):
    name: ClassVar[str]
    Params: ClassVar[type[AdapterParams]]

    @abstractmethod
    def similarity(self, songs: list[Song], params: AdapterParams) -> SimilarityGraph: ...
