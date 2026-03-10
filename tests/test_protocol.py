from __future__ import annotations

import asyncio
import unittest

from psyexp_net.protocol.ack import MessageDeduplicator, PendingAckManager
from psyexp_net.protocol.codec import JsonMessageCodec
from psyexp_net.protocol.message import Message, MessageHeader

"""协议层单元测试。"""


class ProtocolTests(unittest.TestCase):
    def test_codec_roundtrip_with_bytes(self) -> None:
        codec = JsonMessageCodec()
        message = Message(
            header=MessageHeader.create(
                "EVENT_REPORT", sender_id="client-1", sender_role="response"
            ),
            payload={"blob": b"abc", "value": 3},
        )
        decoded = codec.decode(codec.encode(message))
        self.assertEqual(decoded.payload["blob"], b"abc")
        self.assertEqual(decoded.payload["value"], 3)

    def test_deduplicator(self) -> None:
        dedupe = MessageDeduplicator(max_size=2)
        self.assertFalse(dedupe.seen("a"))
        self.assertTrue(dedupe.seen("a"))
        self.assertFalse(dedupe.seen("b"))
        self.assertFalse(dedupe.seen("c"))
        self.assertFalse(dedupe.seen("a"))

    def test_pending_ack_resolve(self) -> None:
        manager = PendingAckManager(timeout_ms=100)
        loop = asyncio.new_event_loop()
        try:
            future = loop.create_future()
            manager.add("m1", "peer-1", future)
            resolved = manager.resolve("m1", "peer-1", {"ok": True})
            self.assertTrue(resolved)
            self.assertEqual(future.result(), {"ok": True})
        finally:
            loop.close()


if __name__ == "__main__":
    unittest.main()
