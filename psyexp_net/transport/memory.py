from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from psyexp_net.protocol.message import Message

from .base import ReceivedMessage, TransportBackend

"""单进程内存传输后端。

主要用于测试、示例和离线开发。
"""


@dataclass(slots=True)
class _EndpointMetrics:
    sent: int = 0
    received: int = 0
    broadcast_sent: int = 0


class InMemoryHub:
    def __init__(self) -> None:
        # server 使用一个公共入口队列，client 各自持有独立接收队列。
        self._server_queue: asyncio.Queue[ReceivedMessage] = asyncio.Queue()
        self._client_queues: dict[str, asyncio.Queue[ReceivedMessage]] = {}

    def ensure_client(self, client_id: str) -> asyncio.Queue[ReceivedMessage]:
        queue = self._client_queues.get(client_id)
        if queue is None:
            queue = asyncio.Queue()
            self._client_queues[client_id] = queue
        return queue

    def remove_client(self, client_id: str) -> None:
        self._client_queues.pop(client_id, None)


@dataclass(slots=True)
class InMemoryServerTransport(TransportBackend):
    hub: InMemoryHub
    metrics: _EndpointMetrics = field(default_factory=_EndpointMetrics)

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send(self, peer_id: str, message: Message) -> None:
        # 服务端向指定客户端单播控制消息。
        queue = self.hub.ensure_client(peer_id)
        await queue.put(
            ReceivedMessage(peer_id="server", message=message, channel="control")
        )
        self.metrics.sent += 1

    async def broadcast(self, message: Message) -> None:
        # 广播通过复制投递到所有客户端队列。
        for queue in self.hub._client_queues.values():
            await queue.put(
                ReceivedMessage(peer_id="server", message=message, channel="broadcast")
            )
            self.metrics.broadcast_sent += 1

    async def recv(self, timeout: float | None = None) -> ReceivedMessage | None:
        try:
            item = await asyncio.wait_for(self.hub._server_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        self.metrics.received += 1
        return item

    def get_metrics(self) -> dict[str, int]:
        return {
            "sent": self.metrics.sent,
            "received": self.metrics.received,
            "broadcast_sent": self.metrics.broadcast_sent,
        }


@dataclass(slots=True)
class InMemoryClientTransport(TransportBackend):
    hub: InMemoryHub
    client_id: str
    metrics: _EndpointMetrics = field(default_factory=_EndpointMetrics)

    async def start(self) -> None:
        self.hub.ensure_client(self.client_id)

    async def stop(self) -> None:
        self.hub.remove_client(self.client_id)

    async def send(self, peer_id: str, message: Message) -> None:
        del peer_id
        # 客户端上行消息统一送到服务端入口队列。
        await self.hub._server_queue.put(
            ReceivedMessage(peer_id=self.client_id, message=message, channel="control")
        )
        self.metrics.sent += 1

    async def broadcast(self, message: Message) -> None:
        await self.send("server", message)

    async def recv(self, timeout: float | None = None) -> ReceivedMessage | None:
        queue = self.hub.ensure_client(self.client_id)
        try:
            item = await asyncio.wait_for(queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        self.metrics.received += 1
        return item

    def get_metrics(self) -> dict[str, int]:
        return {
            "sent": self.metrics.sent,
            "received": self.metrics.received,
            "broadcast_sent": self.metrics.broadcast_sent,
        }
