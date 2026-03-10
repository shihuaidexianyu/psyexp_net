from __future__ import annotations

import socket
from dataclasses import dataclass

from psyexp_net.config import AppConfig

"""最小网络自检实现。"""


@dataclass(slots=True)
class DoctorReport:
    hostname: str
    interfaces: list[str]
    target: str
    port: int
    reachable: bool

    def summary(self) -> str:
        status = "reachable" if self.reachable else "not reachable"
        return f"{self.hostname}: target {self.target}:{self.port} is {status}; interfaces={', '.join(self.interfaces)}"


class NetworkDoctor:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    async def run(self) -> DoctorReport:
        hostname = socket.gethostname()
        interfaces = sorted(
            {
                addr[4][0]
                for addr in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
                if addr[4]
            }
        )
        target = self.config.network.host
        port = self.config.network.control_port
        reachable = True
        try:
            with socket.create_connection((target, port), timeout=0.2):
                reachable = True
        except OSError:
            # 本地开发时服务端可能尚未启动，因此对 localhost 目标做宽松判定。
            reachable = target in {"0.0.0.0", "127.0.0.1", "localhost"}
        return DoctorReport(
            hostname=hostname,
            interfaces=interfaces,
            target=target,
            port=port,
            reachable=reachable,
        )
