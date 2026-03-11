from __future__ import annotations

import socket
import time

from psyexp_net.config import AppConfig

"""基于 zeroconf 的服务注册与发现。"""


class _ServiceListener:
    def __init__(self) -> None:
        self.records: dict[str, object] = {}

    def add_service(self, zeroconf, service_type: str, name: str) -> None:
        info = zeroconf.get_service_info(service_type, name)
        if info is not None:
            self.records[name] = info

    def update_service(self, zeroconf, service_type: str, name: str) -> None:
        self.add_service(zeroconf, service_type, name)

    def remove_service(self, zeroconf, service_type: str, name: str) -> None:
        del zeroconf, service_type
        self.records.pop(name, None)


class ZeroconfDiscoveryService:
    """提供最小可用的 zeroconf 注册和发现能力。"""

    def __init__(
        self,
        config: AppConfig,
        *,
        service_type: str = "_psyexp-net._tcp.local.",
        service_name: str | None = None,
        server_id: str = "server",
        build_info: str = "dev",
        zeroconf_instance=None,
    ) -> None:
        try:
            import zeroconf
        except ImportError as exc:
            raise RuntimeError("ZeroconfDiscoveryService requires `zeroconf`.") from exc
        self.config = config
        self.service_type = service_type
        self.service_name = service_name or f"{server_id}-{config.experiment.session_name}"
        self.server_id = server_id
        self.build_info = build_info
        self._zeroconf_module = zeroconf
        self._zeroconf = zeroconf_instance or zeroconf.Zeroconf()
        self._registered_info = None

    def register(self) -> None:
        if self._registered_info is not None:
            return
        info = self._zeroconf_module.ServiceInfo(
            type_=self.service_type,
            name=self._full_service_name(),
            addresses=self._addresses(),
            port=self.config.network.control_port,
            properties=self._properties(),
            server=f"{socket.gethostname()}.local.",
        )
        self._zeroconf.register_service(info)
        self._registered_info = info

    def unregister(self) -> None:
        if self._registered_info is None:
            return
        self._zeroconf.unregister_service(self._registered_info)
        self._registered_info = None

    def discover(self, timeout: float = 0.5) -> list[dict[str, str | int]]:
        listener = _ServiceListener()
        browser = self._zeroconf_module.ServiceBrowser(
            self._zeroconf, self.service_type, listener
        )
        if timeout > 0:
            time.sleep(timeout)
        try:
            return [self._to_record(info) for info in listener.records.values()]
        finally:
            cancel = getattr(browser, "cancel", None)
            if callable(cancel):
                cancel()

    def close(self) -> None:
        self.unregister()
        close = getattr(self._zeroconf, "close", None)
        if callable(close):
            close()

    def _full_service_name(self) -> str:
        return f"{self.service_name}.{self.service_type}"

    def _properties(self) -> dict[str, str]:
        return {
            "service_name": self.service_name,
            "service_type": self.service_type,
            "server_id": self.server_id,
            "protocol_version": self.config.protocol.version,
            "control_port": str(self.config.network.control_port),
            "pub_port": str(self.config.network.pub_port),
            "session_name": self.config.experiment.session_name,
            "build_info": self.build_info,
            "experiment_profile": self.config.experiment.experiment_profile,
        }

    def _addresses(self) -> list[bytes]:
        host = self.config.network.bind_host
        candidates: list[str] = []
        if host not in {"0.0.0.0", "::"}:
            candidates.append(host)
        else:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                try:
                    probe.connect(("8.8.8.8", 80))
                    candidates.append(probe.getsockname()[0])
                except OSError:
                    candidates.append("127.0.0.1")
        encoded: list[bytes] = []
        for address in dict.fromkeys(candidates):
            try:
                encoded.append(socket.inet_aton(address))
            except OSError:
                continue
        return encoded or [socket.inet_aton("127.0.0.1")]

    def _to_record(self, info) -> dict[str, str | int]:
        properties = self._decode_properties(getattr(info, "properties", {}))
        addresses = self._decode_addresses(info)
        return {
            "service_name": properties.get("service_name", self.service_name),
            "service_type": self.service_type,
            "server_id": properties.get("server_id", ""),
            "protocol_version": properties.get("protocol_version", ""),
            "control_port": int(properties.get("control_port", info.port)),
            "pub_port": int(properties.get("pub_port", self.config.network.pub_port)),
            "session_name": properties.get("session_name", ""),
            "build_info": properties.get("build_info", ""),
            "experiment_profile": properties.get("experiment_profile", ""),
            "host": addresses[0] if addresses else "",
        }

    def _decode_properties(self, properties: dict[object, object]) -> dict[str, str]:
        decoded: dict[str, str] = {}
        for key, value in properties.items():
            if isinstance(key, bytes):
                key = key.decode("utf-8")
            if isinstance(value, bytes):
                value = value.decode("utf-8")
            decoded[str(key)] = str(value)
        return decoded

    def _decode_addresses(self, info) -> list[str]:
        parsed_addresses = getattr(info, "parsed_addresses", None)
        if callable(parsed_addresses):
            return list(parsed_addresses())
        addresses = []
        for raw in getattr(info, "addresses", []):
            try:
                addresses.append(socket.inet_ntoa(raw))
            except OSError:
                continue
        return addresses
