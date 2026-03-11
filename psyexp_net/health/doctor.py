from __future__ import annotations

import importlib.util
import socket
import uuid
from dataclasses import dataclass, field
from statistics import mean

from psyexp_net.config import AppConfig
from psyexp_net.discovery.manual import ManualDiscoveryService
from psyexp_net.enums import MessageType
from psyexp_net.runtime.client import ExperimentClient
from psyexp_net.timing.jitter import compute_jitter_ms

"""网络自检实现。"""


@dataclass(slots=True)
class DoctorReport:
    hostname: str
    interfaces: list[str]
    target: str
    port: int
    discovery_mode: str
    service_visible: bool
    reachable: bool
    handshake_ok: bool | None = None
    protocol_version: str | None = None
    protocol_match: bool | None = None
    avg_rtt_ms: float | None = None
    jitter_ms: float | None = None
    clock_offset_ms: float | None = None
    dependencies: dict[str, bool] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"{self.hostname}: target {self.target}:{self.port}",
            f"discovery={self.discovery_mode}",
            f"visible={'yes' if self.service_visible else 'no'}",
            f"reachable={'yes' if self.reachable else 'no'}",
        ]
        if self.handshake_ok is not None:
            parts.append(f"handshake={'ok' if self.handshake_ok else 'failed'}")
        if self.protocol_match is not None:
            parts.append(f"protocol_match={'yes' if self.protocol_match else 'no'}")
        if self.avg_rtt_ms is not None:
            parts.append(f"avg_rtt_ms={self.avg_rtt_ms:.3f}")
        if self.jitter_ms is not None:
            parts.append(f"jitter_ms={self.jitter_ms:.3f}")
        if self.clock_offset_ms is not None:
            parts.append(f"offset_ms={self.clock_offset_ms:.3f}")
        if self.notes:
            parts.append(f"notes={'; '.join(self.notes)}")
        parts.append(f"interfaces={', '.join(self.interfaces)}")
        return "; ".join(parts)


class NetworkDoctor:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    async def run(self) -> DoctorReport:
        hostname = socket.gethostname()
        interfaces = self._collect_interfaces(hostname)
        dependencies = self._dependency_status()
        notes: list[str] = []

        discovery_record, target, port = self._resolve_target(dependencies, notes)
        protocol_version = (
            str(discovery_record.get("protocol_version"))
            if discovery_record and discovery_record.get("protocol_version")
            else None
        )
        protocol_match = (
            protocol_version == self.config.protocol.version
            if protocol_version is not None
            else None
        )
        reachable = self._check_port(target, port)

        handshake_ok: bool | None = None
        avg_rtt_ms: float | None = None
        jitter_ms: float | None = None
        clock_offset_ms: float | None = None
        if self.config.network.backend == "zmq":
            if not dependencies.get("zmq", False):
                notes.append("pyzmq not installed")
            elif reachable:
                handshake_ok, avg_rtt_ms, jitter_ms, clock_offset_ms = (
                    await self._probe_zmq_server(target, port, discovery_record)
                )
            else:
                notes.append("control port not reachable; skipped handshake probe")

        return DoctorReport(
            hostname=hostname,
            interfaces=interfaces,
            target=target,
            port=port,
            discovery_mode=self.config.network.discovery,
            service_visible=discovery_record is not None,
            reachable=reachable,
            handshake_ok=handshake_ok,
            protocol_version=protocol_version,
            protocol_match=protocol_match,
            avg_rtt_ms=avg_rtt_ms,
            jitter_ms=jitter_ms,
            clock_offset_ms=clock_offset_ms,
            dependencies=dependencies,
            notes=notes,
        )

    def _collect_interfaces(self, hostname: str) -> list[str]:
        try:
            return sorted(
                {
                    addr[4][0]
                    for addr in socket.getaddrinfo(
                        hostname, None, type=socket.SOCK_STREAM
                    )
                    if addr[4]
                }
            )
        except OSError:
            return ["127.0.0.1"]

    def _dependency_status(self) -> dict[str, bool]:
        return {
            "zmq": importlib.util.find_spec("zmq") is not None,
            "zeroconf": importlib.util.find_spec("zeroconf") is not None,
        }

    def _resolve_target(
        self, dependencies: dict[str, bool], notes: list[str]
    ) -> tuple[dict[str, str | int] | None, str, int]:
        if self.config.network.discovery == "zeroconf":
            if not dependencies.get("zeroconf", False):
                notes.append("zeroconf not installed; fallback to manual target")
                return self._manual_target()
            from psyexp_net.discovery.zeroconf_service import ZeroconfDiscoveryService

            service = ZeroconfDiscoveryService(self.config)
            try:
                records = service.discover(timeout=0)
            finally:
                service.close()
            if records:
                record = records[0]
                return (
                    record,
                    str(record.get("host") or self.config.network.host),
                    int(record.get("control_port", self.config.network.control_port)),
                )
            notes.append("zeroconf service not visible; fallback to manual target")
        return self._manual_target()

    def _manual_target(self) -> tuple[dict[str, str | int] | None, str, int]:
        host, port = ManualDiscoveryService(self.config).server_endpoint()
        return None, host, port

    def _check_port(self, target: str, port: int) -> bool:
        try:
            with socket.create_connection((target, port), timeout=0.2):
                return True
        except OSError:
            return target in {"0.0.0.0", "127.0.0.1", "localhost"}

    async def _probe_zmq_server(
        self,
        target: str,
        port: int,
        discovery_record: dict[str, str | int] | None,
    ) -> tuple[bool, float | None, float | None, float | None]:
        from psyexp_net.transport.zmq_lan import ZmqLanTransport

        probe_config = self._make_probe_config(target, port, discovery_record)
        client_id = f"doctor-{uuid.uuid4().hex[:8]}"
        client = ExperimentClient(
            probe_config,
            role="doctor",
            client_id=client_id,
            transport=ZmqLanTransport(
                probe_config, is_server=False, client_id=client_id
            ),
        )
        rtts: list[float] = []
        offsets: list[float] = []
        handshake_ok = False
        try:
            await client.connect()
            response = await client.register()
            handshake_ok = response.msg_type == MessageType.REGISTER_OK.value
            if not handshake_ok:
                return False, None, None, None
            sample_count = min(max(self.config.health.benchmark_ping_count, 1), 5)
            for _ in range(sample_count):
                offset_ms, rtt_ms = await client.sync_clock()
                offsets.append(offset_ms)
                rtts.append(rtt_ms)
            return (
                True,
                mean(rtts) if rtts else None,
                compute_jitter_ms(rtts) if rtts else None,
                mean(offsets) if offsets else None,
            )
        finally:
            await client.close()

    def _make_probe_config(
        self,
        target: str,
        port: int,
        discovery_record: dict[str, str | int] | None,
    ) -> AppConfig:
        pub_port = self.config.network.pub_port
        if discovery_record and discovery_record.get("pub_port") is not None:
            pub_port = int(discovery_record["pub_port"])
        merged = self.config.to_dict()
        merged["network"] = {
            **merged["network"],
            "host": "127.0.0.1" if target in {"0.0.0.0", "::"} else target,
            "control_port": port,
            "pub_port": pub_port,
        }
        return AppConfig.from_mapping(merged)
