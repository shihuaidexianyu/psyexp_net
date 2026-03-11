# psyexp_net

`psyexp_net` 是一个面向心理学与认知科学实验场景的 Python 通讯库。首版实现聚焦单服务器、多客户端实验控制，提供统一协议、ACK/重发跟踪、ready barrier、时间同步、结构化日志、自检与本地基准测试。

当前仓库提供：

* 完整的包结构与核心 API
* 可运行的内存传输后端，适合本地开发与测试
* `pyzmq`/`zeroconf` 的可选局域网传输与发现后端
* 命令行工具、示例程序与 `unittest` 测试

## 安装

```bash
python3 -m pip install -e .
```

如需后续接入真实局域网后端：

```bash
python3 -m pip install -e .[zmq,discovery]
```

## 快速开始

运行内存后端示例：

```bash
python3 examples/demo_inmemory.py
```

运行 CLI demo，可切换到本地 ZMQ 后端：

```bash
python3 -m psyexp_net demo --backend zmq
```

运行网络自检：

```bash
python3 -m psyexp_net doctor
```

运行内存基准测试：

```bash
python3 -m psyexp_net benchmark --clients 4 --seconds 2
```

查看日志摘要：

```bash
python3 -m psyexp_net replay .psyexp_net/sessions/<session-dir>
```

## MVP 范围

已实现：

* 统一消息头、JSON 编解码与字节负载封装
* ACK 跟踪、去重、状态机与 barrier 管理
* 服务器/客户端运行时 API
* `inmemory` / `zmq` 两种 demo 传输路径
* 应用层 ping/pong 时间同步与计划执行
* JSONL 结构化事件记录与基础回放
* `doctor` / `benchmark` / `inspect-log` CLI

已预留扩展点：

* LSL bridge
* 更严格的认证模式

## API 示例

```python
import asyncio

from psyexp_net.config import AppConfig
from psyexp_net.runtime.client import ExperimentClient
from psyexp_net.runtime.server import ExperimentServer
from psyexp_net.transport.memory import InMemoryHub, InMemoryClientTransport, InMemoryServerTransport


async def main() -> None:
    config = AppConfig()
    hub = InMemoryHub()
    server = ExperimentServer(config, InMemoryServerTransport(hub))
    stimulus = ExperimentClient(
        config,
        role="stimulus",
        client_id="stim-01",
        transport=InMemoryClientTransport(hub, "stim-01"),
    )

    await server.start()
    await stimulus.connect()
    await stimulus.register()
    await stimulus.sync_clock()
    await stimulus.ready()

    await server.wait_until_clients_ready(["stimulus"])
    await server.start_session("S001")
    await server.arm_trial("T001")
    await server.start_trial("T001", at="+120ms")
    await server.stop_session()

    await stimulus.close()
    await server.shutdown()


asyncio.run(main())
```
