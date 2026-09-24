from abc import ABC, abstractmethod
from typing import ClassVar

from mixmansion.core.models import Song, SongVectors
from mixmansion.shared.config import AdapterParams


class Categorizer(ABC):
    name: ClassVar[str]
    Params: ClassVar[type[AdapterParams]]
    # Can split the pool into buckets: `labels[song_id][0]` is then the song's bucket key.
    bucketable: ClassVar[bool] = False

    @abstractmethod
    def vectors(self, songs: list[Song], params: AdapterParams) -> SongVectors: ...
