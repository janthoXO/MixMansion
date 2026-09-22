from abc import ABC, abstractmethod
from typing import ClassVar

from mixmansion.core.models import Choice, Song
from mixmansion.shared.config import AdapterParams


class SongRetriever(ABC):
    name: ClassVar[str]
    Params: ClassVar[type[AdapterParams]]

    @abstractmethod
    def retrieve(self, params: AdapterParams) -> list[Song]: ...

    def choices(self, field: str, params: dict) -> list[Choice]:
        """Options for a param that aren't known in advance (your playlists, search hits).

        [] = free input.
        """
        return []
