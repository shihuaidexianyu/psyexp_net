from __future__ import annotations

import asyncio
import contextlib
import time
from collections import defaultdict
from typing import Any, Awaitable, Callable

from psyexp_net.config import AppConfig
from psyexp_net.enums import ClientStatus, MessageType
from psyexp_net.protocol.ack import MessageDeduplicator
from psyexp_net.protocol.message import Message, MessageHeader, make_ack
from psyexp_net.runtime.session import SessionManager
from psyexp_net.security.identity import ClientIdentity
from psyexp_net.timing.sync import SyncEstimator
from psyexp_net.transport.base import TransportBackend

"""客户端运行时。

负责注册、时钟同步、命令执行和结果上报。
"""

Handler = Callable[[Message], Awaitable[None]]


class ExperimentClient:
    def __init__(
        self,
        config: AppConfig,
        *,
        role: str,
        client_id: str,
        transport: TransportBackend,
        client_secret: str | None = None,
    ) -> None:
        self.config = config
        self.role = role
        self.client_id = client_id
        self.transport = transport
        resolved_secret = client_secret
        if resolved_secret is None:
            resolved_secret = config.security.shared_secrets.get(client_id)
        self.identity = ClientIdentity(
            client_id=client_id,
            role=role,
            client_secret=resolved_secret,
        )
        self.status = ClientStatus.DISCOVERED
        self.offset_ms = 0.0
        self.rtt_ms = 0.0
        self.session = SessionManager()
        self.registry_snapshot: list[dict[str, str | float]] = []
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._dedupe = MessageDeduplicator(config.protocol.dedup_cache_size)
        self._responses: dict[str, asyncio.Future[Message]] = {}
        self._loop_task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._running = False
        self._closing = False
        self._reconnect_lock = asyncio.Lock()

    async def connect(self) -> None:
        await self.transport.start()
        self.status = ClientStatus.CONNECTED
        self._running = True
        self._closing = False
        # 接收循环后台运行，避免上层忘记主动拉消息。
        self._loop_task = asyncio.create_task(
            self._recv_loop(), name=f"psyexp-client:{self.client_id}"
        )
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name=f"psyexp-heartbeat:{self.client_id}"
        )

    async def close(self) -> None:
        self._closing = True
        self._running = False
        if self._loop_task is not None:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task
        await self.transport.stop()
        self.status = ClientStatus.DISCONNECTED

    def on(self, message_type: MessageType | str) -> Callable[[Handler], Handler]:
        key = (
            message_type.value
            if isinstance(message_type, MessageType)
            else message_type
        )

        def decorator(func: Handler) -> Handler:
            self._handlers[key].append(func)
            return func

        return decorator

    async def register(self) -> Message:
        payload = {
            "client_id": self.client_id,
            "role": self.role,
            "instance_uuid": self.identity.instance_uuid,
            "client_secret": self.identity.client_secret,
            "protocol_version": self.config.protocol.version,
            "capabilities": ["timing.sync", "structured-logs"],
        }
        response = await self._exchange(
            MessageType.REGISTER,
            payload,
            expected={MessageType.REGISTER_OK.value, MessageType.REGISTER_REJECT.value},
        )
        self.status = (
            ClientStatus.REGISTERED
            if response.msg_type == MessageType.REGISTER_OK.value
            else ClientStatus.REJECTED
        )
        if response.msg_type == MessageType.REGISTER_OK.value:
            self._apply_register_snapshot(response)
        return response

    async def sync_clock(self, *, preserve_status: bool = False) -> tuple[float, float]:
        # 使用四时间戳法估计 RTT 和时钟偏移。
        previous_status = self.status
        estimator = SyncEstimator()
        t0 = time.monotonic()
        response = await self._exchange(
            MessageType.PING, {"t0": t0}, expected={MessageType.PONG.value}
        )
        t3 = time.monotonic()
        sample = estimator.add_sample(
            t0=t0, t1=response.payload["t1"], t2=response.payload["t2"], t3=t3
        )
        self.offset_ms = sample.offset_ms
        self.rtt_ms = sample.rtt_ms
        self.status = previous_status if preserve_status else ClientStatus.SYNCED
        await self._send_status(
            previous_status if preserve_status else ClientStatus.SYNCED
        )
        return self.offset_ms, self.rtt_ms

    async def ready(self) -> None:
        self.status = ClientStatus.READY
        await self._send_status(ClientStatus.READY)

    async def run_forever(self) -> None:
        while self._running:
            await asyncio.sleep(0.1)

    async def report_result(self, payload: dict[str, Any]) -> None:
        await self._send(MessageType.RESULT_REPORT, payload)

    async def report_event(self, payload: dict[str, Any]) -> None:
        await self._send(MessageType.EVENT_REPORT, payload)

    async def reconnect(self) -> None:
        async with self._reconnect_lock:
            if self._closing:
                return
            self.status = ClientStatus.RECONNECTING
            try:
                await self.transport.stop()
            except Exception:
                pass
            await self.transport.start()
            self.status = ClientStatus.CONNECTED
            await self.register()
            await self.sync_clock(preserve_status=True)
            await self.ready()

    async def _recv_loop(self) -> None:
        while self._running:
            try:
                event = await self.transport.recv(timeout=0.05)
            except Exception:
                if self._running and not self._closing:
                    await self.reconnect()
                continue
            if event is None:
                continue
            message = event.message
            if self._dedupe.seen(message.header.msg_id) and message.header.requires_ack:
                # 重复关键消息不重复执行，但仍然回 ACK 以便服务端收敛。
                await self.transport.send(
                    "server", make_ack(original=message, receiver_id=self.client_id)
                )
                continue
            if message.header.reply_to and message.header.reply_to in self._responses:
                # REGISTER/PING 这类请求响应通过 reply_to 关联。
                future = self._responses.pop(message.header.reply_to)
                if not future.done():
                    future.set_result(message)
                continue
            await self._dispatch(message)

    async def _dispatch(self, message: Message) -> None:
        if message.msg_type == MessageType.SESSION_START.value:
            self.status = ClientStatus.RUNNING
            await self._run_handlers(message)
            await self.transport.send(
                "server",
                make_ack(
                    original=message,
                    receiver_id=self.client_id,
                    apply_ts=time.monotonic(),
                ),
            )
            return
        if message.msg_type == MessageType.SESSION_STOP.value:
            await self._run_handlers(message)
            await self.transport.send(
                "server",
                make_ack(
                    original=message,
                    receiver_id=self.client_id,
                    apply_ts=time.monotonic(),
                ),
            )
            return
        if message.msg_type == MessageType.TRIAL_ARM.value:
            await self._run_handlers(message)
            await self.transport.send(
                "server",
                make_ack(
                    original=message,
                    receiver_id=self.client_id,
                    apply_ts=time.monotonic(),
                ),
            )
            return
        if message.msg_type == MessageType.TRIAL_START_AT.value:
            execute_at = float(message.payload["execute_at"])
            # 服务端给的是 server 单调时钟，需要先映射成本地时钟再等待。
            target_local = self._local_monotonic_for_server(execute_at)
            delay = max(0.0, target_local - time.monotonic())
            if delay:
                await asyncio.sleep(delay)
            await self._run_handlers(message)
            await self.transport.send(
                "server",
                make_ack(
                    original=message,
                    receiver_id=self.client_id,
                    apply_ts=time.monotonic(),
                ),
            )
            return
        await self._run_handlers(message)

    async def _run_handlers(self, message: Message) -> None:
        for handler in self._handlers.get(message.msg_type, []):
            await handler(message)

    async def _exchange(
        self, msg_type: MessageType, payload: dict[str, Any], *, expected: set[str]
    ) -> Message:
        message = self._message(msg_type, payload)
        future: asyncio.Future[Message] = asyncio.get_running_loop().create_future()
        self._responses[message.header.msg_id] = future
        await self.transport.send("server", message)
        response = await future
        if response.msg_type not in expected:
            raise RuntimeError(
                f"Unexpected response {response.msg_type} for {msg_type}"
            )
        return response

    async def _send_status(self, status: str) -> None:
        payload = {"status": status, "offset_ms": self.offset_ms, "rtt_ms": self.rtt_ms}
        await self._send(MessageType.CLIENT_STATUS, payload)

    async def _send(self, msg_type: MessageType, payload: dict[str, Any]) -> None:
        await self.transport.send("server", self._message(msg_type, payload))

    def _message(self, msg_type: MessageType, payload: dict[str, Any]) -> Message:
        header = MessageHeader.create(
            msg_type,
            sender_id=self.client_id,
            sender_role=self.role,
        )
        header.client_ts = time.monotonic()
        return Message(header=header, payload=payload)

    def _local_monotonic_for_server(self, server_time: float) -> float:
        return server_time - self.offset_ms / 1000

    def _apply_register_snapshot(self, response: Message) -> None:
        snapshot = response.payload.get("snapshot")
        if isinstance(snapshot, dict):
            self.session.apply_snapshot(snapshot)
        registry = response.payload.get("registry")
        if isinstance(registry, list):
            self.registry_snapshot = list(registry)

    async def _heartbeat_loop(self) -> None:
        interval_s = max(self.config.network.heartbeat_interval_ms / 1000, 0.05)
        sync_every = max(1, int(self.config.network.heartbeat_timeout_ms / max(self.config.network.heartbeat_interval_ms, 1)))
        ticks = 0
        while self._running:
            await asyncio.sleep(interval_s)
            if not self._running:
                break
            try:
                await self._send(
                    MessageType.HEARTBEAT,
                    {
                        "status": self.status,
                        "offset_ms": self.offset_ms,
                        "rtt_ms": self.rtt_ms,
                    },
                )
                ticks += 1
                if ticks % sync_every == 0:
                    await self.sync_clock(preserve_status=True)
                    if (
                        abs(self.offset_ms) > self.config.timing.max_clock_offset_ms
                        or self.rtt_ms > self.config.timing.max_rtt_ms
                    ):
                        self.status = ClientStatus.DEGRADED
                        await self._send_status(ClientStatus.DEGRADED)
            except Exception:
                if self._running and not self._closing:
                    await self.reconnect()
