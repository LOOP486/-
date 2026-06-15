"""计划生成（K4）：intake 与 plan 合一，产出 task_class 与 PlanStep 列表。

模型输出不可靠时回退为单步 standard 计划——规划失败不应让任务死掉。
"""

from __future__ import annotations

import json
import re
from typing import Any

from ue5agent.agent.state import PlanStep
from ue5agent.llm.types import ChatModel

PLAN_PROMPT = """\
你是任务规划器。根据用户任务输出 JSON（不要其它文字）：
{"task_class": "trivial 或 standard",
 "steps": [{"intent": "这一步做什么", "acceptance": "怎样算完成（可验证）"}]}

规则：
- trivial：单步即可完成的查询或小改动，steps 恰好 1 个；
- standard：拆 2-5 步，每步的 acceptance 必须可被证据验证（如"编译零错误"）；
- 修改类任务的最后一步必须包含验证（编译/测试/检查）；
- 步骤只做任务直接需要的事——不要安排环境检查、状态确认等无关步骤；
- 任务一个工具能完成时就规划成 1-2 步，不要为了凑步骤而拆分。
- 白盒搭建若要求俯视截图自查/视觉审查：把"搭建 + 俯视截图自查"放在**同一步**，
  该步 allowed_tools 同时含 wb_build、wb_clear、viewport_screenshot——视觉审查会对
  本步截图判定，发现布局问题需就地用 wb_build 重建，截图与重建分到两步会让该步无法修正。

每步还可附加可选契约字段（不确定就省略，省略即不限制）：
- "allowed_tools": [本步允许用的工具名]——仅当你确定该步只需这几个工具；
- "permission_ceiling": "read"——只读步骤（查询/分析）建议声明，防止误改；
- "preconditions": ["editor_online"]——需要 UE 编辑器在线的步骤建议声明；
- "success_checks": [{"kind": "compile|wb_validate|path_test", "field": "ok", "equals": true}]
  ——仅当该步会调用对应验证工具（ubt_compile/wb_validate/path_test）时才声明，
  查询类步骤和没有这些工具的任务不要写；
- "required_evidence": ["screenshot", "vision_review"]——白盒搭建要求截图/视觉门禁时声明；
- "rollback_policy": "wb_clear"——白盒搭建类步骤失败时整批回滚。
"""

_FENCE = re.compile(r"^```(?:json)?\s*(?P<body>.*?)\s*```\s*$", re.DOTALL)


async def make_plan(
    llm: ChatModel,
    goal: str,
    *,
    role: str = "planner",
    tool_names: list[str] | None = None,
) -> tuple[str, list[PlanStep]]:
    system = PLAN_PROMPT
    if tool_names:
        system += f"\n可用工具（步骤里只能依赖这些）：{', '.join(tool_names)}\n"
    turn = await llm.acomplete(
        role,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": goal},
        ],
    )
    parsed = _parse(turn.content or "")
    if parsed is None:
        return "standard", [PlanStep(id="s1", intent=goal, acceptance="任务目标达成且有验证证据")]
    task_class, raw_steps = parsed
    steps = [
        PlanStep(
            id=f"s{i + 1}",
            intent=str(step.get("intent", "")).strip() or goal,
            acceptance=str(step.get("acceptance", "")).strip(),
            **_contract_fields(step),
        )
        for i, step in enumerate(raw_steps)
    ]
    for step in steps:
        _reconcile_contract(step)
    _reconcile_split_whitebox_build_steps(steps)
    _reconcile_combined_whitebox_build_validation_tools(steps)
    _reconcile_split_whitebox_repair_tools(steps)
    for step in steps:
        _reconcile_contract(step)
    _strip_unrequested_whitebox_visual_gate(goal, steps)
    _reconcile_whitebox_visual_gate(goal, steps)
    return task_class, steps


_CHECK_TOOL_HINTS = {
    "wb_build": "wb_build",
    "wb_validate": "wb_validate",
    "path_test": "path_test",
    "compile": "ubt_compile",
}


