from __future__ import annotations

import asyncio
import importlib
import importlib.util
import socket
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from psyexp_net.cli.main import run_demo
from psyexp_net.config import AppConfig
from psyexp_net.health.benchmark import run_benchmark
from psyexp_net.protocol.message import Message, MessageHeader

_ORIGINAL_FIND_SPEC = importlib.util.find_spec


class _FakeBroker:
    def __init__(self) -> None:
        self.bound: dict[str, _FakeSocket] = {}
        self.dealers: dict[str, _FakeSocket] = {}
        self.subscribers: dict[str, list[_FakeSocket]] = {}

    def bind(self, endpoint: str, socket: "_FakeSocket") -> None:
        self.bound[endpoint] = socket

    def connect(self, endpoint: str, socket: "_FakeSocket") -> None:
        socket.endpoint = endpoint
        if socket.kind == _FakeZmqModule.DEALER:
            identity = socket.options.get(_FakeZmqModule.IDENTITY, b"").decode("utf-8")
            self.dealers[identity] = socket
        elif socket.kind == _FakeZmqModule.SUB:
            self.subscribers.setdefault(endpoint, []).append(socket)

    async def dealer_send(self, socket: "_FakeSocket", payload: bytes) -> None:
        router = self.bound[socket.endpoint]
        identity = socket.options[_FakeZmqModule.IDENTITY]
        await router.queue.put([identity, payload])

    async def router_send(self, parts: list[bytes]) -> None:
        identity = parts[0].decode("utf-8")
        dealer = self.dealers[identity]
        await dealer.queue.put(parts[1])

    async def pub_send(self, endpoint: str, payload: bytes) -> None:
        for subscriber in self.subscribers.get(endpoint, []):
            await subscriber.queue.put(payload)


class _FakeSocket:
    def __init__(self, broker: _FakeBroker, kind: int) -> None:
        self.broker = broker
        self.kind = kind
        self.queue: asyncio.Queue = asyncio.Queue()
        self.options: dict[int, int | bytes] = {}
        self.endpoint = ""
        self.closed = False

    def bind(self, endpoint: str) -> None:
        self.endpoint = endpoint
        self.broker.bind(endpoint, self)

    def connect(self, endpoint: str) -> None:
        self.broker.connect(endpoint, self)

    def setsockopt(self, option: int, value) -> None:
        self.options[option] = value

    async def send(self, payload: bytes) -> None:
        if self.kind == _FakeZmqModule.DEALER:
            await self.broker.dealer_send(self, payload)
            return
        if self.kind == _FakeZmqModule.PUB:
            await self.broker.pub_send(self.endpoint, payload)
            return
        raise AssertionError(f"Unsupported send for kind {self.kind}")

    async def send_multipart(self, parts: list[bytes]) -> None:
        if self.kind != _FakeZmqModule.ROUTER:
            raise AssertionError("send_multipart only supported for ROUTER")
        await self.broker.router_send(parts)

    async def recv(self) -> bytes:
        return await self.queue.get()

    async def recv_multipart(self) -> list[bytes]:
        return await self.queue.get()

    def close(self, linger: int) -> None:
        del linger
        self.closed = True


class _FakePoller:
    def __init__(self) -> None:
        self._registered: list[_FakeSocket] = []

    def register(self, socket: _FakeSocket, flags: int) -> None:
        del flags
        self._registered.append(socket)

    async def poll(self, timeout_ms: int | None = None):
        deadline = None
        if timeout_ms is not None:
            deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        while True:
            ready = [(socket, _FakeZmqModule.POLLIN) for socket in self._registered if not socket.queue.empty()]
            if ready:
                return ready
            if deadline is not None and asyncio.get_running_loop().time() >= deadline:
                return []
            await asyncio.sleep(0)


class _FakeContext:
    def __init__(self, broker: _FakeBroker) -> None:
        self.broker = broker
        self.sockets: list[_FakeSocket] = []
        self.terminated = False

    def socket(self, kind: int) -> _FakeSocket:
        socket = _FakeSocket(self.broker, kind)
        self.sockets.append(socket)
        return socket

    def term(self) -> None:
        self.terminated = True


class _FakeAsyncioModule(types.ModuleType):
    def __init__(self, broker: _FakeBroker) -> None:
        super().__init__("zmq.asyncio")
        self._broker = broker

    def Context(self) -> _FakeContext:
        return _FakeContext(self._broker)

    Poller = _FakePoller


