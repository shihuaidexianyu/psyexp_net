from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from psyexp_net.config import AppConfig
from psyexp_net.enums import ClientStatus
from psyexp_net.logging.replay import ReplayEngine
from psyexp_net.protocol.message import Message
from psyexp_net.runtime.client import ExperimentClient
from psyexp_net.runtime.server import ExperimentServer
from psyexp_net.transport.base import ReceivedMessage, TransportBackend
from psyexp_net.transport.memory import (
    InMemoryClientTransport,
    InMemoryHub,
    InMemoryServerTransport,
)

"""运行时端到端集成测试。"""


class FlakyHeartbeatTransport(TransportBackend):
    def __init__(self, inner: InMemoryClientTransport) -> None:
        self.inner = inner
        self.start_count = 0
        self._armed = False
        self._failed = False

    def arm_heartbeat_failure(self) -> None:
        self._armed = True

    async def start(self) -> None:
        self.start_count += 1
        await self.inner.start()

    async def stop(self) -> None:
        await self.inner.stop()

    async def send(self, peer_id: str, message: Message) -> None:
        if (
            self._armed
            and not self._failed
            and message.msg_type == "HEARTBEAT"
        ):
            self._failed = True
            raise RuntimeError("simulated heartbeat failure")
        await self.inner.send(peer_id, message)

    async def broadcast(self, message: Message) -> None:
        await self.inner.broadcast(message)

    async def recv(self, timeout: float | None = None) -> ReceivedMessage | None:
        return await self.inner.recv(timeout=timeout)

    def get_metrics(self) -> dict[str, int]:
        return self.inner.get_metrics()


class RuntimeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_client_trial_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = AppConfig.from_mapping(
                {
                    "logging": {"base_dir": tmpdir},
                    "experiment": {"required_roles": ["stimulus", "response"]},
                }
            )
            hub = InMemoryHub()
            server = ExperimentServer(config, InMemoryServerTransport(hub))
            stimulus = ExperimentClient(
                config,
                role="stimulus",
                client_id="stim-01",
                transport=InMemoryClientTransport(hub, "stim-01"),
            )
            response = ExperimentClient(
                config,
                role="response",
                client_id="resp-01",
                transport=InMemoryClientTransport(hub, "resp-01"),
            )
            seen_states: list[dict[str, str]] = []

            @stimulus.on("STATE_UPDATE")
            async def on_state(message) -> None:
                seen_states.append(message.payload)

            @response.on("TRIAL_START_AT")
            async def on_trial_start(message) -> None:
                # 用结果回传验证控制命令、客户端执行和服务端汇聚链路。
                await response.report_result(
                    {
                        "trial_id": message.payload["trial_id"],
                        "button": "space",
                    }
                )

            await server.start()
            for client in (stimulus, response):
                await client.connect()
                await client.register()
                await client.sync_clock()
                await client.ready()

            await server.wait_until_clients_ready(timeout=2.0)
            await server.start_session("S001")
            await server.arm_trial("T001")
            await server.broadcast_state({"phase": "stimulus"})
            await server.start_trial("T001", at="+20ms")
            result = await server.collect("response", timeout=1.0)
            await server.end_trial("T001")
            await server.stop_session()

            self.assertEqual(result["trial_id"], "T001")
            self.assertEqual(result["button"], "space")
            self.assertEqual(seen_states[-1]["phase"], "stimulus")

            session_dir = Path(server.recorder.session_dir)
            await stimulus.close()
            await response.close()
            await server.shutdown()

            summary = ReplayEngine(session_dir).summary()
            self.assertGreater(summary["event_count"], 0)
            self.assertIn("send", summary["kinds"])

    async def test_server_marks_client_disconnected_after_heartbeat_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = AppConfig.from_mapping(
                {
                    "logging": {"base_dir": tmpdir},
                    "network": {
                        "heartbeat_interval_ms": 20,
                        "heartbeat_timeout_ms": 80,
                    },
                }
            )
            hub = InMemoryHub()
            server = ExperimentServer(config, InMemoryServerTransport(hub))
            client = ExperimentClient(
                config,
                role="stimulus",
                client_id="stim-timeout",
                transport=InMemoryClientTransport(hub, "stim-timeout"),
            )

            await server.start()
            try:
                await client.connect()
                await client.register()
                await client.sync_clock()
                await client.ready()
                await client.close()
                await asyncio.sleep(0.2)

                self.assertEqual(
                    server.registry.get("stim-timeout").current_status,
                    ClientStatus.DISCONNECTED,
                )
            finally:
                await server.shutdown()

    async def test_client_reconnects_after_heartbeat_send_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = AppConfig.from_mapping(
                {
                    "logging": {"base_dir": tmpdir},
                    "network": {
                        "heartbeat_interval_ms": 20,
                        "heartbeat_timeout_ms": 120,
                    },
                }
            )
            hub = InMemoryHub()
            server = ExperimentServer(config, InMemoryServerTransport(hub))
            transport = FlakyHeartbeatTransport(
                InMemoryClientTransport(hub, "stim-reconnect")
            )
            client = ExperimentClient(
                config,
                role="stimulus",
                client_id="stim-reconnect",
                transport=transport,
            )

            await server.start()
            try:
                await client.connect()
                await client.register()
                await client.sync_clock()
                await client.ready()
                transport.arm_heartbeat_failure()
                await asyncio.sleep(0.2)

                self.assertGreaterEqual(transport.start_count, 2)
                self.assertEqual(client.status, ClientStatus.READY)
                self.assertEqual(
                    server.registry.get("stim-reconnect").current_status,
                    ClientStatus.READY,
                )
            finally:
                await client.close()
                await server.shutdown()

    async def test_late_client_receives_session_snapshot_on_register(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = AppConfig.from_mapping(
                {
                    "logging": {"base_dir": tmpdir},
                }
            )
            hub = InMemoryHub()
            server = ExperimentServer(config, InMemoryServerTransport(hub))
            client = ExperimentClient(
                config,
                role="stimulus",
                client_id="stim-late",
                transport=InMemoryClientTransport(hub, "stim-late"),
            )

            await server.start()
            try:
                await server.start_session("S-LATE")
                await server.arm_trial("T-LATE")
                await server.start_trial("T-LATE", at="+20ms")

                await client.connect()
                response = await client.register()

                self.assertEqual(response.msg_type, "REGISTER_OK")
                self.assertEqual(client.session.session_id, "S-LATE")
                self.assertEqual(client.session.state, "RUNNING")
                self.assertEqual(client.session.current_trial_id, "T-LATE")
                self.assertEqual(client.session.current_phase, "running")
                self.assertTrue(client.registry_snapshot)
            finally:
                await client.close()
                await server.shutdown()

    async def test_reregister_same_client_refreshes_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = AppConfig.from_mapping(
                {
                    "logging": {"base_dir": tmpdir},
                }
            )
            hub = InMemoryHub()
            server = ExperimentServer(config, InMemoryServerTransport(hub))
            client = ExperimentClient(
                config,
                role="stimulus",
                client_id="stim-refresh",
                transport=InMemoryClientTransport(hub, "stim-refresh"),
            )

            await server.start()
            try:
                await client.connect()
                first = await client.register()
                await server.start_session("S-REFRESH")
                second = await client.register()

                self.assertEqual(first.msg_type, "REGISTER_OK")
                self.assertEqual(second.msg_type, "REGISTER_OK")
                self.assertTrue(second.payload["reconnected"])
                self.assertEqual(client.session.session_id, "S-REFRESH")
                self.assertEqual(client.session.state, "RUNNING")
            finally:
                await client.close()
                await server.shutdown()


if __name__ == "__main__":
    unittest.main()