def _reconcile_contract(step: PlanStep) -> None:
    """契约自洽性修正：success_checks 要求的验证工具必须在 allowed_tools 里。

    否则工具面过滤后模型看不见验证工具，证据永远补不上——insufficient 重试
    直到放弃（真机 e2e 实测教训）。裸名即可，ScopedRegistry 支持裸名匹配。
    """
    if not step.allowed_tools or not step.success_checks:
        return
    for check in step.success_checks:
        hint = _CHECK_TOOL_HINTS.get(str(check.get("kind", "")).strip())
        if hint and not any(
            tool == hint or tool.endswith(f"__{hint}") for tool in step.allowed_tools
        ):
            step.allowed_tools.append(hint)


def _reconcile_split_whitebox_build_steps(steps: list[PlanStep]) -> None:
    """白盒 build 与 validate 拆步时，build 步用 wb_build fact 收口，避免卡在 judge。"""
    for index, step in enumerate(steps):
        if step.success_checks or not _looks_like_whitebox_build(step.intent):
            continue
        if not _step_allows_tool(step, "wb_build"):
            continue
        if not any(_looks_like_whitebox_validation(later) for later in steps[index + 1 :]):
            continue
        _ensure_allowed_tool(step, "wb_clear")
        _ensure_allowed_tool(step, "wb_validate")
        step.success_checks.append({"kind": "wb_build", "field": "ok", "equals": True})


def _reconcile_split_whitebox_repair_tools(steps: list[PlanStep]) -> None:
    """白盒验证拆步时保留对应修复工具，允许 agent 自我修复布局。"""
    seen_whitebox_build = False
    for step in steps:
        if _looks_like_whitebox_build(step.intent) and _step_allows_tool(step, "wb_build"):
            seen_whitebox_build = True
            continue
        if not seen_whitebox_build or not _looks_like_whitebox_validation(step):
            continue
        tools = ["wb_clear", "wb_build", "wb_validate"]
        if _looks_like_whitebox_nav_validation(step):
            tools.extend(["navmesh_rebuild", "path_test"])
        for tool in tools:
            _ensure_allowed_tool(step, tool)


def _reconcile_combined_whitebox_build_validation_tools(steps: list[PlanStep]) -> None:
    """同一步 build+validate 时保留清理工具，允许失败后整批重搭。"""
    for step in steps:
        if not _looks_like_whitebox_build(step.intent) or not _looks_like_whitebox_validation(step):
            continue
        if not _step_allows_tool(step, "wb_build"):
            continue
        for tool in ("wb_clear", "wb_build", "wb_validate"):
            _ensure_allowed_tool(step, tool)


def _step_allows_tool(step: PlanStep, tool_name: str) -> bool:
    if not step.allowed_tools:
        return True
    return any(tool == tool_name or tool.endswith(f"__{tool_name}") for tool in step.allowed_tools)


def _ensure_allowed_tool(step: PlanStep, tool_name: str) -> None:
    if not step.allowed_tools:
        return
    if not _step_allows_tool(step, tool_name):
        step.allowed_tools.append(tool_name)


def _looks_like_whitebox_validation(step: PlanStep) -> bool:
    text = " ".join(
        [
            step.intent,
            step.acceptance,
            *(str(check.get("kind", "")) for check in step.success_checks),
        ]
    ).lower()
    return "wb_validate" in text or "path_test" in text or "navmesh" in text


def _looks_like_whitebox_nav_validation(step: PlanStep) -> bool:
    text = " ".join(
        [
            step.intent,
            step.acceptance,
            *(str(check.get("kind", "")) for check in step.success_checks),
        ]
    ).lower()
    return any(token in text for token in ("path_test", "navmesh", "导航", "可达"))


def _reconcile_whitebox_visual_gate(goal: str, steps: list[PlanStep]) -> None:
    """白盒搭建 + 视觉审查必须在同一步形成硬证据闭环。"""
    if not _needs_whitebox_visual_gate(goal, steps):
        return
    build_step = next((step for step in steps if _looks_like_whitebox_build(step.intent)), None)
    if build_step is None:
        return

    for evidence in ("screenshot", "vision_review"):
        if evidence not in build_step.required_evidence:
            build_step.required_evidence.append(evidence)
    for tool in ("wb_build", "wb_clear", "viewport_screenshot"):
        if not any(
            existing == tool or existing.endswith(f"__{tool}")
            for existing in build_step.allowed_tools
        ):
            build_step.allowed_tools.append(tool)
    if "editor_online" not in build_step.preconditions:
        build_step.preconditions.append("editor_online")
    if build_step.rollback_policy == "none":
        build_step.rollback_policy = "wb_clear"


