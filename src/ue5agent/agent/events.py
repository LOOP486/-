"""类型化 TraceEvent 与 RunWriter：runs/<session>/ 产物目录（K1）。

RunWriter.write 与旧 SessionLog.write 同签名，现有 loop 经此兼容层
直接写新 trace；K5 删除 session_log 后它就是唯一入口。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ue5agent.agent.state import Artifact, TaskSession

EVENT_TYPES = frozenset(
    {
        "run_start",
        "phase_enter",
        "phase_exit",
        "llm_turn",
        "tool_call",
        "verify_result",
        "recover_action",
        "checkpoint",
        "budget_warning",
        "run_end",
    }
)


class RunWriter:
    def __init__(self, root: Path, session: TaskSession):
        self.session = session
        self.dir = root / session.id
        self.artifacts_dir = self.dir / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.trace_path = self.dir / "trace.jsonl"

    def event(
        self,
        event_type: str,
        *,
        phase: str | None = None,
        step_id: str | None = None,
        **payload: Any,
    ) -> None:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"未知 trace 事件类型：{event_type}（合法集合见 EVENT_TYPES）")
        record: dict[str, Any] = {
            "ts": round(time.time(), 3),
            "session_id": self.session.id,
            "event": event_type,
        }
        if phase:
            record["phase"] = phase
        if step_id:
            record["step_id"] = step_id
        record.update(payload)
        with self.trace_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def write(self, event: str, **data: Any) -> None:
        """与旧 SessionLog.write 兼容的入口，loop 无需感知差异。"""
        self.event(event, **data)

    def save_artifact(self, kind: str, name: str, content: str | bytes, **meta: Any) -> Artifact:
        path = self.artifacts_dir / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        artifact = Artifact(kind=kind, path=f"artifacts/{name}", meta=meta)
        self.session.artifacts.append(artifact)
        return artifact

    def save_session(self) -> Path:
        return self.session.save(self.dir)

    def write_report(self, text: str) -> Path:
        path = self.dir / "report.md"
        path.write_text(text, encoding="utf-8")
        return path


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


def latest_trace(root: Path) -> Path | None:
    """runs/ 下最新一次运行的 trace（目录名以时间戳开头，字典序即时间序）。"""
    if not root.exists():
        return None
    traces = sorted(root.glob("*/trace.jsonl"), key=lambda p: p.parent.name)
    return traces[-1] if traces else None
