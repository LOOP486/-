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
        )
        for i, step in enumerate(raw_steps)
    ]
    return task_class, steps


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
