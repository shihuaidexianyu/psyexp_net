from __future__ import annotations

import asyncio
import contextlib
import time
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

from psyexp_net.config import AppConfig
from psyexp_net.enums import ClientStatus, ErrorCode, MessageType
from psyexp_net.errors import AckTimeoutError, DuplicateClientError
from psyexp_net.logging.metrics import MetricsCollector
from psyexp_net.logging.recorder import EventRecorder
from psyexp_net.protocol.ack import PendingAckManager
from psyexp_net.protocol.message import Message, MessageHeader, make_ack
from psyexp_net.runtime.barrier import BarrierManager
from psyexp_net.runtime.registry import ClientInfo, ClientRegistry
from psyexp_net.runtime.scheduler import resolve_execute_at
from psyexp_net.runtime.session import SessionManager
from psyexp_net.transport.base import TransportBackend

"""服务端运行时。

负责注册、ACK 跟踪、会话状态和结果汇聚。
"""


class ExperimentServer:
    def __init__(
        self,
        config: AppConfig,
        transport: TransportBackend,
        *,
        recorder: EventRecorder | None = None,
    ) -> None:
        self.config = config
        self.transport = transport
        self.registry = ClientRegistry()
        self.barrier = BarrierManager(self.registry)
        self.session = SessionManager()
        self.metrics = MetricsCollector()
        self.recorder = recorder or EventRecorder(
            base_dir=Path(config.logging.base_dir)
        )
        self.pending_acks = PendingAckManager(timeout_ms=config.protocol.ack_timeout_ms)
        self._results: dict[str, asyncio.Queue[dict[str, Any]]] = defaultdict(
            asyncio.Queue
        )
        self._serve_task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        await self.transport.start()
        self._running = True
        # 后台循环持续消费客户端控制消息。
        self._serve_task = asyncio.create_task(self._serve_loop(), name="psyexp-server")

    async def shutdown(self) -> None:
        self._running = False
        if self._serve_task is not None:
            self._serve_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._serve_task
        await self.transport.stop()
        self.recorder.close()

    async def wait_until_clients_ready(
        self, required_roles: list[str] | None = None, timeout: float = 5.0
    ) -> None:
        roles = required_roles or self.config.experiment.required_roles
        missing = await self.barrier.wait_until_ready(roles, timeout)
        if missing:
            raise TimeoutError(f"Timed out waiting for roles: {missing}")
        self.session.mark_ready()

    async def start_session(self, session_id: str) -> None:
        self.session.create(session_id)
        self.session.start()
        await self._send_to_all(
            MessageType.SESSION_START, {"session_id": session_id}, requires_ack=True
        )

    async def stop_session(self) -> None:
        self.session.stop()
        await self._send_to_all(
            MessageType.SESSION_STOP,
            {"session_id": self.session.session_id},
            requires_ack=True,
        )

    async def arm_trial(self, trial_id: str) -> None:
        self.session.arm_trial(trial_id)
        await self._send_to_all(
            MessageType.TRIAL_ARM,
            {"trial_id": trial_id},
            requires_ack=True,
            trial_id=trial_id,
        )

    async def start_trial(self, trial_id: str, at: str | float | None = None) -> float:
        # 关键控制命令采用未来时间执行，减少接收抖动带来的偏差。
        execute_at = resolve_execute_at(
            at, lead_time_ms=self.config.timing.default_lead_time_ms
        )
        self.session.start_trial(trial_id)
        await self._send_to_all(
            MessageType.TRIAL_START_AT,
            {"trial_id": trial_id, "execute_at": execute_at},
            requires_ack=True,
            trial_id=trial_id,
        )
        return execute_at

    async def end_trial(self, trial_id: str) -> None:
        self.session.end_trial(trial_id)
        await self._send_to_all(
            MessageType.TRIAL_END, {"trial_id": trial_id}, trial_id=trial_id
        )

    async def broadcast_state(self, payload: dict[str, Any]) -> None:
        message = self._message(MessageType.STATE_UPDATE, payload)
        await self.transport.broadcast(message)
        self._record("broadcast_state", "server", message, payload=payload)

    async def collect(self, role: str, timeout: float = 2.0) -> dict[str, Any]:
        return await asyncio.wait_for(self._results[role].get(), timeout=timeout)

    async def _serve_loop(self) -> None:
        while self._running:
            event = await self.transport.recv(timeout=0.05)
            if event is None:
                # 空闲时也要处理 ACK 超时扫描。
                self._expire_acks()
                self._refresh_client_health()
                continue
            self._record("receive", event.peer_id, event.message, channel=event.channel)
            await self._handle_message(event.peer_id, event.message)
            self._expire_acks()
            self._refresh_client_health()

    async def _handle_message(self, peer_id: str, message: Message) -> None:
        # MVP 先集中分发，后续可按消息类型拆分为独立 handler。
        msg_type = message.msg_type
        self.registry.touch(peer_id)
        if msg_type == MessageType.REGISTER:
            await self._handle_register(peer_id, message)
            return
        if msg_type == MessageType.PING:
            await self._handle_ping(peer_id, message)
            return
        if msg_type == MessageType.CLIENT_STATUS:
            status = message.payload.get("status", ClientStatus.CONNECTED)
            self.registry.update_status(peer_id, status)
            if "offset_ms" in message.payload or "rtt_ms" in message.payload:
                self.registry.update_sync(
                    peer_id,
                    offset_ms=float(message.payload.get("offset_ms", 0.0)),
                    rtt_ms=float(message.payload.get("rtt_ms", 0.0)),
                )
            return
        if msg_type == MessageType.RESULT_REPORT:
            role = message.header.sender_role
            await self._results[role].put(message.payload)
            return
        if msg_type == MessageType.EVENT_REPORT:
            self.metrics.increment("event_reports")
            return
        if msg_type == MessageType.ACK:
            receiver_id = message.payload.get("receiver_id", peer_id)
            self.pending_acks.resolve(
                message.payload["reply_to"], receiver_id, message.payload
            )
            return
        if msg_type == MessageType.HEARTBEAT:
            self.registry.update_status(
                peer_id,
                self.registry.get(peer_id).current_status
                if self.registry.get(peer_id)
                else ClientStatus.CONNECTED,
            )
            return

    async def _handle_register(self, peer_id: str, message: Message) -> None:
        try:
            info = ClientInfo(
                client_id=peer_id,
                role=message.payload["role"],
                protocol_version=message.payload.get(
                    "protocol_version", self.config.protocol.version
                ),
                capabilities=message.payload.get("capabilities", []),
                current_status=ClientStatus.REGISTERED,
            )
            registered, refreshed = self.registry.register_or_refresh(info)
            response_type = MessageType.REGISTER_OK
            payload = {
                "accepted": True,
                # 注册成功时带回当前会话快照，便于迟到或重连客户端追平状态。
                "snapshot": asdict(self.session.snapshot()),
                "registry": self.registry.snapshot(),
                "reconnected": refreshed,
                "client_id": registered.client_id,
            }
        except DuplicateClientError:
            response_type = MessageType.REGISTER_REJECT
            payload = {
                "accepted": False,
                "error_code": ErrorCode.DUPLICATE_CLIENT_ID.value,
            }
        response = self._message(response_type, payload, reply_to=message.header.msg_id)
        await self.transport.send(peer_id, response)
        self._record("register_response", peer_id, response, payload=payload)

    async def _handle_ping(self, peer_id: str, message: Message) -> None:
        payload = {
            "t0": message.payload["t0"],
            "t1": time.monotonic(),
        }
        payload["t2"] = time.monotonic()
        response = self._message(
            MessageType.PONG, payload, reply_to=message.header.msg_id
        )
        await self.transport.send(peer_id, response)

    async def _send_to_all(
        self,
        msg_type: MessageType,
        payload: dict[str, Any],
        *,
        requires_ack: bool = False,
        trial_id: str | None = None,
    ) -> None:
        # 每个 client 的 ACK 独立跟踪，因此这里做 fan-out 发送。
        tasks = [
            self._send_control(
                client.client_id,
                msg_type,
                payload,
                requires_ack=requires_ack,
                trial_id=trial_id,
            )
            for client in self.registry.all()
        ]
        if tasks:
            await asyncio.gather(*tasks)

    async def _send_control(
        self,
        peer_id: str,
        msg_type: MessageType,
        payload: dict[str, Any],
        *,
        requires_ack: bool = False,
        trial_id: str | None = None,
    ) -> None:
        message = self._message(
            msg_type, payload, requires_ack=requires_ack, trial_id=trial_id
        )
        future: asyncio.Future | None = None
        if requires_ack:
            # 关键消息发送前先登记 pending 表。
            future = asyncio.get_running_loop().create_future()
            self.pending_acks.add(message.header.msg_id, peer_id, future)
        await self.transport.send(peer_id, message)
        self._record("send", peer_id, message, payload=payload)
        if future is not None:
            try:
                await asyncio.wait_for(
                    future, timeout=self.config.protocol.ack_timeout_ms / 1000
                )
            except asyncio.TimeoutError as exc:
                raise AckTimeoutError(
                    f"ACK timeout for {msg_type} to {peer_id}"
                ) from exc

    def _expire_acks(self) -> None:
        expired = self.pending_acks.expire()
        for entry in expired:
            # 当前阶段仅记录超时；自动重发留给后续网络版实现。
            self.metrics.increment("ack_timeout")
            self._record(
                "ack_timeout",
                entry.peer_id,
                None,
                payload={
                    "msg_id": entry.msg_id,
                    "peer_id": entry.peer_id,
                    "retries": entry.retries,
                },
            )

    def _refresh_client_health(self) -> None:
        timeout_s = self.config.network.heartbeat_timeout_ms / 1000
        degraded_s = timeout_s / 2
        now = time.monotonic()
        for client in self.registry.all():
            age = now - client.last_seen
            if age >= timeout_s and client.current_status != ClientStatus.DISCONNECTED:
                self.registry.update_status(client.client_id, ClientStatus.DISCONNECTED)
                self._record(
                    "disconnect",
                    client.client_id,
                    None,
                    payload={"status": ClientStatus.DISCONNECTED, "last_seen_age_s": age},
                )
                continue
            if (
                age >= degraded_s
                and client.current_status
                in {
                    ClientStatus.CONNECTED,
                    ClientStatus.REGISTERED,
                    ClientStatus.SYNCED,
                    ClientStatus.READY,
                    ClientStatus.RUNNING,
                }
            ):
                self.registry.update_status(client.client_id, ClientStatus.DEGRADED)
                self._record(
                    "degraded",
                    client.client_id,
                    None,
                    payload={"status": ClientStatus.DEGRADED, "last_seen_age_s": age},
                )

    def _message(
        self,
        msg_type: MessageType,
        payload: dict[str, Any],
        *,
        requires_ack: bool = False,
        reply_to: str | None = None,
        trial_id: str | None = None,
    ) -> Message:
        header = MessageHeader.create(
            msg_type,
            sender_id="server",
            session_id=self.session.session_id,
            sender_role="server",
            requires_ack=requires_ack,
            reply_to=reply_to,
            trial_id=trial_id,
        )
        header.server_ts = time.monotonic()
        return Message(header=header, payload=payload)

    def _record(
        self,
        kind: str,
        peer_id: str,
        message: Message | None,
        **extra: Any,
    ) -> None:
        self.recorder.record(
            kind,
            peer_id=peer_id,
            message=message.to_dict() if message is not None else None,
            session=asdict(self.session.snapshot()),
            **extra,
        )