class _FakeZmqModule(types.ModuleType):
    ROUTER = 1
    DEALER = 2
    PUB = 3
    SUB = 4
    POLLIN = 5
    IDENTITY = 6
    SNDHWM = 7
    RCVHWM = 8
    LINGER = 9
    TCP_KEEPALIVE = 10
    IMMEDIATE = 11
    CONNECT_TIMEOUT = 12
    HEARTBEAT_IVL = 13
    HEARTBEAT_TIMEOUT = 14
    HEARTBEAT_TTL = 15
    ROUTER_MANDATORY = 16
    ROUTER_HANDOVER = 17
    SUBSCRIBE = 18

    def __init__(self, broker: _FakeBroker) -> None:
        super().__init__("zmq")
        self.asyncio = _FakeAsyncioModule(broker)


class _FakeServiceInfo:
    def __init__(
        self,
        *,
        type_: str,
        name: str,
        addresses: list[bytes],
        port: int,
        properties: dict[str, str],
        server: str,
    ) -> None:
        self.type_ = type_
        self.name = name
        self.addresses = addresses
        self.port = port
        self.properties = properties
        self.server = server

    def parsed_addresses(self) -> list[str]:
        return [socket.inet_ntoa(raw) for raw in self.addresses]


class _FakeZeroconf:
    registry: dict[str, _FakeServiceInfo] = {}

    def __init__(self) -> None:
        self.closed = False

    def register_service(self, info: _FakeServiceInfo) -> None:
        type(self).registry[info.name] = info

    def unregister_service(self, info: _FakeServiceInfo) -> None:
        type(self).registry.pop(info.name, None)

    def get_service_info(self, service_type: str, name: str):
        info = type(self).registry.get(name)
        if info is None or info.type_ != service_type:
            return None
        return info

    def close(self) -> None:
        self.closed = True


class _FakeServiceBrowser:
    def __init__(self, zeroconf: _FakeZeroconf, service_type: str, listener) -> None:
        self.cancelled = False
        for name, info in type(zeroconf).registry.items():
            if info.type_ == service_type:
                listener.add_service(zeroconf, service_type, name)

    def cancel(self) -> None:
        self.cancelled = True


