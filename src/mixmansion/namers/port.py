from abc import ABC, abstractmethod
from typing import ClassVar

from mixmansion.core.models import Song
from mixmansion.shared.config import AdapterParams


class PlaylistNamer(ABC):
    name: ClassVar[str]
    Params: ClassVar[type[AdapterParams]]

    @abstractmethod
    def name_groups(
        self, groups: list[list[Song]], labels: dict[str, list[str]], params: AdapterParams
    ) -> list[tuple[str, str]]:
        """(name, description) per group, same order."""
