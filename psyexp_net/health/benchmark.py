from __future__ import annotations

import asyncio
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

"""内存后端 benchmark。"""


async def run_inmemory_benchmark(
    config: AppConfig, clients: int, seconds: float
) -> dict[str, float]:
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
    await server.start()
    for peer in peers:
        await peer.connect()
        await peer.register()
        await peer.sync_clock()
        await peer.ready()
    await server.wait_until_clients_ready([peer.role for peer in peers], timeout=2.0)
    latencies: list[float] = []
    deadline = time.monotonic() + seconds
    iteration = 0
    while time.monotonic() < deadline:
        iteration += 1
        started = time.monotonic()
        await server.start_trial(f"trial-{iteration}", at="+20ms")
        # 这里统计一次控制命令 fan-out 加 ACK 回收的总耗时。
        latencies.append((time.monotonic() - started) * 1000)
    for peer in peers:
        await peer.close()
    await server.shutdown()
    return {
        "iterations": float(iteration),
        "avg_control_rtt_ms": mean(latencies) if latencies else 0.0,
        "max_control_rtt_ms": max(latencies) if latencies else 0.0,
    }
