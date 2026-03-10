from __future__ import annotations

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
        self._handle = self._log_path.open("a", encoding="utf-8")

    def record(self, kind: str, **payload: Any) -> None:
        # JSONL 便于流式写入和后续离线分析。
        row = {"ts": time.time(), "kind": kind, **payload}
        self._handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._handle.flush()

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.close()
