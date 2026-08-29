"""Central bridge for synchronous business services calling LLM providers."""
from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Any


def generate_json_sync(provider: Any, system: str, payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Use provider-owned lifecycle when available; retain compatibility for test doubles."""
    sync_method = getattr(provider, "generate_json_sync", None)
    if callable(sync_method):
        return sync_method(system, payload, **kwargs)

    coroutine = provider.generate_json(system, payload, **kwargs)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coroutine)).result()
