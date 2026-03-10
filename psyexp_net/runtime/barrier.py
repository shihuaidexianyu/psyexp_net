from __future__ import annotations

import asyncio
import time

from .registry import ClientRegistry

"""实验级 barrier。

用于等待所有必要角色进入 ready/running 状态。
"""


class BarrierManager:
    def __init__(self, registry: ClientRegistry) -> None:
        self.registry = registry

    async def wait_until_ready(
        self, required_roles: list[str], timeout: float
    ) -> list[str]:
        # MVP 用轻量轮询即可，避免过早引入更复杂的同步原语。
        deadline = time.monotonic() + timeout
        while True:
            missing = self.registry.missing_roles(required_roles)
            if not missing:
                return []
            if time.monotonic() >= deadline:
                return missing
            await asyncio.sleep(0.02)
