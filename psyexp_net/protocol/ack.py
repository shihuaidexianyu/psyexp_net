from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass, field

"""ACK 跟踪和消息去重。"""


@dataclass(slots=True)
class PendingAck:
    msg_id: str
    peer_id: str
    created_at: float
    timeout_at: float
    retries: int = 0
    future: asyncio.Future | None = None


@dataclass(slots=True)
class PendingAckManager:
    timeout_ms: int
    pending: dict[tuple[str, str], PendingAck] = field(default_factory=dict)

    def add(
        self, msg_id: str, peer_id: str, future: asyncio.Future | None = None
    ) -> PendingAck:
        """登记待确认消息。"""
        now = time.monotonic()
        entry = PendingAck(
            msg_id=msg_id,
            peer_id=peer_id,
            created_at=now,
            timeout_at=now + self.timeout_ms / 1000,
            future=future,
        )
        self.pending[(msg_id, peer_id)] = entry
        return entry

    def resolve(self, reply_to: str, peer_id: str, value: object | None = None) -> bool:
        """收到 ACK 后完成等待任务。"""
        entry = self.pending.pop((reply_to, peer_id), None)
        if entry is None:
            return False
        if entry.future is not None and not entry.future.done():
            entry.future.set_result(value)
        return True

    def expire(self) -> list[PendingAck]:
        """提取所有已超时的 ACK 条目。"""
        now = time.monotonic()
        expired = [entry for entry in self.pending.values() if entry.timeout_at <= now]
        for entry in expired:
            self.pending.pop((entry.msg_id, entry.peer_id), None)
        return expired


class MessageDeduplicator:
    """使用 LRU 方式记录最近处理过的消息 ID。"""

    def __init__(self, max_size: int = 512) -> None:
        self.max_size = max_size
        self._items: OrderedDict[str, None] = OrderedDict()

    def seen(self, msg_id: str) -> bool:
        if msg_id in self._items:
            self._items.move_to_end(msg_id)
            return True
        self._items[msg_id] = None
        if len(self._items) > self.max_size:
            self._items.popitem(last=False)
        return False
