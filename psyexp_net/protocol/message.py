from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from psyexp_net.enums import MessageType, Priority

"""统一的协议消息结构。"""


def make_message_id() -> str:
    return uuid.uuid4().hex


@dataclass(slots=True)
class MessageHeader:
    msg_type: str
    version: str = "1.0"
    msg_id: str = field(default_factory=make_message_id)
    reply_to: str | None = None
    sender_id: str = ""
    sender_role: str = ""
    session_id: str | None = None
    run_id: str | None = None
    block_id: str | None = None
    trial_id: str | None = None
    server_ts: float | None = None
    client_ts: float | None = None
    logical_seq: int = 0
    priority: str = Priority.NORMAL
    requires_ack: bool = False
    deadline_ms: int | None = None
    payload_codec: str = "json"
    schema: str = "default"

    @classmethod
    def create(
        cls,
        msg_type: MessageType | str,
        *,
        sender_id: str,
        sender_role: str = "",
        session_id: str | None = None,
        trial_id: str | None = None,
        requires_ack: bool = False,
        reply_to: str | None = None,
        priority: Priority | str = Priority.NORMAL,
        deadline_ms: int | None = None,
    ) -> "MessageHeader":
        """创建一条带默认时间戳和消息 ID 的消息头。"""
        now = time.monotonic()
        kind = msg_type.value if isinstance(msg_type, MessageType) else msg_type
        return cls(
            msg_type=kind,
            sender_id=sender_id,
            sender_role=sender_role,
            session_id=session_id,
            trial_id=trial_id,
            requires_ack=requires_ack,
            reply_to=reply_to,
            priority=priority.value if isinstance(priority, Priority) else priority,
            client_ts=now,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "msg_type": self.msg_type,
            "msg_id": self.msg_id,
            "reply_to": self.reply_to,
            "sender_id": self.sender_id,
            "sender_role": self.sender_role,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "block_id": self.block_id,
            "trial_id": self.trial_id,
            "server_ts": self.server_ts,
            "client_ts": self.client_ts,
            "logical_seq": self.logical_seq,
            "priority": self.priority,
            "requires_ack": self.requires_ack,
            "deadline_ms": self.deadline_ms,
            "payload_codec": self.payload_codec,
            "schema": self.schema,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MessageHeader":
        return cls(**payload)


@dataclass(slots=True)
class Message:
    header: MessageHeader
    payload: Any = field(default_factory=dict)
    attachments: list[bytes] = field(default_factory=list)

    @property
    def msg_type(self) -> str:
        return self.header.msg_type

    def to_dict(self) -> dict[str, Any]:
        return {
            "header": self.header.to_dict(),
            "payload": self.payload,
            "attachments": self.attachments,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Message":
        return cls(
            header=MessageHeader.from_dict(payload["header"]),
            payload=payload.get("payload", {}),
            attachments=payload.get("attachments", []),
        )


def make_ack(
    *,
    original: Message,
    receiver_id: str,
    status: str = "ok",
    apply_ts: float | None = None,
    error_code: str | None = None,
) -> Message:
    """根据原消息构造 ACK。"""
    header = MessageHeader.create(
        MessageType.ACK,
        sender_id=receiver_id,
        reply_to=original.header.msg_id,
    )
    return Message(
        header=header,
        payload={
            "reply_to": original.header.msg_id,
            "receiver_id": receiver_id,
            "receive_ts": time.monotonic(),
            "apply_ts": apply_ts,
            "status": status,
            "error_code": error_code,
        },
    )
