from __future__ import annotations

import pytest

from app.llm.runtime import generate_json_sync
from app.llm.tasks import LLMTask


def test_generate_json_sync_prefers_gateway_owned_sync_method():
    class Gateway:
        def __init__(self):
            self.calls = []

        def generate_json_sync(self, system, payload, **kwargs):
            self.calls.append((system, payload, kwargs))
            return {"result": "owner-loop"}

        async def generate_json(self, *_args, **_kwargs):
            raise AssertionError("gateway-owned sync method must be used")

    gateway = Gateway()
    context = {"decision_run_id": "run-1"}

    assert generate_json_sync(
        gateway, "system", {"fact": 1},
        task=LLMTask.DECISION_NARRATIVE,
        request_context=context,
    ) == {"result": "owner-loop"}
    assert gateway.calls == [(
        "system", {"fact": 1},
        {"task": LLMTask.DECISION_NARRATIVE, "request_context": context},
    )]


@pytest.mark.asyncio
async def test_generate_json_sync_handles_async_test_double_from_running_loop():
    class AsyncProvider:
        async def generate_json(self, system, payload, **kwargs):
            return {"system": system, "payload": payload, "task": kwargs["task"].value}

    assert generate_json_sync(
        AsyncProvider(), "system", {"fact": 1}, task=LLMTask.DECISION_NARRATIVE,
    ) == {"system": "system", "payload": {"fact": 1}, "task": "decision_narrative"}
