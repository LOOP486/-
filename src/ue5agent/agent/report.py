"""最终变更报告（K4）：从 TaskSession 生成 markdown。"""

from __future__ import annotations

from ue5agent.agent.state import TaskSession

_STATUS_LABEL = {
    "done": "✅ 完成",
    "failed": "❌ 失败",
    "skipped": "⏭ 跳过",
    "pending": "待执行",
    "running": "执行中",
}


def build_report(session: TaskSession, summaries: dict[str, str], final_answer: str = "") -> str:
    lines = [
        f"# 任务报告：{session.goal}",
        "",
        f"- 会话：{session.id}",
        f"- 结果：{'完成' if session.status == 'done' else session.status}"
        f"（{session.task_class}，{len(session.plan)} 步）",
        "",
    ]
    if final_answer:
        lines += ["## 结果", "", final_answer, ""]
    lines += ["## 步骤", ""]
    for step in session.plan:
        label = _STATUS_LABEL.get(step.status, step.status)
        lines.append(f"### {step.id} {step.intent} — {label}（尝试 {step.attempts} 次）")
        if step.acceptance:
            lines.append(f"- 验收标准：{step.acceptance}")
        summary = summaries.get(step.id)
        if summary:
            lines.append(f"- 执行小结：{summary[:300]}")
        lines.append("")
    if session.artifacts:
        lines.append("## 产物")
        lines.extend(f"- {a.kind}: {a.path}" for a in session.artifacts)
    return "\n".join(lines)
