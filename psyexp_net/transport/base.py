from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from psyexp_net.protocol.message import Message

"""传输层抽象接口。"""


@dataclass(slots=True)
class ReceivedMessage:
    peer_id: str
    message: Message
    channel: str = "control"


class TransportBackend(ABC):
    """所有传输后端都需要遵循的统一接口。"""

    @abstractmethod
    async def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def send(self, peer_id: str, message: Message) -> None:
        raise NotImplementedError

    @abstractmethod
    async def broadcast(self, message: Message) -> None:
        raise NotImplementedError

    @abstractmethod
    async def recv(self, timeout: float | None = None) -> ReceivedMessage | None:
        raise NotImplementedError

    @abstractmethod
    def get_metrics(self) -> dict[str, int]:
        raise NotImplementedError
