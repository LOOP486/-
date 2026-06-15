"""TaskRunner（K4）：驱动 TaskSession 的阶段状态机。

intake/plan → 逐步 [execute（步内微循环 AgentLoop）→ verify（judge）→ recover] → report
- fast path：trivial 任务单步直通；
- 步内微循环：状态机只管宏步骤边界与证据，模型在步内仍是自由 tool-calling；
- recover：验收未通过带 judge 理由重试，超出尝试上限则放弃（abort）。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ue5agent.agent.events import RunWriter
from ue5agent.agent.planner import make_plan
from ue5agent.agent.report import build_report
from ue5agent.agent.state import PlanStep
from ue5agent.agent.verifier import (
    VerifyResult,
    deterministic_verdict,
    evaluate_required_evidence,
    evaluate_success_checks,
    verify_step,
)
from ue5agent.agent.vision_review import VisionReviewResult
from ue5agent.core.context import build_project_brief
from ue5agent.core.errors import ErrorCategory, classify
from ue5agent.core.loop import AgentLoop, BudgetExhausted
from ue5agent.llm.types import ChatModel
from ue5agent.tools.registry import ScopedRegistry, ToolRegistry

KERNEL_SYSTEM_PROMPT = """\
你是按计划执行任务的工程 agent。完成当前步骤后用一句话总结做了什么；
修改类步骤必须先用工具产生验证证据（编译/测试/检查结果）再总结。
"""

VisionReviewer = Callable[[list[str], str], Awaitable[VisionReviewResult]]
"""视觉审查钩子（A4 子任务3）：入参 (截图路径列表, 关卡需求) → 结构化审查结果。
注入式解耦——runner 不直接依赖 vision_review/config，未配 vision 时传 None 即关闭。"""

# B3 恢复策略表：错误类别 → 恢复动作。未列出的类别（transient/ubt_compile_error/
# tool_arg_error/evidence_missing/permission_denied）走默认 "retry"——带 judge 理由
# 正常重试是这些类别的正确处理（编译错进修复循环、缺证据补采、参数错修正后重试）。
_RECOVERY_TABLE: dict[ErrorCategory, str] = {
    ErrorCategory.ENV_UNREADY: "abort_env",  # 桥从未开：重试无意义，快速终止
    ErrorCategory.BRIDGE_DOWN: "probe_bridge",  # 桥中途掉线：探活后定夺
    ErrorCategory.PARTIAL_SIDE_EFFECT: "rollback_retry",  # 半截副作用：先回滚再重试
}

_ABORT_HINTS: dict[ErrorCategory, str] = {
    ErrorCategory.ENV_UNREADY: (
        "[环境未就绪] 编辑器桥连接被拒：请先启动 UE 编辑器并加载工程"
        "（UnrealMCP 插件随工程加载）后重跑。环境就绪前不再重试。"
    ),
    ErrorCategory.BRIDGE_DOWN: (
        "[桥已掉线] 编辑器桥中途断开且探活仍不可达（编辑器可能已崩溃/退出）："
        "请重启 UE 编辑器后重跑。不再对死桥空转重试。"
    ),
}


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
        self.facts: list[dict] = []
        """本次尝试中工具附带的结构化事实（A3 证据信封），喂给确定性验收规则。"""
        self.error_categories: list[ErrorCategory] = []
        """本次尝试中工具失败的错误类别（B3），按出现顺序记录，供恢复策略表路由。"""

    def write(self, event: str, **data: Any) -> None:
        self._writer.write(event, **data)
        if event == "tool_call":
            preview = str(data.get("result_preview", ""))
            self.tool_lines.append(f"{data.get('tool')} -> {preview[:800]}")
            facts = data.get("facts")
            if isinstance(facts, dict):
                self.facts.append(facts)
            if preview.startswith(("[error]", "[denied]")):
                self.error_categories.append(classify(preview))

    @property
    def env_unready(self) -> bool:
        """本次尝试是否出现过环境未就绪错误（向后兼容；由错误类别派生）。"""
        return ErrorCategory.ENV_UNREADY in self.error_categories

    def dominant_error_category(self) -> ErrorCategory | None:
        """本次尝试用于恢复路由的主导错误类别：取最后一个工具失败的类别
        （最贴近"步骤为何没成"的现场），无失败则 None。"""
        return self.error_categories[-1] if self.error_categories else None

    def evidence(self, last: int = 12) -> str:
        return "\n".join(self.tool_lines[-last:])

    def reset(self) -> None:
        self.tool_lines.clear()
        self.facts.clear()
        self.error_categories.clear()


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
        vision_reviewer: VisionReviewer | None = None,
        vision_timeout_seconds: float = 120.0,
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
        self._vision_reviewer = vision_reviewer
        self._vision_timeout = vision_timeout_seconds

    def _build_step_loop(self, step: PlanStep, tee: _EvidenceTee) -> AgentLoop:
        """按步骤契约构造微循环（B1）：工具面收紧、预算只许收小不许放大。"""
        registry: Any = self._registry
        if step.allowed_tools or step.permission_ceiling:
            registry = ScopedRegistry(
                self._registry,
                allowed_tools=step.allowed_tools,
                permission_ceiling=step.permission_ceiling,
            )
        max_iterations = self._step_max_iterations
        wall_seconds = self._step_wall_seconds
        budget = step.step_budget or {}
        try:
            if budget.get("max_turns"):
                max_iterations = min(int(budget["max_turns"]), max_iterations)
            if budget.get("max_seconds"):
                wall_seconds = min(float(budget["max_seconds"]), wall_seconds)
        except (TypeError, ValueError):
            pass  # 预算字段不合法时沿用 runner 默认值
        return AgentLoop(
            self._llm,
            registry,
            system_prompt=self._system_prompt,
            max_iterations=max_iterations,
            max_wall_seconds=wall_seconds,
            session_log=tee,
        )

    async def _probe_editor_online(self) -> bool:
        """探活编辑器桥是否在线（bridge_down 恢复用）。无探测工具时保守返回 True，
        让步骤走正常重试而非误判掉线终止。"""
        probe = next((n for n in self._registry.names() if n.endswith("editor_status")), None)
        if probe is None:
            return True
        try:
            outcome = await self._registry.run(probe, "{}")
        except Exception:
            return False
        return outcome.text.lstrip().startswith("online")

    async def _check_preconditions(self, step: PlanStep) -> str | None:
        """探测步骤前置条件；未满足时返回补救指引（注入执行提示，由模型在步内补救）。"""
        unmet: list[str] = []
        for cond in step.preconditions:
            if cond == "editor_online":
                probe = next(
                    (n for n in self._registry.names() if n.endswith("editor_status")), None
                )
                if probe is None:
                    continue  # 无探测工具：条件未知，不拦截
                try:
                    outcome = await self._registry.run(probe, "{}")
                    online = outcome.text.lstrip().startswith("online")
                except Exception:
                    online = False
                if not online:
                    unmet.append(
                        "editor_online（编辑器桥不可达；若有 editor_launch 工具可先启动编辑器）"
                    )
            else:
                self._writer.event("precondition_unknown", step_id=step.id, condition=cond)
        if unmet:
            self._writer.event("precondition_unmet", step_id=step.id, conditions=unmet)
            return "；".join(unmet)
        return None

    async def _apply_rollback(self, step: PlanStep, facts: list[dict]) -> None:
        """步骤最终失败后的契约回滚。dangerous 级回滚（restore_checkpoint）只提示不自动执行。"""
        policy = step.rollback_policy
        if policy in ("", "none"):
            return
        if policy == "wb_clear":
            tool = next((n for n in self._registry.names() if n.endswith("wb_clear")), None)
            if tool is None:
                self._writer.event(
                    "rollback_action",
                    step_id=step.id,
                    policy=policy,
                    result="未挂载 wb_clear，跳过",
                )
                return
            # 清理必须用本步实际落地的前缀（模型可能没用默认 WB），从 wb_build 事实取
            prefix = next(
                (
                    f["prefix"]
                    for f in reversed(facts)
                    if f.get("kind") == "wb_build" and isinstance(f.get("prefix"), str)
                ),
                None,
            )
            arguments = json.dumps({"prefix": prefix}) if prefix else "{}"
            try:
                outcome = await self._registry.run(tool, arguments)
                result = outcome.text[:200]
            except Exception as exc:
                result = f"[error] {type(exc).__name__}: {exc}"
            self._writer.event("rollback_action", step_id=step.id, policy=policy, result=result)
            return
        # restore_checkpoint 等危险回滚：自动 checkpoint 已在写操作前打好，
        # 还原属 dangerous 级，留给用户决定（repo_restore + checkpoint ref）
        self._writer.event(
            "rollback_action",
            step_id=step.id,
            policy=policy,
            result="未自动执行（dangerous 级）；如需还原请用 repo_list_checkpoints + repo_restore",
        )

    async def _probe_project_brief(self) -> str:
        """B4：开场只读探测 editor_status/repo_status/engine_info，拼成工程状态摘要。

        探测工具不存在或失败的项静默跳过；全无结果时返回空串（调用方不注入）。
        探测走 registry（read 级），不消耗模型轮次。
        """

        async def probe(suffix: str) -> str | None:
            name = next((n for n in self._registry.names() if n.endswith(suffix)), None)
            if name is None:
                return None
            try:
                outcome = await self._registry.run(name, "{}")
            except Exception:
                return None
            return outcome.text if getattr(outcome, "ok", False) else None

        editor = await probe("editor_status")
        repo = await probe("repo_status")
        engine = await probe("engine_info")
        return build_project_brief(editor=editor, repo=repo, engine=engine)

    @staticmethod
    def _progress_line(plan: list[PlanStep], current_index: int) -> str:
        """注入每步提示的一行进度（即使步内 compact 也随新提示重述，不会丢任务进度）。"""
        done = [s.id for s in plan if s.status == "done"]
        failed = [s.id for s in plan if s.status == "failed"]
        remaining = [s.id for i, s in enumerate(plan) if i > current_index]
        cur = plan[current_index].id
        segs = [f"已完成 {done or '无'}"]
        if failed:
            segs.append(f"失败 {failed}")
        segs.append(f"当前 {cur}")
        segs.append(f"待办 {remaining or '无'}")
        return f"[进度] 共 {len(plan)} 步：" + "；".join(segs)

    @staticmethod
    def _render_progress(session: Any) -> str:
        """渲染 progress.md（B4）：每步收口刷新，供人/恢复查看。"""
        marks = {
            "done": "[x]",
            "failed": "[!]",
            "running": "[>]",
            "skipped": "[-]",
            "pending": "[ ]",
        }
        lines = [f"# 进度：{session.goal}", "", f"状态：{session.status}", ""]
        for step in session.plan:
            mark = marks.get(step.status, "[?]")
            lines.append(f"- {mark} {step.id} {step.intent}（{step.status}，尝试 {step.attempts}）")
        return "\n".join(lines) + "\n"

    async def _run_vision_review(
        self, step: PlanStep, goal: str, tee: _EvidenceTee
    ) -> VisionReviewResult | None:
        """A4 子任务3：对本步产出的截图做视觉审查，结果并入 tee.facts 驱动验收。

        触发条件：注入了 vision_reviewer（已配 vision 角色）且本步实际产出了截图
        （viewport_screenshot 落地的 screenshot 事实）。两者缺一则跳过——视觉审查是
        增量证据，绝不改变"没截图任务"的既有行为。审查结果以 vision_review 事实并入
        证据通道：存在 high 问题或解析失败 → ok=False，被 deterministic_verdict 判 fail。
        审查链路本身故障（vision 模型不可用等）只记 trace、不炸步骤验收。
        """
        if self._vision_reviewer is None:
            return None
        shots = [
            str(f["path"]) for f in tee.facts if f.get("kind") == "screenshot" and f.get("path")
        ]
        if not shots:
            return None
        # 硬超时兜底：litellm 对某些多模态端点（实测 moonshot）的调用会阻塞事件循环、
        # 不遵守自身 request_timeout，会无限冻结整个 run（wall budget 检查在步边界，
        # 拦不住步内挂起）。两点保证可靠超时：① reviewer 内部把 LLM 调用放进工作线程
        # （cli 接线），主事件循环保持空闲；② 这里用 asyncio.wait（而非 wait_for）——
        # 超时只放弃 pending 任务（线程成孤儿），不去 await 不可取消的执行器 future。
        task = asyncio.ensure_future(self._vision_reviewer(shots, goal))
        done, _pending = await asyncio.wait({task}, timeout=self._vision_timeout)
        if task not in done:
            self._writer.event(
                "vision_review_error",
                step_id=step.id,
                error=f"视觉审查超时（>{self._vision_timeout:.0f}s），本步降级为不做视觉门禁",
            )
            return None
        try:
            result = task.result()
        except Exception as exc:  # 视觉审查故障不应改变步骤命运，降级为"截图存档供人看"
            self._writer.event(
                "vision_review_error",
                step_id=step.id,
                error=f"{type(exc).__name__}: {exc}",
            )
            return None
        facts = result.to_facts()
        tee.facts.append(facts)
        self._writer.event(
            "vision_review",
            step_id=step.id,
            passed=result.passed,
            high_count=len(result.high_severity),
            summary=result.summary(),
            facts=facts,
        )
        return result

    async def run(self, goal: str) -> RunOutcome:
        session = self._session
        session.goal = goal
        self._writer.event("run_start", phase="intake", user_input=goal)

        deadline = time.monotonic() + self._total_wall_seconds
        self._writer.event("phase_enter", phase="plan")
        session.task_class, session.plan = await make_plan(
            self._llm,
            goal,
            tool_names=self._registry.names(),
        )
        self._writer.event(
            "phase_exit",
            phase="plan",
            task_class=session.task_class,
            steps=[s.intent for s in session.plan],
        )
        self._writer.save_session()

        # B4 上下文工程：开场一次性探测工程状态，作为 system 上下文注入（省去模型逐个探测的
        # 轮次）。预置到共享 history 的 system 消息里——位于首位，compact_history 永远保留它。
        brief = await self._probe_project_brief()
        system_content = f"{self._system_prompt}\n\n{brief}" if brief else self._system_prompt
        if brief:
            self._writer.event("context_brief", brief=brief)
        tee = _EvidenceTee(self._writer)
        history: list[dict[str, Any]] = [{"role": "system", "content": system_content}]
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
                    f"{self._progress_line(session.plan, index)}\n"
                    f"总目标：{goal}\n当前步骤（{step.id}）：{step.intent}\n"
                    f"验收标准：{step.acceptance or '无'}"
                )
                precondition_hint = await self._check_preconditions(step)
                if precondition_hint:
                    prompt += f"\n[前置条件未满足] {precondition_hint}——请先恢复环境再做本步骤。"
                loop = self._build_step_loop(step, tee)
                execution_verdict: VerifyResult | None = None
                try:
                    result = await loop.run(prompt, role="coder", history=history)
                    summaries[step.id] = result.final_text
                except BudgetExhausted as exc:
                    summaries[step.id] = f"[步内预算耗尽] {exc}"
                    execution_verdict = VerifyResult("fail", f"步内预算耗尽：{exc}")
                except Exception as exc:  # LLM/工具底层故障：记为步骤失败，不炸整个会话
                    reason = f"步骤执行异常：{type(exc).__name__}: {exc}"
                    summaries[step.id] = f"[{reason}]"
                    execution_verdict = VerifyResult("fail", reason)
                self._writer.event("phase_exit", phase="execute", step_id=step.id)

                # A4：对本步截图做视觉审查，结果并入 tee.facts（驱动下方确定性验收）
                vision_result = None
                if execution_verdict is None:
                    vision_result = await self._run_vision_review(step, goal, tee)

                # 验收优先级：硬证据门禁 → 步骤契约 success_checks（B1）
                # → 通用确定性规则（A3）→ LLM judge
                if execution_verdict is not None:
                    mode = "execution"
                    verdict = execution_verdict
                else:
                    mode = "judge"
                    det = evaluate_required_evidence(step.required_evidence, tee.facts)
                    if det is not None:
                        mode = "required_evidence"
                    contract = (
                        evaluate_success_checks(step.success_checks, tee.facts)
                        if step.success_checks
                        else None
                    )
                    decisive = deterministic_verdict(tee.facts)
                    if det is None and decisive is not None and decisive.verdict == "fail":
                        det = decisive
                        mode = "deterministic"
                    if det is None and contract is not None:
                        det = contract
                        mode = "contract"
                    if det is None and decisive is not None:
                        det = decisive
                        mode = "deterministic"
                    if det is not None:
                        verdict = det
                    else:
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
                            verdict = VerifyResult(
                                "fail", f"验收过程异常：{type(exc).__name__}: {exc}"
                            )
                self._writer.event(
                    "verify_result",
                    step_id=step.id,
                    verdict=verdict.verdict,
                    reason=verdict.reason,
                    mode=mode,
                )
                if verdict.verdict == "pass":
                    step.status = "done"
                    break
                if mode == "execution":
                    step.status = "failed"
                    aborted = True
                    self._writer.event(
                        "recover_action", step_id=step.id, action="abort", reason=verdict.reason
                    )
                    break
                # B3 恢复策略表：按主导错误类别差异化处理（默认=正常重试）
                category = tee.dominant_error_category()
                recovery = "retry" if category is None else _RECOVERY_TABLE.get(category, "retry")
                if recovery == "probe_bridge":
                    # 桥中途掉线：探活一次。仍不可达 → 当作环境性失败快速终止（踩坑史第 8 条：
                    # 别对死桥空转重试）；恢复在线 → 落入正常重试。
                    online = await self._probe_editor_online()
                    self._writer.event(
                        "recover_action",
                        step_id=step.id,
                        action="probe_bridge",
                        reason=f"bridge_down 探活：{'online' if online else 'offline'}",
                    )
                    recovery = "retry" if online else "abort_env"
                if recovery == "abort_env":
                    # 环境性失败（编辑器桥不可达/掉线）：重试只会空耗预算，直接终止并给指引
                    step.status = "failed"
                    aborted = True
                    hint = (
                        _ABORT_HINTS.get(category, _ABORT_HINTS[ErrorCategory.ENV_UNREADY])
                        if category is not None
                        else _ABORT_HINTS[ErrorCategory.ENV_UNREADY]
                    )
                    summaries[step.id] = f"{summaries.get(step.id, '')}\n\n{hint}".strip()
                    self._writer.event(
                        "recover_action",
                        step_id=step.id,
                        action="abort",
                        reason=f"{category.value if category else 'env'}：环境不可达，跳过重试",
                    )
                    break
                if step.attempts >= self._max_step_attempts:
                    step.status = "failed"
                    aborted = True
                    self._writer.event(
                        "recover_action", step_id=step.id, action="abort", reason=verdict.reason
                    )
                    await self._apply_rollback(step, tee.facts)
                    break
                if recovery == "rollback_retry":
                    # 部分副作用（如 spawn 落了一半）：重试前先回滚清理，避免残留叠加
                    await self._apply_rollback(step, tee.facts)
                self._writer.event(
                    "recover_action", step_id=step.id, action="retry", reason=verdict.reason
                )
                retry_note = (
                    f"步骤 {step.id} 验收未通过（{verdict.verdict}）：{verdict.reason}。"
                    "请修正并补充验证证据。"
                )
                # A4 局部重生成回灌：把视觉问题与问题区域喂回模型，引导其重新落地。
                # 整批重建（wb_build 先清同前缀再重搭）是兜底路径；模型应优先修问题区域。
                if vision_result is not None and not vision_result.passed:
                    areas = "、".join(sorted({i.area for i in vision_result.high_severity}))
                    retry_note += f"\n视觉审查：{vision_result.summary()}。"
                    if areas:
                        retry_note += (
                            f"请重点修正这些区域（{areas}）的布局，再用 wb_build 重新落地"
                            "（wb_build 会先整批清理同前缀旧构件再重建）。"
                        )
                history.append({"role": "user", "content": retry_note})
            self._writer.save_session()
            self._writer.write_progress(self._render_progress(session))

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
