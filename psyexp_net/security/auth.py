from __future__ import annotations

from psyexp_net.config import SecurityConfig
from psyexp_net.errors import AuthenticationError

"""认证入口。

MVP 默认信任局域网，但保留共享密钥校验能力。
"""


class TrustedLanAuthenticator:
    def __init__(self, config: SecurityConfig) -> None:
        self.config = config

    def validate(self, client_id: str, client_secret: str | None = None) -> None:
        if not self.config.require_secret:
            return
        expected = self.config.shared_secrets.get(client_id)
        if expected is None or expected != client_secret:
            raise AuthenticationError(f"Authentication failed for {client_id}")
