from abc import ABC, abstractmethod
from typing import ClassVar

from mixmansion.core.models import Plan
from mixmansion.shared.config import AdapterParams


class PlanStore(ABC):
    name: ClassVar[str]
    Params: ClassVar[type[AdapterParams]]

    @abstractmethod
    def load(self, ref: str) -> Plan: ...

    @abstractmethod
    def save(self, plan: Plan, ref: str | None = None) -> str:
        """Returns the ref (path or id)."""
