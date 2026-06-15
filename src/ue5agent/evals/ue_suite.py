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

from ue5agent.agent.events import read_events


class UeEvalTask(BaseModel):
    name: str
    prompt: str
    checks: list[dict[str, Any]] = Field(min_length=1)
    max_iterations: int = 40
    prompt_id: str | None = None
    """标准评测的提示词版本；用于固定 SPC/DST 初始题面。"""
    prompt_locked: bool = False
    """为 True 表示该任务已作为标准题面冻结，后续只追加新版本不原地改写。"""
    planner_model: str | None = None
    """该任务固定使用的文本规划模型；为空则沿用 CLI/config。"""
    vision_model: str | None = None
    """该任务固定使用的视觉角色模型；为空则沿用 CLI/config。"""


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
    trace_path: str = ""
    """本次运行的 trace.jsonl 路径，用于黑盒复盘。"""
    run_dir: str = ""
    """本次运行的 runs/<session> 目录。"""
    tool_calls: list[str] = field(default_factory=list)
    """trace 中按顺序记录的工具调用名。"""
    facts: list[dict[str, Any]] = field(default_factory=list)
    """trace 中工具回传的结构化 facts。"""
    tool_errors: list[str] = field(default_factory=list)
    """trace 中以 [error]/[denied] 开头的工具结果摘要。"""


@dataclass
class UeTaskResult:
    name: str
    passed: bool
    failures: list[str]
    failure_type: str
    """失败类型：用于 baseline 聚合，不再把 LLM timeout/视觉/几何问题混成一类。"""
    success: bool
    iteration_count: int
    max_step_attempts: int
    human_intervention: int
    trace_path: str = ""
    tool_calls: list[str] = field(default_factory=list)
    tool_errors: list[str] = field(default_factory=list)


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
    if kind == "no_tool_errors":
        if record.tool_errors:
            return "trace 中出现工具错误：" + "；".join(record.tool_errors[:5])
        return None
    if kind == "no_unrecovered_tool_errors":
        if not record.tool_errors or record.success:
            return None
        return "trace 中出现未恢复工具错误：" + "；".join(record.tool_errors[:5])
    if kind == "tool_called":
        tool = str(check["tool"])
        at_least = int(check.get("at_least", 1))
        count = sum(1 for call in record.tool_calls if _tool_matches(call, tool))
        if count >= at_least:
            return None
        return f"trace 应调用工具 {tool} 至少 {at_least} 次，实际 {count} 次"
    if kind == "tool_not_called":
        tool = str(check["tool"])
        count = sum(1 for call in record.tool_calls if _tool_matches(call, tool))
        if count == 0:
            return None
        return f"trace 不应调用工具 {tool}，实际 {count} 次"
    if kind in {"fact_equals", "fact_lte", "fact_gte", "fact_nonempty"}:
        fact = _latest_fact(record.facts, str(check["kind"]))
        if fact is None:
            return f"trace 缺少 facts kind={check['kind']}"
        path = str(check.get("path", "ok"))
        actual = _field_path(fact, path)
        if kind == "fact_nonempty":
            if actual:
                return None
            return f"facts {check['kind']}.{path} 为空或缺失"
        if kind == "fact_equals":
            expected = check.get("equals")
            if actual == expected:
                return None
            return f"facts {check['kind']}.{path}={actual!r}，期望 {expected!r}"
        expected_num = float(check["value"])
        try:
            actual_num = float(actual)
        except (TypeError, ValueError):
            return f"facts {check['kind']}.{path}={actual!r} 不是数值"
        if kind == "fact_lte":
            return (
                None
                if actual_num <= expected_num
                else (f"facts {check['kind']}.{path}={actual_num}，应 <= {expected_num}")
            )
        return (
            None
            if actual_num >= expected_num
            else (f"facts {check['kind']}.{path}={actual_num}，应 >= {expected_num}")
        )
    if kind == "fact_any":
        fact_kind = str(check["kind"])
        conditions = check.get("where", [])
        if not isinstance(conditions, list) or not conditions:
            return "fact_any 缺少 where 条件"
        candidates = [fact for fact in record.facts if fact.get("kind") == fact_kind]
        if any(_fact_matches_conditions(fact, conditions) for fact in candidates):
            return None
        return f"trace 中没有任何 {fact_kind} fact 同时满足 {conditions}"
    return f"未知检查类型：{kind}"


