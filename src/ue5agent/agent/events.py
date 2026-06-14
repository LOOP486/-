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
from ue5agent.core.redaction import redact

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
        "run_error",
        "run_end",
        # B1 步骤契约
        "precondition_unmet",
        "precondition_unknown",
        "rollback_action",
        # A4 视觉审查
        "vision_review",
        "vision_review_error",
        # B4 上下文工程
        "context_brief",
        # E2 子代理
        "subagent_start",
        "subagent_end",
    }
)


class RunWriter:
    def __init__(self, root: Path, session: TaskSession, *, secrets: set[str] | None = None):
        self.session = session
        self.dir = root / session.id
        self.artifacts_dir = self.dir / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.trace_path = self.dir / "trace.jsonl"
        self._secrets = secrets or set()
        """落盘前掩码的 secret 值集合（D1.2）；空集时不改动任何文本。"""

    def _redact(self, text: str) -> str:
        return redact(text, self._secrets) if self._secrets else text

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
        line = self._redact(json.dumps(record, ensure_ascii=False))
        with self.trace_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def write(self, event: str, **data: Any) -> None:
        """与旧 SessionLog.write 兼容的入口，loop 无需感知差异。"""
        self.event(event, **data)

    def save_artifact(self, kind: str, name: str, content: str | bytes, **meta: Any) -> Artifact:
        path = self.artifacts_dir / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(self._redact(content), encoding="utf-8")
        artifact = Artifact(kind=kind, path=f"artifacts/{name}", meta=meta)
        self.session.artifacts.append(artifact)
        return artifact

    def save_session(self) -> Path:
        return self.session.save(self.dir)

    def write_report(self, text: str) -> Path:
        path = self.dir / "report.md"
        path.write_text(self._redact(text), encoding="utf-8")
        return path

    def write_progress(self, text: str) -> Path:
        """长任务进度落盘 runs/<session>/progress.md（B4），每步收口刷新供人/恢复查看。"""
        path = self.dir / "progress.md"
        path.write_text(self._redact(text), encoding="utf-8")
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


def resolve_trace_path(path: Path) -> Path:
    """接受 trace 文件或 run 目录；目录输入自动解析为 trace.jsonl。"""
    return path / "trace.jsonl" if path.is_dir() else path


def latest_trace(root: Path) -> Path | None:
    """runs/ 下最新一次运行的 trace（目录名以时间戳开头，字典序即时间序）。"""
    if not root.exists():
        return None
    traces = sorted(root.glob("*/trace.jsonl"), key=lambda p: p.parent.name)
    return traces[-1] if traces else None


def prune_runs(
    root: Path,
    *,
    keep: int = 20,
    days: float | None = None,
    keep_screenshots: bool = False,
    now: float | None = None,
) -> list[str]:
    """清理旧的 runs/<session> 目录（D2.2），返回被删目录名（按时间升序）。

    规则：按目录名（时间戳前缀）排序，保留最近 keep 个；若给 days，更早于 days 天的
    也删（即便在 keep 内）。keep_screenshots=True 时保留各目录的 artifacts/*.png（删其余）。
    非目录、不含 trace.jsonl 的项跳过（不误删用户文件）。
    """
    import shutil
    import time as _time

    if not root.exists():
        return []
    runs = sorted(
        (p for p in root.iterdir() if p.is_dir() and (p / "trace.jsonl").exists()),
        key=lambda p: p.name,
    )
    cutoff = (now if now is not None else _time.time()) - days * 86400 if days else None
    deleted: list[str] = []
    for index, run_dir in enumerate(runs):
        too_old = cutoff is not None and run_dir.stat().st_mtime < cutoff
        beyond_keep = index < len(runs) - keep
        if not (too_old or beyond_keep):
            continue
        if keep_screenshots and _has_screenshots(run_dir):
            _delete_except_screenshots(run_dir)
        else:
            shutil.rmtree(run_dir, ignore_errors=True)
        deleted.append(run_dir.name)
    return deleted


def _has_screenshots(run_dir: Path) -> bool:
    return any((run_dir / "artifacts").glob("*.png")) if (run_dir / "artifacts").exists() else False


def _delete_except_screenshots(run_dir: Path) -> None:
    """删除 run 目录下除 artifacts/*.png 外的内容（保留截图证据）。"""
    import shutil

    for child in run_dir.iterdir():
        if child.name == "artifacts":
            for art in child.iterdir():
                if art.suffix.lower() != ".png":
                    art.unlink(missing_ok=True) if art.is_file() else shutil.rmtree(
                        art, ignore_errors=True
                    )
        elif child.is_file():
            child.unlink(missing_ok=True)
        else:
            shutil.rmtree(child, ignore_errors=True)
