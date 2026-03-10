from __future__ import annotations

from dataclasses import dataclass

from psyexp_net.config import AppConfig

"""手工发现服务。

在没有 zeroconf 时，直接从配置里读取服务端地址。
"""


@dataclass(slots=True)
class ManualDiscoveryService:
    config: AppConfig

    def server_endpoint(self) -> tuple[str, int]:
        return self.config.network.host, self.config.network.control_port
