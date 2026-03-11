from __future__ import annotations

import time
from statistics import mean

from psyexp_net.config import AppConfig
from psyexp_net.runtime.client import ExperimentClient
from psyexp_net.runtime.server import ExperimentServer
from psyexp_net.transport.memory import (
    InMemoryClientTransport,
    InMemoryHub,
    InMemoryServerTransport,
)
from psyexp_net.transport.zmq_lan import ZmqLanTransport

"""benchmark 实现。"""


def _config_with_network_overrides(
    config: AppConfig, **network_overrides: object
) -> AppConfig:
    merged = config.to_dict()
    network = dict(merged["network"])
    network.update(network_overrides)
    merged["network"] = network
    return AppConfig.from_mapping(merged)


def normalize_local_network_config(config: AppConfig) -> AppConfig:
    if config.network.backend != "zmq":
        return config
    overrides: dict[str, object] = {}
    if config.network.bind_host in {"0.0.0.0", "::"}:
        overrides["bind_host"] = "127.0.0.1"
    if config.network.host in {"0.0.0.0", "::"}:
        overrides["host"] = "127.0.0.1"
    if not overrides:
        return config
    return _config_with_network_overrides(config, **overrides)


def _build_stack(
    config: AppConfig, clients: int
) -> tuple[ExperimentServer, list[ExperimentClient]]:
    if config.network.backend == "inmemory":
        hub = InMemoryHub()
        server = ExperimentServer(config, InMemoryServerTransport(hub))
        peers = [
            ExperimentClient(
                config,
                role=f"role-{index}",
                client_id=f"client-{index}",
                transport=InMemoryClientTransport(hub, f"client-{index}"),
            )
            for index in range(clients)
        ]
        return server, peers
    if config.network.backend == "zmq":
        server = ExperimentServer(config, ZmqLanTransport(config, is_server=True))
        peers = [
            ExperimentClient(
                config,
                role=f"role-{index}",
                client_id=f"client-{index}",
                transport=ZmqLanTransport(
                    config, is_server=False, client_id=f"client-{index}"
                ),
            )
            for index in range(clients)
        ]
        return server, peers
    raise ValueError(f"Unsupported backend: {config.network.backend}")


async def run_benchmark(config: AppConfig, clients: int, seconds: float) -> dict[str, float | str]:
    config = normalize_local_network_config(config)
    server, peers = _build_stack(config, clients)
    for peer in peers:
        @peer.on("TRIAL_START_AT")
        async def on_trial_start(message, _peer=peer) -> None:
            await _peer.report_result(
                {
                    "trial_id": message.payload["trial_id"],
                    "client_id": _peer.client_id,
                }
            )

    await server.start()
    try:
        for peer in peers:
            await peer.connect()
            await peer.register()
            await peer.sync_clock()
            await peer.ready()
        await server.wait_until_clients_ready([peer.role for peer in peers], timeout=2.0)

        control_latencies: list[float] = []
        broadcast_latencies: list[float] = []
        sync_rtts = [peer.rtt_ms for peer in peers]
        sync_offsets = [peer.offset_ms for peer in peers]
        data_reports = 0
        deadline = time.monotonic() + seconds
        iteration = 0
        while time.monotonic() < deadline:
            iteration += 1
            trial_id = f"trial-{iteration}"

            started = time.monotonic()
            await server.start_trial(trial_id, at="+20ms")
            for peer in peers:
                await server.collect(peer.role, timeout=1.0)
                data_reports += 1
            control_latencies.append((time.monotonic() - started) * 1000)

            broadcast_started = time.monotonic()
            await server.broadcast_state({"iteration": iteration})
            broadcast_latencies.append((time.monotonic() - broadcast_started) * 1000)
        elapsed = max(seconds, 0.001)
        return {
            "backend": config.network.backend,
            "client_count": float(clients),
            "iterations": float(iteration),
            "avg_control_rtt_ms": mean(control_latencies) if control_latencies else 0.0,
            "max_control_rtt_ms": max(control_latencies) if control_latencies else 0.0,
            "avg_broadcast_ms": mean(broadcast_latencies) if broadcast_latencies else 0.0,
            "max_broadcast_ms": max(broadcast_latencies) if broadcast_latencies else 0.0,
            "avg_sync_rtt_ms": mean(sync_rtts) if sync_rtts else 0.0,
            "avg_clock_offset_ms": mean(sync_offsets) if sync_offsets else 0.0,
            "data_reports": float(data_reports),
            "data_reports_per_sec": data_reports / elapsed,
        }
    finally:
        for peer in peers:
            await peer.close()
        await server.shutdown()


async def run_inmemory_benchmark(
    config: AppConfig, clients: int, seconds: float
) -> dict[str, float | str]:
    config = _config_with_network_overrides(config, backend="inmemory")
    return await run_benchmark(config, clients, seconds)
