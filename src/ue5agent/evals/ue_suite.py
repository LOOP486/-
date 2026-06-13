"""UE 在线评测档（E3 / C3）：用完整 TaskRunner（规划→执行→验收）跑需编辑器在线的任务，
量化一次通过率 / 迭代次数 / 人工干预次数。

与沙盒档（runner.py + sandbox.py）的区别：
- 沙盒档用裸 AgentLoop + 内存工具，度量"工具调用能力"，纯离线、作 CI 门禁。
- UE 档用 TaskRunner + 真实 MCP 工具面（含 ue_editor 桥），度量"端到端把活干对"的能力，
  需编辑器在线，作真机回归。

本模块只负责**编排 + 指标 + 检查器**（全部可离线单测）：真正构造 TaskRunner、挂载 MCP、
连接编辑器的胶水由 cli 提供 `run_one` 回调注入。这样跑分逻辑无需真机即可回归。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class UeEvalTask(BaseModel):
    name: str
    prompt: str
    checks: list[dict[str, Any]] = Field(min_length=1)
    max_iterations: int = 40


@dataclass
class UeRunRecord:
    """一次 UE 任务运行的可观测产物（由注入的 run_one 回调填充）。"""

    success: bool
    final_answer: str = ""
    iteration_count: int = 0
    """各步骤尝试次数之和（= run_end 的 turns），作"迭代次数"指标。"""
    max_step_attempts: int = 0
    """单步最多尝试次数；==1 表示全步一次通过、无重试。"""
    human_intervention: int = 0
    """人工确认介入次数（无人值守 eval 恒为 0；保留为交互式未来用）。"""
    error: str = ""
    """运行抛出异常时的描述（记为该任务失败，不中断整批）。"""


@dataclass
class UeTaskResult:
    name: str
    passed: bool
    failures: list[str]
    success: bool
    iteration_count: int
    max_step_attempts: int
    human_intervention: int


@dataclass
class UeEvalReport:
    results: list[UeTaskResult] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.passed) / len(self.results)

    @property
    def first_try_pass_rate(self) -> float:
        """一次通过率：通过、单步无重试（max_step_attempts<=1）、且零人工干预。"""
        if not self.results:
            return 0.0
        good = sum(
            1
            for r in self.results
            if r.passed and r.max_step_attempts <= 1 and r.human_intervention == 0
        )
        return good / len(self.results)

    @property
    def avg_iterations(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.iteration_count for r in self.results) / len(self.results)

    @property
    def total_human_intervention(self) -> int:
        return sum(r.human_intervention for r in self.results)


def load_ue_tasks(path: Path) -> list[UeEvalTask]:
    with path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, list):
        raise ValueError(f"{path} 应为任务列表")
    return [UeEvalTask.model_validate(item) for item in raw]


def evaluate_ue_check(check: dict[str, Any], record: UeRunRecord) -> str | None:
    """通过返回 None，失败返回原因。未知检查类型视为失败（任务文件写错要暴露）。"""
    kind = check.get("type")
    if kind == "run_succeeded":
        if record.success:
            return None
        return f"任务应成功收口，实际失败：{record.error or '验收未通过'}"
    if kind == "run_failed":
        # 故障注入用例：agent 应优雅 fail-fast，而非假成功
        return None if not record.success else "任务应判失败（故障注入），实际却成功收口"
    if kind == "final_contains":
        text = str(check["text"])
        return None if text in record.final_answer else f"最终答复应包含「{text}」"
    if kind == "final_contains_any":
        options = [str(x) for x in check.get("texts", [])]
        if any(opt in record.final_answer for opt in options):
            return None
        return f"最终答复应包含其中之一：{options}"
    if kind == "final_not_contains":
        text = str(check["text"])
        return None if text not in record.final_answer else f"最终答复不应包含「{text}」"
    if kind == "max_iterations":
        limit = int(check["value"])
        if record.iteration_count > limit:
            return f"迭代次数 {record.iteration_count} 超过上限 {limit}"
        return None
    if kind == "no_human_intervention":
        if record.human_intervention > 0:
            return f"出现 {record.human_intervention} 次人工干预"
        return None
    return f"未知检查类型：{kind}"


async def run_ue_suite(
    tasks: list[UeEvalTask],
    run_one: Callable[[UeEvalTask], Awaitable[UeRunRecord]],
) -> UeEvalReport:
    """逐个任务运行（run_one 由调用方注入：真机里构造 TaskRunner+MCP，测试里给替身）。"""
    results: list[UeTaskResult] = []
    for task in tasks:
        record = await run_one(task)
        failures: list[str] = []
        if record.error:
            failures.append(record.error)
        for check in task.checks:
            failure = evaluate_ue_check(check, record)
            if failure:
                failures.append(failure)
        results.append(
            UeTaskResult(
                name=task.name,
                passed=not failures,
                failures=failures,
                success=record.success,
                iteration_count=record.iteration_count,
                max_step_attempts=record.max_step_attempts,
                human_intervention=record.human_intervention,
            )
        )
    return UeEvalReport(results=results)
