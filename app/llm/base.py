from abc import ABC, abstractmethod
from typing import Any

from app.llm.tasks import LLMTask


class LLMProvider(ABC):
    @property
    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def health(self) -> dict: ...

    @abstractmethod
    async def map_sheet(self, profile, canonical_schemas, rule_suggestion): ...

    async def generate_json(
        self,
        system: str,
        payload: dict,
        *,
        task: LLMTask = LLMTask.EXCEL_MAPPING,
        request_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise RuntimeError("JSON generation is unavailable")

    async def load(self) -> None:
        return None

    async def close(self) -> None:
        return None
