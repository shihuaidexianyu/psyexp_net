from __future__ import annotations

from psyexp_net.protocol.message import Message

from .base import ReceivedMessage, TransportBackend

"""真实局域网 ZMQ 后端的预留入口。"""


class ZmqLanTransport(TransportBackend):
    """预留给 ROUTER/DEALER + PUB/SUB 网络后端。"""

    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs
        try:
            import zmq.asyncio  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("ZmqLanTransport requires `pyzmq`.") from exc

    async def start(self) -> None:
        raise NotImplementedError(
            "ZMQ runtime wiring is reserved for the next network-backed milestone."
        )

    async def stop(self) -> None:
        return None

    async def send(self, peer_id: str, message: Message) -> None:
        del peer_id, message
        raise NotImplementedError

    async def broadcast(self, message: Message) -> None:
        del message
        raise NotImplementedError

    async def recv(self, timeout: float | None = None) -> ReceivedMessage | None:
        del timeout
        raise NotImplementedError

    def get_metrics(self) -> dict[str, int]:
        return {}
