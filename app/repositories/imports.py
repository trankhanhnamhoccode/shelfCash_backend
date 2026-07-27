from abc import ABC, abstractmethod
from typing import Any


class ImportRepository(ABC):
    @abstractmethod
    def create(self, record: dict[str, Any]) -> None: ...

    @abstractmethod
    def get(self, import_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def update(self, import_id: str, **changes: Any) -> dict[str, Any]: ...