class OptionalBackendsTests(unittest.IsolatedAsyncioTestCase):
    def _load_zmq_transport(self):
        broker = _FakeBroker()
        zmq_module = _FakeZmqModule(broker)
        return {
            "zmq": zmq_module,
            "zmq.asyncio": zmq_module.asyncio,
        }, broker

    def _load_zeroconf_service(self):
        zeroconf_module = types.ModuleType("zeroconf")
        zeroconf_module.Zeroconf = _FakeZeroconf
        zeroconf_module.ServiceInfo = _FakeServiceInfo
        zeroconf_module.ServiceBrowser = _FakeServiceBrowser
        return {"zeroconf": zeroconf_module}

    def _load_doctor_module(self):
        module = importlib.import_module("psyexp_net.health.doctor")
        return importlib.reload(module)

    def _find_spec_side_effect(self, name: str):
        if name in {"zmq", "zeroconf"}:
            return object()
        return _ORIGINAL_FIND_SPEC(name)

    async def test_zmq_transport_roundtrip_and_broadcast(self) -> None:
        modules, _broker = self._load_zmq_transport()
        with patch.dict(sys.modules, modules):
            module = importlib.import_module("psyexp_net.transport.zmq_lan")
            module = importlib.reload(module)
            config = AppConfig.from_mapping(
                {
                    "network": {
                        "host": "127.0.0.1",
                        "bind_host": "127.0.0.1",
                        "control_port": 6501,
                        "pub_port": 6502,
                    }
                }
            )
            server = module.ZmqLanTransport(config, is_server=True)
            client = module.ZmqLanTransport(
                config, is_server=False, client_id="client-01"
            )

            await server.start()
            await client.start()

            request = Message(
                header=MessageHeader.create("PING", sender_id="client-01"),
                payload={"step": "up"},
            )
            await client.send("server", request)
            server_event = await server.recv(timeout=0.01)
            self.assertEqual(server_event.peer_id, "client-01")
            self.assertEqual(server_event.message.payload["step"], "up")

            response = Message(
                header=MessageHeader.create("PONG", sender_id="server"),
                payload={"step": "down"},
            )
            await server.send("client-01", response)
            client_event = await client.recv(timeout=0.01)
            self.assertEqual(client_event.channel, "control")
            self.assertEqual(client_event.message.payload["step"], "down")

            broadcast = Message(
                header=MessageHeader.create("STATE_UPDATE", sender_id="server"),
                payload={"phase": "stimulus"},
            )
            await server.broadcast(broadcast)
            client_broadcast = await client.recv(timeout=0.01)
            self.assertEqual(client_broadcast.channel, "broadcast")
            self.assertEqual(client_broadcast.message.payload["phase"], "stimulus")

            self.assertEqual(server.get_metrics()["sent"], 1)
            self.assertEqual(server.get_metrics()["broadcast_sent"], 1)
            self.assertEqual(client.get_metrics()["sent"], 1)

            await client.stop()
            await server.stop()
            self.assertTrue(server._context is None)
            self.assertTrue(client._context is None)

    async def test_run_demo_with_zmq_backend(self) -> None:
        modules, _broker = self._load_zmq_transport()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(sys.modules, modules):
                module = importlib.import_module("psyexp_net.cli.main")
                module = importlib.reload(module)
                result = await module.run_demo(
                    AppConfig.from_mapping(
                        {
                            "logging": {"base_dir": tmpdir},
                            "network": {
                                "backend": "zmq",
                                "host": "0.0.0.0",
                                "bind_host": "0.0.0.0",
                                "control_port": 6511,
                                "pub_port": 6512,
                            },
                        }
                    )
                )
        self.assertEqual(result["backend"], "zmq")
        self.assertEqual(result["network"]["host"], "127.0.0.1")
        self.assertEqual(result["network"]["bind_host"], "127.0.0.1")
        self.assertEqual(result["result"]["response"], "space")

    async def test_zeroconf_service_register_and_discover(self) -> None:
        _FakeZeroconf.registry.clear()
        with patch.dict(sys.modules, self._load_zeroconf_service()):
            module = importlib.import_module("psyexp_net.discovery.zeroconf_service")
            module = importlib.reload(module)
            config = AppConfig.from_mapping(
                {
                    "network": {
                        "bind_host": "127.0.0.1",
                        "control_port": 7201,
                        "pub_port": 7202,
                    },
                    "experiment": {
                        "session_name": "visual-search",
                        "experiment_profile": "lab-a",
                    },
                }
            )
            service = module.ZeroconfDiscoveryService(
                config,
                service_name="primary-server",
                server_id="srv-01",
                build_info="test-build",
            )
            service.register()
            discovered = service.discover(timeout=0)

            self.assertEqual(len(discovered), 1)
            self.assertEqual(discovered[0]["service_name"], "primary-server")
            self.assertEqual(discovered[0]["server_id"], "srv-01")
            self.assertEqual(discovered[0]["control_port"], 7201)
            self.assertEqual(discovered[0]["pub_port"], 7202)
            self.assertEqual(discovered[0]["experiment_profile"], "lab-a")
            self.assertEqual(discovered[0]["host"], "127.0.0.1")

            service.unregister()
            self.assertEqual(service.discover(timeout=0), [])
            service.close()
            self.assertTrue(service._zeroconf.closed)

    async def test_run_demo_inmemory_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = await run_demo(
                AppConfig.from_mapping(
                    {
                        "logging": {"base_dir": tmpdir},
                        "network": {"backend": "inmemory"},
                    }
                )
            )
        self.assertEqual(result["backend"], "inmemory")
        self.assertEqual(result["result"]["response"], "space")

    async def test_run_server_and_client_commands_over_zmq(self) -> None:
        modules, _broker = self._load_zmq_transport()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(sys.modules, modules):
                cli_module = importlib.import_module("psyexp_net.cli.main")
                cli_module = importlib.reload(cli_module)
                config = AppConfig.from_mapping(
                    {
                        "logging": {"base_dir": tmpdir},
                        "network": {
                            "backend": "zmq",
                            "host": "0.0.0.0",
                            "bind_host": "0.0.0.0",
                            "control_port": 6531,
                            "pub_port": 6532,
                        },
                        "experiment": {
                            "required_roles": ["stimulus", "response"],
                        },
                    }
                )
                server_task = asyncio.create_task(
                    cli_module.run_server_command(
                        config,
                        duration=0.0,
                        publish_discovery=False,
                        wait_for_ready=True,
                        start_session=True,
                        session_id="S-CMD",
                        trial_id="T-CMD",
                        result_role="response",
                    )
                )
                await asyncio.sleep(0)
                stimulus_task = asyncio.create_task(
                    cli_module.run_client_command(
                        config,
                        role="stimulus",
                        client_id="stim-cmd",
                        duration=0.2,
                        report_on_trial_start=False,
                    )
                )
                response_task = asyncio.create_task(
                    cli_module.run_client_command(
                        config,
                        role="response",
                        client_id="resp-cmd",
                        duration=0.2,
                        report_on_trial_start=True,
                    )
                )
                server_result, stimulus_result, response_result = await asyncio.gather(
                    server_task, stimulus_task, response_task
                )
        self.assertEqual(server_result["session_id"], "S-CMD")
        self.assertEqual(server_result["trial_id"], "T-CMD")
        self.assertEqual(server_result["collected_result"]["response"], "space")
        self.assertEqual(server_result["collected_result"]["client_id"], "resp-cmd")
        self.assertEqual(stimulus_result["register_response"], "REGISTER_OK")
        self.assertEqual(response_result["register_response"], "REGISTER_OK")
        self.assertIn("TRIAL_START_AT", response_result["seen_messages"])

    async def test_run_benchmark_inmemory_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = await run_benchmark(
                AppConfig.from_mapping(
                    {
                        "logging": {"base_dir": tmpdir},
                        "network": {"backend": "inmemory"},
                    }
                ),
                clients=2,
                seconds=0.05,
            )
        self.assertEqual(result["backend"], "inmemory")
        self.assertGreaterEqual(result["iterations"], 1.0)
        self.assertGreaterEqual(result["data_reports"], 2.0)
        self.assertIn("avg_broadcast_ms", result)

    async def test_run_benchmark_zmq_backend(self) -> None:
        modules, _broker = self._load_zmq_transport()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(sys.modules, modules):
                benchmark_module = importlib.import_module("psyexp_net.health.benchmark")
                benchmark_module = importlib.reload(benchmark_module)
                result = await benchmark_module.run_benchmark(
                    AppConfig.from_mapping(
                        {
                            "logging": {"base_dir": tmpdir},
                            "network": {
                                "backend": "zmq",
                                "host": "0.0.0.0",
                                "bind_host": "0.0.0.0",
                                "control_port": 6521,
                                "pub_port": 6522,
                            },
                        }
                    ),
                    clients=2,
                    seconds=0.05,
                )
        self.assertEqual(result["backend"], "zmq")
        self.assertGreaterEqual(result["iterations"], 1.0)
        self.assertGreaterEqual(result["data_reports"], 2.0)
        self.assertIn("avg_broadcast_ms", result)

    async def test_doctor_manual_reachable_port(self) -> None:
        doctor_module = self._load_doctor_module()
        class _Conn:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        with patch("socket.create_connection", return_value=_Conn()):
            report = await doctor_module.NetworkDoctor(
                AppConfig.from_mapping(
                    {
                        "network": {
                            "backend": "inmemory",
                            "host": "127.0.0.1",
                            "control_port": 7555,
                            "discovery": "manual",
                        }
                    }
                )
            ).run()
        self.assertTrue(report.reachable)
        self.assertIsNone(report.handshake_ok)
        self.assertEqual(report.target, "127.0.0.1")

    async def test_doctor_zmq_zeroconf_handshake(self) -> None:
        _FakeZeroconf.registry.clear()
        modules, _broker = self._load_zmq_transport()
        zeroconf_modules = self._load_zeroconf_service()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(sys.modules, {**modules, **zeroconf_modules}):
                zmq_module = importlib.import_module("psyexp_net.transport.zmq_lan")
                zmq_module = importlib.reload(zmq_module)
                discovery_module = importlib.import_module(
                    "psyexp_net.discovery.zeroconf_service"
                )
                discovery_module = importlib.reload(discovery_module)
                doctor_module = self._load_doctor_module()
                config = AppConfig.from_mapping(
                    {
                        "logging": {"base_dir": tmpdir},
                        "network": {
                            "backend": "zmq",
                            "host": "127.0.0.1",
                            "bind_host": "127.0.0.1",
                            "control_port": 7401,
                            "pub_port": 7402,
                            "discovery": "zeroconf",
                        },
                    }
                )
                from psyexp_net.runtime.server import ExperimentServer

                server = ExperimentServer(
                    config, zmq_module.ZmqLanTransport(config, is_server=True)
                )
                discovery = discovery_module.ZeroconfDiscoveryService(
                    config, service_name="doctor-server", server_id="srv-doctor"
                )
                await server.start()
                discovery.register()

                class _Conn:
                    def __enter__(self):
                        return self

                    def __exit__(self, exc_type, exc, tb):
                        return False

                try:
                    with patch(
                        "importlib.util.find_spec",
                        side_effect=self._find_spec_side_effect,
                    ):
                        with patch("socket.create_connection", return_value=_Conn()):
                            report = await doctor_module.NetworkDoctor(config).run()
                finally:
                    discovery.close()
                    await server.shutdown()

        self.assertTrue(report.service_visible)
        self.assertTrue(report.reachable)
        self.assertTrue(report.handshake_ok)
        self.assertTrue(report.protocol_match)
        self.assertIsNotNone(report.avg_rtt_ms)
        self.assertIsNotNone(report.jitter_ms)


if __name__ == "__main__":
    unittest.main()
