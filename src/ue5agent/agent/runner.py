"""TaskRunner（K4）：驱动 TaskSession 的阶段状态机。

intake/plan → 逐步 [execute（步内微循环 AgentLoop）→ verify（judge）→ recover] → report
- fast path：trivial 任务单步直通；
- 步内微循环：状态机只管宏步骤边界与证据，模型在步内仍是自由 tool-calling；
- recover：验收未通过带 judge 理由重试，超出尝试上限则放弃（abort）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ue5agent.agent.events import RunWriter
from ue5agent.agent.planner import make_plan
from ue5agent.agent.report import build_report
from ue5agent.agent.verifier import VerifyResult, verify_step
from ue5agent.core.loop import AgentLoop, BudgetExhausted
from ue5agent.llm.types import ChatModel
from ue5agent.tools.registry import ToolRegistry

KERNEL_SYSTEM_PROMPT = """\
你是按计划执行任务的工程 agent。完成当前步骤后用一句话总结做了什么；
修改类步骤必须先用工具产生验证证据（编译/测试/检查结果）再总结。
"""


@dataclass
class RunOutcome:
    success: bool
    report: str
    session_id: str
    final_answer: str = ""
    """最后一个执行步骤的完整答复——查询类任务的交付物，不截断。"""


class _EvidenceTee:
    """转发 trace 的同时收集本步骤的工具证据（喂给 judge）。"""

    def __init__(self, writer: RunWriter):
        self._writer = writer
        self.tool_lines: list[str] = []

    def write(self, event: str, **data: Any) -> None:
        self._writer.write(event, **data)
        if event == "tool_call":
            self.tool_lines.append(
                f"{data.get('tool')} -> {str(data.get('result_preview', ''))[:800]}"
            )

    def evidence(self, last: int = 12) -> str:
        return "\n".join(self.tool_lines[-last:])

    def reset(self) -> None:
        self.tool_lines.clear()


class TaskRunner:
    def __init__(
        self,
        llm: ChatModel,
        registry: ToolRegistry,
        writer: RunWriter,
        *,
        system_prompt: str = KERNEL_SYSTEM_PROMPT,
        max_step_attempts: int = 3,
        step_max_iterations: int = 15,
        step_wall_seconds: float = 300.0,
        total_wall_seconds: float = 1200.0,
    ):
        self._llm = llm
        self._registry = registry
        self._writer = writer
        self._session = writer.session
        self._system_prompt = system_prompt
        self._max_step_attempts = max_step_attempts
        self._step_max_iterations = step_max_iterations
        self._step_wall_seconds = step_wall_seconds
        self._total_wall_seconds = total_wall_seconds

    async def run(self, goal: str) -> RunOutcome:
        session = self._session
        session.goal = goal
        self._writer.event("run_start", phase="intake", user_input=goal)

        deadline = time.monotonic() + self._total_wall_seconds
        self._writer.event("phase_enter", phase="plan")
        session.task_class, session.plan = await make_plan(
            self._llm, goal, tool_names=self._registry.names()
        )
        self._writer.event(
            "phase_exit",
            phase="plan",
            task_class=session.task_class,
            steps=[s.intent for s in session.plan],
        )
        self._writer.save_session()

        tee = _EvidenceTee(self._writer)
        loop = AgentLoop(
            self._llm,
            self._registry,
            system_prompt=self._system_prompt,
            max_iterations=self._step_max_iterations,
            max_wall_seconds=self._step_wall_seconds,
            session_log=tee,
        )
        history: list[dict[str, Any]] = []
        summaries: dict[str, str] = {}
        aborted = False

        for index, step in enumerate(session.plan):
            if aborted:
                step.status = "skipped"
                continue
            session.current_step = index
            step.status = "running"
            while True:
                if time.monotonic() >= deadline:
                    step.status = "failed"
                    aborted = True
                    summaries.setdefault(step.id, "[会话总预算耗尽]")
                    self._writer.event("budget_warning", step_id=step.id, reason="total_wall_clock")
                    break
                step.attempts += 1
                tee.reset()
                self._writer.event("phase_enter", phase="execute", step_id=step.id)
                prompt = (
                    f"总目标：{goal}\n当前步骤（{step.id}）：{step.intent}\n"
                    f"验收标准：{step.acceptance or '无'}"
                )
                try:
                    result = await loop.run(prompt, role="coder", history=history)
                    summaries[step.id] = result.final_text
                except BudgetExhausted as exc:
                    summaries[step.id] = f"[步内预算耗尽] {exc}"
                except Exception as exc:  # LLM/工具底层故障：记为步骤失败，不炸整个会话
                    summaries[step.id] = f"[步骤异常] {type(exc).__name__}: {exc}"
                self._writer.event("phase_exit", phase="execute", step_id=step.id)

                try:
                    verdict = await verify_step(
                        self._llm,
                        goal=goal,
                        intent=step.intent,
                        acceptance=step.acceptance,
                        evidence=tee.evidence(),
                        summary=summaries.get(step.id, ""),
                    )
                except Exception as exc:  # judge 不可用时按失败处理，走重试/放弃
                    verdict = VerifyResult("fail", f"验收过程异常：{type(exc).__name__}: {exc}")
                self._writer.event(
                    "verify_result",
                    step_id=step.id,
                    verdict=verdict.verdict,
                    reason=verdict.reason,
                )
                if verdict.verdict == "pass":
                    step.status = "done"
                    break
                if step.attempts >= self._max_step_attempts:
                    step.status = "failed"
                    aborted = True
                    self._writer.event(
                        "recover_action", step_id=step.id, action="abort", reason=verdict.reason
                    )
                    break
                self._writer.event(
                    "recover_action", step_id=step.id, action="retry", reason=verdict.reason
                )
                history.append(
                    {
                        "role": "user",
                        "content": (
                            f"步骤 {step.id} 验收未通过（{verdict.verdict}）：{verdict.reason}。"
                            "请修正并补充验证证据。"
                        ),
                    }
                )
            self._writer.save_session()

        session.status = "aborted" if aborted else "done"
        executed = [s for s in session.plan if s.status in ("done", "failed")]
        final_answer = summaries.get(executed[-1].id, "") if executed else ""
        self._writer.event("phase_enter", phase="final_report")
        report = build_report(session, summaries, final_answer=final_answer)
        self._writer.write_report(report)
        self._writer.save_session()
        self._writer.event(
            "run_end",
            turns=sum(s.attempts for s in session.plan),
            tool_calls=len(tee.tool_lines),
        )
        return RunOutcome(
            success=not aborted,
            report=report,
            session_id=session.id,
            final_answer=final_answer,
        )
