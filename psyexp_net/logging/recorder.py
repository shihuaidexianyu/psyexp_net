from __future__ import annotations

from collections import Counter
import json
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

"""结构化 JSONL 事件记录器。"""


class EventRecorder:
    def __init__(self, *, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        # 时间戳加随机后缀，避免同一秒内多次运行目录冲突。
        session_dir = (
            self.base_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"
        )
        session_dir.mkdir(parents=True, exist_ok=True)
        self.session_dir = session_dir
        self._log_path = session_dir / "events.jsonl"
        self._summary_path = session_dir / "summary.json"
        self._handle = self._log_path.open("a", encoding="utf-8")
        self._event_count = 0
        self._kinds: Counter[str] = Counter()
        self._trial_ids: set[str] = set()
        self._peer_ids: set[str] = set()
        self._last_session: dict[str, Any] | None = None

    def record(self, kind: str, **payload: Any) -> None:
        # JSONL 便于流式写入和后续离线分析。
        row = {"ts": time.time(), "kind": kind, **payload}
        self._handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._handle.flush()
        self._event_count += 1
        self._kinds[kind] += 1
        peer_id = payload.get("peer_id")
        if isinstance(peer_id, str) and peer_id:
            self._peer_ids.add(peer_id)
        message = payload.get("message")
        if isinstance(message, dict):
            trial_id = message.get("header", {}).get("trial_id")
            if isinstance(trial_id, str) and trial_id:
                self._trial_ids.add(trial_id)
        session = payload.get("session")
        if isinstance(session, dict):
            self._last_session = session
            trial_id = session.get("trial_id")
            if isinstance(trial_id, str) and trial_id:
                self._trial_ids.add(trial_id)

    def write_summary(self, **extra: Any) -> dict[str, Any]:
        summary = {
            "session_dir": str(self.session_dir),
            "event_count": self._event_count,
            "kinds": dict(self._kinds),
            "trial_ids": sorted(self._trial_ids),
            "peer_ids": sorted(self._peer_ids),
            "last_session": self._last_session,
            **extra,
        }
        self._summary_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return summary

    def close(self, **extra: Any) -> None:
        self.write_summary(**extra)
        if not self._handle.closed:
            self._handle.close()