def summarize_trace(path: Path) -> dict[str, Any]:
    """从 trace 中抽取黑盒 eval 需要的最小摘要：工具调用、facts、工具错误。"""
    tool_calls: list[str] = []
    facts: list[dict[str, Any]] = []
    tool_errors: list[str] = []
    run_errors: list[str] = []
    if not path.exists():
        return {
            "tool_calls": tool_calls,
            "facts": facts,
            "tool_errors": tool_errors,
            "run_errors": run_errors,
        }
    for event in read_events(path):
        fact = event.get("facts")
        if isinstance(fact, dict):
            facts.append(fact)
        if event.get("event") == "run_error":
            failure_type = str(event.get("failure_type") or "execution_error")
            error = str(event.get("error") or "").strip()
            run_errors.append(f"{failure_type}: {error}" if error else failure_type)
            continue
        if event.get("event") != "tool_call":
            continue
        tool = str(event.get("tool", ""))
        if tool:
            tool_calls.append(tool)
        preview = str(event.get("result_preview", ""))
        if preview.startswith(("[error]", "[denied]", "Error executing tool")):
            tool_errors.append(f"{tool}: {preview}")
    return {
        "tool_calls": tool_calls,
        "facts": facts,
        "tool_errors": tool_errors,
        "run_errors": run_errors,
    }


def _tool_matches(actual: str, expected: str) -> bool:
    return actual == expected or actual.endswith(f"__{expected}")


def _latest_fact(facts: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    for fact in reversed(facts):
        if fact.get("kind") == kind:
            return fact
    return None


def _field_path(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _fact_matches_conditions(fact: dict[str, Any], conditions: list[Any]) -> bool:
    for condition in conditions:
        if not isinstance(condition, dict):
            return False
        actual = _field_path(fact, str(condition.get("path", "")))
        if "equals" in condition and actual != condition["equals"]:
            return False
        if "gte" in condition:
            try:
                if float(actual) < float(condition["gte"]):
                    return False
            except (TypeError, ValueError):
                return False
        if "lte" in condition:
            try:
                if float(actual) > float(condition["lte"]):
                    return False
            except (TypeError, ValueError):
                return False
    return True


_ENV_UNREADY_MARKERS = (
    "[env:unready]",
    "编辑器桥连接被拒",
    "连不上编辑器桥",
    "编辑器桥通信中断",
    "编辑器桥通信失败",
    "ConnectionRefusedError",
    "WinError 10061",
)


def classify_ue_failure_type(record: UeRunRecord, failures: list[str]) -> str:
    """给 UE eval 失败打稳定分类，便于 baseline 复盘和聚合。"""
    if not failures:
        return ""
    text = "\n".join(
        [
            str(record.error or ""),
            *(str(item) for item in record.tool_errors),
            *(str(item) for item in failures),
        ]
    )
    if "llm_timeout" in text or "LLM 请求被取消" in text:
        return "llm_timeout"
    if any(marker in text for marker in _ENV_UNREADY_MARKERS):
        return "env_unready"

    vision = _latest_fact(record.facts, "vision_review")
    if vision is not None:
        parsed = vision.get("parsed")
        high_count = _as_number(vision.get("high_count"))
        issue_count = _as_number(vision.get("issue_count"))
        if parsed is False:
            return "vision_parse"
        if high_count is not None and high_count > 0:
            return "vision_high"
        if issue_count is not None and issue_count > 0:
            return "vision_medium_low"

    validate = _latest_fact(record.facts, "wb_validate")
    if validate is not None and validate.get("ok") is False:
        return "geometry_check"
    if "布局校验" in text or "LayoutError" in text:
        return "layout_error"
    if "trace 缺少 facts" in text or "为空或缺失" in text:
        return "evidence_missing"
    if record.tool_errors:
        return "tool_error"
    if record.error:
        return "run_error"
    return "check_failed"


def _as_number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
        failure_type = classify_ue_failure_type(record, failures)
        results.append(
            UeTaskResult(
                name=task.name,
                passed=not failures,
                failures=failures,
                failure_type=failure_type,
                success=record.success,
                iteration_count=record.iteration_count,
                max_step_attempts=record.max_step_attempts,
                human_intervention=record.human_intervention,
                trace_path=record.trace_path,
                tool_calls=record.tool_calls,
                tool_errors=record.tool_errors,
            )
        )
    return UeEvalReport(results=results)
