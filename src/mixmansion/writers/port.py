from abc import ABC, abstractmethod
from typing import ClassVar

from mixmansion.shared.config import AdapterParams


class PlaylistNotFound(Exception): ...


class PlaylistWriter(ABC):
    name: ClassVar[str]
    Params: ClassVar[type[AdapterParams]]

    @abstractmethod
    def create(self, name: str, description: str, params: AdapterParams) -> str:
        """Returns the new playlist id."""

    @abstractmethod
    def replace_tracks(self, playlist_id: str, track_ids: list[str], params: AdapterParams) -> None:
        """Raises PlaylistNotFound if the playlist no longer exists."""
