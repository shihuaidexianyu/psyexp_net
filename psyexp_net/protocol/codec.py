from __future__ import annotations

import base64
import json
from typing import Any

from psyexp_net.errors import ProtocolError

from .message import Message

"""JSON 编解码器。

bytes 会被显式包装成 base64，避免使用不安全的对象序列化。
"""


class JsonMessageCodec:
    """带字节包装的 JSON 编解码器。"""

    @staticmethod
    def _encode_obj(value: Any) -> Any:
        if isinstance(value, bytes):
            return {"__bytes__": base64.b64encode(value).decode("ascii")}
        if isinstance(value, list):
            return [JsonMessageCodec._encode_obj(item) for item in value]
        if isinstance(value, dict):
            return {
                key: JsonMessageCodec._encode_obj(item) for key, item in value.items()
            }
        return value

    @staticmethod
    def _decode_obj(value: Any) -> Any:
        if isinstance(value, list):
            return [JsonMessageCodec._decode_obj(item) for item in value]
        if isinstance(value, dict):
            if "__bytes__" in value:
                return base64.b64decode(value["__bytes__"].encode("ascii"))
            return {
                key: JsonMessageCodec._decode_obj(item) for key, item in value.items()
            }
        return value

    def encode(self, message: Message) -> bytes:
        try:
            payload = self._encode_obj(message.to_dict())
            return json.dumps(
                payload, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ProtocolError("Failed to encode message") from exc

    def decode(self, data: bytes) -> Message:
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProtocolError("Failed to decode message") from exc
        return Message.from_dict(self._decode_obj(payload))
