from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psyexp_net.config import AppConfig
from psyexp_net.runtime.client import ExperimentClient
from psyexp_net.runtime.server import ExperimentServer
from psyexp_net.transport.memory import (
    InMemoryClientTransport,
    InMemoryHub,
    InMemoryServerTransport,
)

"""单进程内存后端演示。"""


async def main() -> None:
    config = AppConfig.from_mapping(
        {
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

    @response.on("TRIAL_START_AT")
    async def on_trial_start(message) -> None:
        # 模拟响应端在 trial 开始时立即上报一次结果。
        await response.report_result(
            {
                "trial_id": message.payload["trial_id"],
                "response": "space",
                "apply_ts": message.payload["execute_at"],
            }
        )

    await server.start()
    for client in (stimulus, response):
        await client.connect()
        await client.register()
        await client.sync_clock()
        await client.ready()

    await server.wait_until_clients_ready()
    await server.start_session("S001")
    await server.arm_trial("T001")
    await server.broadcast_state({"phase": "stimulus"})
    execute_at = await server.start_trial("T001", at="+120ms")
    result = await server.collect("response")
    await server.end_trial("T001")
    await server.stop_session()

    for client in (stimulus, response):
        await client.close()
    await server.shutdown()

    print(
        json.dumps(
            {
                "execute_at": execute_at,
                "result": result,
                "log_dir": str(server.recorder.session_dir),
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
