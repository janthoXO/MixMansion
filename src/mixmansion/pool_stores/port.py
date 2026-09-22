from abc import ABC, abstractmethod
from typing import ClassVar

from mixmansion.core.models import SongPool
from mixmansion.shared.config import AdapterParams


class PoolStore(ABC):
    name: ClassVar[str]
    Params: ClassVar[type[AdapterParams]]

    @abstractmethod
    def load(self, pool_id: str) -> SongPool:
        """Empty pool if it doesn't exist."""

    @abstractmethod
    def save(self, pool_id: str, pool: SongPool) -> None: ...

    @abstractmethod
    def delete(self, pool_id: str) -> None: ...
