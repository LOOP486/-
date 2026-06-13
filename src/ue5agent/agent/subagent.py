"""子代理（E2）：上下文隔离的子任务执行，主循环只收摘要。

设计（stage-e-plan E2，复用既有接缝不另起架构）：
- SubAgent = 独立 history + 独立 system + 受限工具面（ScopedRegistry）+ 角色级模型
  （LiteLLMClient 的 role 路由，未配置的角色回退主控模型）。
- 以 `spawn_subagent` 工具的形态暴露为步内能力：模型把"读多个资产并总结"类
  探索子任务交给子代理，自己的上下文不被子任务的工具细节淹没（呼应 B4）。
- 右尺寸边界：子代理工具面硬限只读（探索/审查用途；写操作必须留在主循环——
  checkpoint/回滚/验收机器都在那里）；恒排除 spawn_subagent 自身（嵌套深度 1，
  防递归失控）；子代理的 facts 不进主步骤证据通道（验证类工具应在主步骤跑）。
- 产物：摘要全文落 Artifact(kind="subagent_summary")，回传主循环的文本截断保底。
"""

from __future__ import annotations

import json
import re
from typing import Any

from ue5agent.core.context import truncate
from ue5agent.core.loop import AgentLoop, BudgetExhausted
from ue5agent.core.permissions import PermissionLevel
from ue5agent.llm.types import ChatModel
from ue5agent.tools.registry import ScopedRegistry, ToolRegistry, ToolSpec

SUBAGENT_TOOL_NAME = "spawn_subagent"

SUBAGENT_SYSTEM_PROMPT = """\
你是承接子任务的探索型子代理，工具面只读。完成给定子任务后，最后一条回复输出
简洁的结构化摘要（要点列表；关键名称/路径/数字必须保留），不要复述工具原始输出。
摘要是你交回主代理的唯一产物——主代理看不到你的过程，摘要必须自包含。
"""

_MAX_SUMMARY_CHARS = 4000
"""回传主循环的摘要上限；全文进 artifact，不丢信息。"""

_PARAMETERS = {
    "type": "object",
    "properties": {
        "task": {
            "type": "string",
            "description": "交给子代理的子任务。子代理看不到主对话，描述必须自包含"
            "（含具体资产路径/文件名/要回答的问题）。",
        },
        "role": {
            "type": "string",
            "description": "子代理角色，决定模型路由（如 explorer/reviewer，"
            "见 models.yaml roles）；未配置的角色回退主控模型。缺省 explorer。",
        },
        "allowed_tools": {
            "type": "array",
            "items": {"type": "string"},
            "description": "进一步收窄子代理的工具白名单（裸名即可）；"
            "省略=全部只读工具。写工具在此无效——子代理工具面硬限只读。",
        },
    },
    "required": ["task"],
}


class _SubagentTraceSink:
    """子代理微循环的 trace 适配：事件原样进主 trace，附 subagent=<role> 标记。

    刻意不经过 runner 的 _EvidenceTee——子代理的工具调用不进主步骤证据，
    主循环的证据是 spawn_subagent 这一次调用的摘要文本本身。
    """

    def __init__(self, writer: Any, label: str):
        self._writer = writer
        self._label = label

    def write(self, event: str, **data: Any) -> None:
        self._writer.write(event, subagent=self._label, **data)


def _readonly_face(registry: ToolRegistry, allowed: list[str] | None) -> ScopedRegistry | None:
    """构造子代理工具面：只读工具 ∩（可选）调用方白名单，恒排除 spawn_subagent。

    无任何可用工具时返回 None（调用方给出可读错误，而非放出一个全拒的空面）。
    白名单匹配支持裸名（模型通常写 bp_read，注册名是 ue_editor__bp_read）。
    """
    names: list[str] = []
    for name in registry.names():
        spec = registry.get(name)
        if spec is None or name == SUBAGENT_TOOL_NAME:
            continue
        if spec.level is not PermissionLevel.READ:
            continue
        if allowed and not any(name == e or name.endswith(f"__{e}") for e in allowed):
            continue
        names.append(name)
    if not names:
        return None
    return ScopedRegistry(registry, allowed_tools=names)


