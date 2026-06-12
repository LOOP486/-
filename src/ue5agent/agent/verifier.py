"""验收 gate（K4 + A3 两段式）：确定性规则先行，LLM judge 兜底。

A3 起工具可经 [facts] 标记行附带结构化事实（ToolOutcome.facts）。本模块的
deterministic_verdict 基于事实做规则判定：可判则直接给结论（不调 LLM，省 token
且消除误判）；无事实或事实不可判时回落到 LLM judge（verify_step），行为同 K4。
"""

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


_DECISIVE_KINDS = ("compile", "wb_validate", "path_test")
"""可单独支撑 pass 结论的事实类别：均为独立验证动作的客观输出。
wb_build 这类"操作成功"事实只参与 fail 判定（建好不等于建对）。"""


def deterministic_verdict(facts: list[dict]) -> VerifyResult | None:
    """确定性验收规则：按类别取最新事实，规则可判则返回结论，否则 None。

    - 任一类别最新事实 ok=False → fail（客观失败没有商量余地）；
    - 存在决定性类别（compile/wb_validate/path_test）且全部 ok → pass；
    - 无事实、或只有非决定性事实 → None（交给 LLM judge）。
    """
    latest: dict[str, dict] = {}
    for fact in facts:
        kind = fact.get("kind")
        if isinstance(kind, str):
            latest[kind] = fact
    if not latest:
        return None
    failed = {kind: f for kind, f in latest.items() if f.get("ok") is False}
    if failed:
        details = "；".join(f"{kind}（{_fact_brief(f)}）" for kind, f in failed.items())
        return VerifyResult("fail", f"确定性证据失败：{details}")
    if any(kind in latest for kind in _DECISIVE_KINDS):
        kinds = "、".join(kind for kind in latest if kind in _DECISIVE_KINDS)
        return VerifyResult("pass", f"确定性证据通过：{kinds}")
    return None


def _fact_brief(fact: dict) -> str:
    keep = {
        k: v for k, v in fact.items() if k not in ("kind", "ok") and not isinstance(v, dict | list)
    }
    return json.dumps(keep, ensure_ascii=False) if keep else "无详情"


def evaluate_success_checks(checks: list[dict], facts: list[dict]) -> VerifyResult | None:
    """步骤契约的声明式验收（B1）：每条 check 绑定一类事实。

    check 形如 {"kind": "path_test", "field": "reachable", "equals": true}，
    field/equals 缺省为 ok/true。判定：
    - 任一 check 的事实存在但字段不符 → fail；
    - 任一 check 的事实缺失 → insufficient（驱动执行方补调验证工具）；
    - 全部满足 → pass；checks 无有效条目 → None（回落到通用规则/judge）。
    """
    latest: dict[str, dict] = {}
    for fact in facts:
        kind = fact.get("kind")
        if isinstance(kind, str):
            latest[kind] = fact
    missing: list[str] = []
    failed: list[str] = []
    valid = 0
    for check in checks:
        if not isinstance(check, dict):
            continue
        kind = str(check.get("kind", "")).strip()
        if not kind:
            continue
        valid += 1
        matched = latest.get(kind)
        if matched is None:
            missing.append(kind)
            continue
        field_name = str(check.get("field", "ok"))
        expected = check.get("equals", True)
        actual = matched.get(field_name)
        if actual != expected:
            failed.append(f"{kind}.{field_name}={actual!r}（期望 {expected!r}）")
    if not valid:
        return None
    if failed:
        return VerifyResult("fail", "契约检查未过：" + "；".join(failed))
    if missing:
        return VerifyResult(
            "insufficient",
            "缺少契约要求的证据：" + "、".join(missing) + "——请调用相应验证工具产生证据",
        )
    return VerifyResult("pass", f"契约检查全部通过（{valid} 项）")


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
