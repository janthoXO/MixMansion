from abc import ABC, abstractmethod
from typing import ClassVar

from mixmansion.core.models import Song, SongVectors
from mixmansion.shared.config import AdapterParams


class Categorizer(ABC):
    name: ClassVar[str]
    Params: ClassVar[type[AdapterParams]]

    @abstractmethod
    def vectors(self, songs: list[Song], params: AdapterParams) -> SongVectors: ...
