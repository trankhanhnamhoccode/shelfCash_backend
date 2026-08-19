from abc import ABC, abstractmethod
from typing import Any


class LLMProvider(ABC):
    @property
    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def health(self) -> dict: ...

    @abstractmethod
    async def map_sheet(self, profile, canonical_schemas, rule_suggestion): ...

    async def generate_json(self, system: str, payload: dict, *, max_new_tokens: int | None = None) -> dict[str, Any]:
        raise RuntimeError("JSON generation is unavailable")

    async def load(self) -> None:
        return None

    async def close(self) -> None:
        return None