def register_spawn_subagent(
    registry: ToolRegistry,
    *,
    llm: ChatModel,
    writer: Any,
    max_iterations: int = 8,
    wall_seconds: float = 180.0,
) -> None:
    """把 spawn_subagent 注册进工具面（READ 级）。

    runner 每次任务构造时调用（replace=True：chat 多任务复用同一 registry，
    闭包须指向当前任务的 writer）。预算刻意小于主步骤——子任务跑不完该拆小，
    不该吃掉主任务的墙钟。
    """

    async def spawn_subagent(
        task: str, role: str = "explorer", allowed_tools: list[str] | None = None
    ) -> str:
        if not task.strip():
            return "[error] 子任务描述为空：task 必须是自包含的子任务说明"
        label = re.sub(r"[^\w-]", "_", role.strip() or "explorer")[:24]
        # AgentLoop 鸭子接受 ScopedRegistry（同 runner._build_step_loop 的 registry: Any）
        face: Any = _readonly_face(registry, allowed_tools)
        if face is None:
            return (
                "[error] 子代理无可用只读工具（白名单过滤后为空或注册表无只读工具），"
                "请直接在主循环完成该子任务"
            )
        writer.event(
            "subagent_start",
            role=label,
            task_preview=task[:200],
            tools=face.names(),
        )
        loop = AgentLoop(
            llm,
            face,
            system_prompt=SUBAGENT_SYSTEM_PROMPT,
            max_iterations=max_iterations,
            max_wall_seconds=wall_seconds,
            session_log=_SubagentTraceSink(writer, label),
        )
        try:
            result = await loop.run(task, role=label)
        except BudgetExhausted as exc:
            writer.event("subagent_end", role=label, ok=False, error=str(exc))
            return f"[error] 子代理（{label}）预算耗尽未产出摘要：{exc}。可拆小子任务后重试"
        except Exception as exc:  # 子代理故障不炸主循环，转错误文本回传
            writer.event("subagent_end", role=label, ok=False, error=f"{type(exc).__name__}: {exc}")
            return f"[error] 子代理（{label}）执行异常：{type(exc).__name__}: {exc}"
        summary = (result.final_text or "").strip()
        if not summary:
            writer.event("subagent_end", role=label, ok=False, error="空摘要")
            return f"[error] 子代理（{label}）未产出摘要"
        seq = sum(1 for a in writer.session.artifacts if a.kind == "subagent_summary") + 1
        artifact = writer.save_artifact(
            "subagent_summary",
            f"subagent_{label}_{seq:02d}.md",
            f"# 子代理摘要（{label}）\n\n任务：{task}\n\n{summary}\n",
            role=label,
            turns=result.turns,
            tool_calls=result.tool_call_count,
        )
        writer.event(
            "subagent_end",
            role=label,
            ok=True,
            turns=result.turns,
            tool_calls=result.tool_call_count,
            artifact=artifact.path,
        )
        clipped = truncate(summary, _MAX_SUMMARY_CHARS)
        return (
            f"[subagent:{label}] {clipped}\n"
            f"（子代理 {result.turns} 轮 / {result.tool_call_count} 次工具调用；"
            f"摘要全文已存 {artifact.path}）"
        )

    registry.register(
        ToolSpec(
            name=SUBAGENT_TOOL_NAME,
            description=(
                "把探索性子任务交给上下文隔离的子代理执行，只把摘要带回主上下文。"
                "适用：需要读多个蓝图/资产/大量文件而只需要结论时。子代理只能用只读"
                "工具；简单任务（一两次工具调用能完成）不要用它，直接调具体工具。"
            ),
            parameters=json.loads(json.dumps(_PARAMETERS)),
            level=PermissionLevel.READ,
            handler=spawn_subagent,
        ),
        replace=True,
    )
