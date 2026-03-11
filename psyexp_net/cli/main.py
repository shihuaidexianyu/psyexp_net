from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from psyexp_net.config import AppConfig
from psyexp_net.discovery.manual import ManualDiscoveryService
from psyexp_net.discovery.zeroconf_service import ZeroconfDiscoveryService
from psyexp_net.health.benchmark import run_benchmark
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

    doctor = subparsers.add_parser("doctor")
    doctor.add_argument("--backend", choices=("inmemory", "zmq"), default=None)

    benchmark = subparsers.add_parser("benchmark")
    benchmark.add_argument("--clients", type=int, default=4)
    benchmark.add_argument("--seconds", type=float, default=2.0)
    benchmark.add_argument(
        "--backend",
        choices=("inmemory", "zmq"),
        default=None,
        help="Run benchmark with the selected transport backend.",
    )

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

    server = subparsers.add_parser("server")
    server_subparsers = server.add_subparsers(dest="server_command", required=True)
    server_start = server_subparsers.add_parser("start")
    server_start.add_argument("--backend", choices=("zmq",), default=None)
    server_start.add_argument("--duration", type=float, default=None)
    server_start.add_argument("--publish-discovery", action="store_true")
    server_start.add_argument("--wait-for-ready", action="store_true")
    server_start.add_argument("--start-session", action="store_true")
    server_start.add_argument("--session-id", default="S001")
    server_start.add_argument("--trial-id", default="T001")
    server_start.add_argument("--result-role", default="response")

    client = subparsers.add_parser("client")
    client_subparsers = client.add_subparsers(dest="client_command", required=True)
    client_connect = client_subparsers.add_parser("connect")
    client_connect.add_argument("--backend", choices=("zmq",), default=None)
    client_connect.add_argument("--role", required=True)
    client_connect.add_argument("--client-id", required=True)
    client_connect.add_argument("--duration", type=float, default=None)
    client_connect.add_argument("--report-on-trial-start", action="store_true")

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
        result = asyncio.run(run_benchmark(config, clients=args.clients, seconds=args.seconds))
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
    if args.command == "server":
        if args.server_command == "start":
            print(
                json.dumps(
                    asyncio.run(
                        run_server_command(
                            config,
                            duration=args.duration,
                            publish_discovery=args.publish_discovery,
                            wait_for_ready=args.wait_for_ready,
                            start_session=args.start_session,
                            session_id=args.session_id,
                            trial_id=args.trial_id,
                            result_role=args.result_role,
                        )
                    ),
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return
    if args.command == "client":
        if args.client_command == "connect":
            print(
                json.dumps(
                    asyncio.run(
                        run_client_command(
                            config,
                            role=args.role,
                            client_id=args.client_id,
                            duration=args.duration,
                            report_on_trial_start=args.report_on_trial_start,
                        )
                    ),
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


def _normalize_loopback_network(config: AppConfig) -> AppConfig:
    bind_host = config.network.bind_host
    host = config.network.host
    overrides: dict[str, object] = {}
    if bind_host in {"0.0.0.0", "::"}:
        overrides["bind_host"] = "127.0.0.1"
    if host in {"0.0.0.0", "::"}:
        overrides["host"] = "127.0.0.1"
    if overrides:
        return _config_with_network_overrides(config, **overrides)
    return config


def _resolve_local_demo_config(config: AppConfig) -> AppConfig:
    if config.network.backend != "zmq":
        return config
    config = _normalize_loopback_network(config)
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


def _build_server(config: AppConfig) -> ExperimentServer:
    if config.network.backend == "zmq":
        return ExperimentServer(config, ZmqLanTransport(config, is_server=True))
    raise ValueError(f"Unsupported server backend: {config.network.backend}")


def _build_client(config: AppConfig, *, role: str, client_id: str) -> ExperimentClient:
    if config.network.backend == "zmq":
        return ExperimentClient(
            config,
            role=role,
            client_id=client_id,
            transport=ZmqLanTransport(config, is_server=False, client_id=client_id),
        )
    raise ValueError(f"Unsupported client backend: {config.network.backend}")


async def _sleep_or_wait_forever(duration: float | None) -> None:
    if duration is None:
        await asyncio.Event().wait()
    elif duration > 0:
        await asyncio.sleep(duration)


async def run_server_command(
    config: AppConfig,
    *,
    duration: float | None,
    publish_discovery: bool,
    wait_for_ready: bool,
    start_session: bool,
    session_id: str,
    trial_id: str,
    result_role: str,
) -> dict[str, Any]:
    if config.network.backend != "zmq":
        raise ValueError("server command currently requires network.backend=zmq")
    config = _normalize_loopback_network(config)
    server = _build_server(config)
    discovery = None
    if publish_discovery or config.network.discovery == "zeroconf":
        discovery = ZeroconfDiscoveryService(
            config, service_name="psyexp-server", server_id="server"
        )
    await server.start()
    if discovery is not None:
        discovery.register()
    collected: dict[str, Any] | None = None
    try:
        if wait_for_ready:
            await server.wait_until_clients_ready(timeout=5.0)
        if start_session:
            await server.start_session(session_id)
            await server.arm_trial(trial_id)
            await server.broadcast_state({"phase": "stimulus"})
            await server.start_trial(trial_id, at="+50ms")
            with_result = result_role in {client.role for client in server.registry.all()}
            if with_result:
                collected = await server.collect(result_role, timeout=2.0)
            await server.end_trial(trial_id)
            await server.stop_session()
        await _sleep_or_wait_forever(duration)
    finally:
        if discovery is not None:
            discovery.close()
        session_dir = str(server.recorder.session_dir)
        registry = server.registry.snapshot()
        await server.shutdown()
    return {
        "backend": config.network.backend,
        "network": asdict(config.network),
        "session_id": session_id if start_session else None,
        "trial_id": trial_id if start_session else None,
        "clients": registry,
        "collected_result": collected,
        "log_dir": session_dir,
    }


async def run_client_command(
    config: AppConfig,
    *,
    role: str,
    client_id: str,
    duration: float | None,
    report_on_trial_start: bool,
) -> dict[str, Any]:
    if config.network.backend != "zmq":
        raise ValueError("client command currently requires network.backend=zmq")
    config = _normalize_loopback_network(config)
    client = _build_client(config, role=role, client_id=client_id)
    seen_messages: list[str] = []

    @client.on("SESSION_START")
    async def on_session_start(message) -> None:
        seen_messages.append(message.msg_type)

    @client.on("TRIAL_ARM")
    async def on_trial_arm(message) -> None:
        seen_messages.append(message.msg_type)

    @client.on("TRIAL_START_AT")
    async def on_trial_start(message) -> None:
        seen_messages.append(message.msg_type)
        if report_on_trial_start:
            await client.report_result(
                {
                    "trial_id": message.payload["trial_id"],
                    "response": "space",
                    "client_id": client.client_id,
                }
            )

    @client.on("STATE_UPDATE")
    async def on_state_update(message) -> None:
        seen_messages.append(message.msg_type)

    await client.connect()
    try:
        register_response = await client.register()
        await client.sync_clock()
        await client.ready()
        await _sleep_or_wait_forever(duration)
        return {
            "backend": config.network.backend,
            "network": asdict(config.network),
            "client_id": client.client_id,
            "role": client.role,
            "status": client.status,
            "register_response": register_response.msg_type,
            "offset_ms": client.offset_ms,
            "rtt_ms": client.rtt_ms,
            "seen_messages": seen_messages,
        }
    finally:
        await client.close()


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
