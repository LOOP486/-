"""验收 gate（K4）：judge 角色只看目标/验收标准/工具证据，不信自我陈述。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ue5agent.llm.types import ChatModel

JUDGE_PROMPT = """\
你是验收员。根据步骤的验收标准和执行证据，输出 JSON（不要其它文字）：
{"verdict": "pass 或 fail 或 insufficient", "reason": "一句话理由"}

- pass：证据明确支持验收标准已达成；
- fail：证据表明未达成或做错了；
- insufficient：证据不足以判断（执行方应补充验证证据后重试）。
判定基准：
- 修改类步骤（写代码/改资产/编译）：以工具返回的客观结果为准，不轻信总结陈述；
- 查询/解释类步骤：执行方的答复本身就是交付物，只要证据能支撑其关键事实即 pass，
  不要因证据是摘要形式而判 insufficient。
"""

_FENCE = re.compile(r"^```(?:json)?\s*(?P<body>.*?)\s*```\s*$", re.DOTALL)


@dataclass
class VerifyResult:
    verdict: str  # pass | fail | insufficient
    reason: str


async def verify_step(
    llm: ChatModel,
    *,
    goal: str,
    intent: str,
    acceptance: str,
    evidence: str,
    summary: str,
    role: str = "judge",
) -> VerifyResult:
    """acceptance 为空的步骤（纯查询类）自动通过。"""
    if not acceptance:
        return VerifyResult("pass", "无验收标准，自动通过")
    turn = await llm.acomplete(
        role,
        [
            {"role": "system", "content": JUDGE_PROMPT},
            {
                "role": "user",
                "content": (
                    f"总目标：{goal}\n步骤意图：{intent}\n验收标准：{acceptance}\n\n"
                    f"工具证据（客观）：\n{evidence or '（无工具调用）'}\n\n"
                    f"执行方总结（仅供参考）：{summary}"
                ),
            },
        ],
    )
    return _parse(turn.content or "")


def _parse(text: str) -> VerifyResult:
    body = text.strip()
    fence = _FENCE.match(body)
    if fence:
        body = fence.group("body")
    try:
        data = json.loads(body)
        verdict = str(data.get("verdict", "")).lower()
        if verdict in ("pass", "fail", "insufficient"):
            return VerifyResult(verdict, str(data.get("reason", "")))
    except json.JSONDecodeError:
        pass
    # judge 输出不可解析时保守处理：要求补证据而不是放行
    return VerifyResult("insufficient", f"验收输出不可解析：{body[:80]}")