def _needs_whitebox_visual_gate(goal: str, steps: list[PlanStep]) -> bool:
    text = goal.lower()
    wants_whitebox = any(token in text for token in ("白盒", "whitebox", "wb_build"))
    wants_visual = _goal_requests_visual_gate(goal)
    return wants_whitebox and wants_visual


def _strip_unrequested_whitebox_visual_gate(goal: str, steps: list[PlanStep]) -> None:
    """用户未明确要求截图/视觉时，撤掉 planner 幻觉出的视觉硬门禁。"""
    if _goal_requests_visual_gate(goal):
        return
    visual_evidence = {"screenshot", "vision_review"}
    visual_tools = {"viewport_screenshot"}
    for step in steps:
        if not (_looks_like_whitebox_build(step.intent) or _looks_like_whitebox_validation(step)):
            continue
        step.required_evidence = [
            evidence for evidence in step.required_evidence if evidence not in visual_evidence
        ]
        step.allowed_tools = [
            tool
            for tool in step.allowed_tools
            if not any(tool == name or tool.endswith(f"__{name}") for name in visual_tools)
        ]


def _goal_requests_visual_gate(goal: str) -> bool:
    text = goal.lower()
    negative_tokens = (
        "不做截图",
        "不要截图",
        "无需截图",
        "不要调用截图",
        "不要调用 viewport_screenshot",
        "不要使用 viewport_screenshot",
        "不调用 viewport_screenshot",
        "禁用 viewport_screenshot",
        "不做视觉",
        "不要视觉",
        "无需视觉",
        "no screenshot",
        "do not call viewport_screenshot",
        "do not use viewport_screenshot",
        "no vision",
        "without screenshot",
        "without vision",
    )
    if any(token in text for token in negative_tokens):
        return False
    return any(
        token in text
        for token in ("截图", "视觉", "vision_review", "viewport_screenshot", "screenshot")
    )


def _looks_like_whitebox_build(intent: str) -> bool:
    text = intent.lower()
    return ("白盒" in text or "whitebox" in text or "wb_build" in text) and any(
        token in text for token in ("搭建", "构建", "生成", "build", "落地")
    )


_CEILING_VALUES = ("read", "write_safe", "write_project", "dangerous")


def _contract_fields(step: dict[str, Any]) -> dict[str, Any]:
    """容错提取契约字段（B1）：类型不符一律回退默认值，绝不让坏计划炸掉任务。"""
    ceiling = str(step.get("permission_ceiling", "")).strip()
    rollback = str(step.get("rollback_policy", "")).strip()
    budget = step.get("step_budget")
    return {
        "allowed_tools": _str_list(step.get("allowed_tools")),
        "permission_ceiling": ceiling if ceiling in _CEILING_VALUES else "",
        "preconditions": _str_list(step.get("preconditions")),
        "success_checks": _normalize_success_checks(
            [c for c in step.get("success_checks", []) if isinstance(c, dict)]
        )
        if isinstance(step.get("success_checks"), list)
        else [],
        "required_evidence": _str_list(step.get("required_evidence")),
        "rollback_policy": rollback or "none",
        "step_budget": budget if isinstance(budget, dict) else {},
    }


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _normalize_success_checks(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """修正弱模型常见字段别名，避免工具 facts 已正确却被契约误判。"""
    normalized: list[dict[str, Any]] = []
    for check in checks:
        item = dict(check)
        kind = str(item.get("kind", "")).strip()
        field = str(item.get("field", "")).strip()
        if kind == "path_test" and field == "success":
            item["field"] = "reachable"
        normalized.append(item)
    return normalized


def _parse(text: str) -> tuple[str, list[dict[str, Any]]] | None:
    body = text.strip()
    fence = _FENCE.match(body)
    if fence:
        body = fence.group("body")
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    steps = data.get("steps")
    task_class = data.get("task_class")
    if task_class not in ("trivial", "standard") or not isinstance(steps, list) or not steps:
        return None
    return task_class, steps
