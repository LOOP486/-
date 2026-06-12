"""Agent Kernel 的任务状态数据结构（kernel-refactor-plan §3.4，K1）。

K4 的 Runner 状态机会完整驱动这些结构；K4 之前由兼容层最小填充
（单步 fast-path 形态），先把数据结构与持久化打牢，让 runs/ 产物目录
和结构化 trace 有所依附。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Budgets:
    max_iterations: int = 40
    max_tool_result_chars: int = 30_000
    compact_budget_chars: int = 200_000


@dataclass
class Artifact:
    kind: str
    """diff | build_log | screenshot | report | file"""
    path: str
    """相对 runs/<session>/ 的路径"""
    meta: dict = field(default_factory=dict)


@dataclass
class PlanStep:
    id: str
    intent: str
    """这一步要达成什么"""
    acceptance: str = ""
    """怎样算完成（verify 的依据）"""
    status: str = "pending"
    """pending | running | done | failed | skipped"""
    attempts: int = 0
    evidence: list[str] = field(default_factory=list)
    """验收证据：Artifact.path 引用"""
    # ---- 契约字段（B1）：全部带默认值，弱模型省略时行为同 v1 ----
    allowed_tools: list[str] = field(default_factory=list)
    """本步允许的工具白名单（裸名或全名）；空 = 不限"""
    permission_ceiling: str = ""
    """本步允许的最高权限级（read/write_safe/write_project/dangerous）；空 = 不限"""
    preconditions: list[str] = field(default_factory=list)
    """前置条件（如 editor_online），执行前探测、未满足时在提示中注入补救指引"""
    success_checks: list[dict] = field(default_factory=list)
    """声明式验收：[{"kind": "wb_validate", "field": "ok", "equals": true}]，
    绑定 facts 证据通道（A3）；缺证据 → insufficient，驱动补证据重试"""
    rollback_policy: str = "none"
    """步骤最终失败时的回滚策略：none | wb_clear（restore_checkpoint 仅提示不自动执行）"""
    step_budget: dict = field(default_factory=dict)
    """步级预算收紧：{"max_seconds": int, "max_turns": int}，只能小于 runner 默认值"""


@dataclass
class TaskSession:
    id: str
    goal: str
    task_class: str = "standard"
    """trivial | standard | complex（intake 产出，trivial 走 fast path）"""
    status: str = "running"
    """running | done | aborted | awaiting_user"""
    plan: list[PlanStep] = field(default_factory=list)
    current_step: int = 0
    budgets: Budgets = field(default_factory=Budgets)
    artifacts: list[Artifact] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @classmethod
    def new(cls, goal: str, **kwargs) -> TaskSession:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        return cls(id=f"{stamp}_{_slug(goal)}", goal=goal, **kwargs)

    def save(self, directory: Path) -> Path:
        """持久化到 runs/<id>/session.json，进程重启后可恢复。"""
        self.updated_at = time.time()
        path = directory / "session.json"
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> TaskSession:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["budgets"] = Budgets(**data.get("budgets", {}))
        data["plan"] = [PlanStep(**step) for step in data.get("plan", [])]
        data["artifacts"] = [Artifact(**artifact) for artifact in data.get("artifacts", [])]
        return cls(**data)


def _slug(text: str, max_chars: int = 24) -> str:
    """目标文本转目录安全的短标识（保留中文，其余非词字符折叠为连字符）。"""
    cleaned = re.sub(r"[^\w一-鿿]+", "-", text).strip("-")
    return cleaned[:max_chars].rstrip("-") or "task"
