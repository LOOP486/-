"""评测 runner：加载任务、逐个在干净沙盒中运行、汇总报告。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from ue5agent.core.loop import AgentLoop, LoopResult
from ue5agent.evals.checks import TaskOutcome, evaluate_check
from ue5agent.evals.sandbox import build_sandbox_registry
from ue5agent.llm.types import ChatModel

EVAL_SYSTEM_PROMPT = """\
你在评测沙盒中工作。用可用的工具准确完成任务；任务说不调用工具时就直接回答。
完成后给出简短的最终答复。
"""


class EvalTask(BaseModel):
    name: str
    prompt: str
    checks: list[dict[str, Any]] = Field(min_length=1)
    max_turns: int = 8


@dataclass
class TaskRunResult:
    name: str
    passed: bool
    failures: list[str]
    turns: int
    tool_calls: int
    prompt_tokens: int
    completion_tokens: int


@dataclass
class EvalReport:
    results: list[TaskRunResult]

    @property
    def pass_rate(self) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.passed) / len(self.results)

    @property
    def total_tokens(self) -> int:
        return sum(r.prompt_tokens + r.completion_tokens for r in self.results)


def load_tasks(path: Path) -> list[EvalTask]:
    with path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, list):
        raise ValueError(f"{path} 应为任务列表")
    return [EvalTask.model_validate(item) for item in raw]


async def run_eval(
    tasks: list[EvalTask],
    model_factory: Callable[[], ChatModel],
    *,
    role: str = "planner",
) -> EvalReport:
    """每个任务用干净的沙盒与全新对话运行，互不串扰。"""
    results = [await _run_task(task, model_factory(), role) for task in tasks]
    return EvalReport(results=results)


async def _run_task(task: EvalTask, model: ChatModel, role: str) -> TaskRunResult:
    registry = build_sandbox_registry()
    loop = AgentLoop(
        model,
        registry,
        system_prompt=EVAL_SYSTEM_PROMPT,
        max_iterations=task.max_turns,
    )
    result: LoopResult | None = None
    failures: list[str] = []
    try:
        result = await loop.run(task.prompt, role=role)
    except Exception as exc:  # 评测中任何异常记为该任务失败，不中断整批
        failures.append(f"{type(exc).__name__}: {exc}")
    outcome = TaskOutcome(result=result, calls=registry.calls, tool_results=registry.results)
    for check in task.checks:
        failure = evaluate_check(check, outcome)
        if failure:
            failures.append(failure)
    return TaskRunResult(
        name=task.name,
        passed=not failures,
        failures=failures,
        turns=result.turns if result else task.max_turns,
        tool_calls=result.tool_call_count if result else len(registry.calls),
        prompt_tokens=result.prompt_tokens if result else 0,
        completion_tokens=result.completion_tokens if result else 0,
    )
