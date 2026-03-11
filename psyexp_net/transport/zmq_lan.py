from __future__ import annotations

from dataclasses import dataclass

from psyexp_net.config import AppConfig
from psyexp_net.protocol.codec import JsonMessageCodec
from psyexp_net.protocol.message import Message

from .base import ReceivedMessage, TransportBackend

"""基于 pyzmq 的局域网传输后端。"""


@dataclass(slots=True)
class _ZmqMetrics:
    sent: int = 0
    received: int = 0
    broadcast_sent: int = 0


class ZmqLanTransport(TransportBackend):
    """统一封装服务端和客户端的 ZMQ 运行时。"""

    def __init__(
        self,
        config: AppConfig,
        *,
        is_server: bool,
        client_id: str | None = None,
        codec: JsonMessageCodec | None = None,
    ) -> None:
        try:
            import zmq
            import zmq.asyncio
        except ImportError as exc:
            raise RuntimeError("ZmqLanTransport requires `pyzmq`.") from exc
        if not is_server and not client_id:
            raise ValueError("client_id is required when is_server=False")
        self.config = config
        self.is_server = is_server
        self.client_id = client_id
        self.codec = codec or JsonMessageCodec()
        self.metrics = _ZmqMetrics()
        self._zmq = zmq
        self._asyncio = zmq.asyncio
        self._context = None
        self._control_socket = None
        self._broadcast_socket = None
        self._poller = None
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._context = self._asyncio.Context()
        if self.is_server:
            self._control_socket = self._context.socket(self._zmq.ROUTER)
            self._broadcast_socket = self._context.socket(self._zmq.PUB)
            self._configure_server_sockets()
            self._control_socket.bind(self._control_bind_endpoint())
            self._broadcast_socket.bind(self._broadcast_bind_endpoint())
        else:
            self._control_socket = self._context.socket(self._zmq.DEALER)
            self._broadcast_socket = self._context.socket(self._zmq.SUB)
            self._configure_client_sockets()
            self._control_socket.connect(self._control_connect_endpoint())
            self._broadcast_socket.connect(self._broadcast_connect_endpoint())
        self._poller = self._asyncio.Poller()
        self._poller.register(self._control_socket, self._zmq.POLLIN)
        if not self.is_server:
            self._poller.register(self._broadcast_socket, self._zmq.POLLIN)
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return
        linger = self.config.network.linger_ms
        if self._control_socket is not None:
            self._control_socket.close(linger)
        if self._broadcast_socket is not None:
            self._broadcast_socket.close(linger)
        if self._context is not None:
            self._context.term()
        self._context = None
        self._control_socket = None
        self._broadcast_socket = None
        self._poller = None
        self._started = False

    async def send(self, peer_id: str, message: Message) -> None:
        encoded = self.codec.encode(message)
        if self.is_server:
            await self._control_socket.send_multipart([peer_id.encode("utf-8"), encoded])
        else:
            del peer_id
            await self._control_socket.send(encoded)
        self.metrics.sent += 1

    async def broadcast(self, message: Message) -> None:
        if not self.is_server:
            await self.send("server", message)
            return
        await self._broadcast_socket.send(self.codec.encode(message))
        self.metrics.broadcast_sent += 1

    async def recv(self, timeout: float | None = None) -> ReceivedMessage | None:
        if not self._started or self._poller is None:
            return None
        timeout_ms = None if timeout is None else max(0, int(timeout * 1000))
        events = dict(await self._poller.poll(timeout_ms))
        if not events:
            return None
        if self.is_server:
            parts = await self._control_socket.recv_multipart()
            peer_id = parts[0].decode("utf-8")
            message = self.codec.decode(parts[-1])
            self.metrics.received += 1
            return ReceivedMessage(peer_id=peer_id, message=message, channel="control")
        if self._control_socket in events:
            payload = await self._control_socket.recv()
            channel = "control"
        else:
            payload = await self._broadcast_socket.recv()
            channel = "broadcast"
        self.metrics.received += 1
        return ReceivedMessage(
            peer_id="server",
            message=self.codec.decode(payload),
            channel=channel,
        )

    def get_metrics(self) -> dict[str, int]:
        return {
            "sent": self.metrics.sent,
            "received": self.metrics.received,
            "broadcast_sent": self.metrics.broadcast_sent,
        }

    def _configure_server_sockets(self) -> None:
        self._configure_common_socket(self._control_socket)
        self._configure_common_socket(self._broadcast_socket)
        self._setsockopt_if_supported(
            self._control_socket,
            "ROUTER_MANDATORY",
            self.config.network.router_mandatory,
        )
        self._setsockopt_if_supported(
            self._control_socket,
            "ROUTER_HANDOVER",
            self.config.network.router_handover,
        )

    def _configure_client_sockets(self) -> None:
        self._configure_common_socket(self._control_socket)
        self._configure_common_socket(self._broadcast_socket)
        identity = self.client_id.encode("utf-8")
        self._control_socket.setsockopt(self._zmq.IDENTITY, identity)
        if hasattr(self._zmq, "SUBSCRIBE"):
            self._broadcast_socket.setsockopt(self._zmq.SUBSCRIBE, b"")

    def _configure_common_socket(self, socket) -> None:
        socket.setsockopt(self._zmq.SNDHWM, self.config.network.send_hwm)
        socket.setsockopt(self._zmq.RCVHWM, self.config.network.recv_hwm)
        socket.setsockopt(self._zmq.LINGER, self.config.network.linger_ms)
        self._setsockopt_if_supported(
            socket, "TCP_KEEPALIVE", self.config.network.tcp_keepalive
        )
        self._setsockopt_if_supported(
            socket, "IMMEDIATE", self.config.network.immediate
        )
        self._setsockopt_if_supported(
            socket, "CONNECT_TIMEOUT", self.config.network.connect_timeout_ms
        )
        self._setsockopt_if_supported(
            socket, "HEARTBEAT_IVL", self.config.network.heartbeat_interval_ms
        )
        self._setsockopt_if_supported(
            socket, "HEARTBEAT_TIMEOUT", self.config.network.heartbeat_timeout_ms
        )
        self._setsockopt_if_supported(
            socket, "HEARTBEAT_TTL", self.config.network.heartbeat_timeout_ms
        )

    def _setsockopt_if_supported(self, socket, name: str, value: int | bool) -> None:
        option = getattr(self._zmq, name, None)
        if option is None:
            return
        if isinstance(value, bool):
            value = int(value)
        socket.setsockopt(option, value)

    def _control_bind_endpoint(self) -> str:
        return f"tcp://{self.config.network.bind_host}:{self.config.network.control_port}"

    def _broadcast_bind_endpoint(self) -> str:
        return f"tcp://{self.config.network.bind_host}:{self.config.network.pub_port}"

    def _control_connect_endpoint(self) -> str:
        return f"tcp://{self.config.network.host}:{self.config.network.control_port}"

    def _broadcast_connect_endpoint(self) -> str:
        return f"tcp://{self.config.network.host}:{self.config.network.pub_port}"
