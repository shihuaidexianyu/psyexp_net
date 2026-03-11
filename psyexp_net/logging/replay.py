from __future__ import annotations

import json
from pathlib import Path
from typing import Any

"""基于事件日志的回放工具。"""


class ReplayEngine:
    def __init__(self, session_dir: str | Path) -> None:
        self.session_dir = Path(session_dir)
        self.log_path = self.session_dir / "events.jsonl"

    def events(self) -> list[dict[str, Any]]:
        if not self.log_path.exists():
            return []
        with self.log_path.open("r", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def summary(self) -> dict[str, Any]:
        events = self.events()
        trial_ids = sorted(
            {
                trial_id
                for event in events
                for trial_id in [self._trial_id_for_event(event)]
                if trial_id is not None
            }
        )
        acked_messages = sum(
            1
            for event in events
            if event.get("kind") == "send"
            and self.message_timeline(self._message_id(event)).get("acks")
        )
        return {
            "session_dir": str(self.session_dir),
            "event_count": len(events),
            "kinds": sorted({event["kind"] for event in events}),
            "trial_ids": trial_ids,
            "acked_messages": acked_messages,
        }

    def trial_timeline(self, trial_id: str) -> list[dict[str, Any]]:
        timeline: list[dict[str, Any]] = []
        for event in self.events():
            event_trial_id = self._trial_id_for_event(event)
            if event_trial_id != trial_id:
                continue
            timeline.append(
                {
                    "ts": event.get("ts"),
                    "kind": event.get("kind"),
                    "peer_id": event.get("peer_id"),
                    "msg_type": self._message_type(event),
                    "msg_id": self._message_id(event),
                    "reply_to": self._reply_to(event),
                    "session_state": event.get("session", {}).get("state"),
                    "phase": event.get("session", {}).get("phase"),
                }
            )
        return timeline

    def message_timeline(self, msg_id: str | None) -> dict[str, Any]:
        if not msg_id:
            return {"msg_id": msg_id, "events": [], "acks": []}
        events = self.events()
        send_event = next(
            (
                event
                for event in events
                if event.get("kind") == "send" and self._message_id(event) == msg_id
            ),
            None,
        )
        related = [
            event
            for event in events
            if self._message_id(event) == msg_id or self._reply_to(event) == msg_id
        ]
        related.sort(key=lambda event: float(event.get("ts", 0.0)))
        acks = []
        send_ts = float(send_event.get("ts", 0.0)) if send_event is not None else None
        for event in related:
            if self._message_type(event) != "ACK":
                continue
            payload = self._message_payload(event)
            ack_receive_ts = payload.get("receive_ts")
            apply_ts = payload.get("apply_ts")
            acks.append(
                {
                    "peer_id": event.get("peer_id"),
                    "receiver_id": payload.get("receiver_id"),
                    "status": payload.get("status"),
                    "receive_ts": ack_receive_ts,
                    "apply_ts": apply_ts,
                    "ack_event_ts": event.get("ts"),
                    "ack_latency_ms": (
                        (float(event["ts"]) - send_ts) * 1000
                        if send_ts is not None and event.get("ts") is not None
                        else None
                    ),
                    "apply_latency_ms": (
                        (float(apply_ts) - float(self._message_header(send_event).get("server_ts", 0.0))) * 1000
                        if send_event is not None and apply_ts is not None
                        else None
                    ),
                }
            )
        return {
            "msg_id": msg_id,
            "msg_type": self._message_type(send_event),
            "peer_id": send_event.get("peer_id") if send_event else None,
            "trial_id": self._trial_id_for_event(send_event) if send_event else None,
            "send_ts": send_event.get("ts") if send_event else None,
            "events": [
                {
                    "ts": event.get("ts"),
                    "kind": event.get("kind"),
                    "peer_id": event.get("peer_id"),
                    "msg_type": self._message_type(event),
                    "msg_id": self._message_id(event),
                    "reply_to": self._reply_to(event),
                }
                for event in related
            ],
            "acks": acks,
        }

    def _message(self, event: dict[str, Any] | None) -> dict[str, Any]:
        if not event:
            return {}
        return event.get("message") or {}

    def _message_header(self, event: dict[str, Any] | None) -> dict[str, Any]:
        return self._message(event).get("header") or {}

    def _message_payload(self, event: dict[str, Any] | None) -> dict[str, Any]:
        payload = self._message(event).get("payload")
        return payload if isinstance(payload, dict) else {}

    def _message_id(self, event: dict[str, Any] | None) -> str | None:
        return self._message_header(event).get("msg_id")

    def _reply_to(self, event: dict[str, Any] | None) -> str | None:
        header = self._message_header(event)
        if header.get("reply_to"):
            return header["reply_to"]
        return self._message_payload(event).get("reply_to")

    def _message_type(self, event: dict[str, Any] | None) -> str | None:
        return self._message_header(event).get("msg_type")

    def _trial_id_for_event(self, event: dict[str, Any] | None) -> str | None:
        header = self._message_header(event)
        if header.get("trial_id"):
            return header["trial_id"]
        session = event.get("session") if event else None
        if isinstance(session, dict):
            return session.get("trial_id")
        return None
