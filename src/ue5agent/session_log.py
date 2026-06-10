"""会话操作日志：JSONL 追加写，作为审计与回滚的依据。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class SessionLog:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.path = directory / f"session-{stamp}.jsonl"

    def write(self, event: str, **data: Any) -> None:
        record = {"ts": round(time.time(), 3), "event": event, **data}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_events(path: Path) -> list[dict[str, Any]]:
    """读取一份 trace 的全部事件（坏行跳过，不让单行损坏毁掉回放）。"""
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def latest_session(directory: Path) -> Path | None:
    if not directory.exists():
        return None
    files = sorted(directory.glob("session-*.jsonl"))
    return files[-1] if files else None
