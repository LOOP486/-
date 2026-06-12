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
    return task_class, steps


_CHECK_TOOL_HINTS = {
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
        "success_checks": [c for c in step.get("success_checks", []) if isinstance(c, dict)]
        if isinstance(step.get("success_checks"), list)
        else [],
        "rollback_policy": rollback or "none",
        "step_budget": budget if isinstance(budget, dict) else {},
    }


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


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
