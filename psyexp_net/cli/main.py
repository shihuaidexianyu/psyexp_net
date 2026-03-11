from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from psyexp_net.config import AppConfig
from psyexp_net.discovery.manual import ManualDiscoveryService
from psyexp_net.discovery.zeroconf_service import ZeroconfDiscoveryService
from psyexp_net.health.benchmark import run_inmemory_benchmark
from psyexp_net.health.doctor import NetworkDoctor
from psyexp_net.logging.replay import ReplayEngine
from psyexp_net.runtime.client import ExperimentClient
from psyexp_net.runtime.server import ExperimentServer
from psyexp_net.transport.memory import (
    InMemoryClientTransport,
    InMemoryHub,
    InMemoryServerTransport,
)
from psyexp_net.transport.zmq_lan import ZmqLanTransport

"""命令行入口。

当前主要提供本地 demo、自检、benchmark 和日志检查能力。
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="psyexp-net")
    parser.add_argument("--config", type=Path, default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor")

    benchmark = subparsers.add_parser("benchmark")
    benchmark.add_argument("--clients", type=int, default=4)
    benchmark.add_argument("--seconds", type=float, default=2.0)

    demo = subparsers.add_parser("demo")
    demo.add_argument(
        "--backend",
        choices=("inmemory", "zmq"),
        default=None,
        help="Run demo with the selected transport backend.",
    )

    replay = subparsers.add_parser("replay")
    replay.add_argument("session_dir", type=Path)

    inspect_log = subparsers.add_parser("inspect-log")
    inspect_log.add_argument("path", type=Path)

    return parser


def load_config(path: Path | None) -> AppConfig:
    return AppConfig.from_file(path) if path else AppConfig()


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    config = load_config(args.config)
    config = apply_backend_override(config, getattr(args, "backend", None))

    if args.command == "doctor":
        report = asyncio.run(NetworkDoctor(config).run())
        print(report.summary())
        return
    if args.command == "benchmark":
        result = asyncio.run(
            run_inmemory_benchmark(config, clients=args.clients, seconds=args.seconds)
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    if args.command == "demo":
        print(json.dumps(asyncio.run(run_demo(config)), indent=2, ensure_ascii=False))
        return
    if args.command == "replay":
        replay = ReplayEngine(args.session_dir)
        print(json.dumps(replay.summary(), indent=2, ensure_ascii=False))
        return
    if args.command == "inspect-log":
        data = [
            json.loads(line)
            for line in args.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        print(
            json.dumps(
                {"rows": len(data), "kinds": sorted({item["kind"] for item in data})},
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    parser.error(f"Unknown command {args.command}")


def apply_backend_override(config: AppConfig, backend: str | None) -> AppConfig:
    if backend is None:
        return config
    merged = config.to_dict()
    merged.setdefault("network", {})["backend"] = backend
    return AppConfig.from_mapping(merged)


def _config_with_network_overrides(
    config: AppConfig, **network_overrides: object
) -> AppConfig:
    merged = config.to_dict()
    network = dict(merged["network"])
    network.update(network_overrides)
    merged["network"] = network
    return AppConfig.from_mapping(merged)


def _resolve_local_demo_config(config: AppConfig) -> AppConfig:
    if config.network.backend != "zmq":
        return config
    bind_host = config.network.bind_host
    host = config.network.host
    overrides: dict[str, object] = {}
    if bind_host in {"0.0.0.0", "::"}:
        overrides["bind_host"] = "127.0.0.1"
    if host in {"0.0.0.0", "::"}:
        overrides["host"] = "127.0.0.1"
    if overrides:
        config = _config_with_network_overrides(config, **overrides)
    if config.network.discovery == "zeroconf":
        discovery = ZeroconfDiscoveryService(
            config, service_name="psyexp-demo", server_id="demo"
        )
        try:
            discovery.register()
            records = discovery.discover(timeout=0)
        finally:
            discovery.close()
        if records and records[0]["host"]:
            return _config_with_network_overrides(config, host=records[0]["host"])
        return config
    host, _port = ManualDiscoveryService(config).server_endpoint()
    if host in {"0.0.0.0", "::"}:
        return _config_with_network_overrides(config, host="127.0.0.1")
    return config


def _build_demo_stack(
    config: AppConfig,
) -> tuple[ExperimentServer, ExperimentClient, ExperimentClient]:
    if config.network.backend == "inmemory":
        hub = InMemoryHub()
        server = ExperimentServer(config, InMemoryServerTransport(hub))
        stimulus_transport = InMemoryClientTransport(hub, "stim-01")
        response_transport = InMemoryClientTransport(hub, "resp-01")
    elif config.network.backend == "zmq":
        server = ExperimentServer(config, ZmqLanTransport(config, is_server=True))
        stimulus_transport = ZmqLanTransport(
            config, is_server=False, client_id="stim-01"
        )
        response_transport = ZmqLanTransport(
            config, is_server=False, client_id="resp-01"
        )
    else:
        raise ValueError(f"Unsupported backend: {config.network.backend}")
    stimulus = ExperimentClient(
        config,
        role="stimulus",
        client_id="stim-01",
        transport=stimulus_transport,
    )
    response = ExperimentClient(
        config,
        role="response",
        client_id="resp-01",
        transport=response_transport,
    )
    return server, stimulus, response


async def run_demo(config: AppConfig) -> dict[str, object]:
    # demo 固定使用 stimulus/response 两个角色，演示最小实验流。
    config = AppConfig.from_mapping(
        {
            **config.to_dict(),
            "experiment": {"required_roles": ["stimulus", "response"]},
        }
    )
    config = _resolve_local_demo_config(config)
    server, stimulus, response = _build_demo_stack(config)

    @response.on("TRIAL_START_AT")
    async def on_trial_start(message) -> None:
        await response.report_result(
            {"trial_id": message.payload["trial_id"], "response": "space"}
        )

    await server.start()
    for client in (stimulus, response):
        await client.connect()
        await client.register()
        await client.sync_clock()
        await client.ready()
    await server.wait_until_clients_ready(timeout=2.0)
    await server.start_session("DEMO")
    await server.arm_trial("T001")
    execute_at = await server.start_trial("T001", at="+50ms")
    result = await server.collect("response")
    await server.stop_session()
    for client in (stimulus, response):
        await client.close()
    session_dir = str(server.recorder.session_dir)
    await server.shutdown()
    return {
        "backend": config.network.backend,
        "network": asdict(config.network),
        "execute_at": execute_at,
        "result": result,
        "log_dir": session_dir,
    }
