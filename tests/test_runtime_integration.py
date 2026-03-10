from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from psyexp_net.config import AppConfig
from psyexp_net.logging.replay import ReplayEngine
from psyexp_net.runtime.client import ExperimentClient
from psyexp_net.runtime.server import ExperimentServer
from psyexp_net.transport.memory import (
    InMemoryClientTransport,
    InMemoryHub,
    InMemoryServerTransport,
)

"""运行时端到端集成测试。"""


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


if __name__ == "__main__":
    unittest.main()
