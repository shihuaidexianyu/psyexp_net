from __future__ import annotations

import time
from dataclasses import dataclass, field

from psyexp_net.enums import ClientStatus
from psyexp_net.errors import DuplicateClientError

"""服务端客户端注册表。"""


@dataclass(slots=True)
class ClientInfo:
    client_id: str
    role: str
    host: str = "localhost"
    process_info: dict[str, str] = field(default_factory=dict)
    protocol_version: str = "1.0"
    capabilities: list[str] = field(default_factory=list)
    connected_since: float = field(default_factory=time.monotonic)
    last_seen: float = field(default_factory=time.monotonic)
    current_status: str = ClientStatus.CONNECTED
    offset_estimate_ms: float = 0.0
    rtt_ms: float = 0.0
    queue_health: dict[str, float] = field(default_factory=dict)

    def touch(self) -> None:
        self.last_seen = time.monotonic()


class ClientRegistry:
    def __init__(self) -> None:
        self._clients: dict[str, ClientInfo] = {}

    def register(self, info: ClientInfo) -> ClientInfo:
        # 已断开的 client_id 允许重连，其他情况视为重复注册。
        existing = self._clients.get(info.client_id)
        if (
            existing is not None
            and existing.current_status != ClientStatus.DISCONNECTED
        ):
            raise DuplicateClientError(f"Duplicate client id: {info.client_id}")
        self._clients[info.client_id] = info
        return info

    def update_status(self, client_id: str, status: str) -> None:
        if client_id not in self._clients:
            return
        self._clients[client_id].current_status = status
        self._clients[client_id].touch()

    def update_sync(self, client_id: str, *, offset_ms: float, rtt_ms: float) -> None:
        if client_id not in self._clients:
            return
        info = self._clients[client_id]
        info.offset_estimate_ms = offset_ms
        info.rtt_ms = rtt_ms
        info.touch()

    def get(self, client_id: str) -> ClientInfo | None:
        return self._clients.get(client_id)

    def all(self) -> list[ClientInfo]:
        return list(self._clients.values())

    def clients_for_role(self, role: str) -> list[ClientInfo]:
        return [client for client in self._clients.values() if client.role == role]

    def missing_roles(self, required_roles: list[str]) -> list[str]:
        """返回 barrier 还缺失的角色。"""
        missing: list[str] = []
        for role in required_roles:
            matching = [
                item
                for item in self.clients_for_role(role)
                if item.current_status in {ClientStatus.READY, ClientStatus.RUNNING}
            ]
            if not matching:
                missing.append(role)
        return missing

    def snapshot(self) -> list[dict[str, str | float]]:
        return [
            {
                "client_id": client.client_id,
                "role": client.role,
                "status": client.current_status,
                "offset_estimate_ms": client.offset_estimate_ms,
                "rtt_ms": client.rtt_ms,
            }
            for client in self._clients.values()
        ]
