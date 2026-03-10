from __future__ import annotations

import json
from pathlib import Path
from typing import Any

"""基于事件日志的最小回放工具。"""


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
        # MVP 只提供概览摘要，后续再扩展为 trial 级时间线重建。
        events = self.events()
        return {
            "session_dir": str(self.session_dir),
            "event_count": len(events),
            "kinds": sorted({event["kind"] for event in events}),
        }
