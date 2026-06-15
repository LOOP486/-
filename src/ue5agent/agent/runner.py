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
_EARLY_CONTRACT_PASS_KINDS = {"compile", "wb_validate", "path_test"}
_WHITEBOX_BUILD_PROMPT_SUFFIX = (
    "\n[白盒执行约束] 不要在工具调用前展开完整设计说明；"
    "优先一次性调用 wb_build，再按验收要求调用验证/截图工具；"
    "layout_json 已由 trace artifact 保存，回复中不要重复粘贴完整 JSON，"
    "最终报告再简短总结。"
    "\n[白盒构型守则] 先在脑中按整数格画 room.rect 邻接表，再写 layout_json；"
    "优先使用正交、非重叠、边界完整对齐的矩形房间，先保证连通和可走。"
    "共享墙门洞必须两侧成对且 at/width 对齐；不确定时使用同尺寸相邻矩形和 width=2 的对称门洞。"
    "windows 只用于明确的外轮廓墙；不确定某面墙是否是外墙就不要写 windows，"
    "空间结构/导航任务可以完全省略 windows。"
    "遇到 wb_build 的布局校验错误时，不要质疑校验器或反复微调同一复杂布局；"
    "应退回更简单的正交连通布局，删除非必要 windows，并重新成对校准共享墙门洞。"
)


def _execution_failure_type(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}"
    if "LLM 请求被取消" in text or "LLM 请求超时" in text or "CancelledError" in text:
        return "llm_timeout"
    if "TimeoutError" in text and (
        type(exc).__name__ == "LLMUnavailable" or "全部模型不可用" in text
    ):
        return "llm_timeout"
    if isinstance(exc, BudgetExhausted):
        return "step_budget_exhausted"
    return "execution_error"


def _is_whitebox_build_step(step: PlanStep) -> bool:
    text = f"{step.intent} {step.acceptance}".lower()
    allows_build = any(
        tool == "wb_build" or tool.endswith("__wb_build") for tool in step.allowed_tools
    )
    build_intent = "wb_build" in text or (
        "白盒" in text and any(word in text for word in ("搭", "创建", "生成", "落地"))
    )
    return build_intent or (allows_build and "build" in text)


def _whitebox_recovery_hint(step: PlanStep, evidence: str) -> str:
    if not _is_whitebox_build_step(step):
        return ""
    text = evidence.lower()
    hints: list[str] = []
    if "窗只能开在外墙" in text or ("window" in text and "外墙" in text):
        hints.append(
            "若报 windows/窗只能开在外墙，下一轮先删除所有非必要 windows；"
            "窗不影响连通和导航，结构任务宁可无窗也不要猜共享墙。"
        )
    if "内部共享墙门洞必须两侧对齐" in text or "门洞必须两侧对齐" in text:
        hints.append(
            "若报共享墙门洞未对齐，重建为更简单的矩形邻接："
            "两个相邻房间在同一共享边各写一扇 at/width 完全相同的门，"
            "避免让支线房间同时贴住多间房形成歧义共享边。"
        )
    if "楼梯" in text or "stair" in text:
        hints.append(
            "若报楼梯穿墙/越界/楼梯井夹缝，先把楼梯放到大房间内部、离外墙和门洞至少一格，"
            "使用与朝向一致的 footprint，不要让楼梯切断门到门路线。"
        )
    if not hints:
        hints.append(
            "下一轮优先简化拓扑：少房间、少窗、少特殊形状；"
            "先让 wb_build/wb_validate/path_test 通过，"
            "再考虑表达细节。"
        )
    return "\n[白盒错误恢复] " + " ".join(hints)


def _is_report_only_step(step: PlanStep) -> bool:
    text = f"{step.intent} {step.acceptance}".lower()
    has_report_intent = any(
        token in text for token in ("报告", "总结", "汇总", "report", "summary")
    )
    if not has_report_intent:
        return False
    tool_markers = (
        "wb_build",
        "wb_validate",
        "path_test",
        "navmesh_rebuild",
        "ubt_compile",
        "viewport_screenshot",
    )
    return not any(marker in text for marker in tool_markers)


def _report_only_summary() -> str:
    return "前序步骤已完成，验证结果已纳入最终报告。"


def _compact_llm_timeout_retry_history(
    *,
    system_content: str,
    goal: str,
    step: PlanStep,
    retry_note: str,
) -> list[dict[str, Any]]:
    """LLM/工具墙钟超时后重试使用干净上下文，避免孤儿 tool_call 污染下一轮。"""
    return [
        {"role": "system", "content": system_content},
        {
            "role": "user",
            "content": "\n".join(
                [
                    "LLM 超时重试摘要：",
                    f"总目标：{goal}",
                    f"当前步骤（{step.id}）：{step.intent}",
                    f"验收标准：{step.acceptance or '无'}",
                    retry_note,
                ]
            ),
        },
    ]


def _compact_vision_retry_history(
    history: list[dict[str, Any]],
    *,
    goal: str,
    facts: list[dict],
    vision_result: VisionReviewResult,
    retry_note: str,
) -> list[dict[str, Any]]:
    """视觉失败后只保留系统提示与可行动摘要，避免前一轮长 JSON/说明继续膨胀。"""
    system = next((msg for msg in history if msg.get("role") == "system"), None)
    compacted = [system] if system is not None else []
    folder_root = _latest_fact_field(facts, "wb_build", "folder_root") or _latest_fact_field(
        facts, "wb_build", "outliner_folder_root"
    )
    shot = _latest_fact_field(facts, "screenshot", "path")
    high_issues = vision_result.high_severity
    issue_lines = (
        [f"- {issue.area}: {issue.issue}" for issue in high_issues]
        if high_issues
        else [f"- {vision_result.summary()}"]
    )
    lines = [
        "视觉失败重试摘要：",
        f"原目标：{goal}",
        f"最新 wb_build.folder_root：{folder_root or '未知'}",
        f"最新截图路径：{shot or '未知'}",
        "vision high issues：",
        *issue_lines,
        retry_note,
    ]
    compacted.append({"role": "user", "content": "\n".join(lines)})
    return compacted


def _latest_fact_field(facts: list[dict], kind: str, field: str) -> Any:
    for fact in reversed(facts):
        if fact.get("kind") == kind and fact.get(field) is not None:
            return fact[field]
    return None


def _with_auto_screenshot_focus(name: str, arguments_json: str, facts: list[dict]) -> str:
    """截图工具未显式传 focus_prefix 时，用最新 wb_build 文件夹精确聚焦本批白盒。"""
    if name != "viewport_screenshot" and not name.endswith("__viewport_screenshot"):
        return arguments_json
    try:
        arguments = json.loads(arguments_json or "{}")
    except json.JSONDecodeError:
        return arguments_json
    if not isinstance(arguments, dict):
        return arguments_json
    if arguments.get("focus_prefix"):
        return arguments_json
    focus = (
        _latest_fact_field(facts, "wb_build", "folder_root")
        or _latest_fact_field(facts, "wb_build", "outliner_folder_root")
        or _latest_fact_field(facts, "wb_build", "prefix")
    )
    if isinstance(focus, str) and focus.strip():
        arguments["focus_prefix"] = focus.strip()
        arguments.setdefault("clean_view", True)
        # 自动聚焦时以 UE 侧 bbox/margin 为准，避免模型手写相机高度把白盒主体截到画面外。
        arguments.pop("location", None)
        arguments.pop("rotation", None)
        return json.dumps(arguments, ensure_ascii=False)
    return arguments_json


def _with_whitebox_layout_guardrails(name: str, arguments_json: str) -> str:
    """在派发 wb_build 前做轻量 DSL 修正，把确定非法的模型输出拉回可验证空间。

    这里不替模型重设计布局，只处理确定性的结构错误：
    - windows 只能在外墙；共享墙上的窗删除即可，结构/导航任务不依赖窗；
    - 共享墙门洞必须两侧成对；单侧门洞可安全补齐对侧同轴同宽门洞。
    - 常见楼梯 footprint 如果明显越界，先收进所在房间内部，避免白跑一轮 LayoutError。
    """
    if name != "wb_build" and not name.endswith("__wb_build"):
        return arguments_json
    try:
        arguments = json.loads(arguments_json or "{}")
    except json.JSONDecodeError:
        return arguments_json
    if not isinstance(arguments, dict):
        return arguments_json
    raw_layout = arguments.get("layout_json")
    if isinstance(raw_layout, str):
        try:
            layout = json.loads(raw_layout)
        except json.JSONDecodeError:
            return arguments_json
    elif isinstance(raw_layout, dict):
        layout = raw_layout
    else:
        return arguments_json
    if not isinstance(layout, dict):
        return arguments_json
    if not _apply_whitebox_layout_guardrails(layout):
        return arguments_json
    arguments["layout_json"] = json.dumps(layout, ensure_ascii=False)
    return json.dumps(arguments, ensure_ascii=False)


def _apply_whitebox_layout_guardrails(layout: dict[str, Any]) -> bool:
    rooms = layout.get("rooms")
    if not isinstance(rooms, list):
        return False
    room_dicts = [room for room in rooms if isinstance(room, dict)]
    changed = _drop_internal_windows(room_dicts)
    changed = _mirror_internal_doors(room_dicts) or changed
    changed = _clamp_stairs_to_rooms(layout, room_dicts) or changed
    return changed


def _drop_internal_windows(rooms: list[dict[str, Any]]) -> bool:
    changed = False
    for room in rooms:
        windows = room.get("windows")
        if not isinstance(windows, list):
            continue
        kept: list[Any] = []
        for window in windows:
            segment = _layout_opening_segment(room, window)
            if segment is not None and _adjacent_room_wall(rooms, room, segment) is not None:
                changed = True
                continue
            kept.append(window)
        if len(kept) != len(windows):
            room["windows"] = kept
    return changed


def _mirror_internal_doors(rooms: list[dict[str, Any]]) -> bool:
    changed = False
    for room in rooms:
        doors = room.get("doors")
        if not isinstance(doors, list):
            continue
        for door in list(doors):
            segment = _layout_opening_segment(room, door)
            if segment is None:
                continue
            adjacent = _adjacent_room_wall(rooms, room, segment)
            if adjacent is None:
                continue
            other, other_wall = adjacent
            if _room_has_door_segment(other, segment):
                continue
            mirrored = _door_from_segment(other, other_wall, segment)
            if mirrored is None:
                continue
            other_doors = other.setdefault("doors", [])
            if isinstance(other_doors, list):
                other_doors.append(mirrored)
                changed = True
    return changed


_COMMON_STAIR_FOOTPRINTS: dict[str, tuple[int, int]] = {
    "stair_1": (1, 2),
    "stair_1_001": (1, 2),
    "stair_2": (3, 6),
    "stair_2_001": (3, 6),
}


def _clamp_stairs_to_rooms(layout: dict[str, Any], rooms: list[dict[str, Any]]) -> bool:
    stairs = layout.get("stairs")
    if not isinstance(stairs, list):
        return False
    rooms_by_name = {room.get("name"): room for room in rooms if isinstance(room.get("name"), str)}
    changed = False
    for stair in stairs:
        if not isinstance(stair, dict):
            continue
        room_name = stair.get("room")
        room = rooms_by_name.get(room_name)
        if room is None:
            continue
        at = _layout_xy(stair.get("at"))
        rect = _layout_rect(room)
        footprint = _stair_footprint_for_guardrail(stair)
        if at is None or rect is None or footprint is None:
            continue
        _x, _y, room_w, room_d = rect
        fw, fd = footprint
        if room_w < fw or room_d < fd:
            continue
        margin_x = 1 if room_w - fw >= 2 else 0
        margin_y = 1 if room_d - fd >= 2 else 0
        clamped = (
            min(max(at[0], margin_x), room_w - fw - margin_x),
            min(max(at[1], margin_y), room_d - fd - margin_y),
        )
        if clamped != at:
            stair["at"] = [clamped[0], clamped[1]]
            changed = True
    return changed


def _stair_footprint_for_guardrail(stair: dict[str, Any]) -> tuple[int, int] | None:
    raw_footprint = _layout_xy(stair.get("footprint"))
    key = stair.get("key")
    base = raw_footprint
    if base is None and isinstance(key, str):
        base = _COMMON_STAIR_FOOTPRINTS.get(key.strip().lower())
    if base is None and key is None:
        base = (3, 6)
    if base is None:
        return None
    facing = stair.get("facing")
    if not isinstance(facing, str):
        return None
    if facing.strip().lower() in {"east", "west"}:
        return (base[1], base[0])
    if facing.strip().lower() in {"north", "south"}:
        return base
    return None


def _adjacent_room_wall(
    rooms: list[dict[str, Any]],
    room: dict[str, Any],
    segment: tuple[str, int, int, int],
) -> tuple[dict[str, Any], str] | None:
    axis, coord, lo, hi = segment
    level = _layout_room_level(room)
    if level is None:
        return None
    for other in rooms:
        if other is room or _layout_room_level(other) != level:
            continue
        for wall in ("north", "south", "east", "west"):
            other_segment = _layout_wall_segment(other, wall)
            if other_segment is None:
                continue
            other_axis, other_coord, other_lo, other_hi = other_segment
            if axis != other_axis or coord != other_coord:
                continue
            if min(hi, other_hi) - max(lo, other_lo) > 0:
                return other, wall
    return None


def _room_has_door_segment(room: dict[str, Any], segment: tuple[str, int, int, int]) -> bool:
    doors = room.get("doors")
    if not isinstance(doors, list):
        return False
    return any(_layout_opening_segment(room, door) == segment for door in doors)


def _door_from_segment(
    room: dict[str, Any], wall: str, segment: tuple[str, int, int, int]
) -> dict[str, int | str] | None:
    rect = _layout_rect(room)
    if rect is None:
        return None
    x, y, w, d = rect
    _axis, _coord, lo, hi = segment
    width = hi - lo
    if width <= 0:
        return None
    if wall in ("north", "south"):
        at = lo - x
        length = w
    else:
        at = lo - y
        length = d
    if at < 0 or at + width > length:
        return None
    return {"wall": wall, "at": at, "width": width}


def _layout_opening_segment(
    room: dict[str, Any], opening: object
) -> tuple[str, int, int, int] | None:
    if not isinstance(opening, dict):
        return None
    wall = opening.get("wall")
    at = _layout_int(opening.get("at"))
    width = _layout_int(opening.get("width", 1))
    if not isinstance(wall, str) or at is None or width is None:
        return None
    rect = _layout_rect(room)
    if rect is None:
        return None
    x, y, w, d = rect
    if wall == "south":
        return ("y", y, x + at, x + at + width)
    if wall == "north":
        return ("y", y + d, x + at, x + at + width)
    if wall == "west":
        return ("x", x, y + at, y + at + width)
    if wall == "east":
        return ("x", x + w, y + at, y + at + width)
    return None


def _layout_wall_segment(room: dict[str, Any], wall: str) -> tuple[str, int, int, int] | None:
    rect = _layout_rect(room)
    if rect is None:
        return None
    x, y, w, d = rect
    if wall == "south":
        return ("y", y, x, x + w)
    if wall == "north":
        return ("y", y + d, x, x + w)
    if wall == "west":
        return ("x", x, y, y + d)
    if wall == "east":
        return ("x", x + w, y, y + d)
    return None


def _layout_rect(room: dict[str, Any]) -> tuple[int, int, int, int] | None:
    raw = room.get("rect")
    if not isinstance(raw, list | tuple) or len(raw) != 4:
        return None
    values = tuple(_layout_int(value) for value in raw)
    if any(value is None for value in values):
        return None
    return values  # type: ignore[return-value]


def _layout_xy(value: object) -> tuple[int, int] | None:
    if not isinstance(value, list | tuple) or len(value) != 2:
        return None
    x = _layout_int(value[0])
    y = _layout_int(value[1])
    if x is None or y is None:
        return None
    return (x, y)


def _layout_room_level(room: dict[str, Any]) -> int | None:
    return _layout_int(room.get("level", 0))


def _layout_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        try:
            parsed = int(stripped)
        except ValueError:
            return None
        return parsed if stripped == str(parsed) else None
    return None


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


class _AutoScreenshotFocusRegistry:
    """在 runner 层补全截图聚焦参数，不污染 core loop 的工具协议。"""

    def __init__(self, base: Any, tee: _EvidenceTee):
        self._base = base
        self._tee = tee

    def specs(self) -> list[dict[str, Any]]:
        return self._base.specs()

    def names(self) -> list[str]:
        return self._base.names()

    def get(self, name: str) -> Any:
        return self._base.get(name)

    async def run(self, name: str, arguments_json: str) -> Any:
        arguments_json = _with_whitebox_layout_guardrails(name, arguments_json)
        arguments_json = _with_auto_screenshot_focus(name, arguments_json, self._tee.facts)
        return await self._base.run(name, arguments_json)

    async def dispatch(self, name: str, arguments_json: str) -> str:
        return (await self.run(name, arguments_json)).text


def _visual_step_ready_for_review(step: PlanStep, facts: list[dict]) -> bool:
    """白盒视觉步骤拿到必要工具证据后，应交还 runner 做 vision_review。"""
    if not any(item in step.required_evidence for item in ("screenshot", "vision_review")):
        return False
    if not _latest_fact_passed(facts, "screenshot", require_framing=True):
        return False
    if _is_whitebox_build_step(step) and not _latest_fact_passed(facts, "wb_build"):
        return False
    if _step_allows_tool(step, "wb_validate") and not _latest_fact_passed(facts, "wb_validate"):
        return False
    if step.success_checks:
        contract = evaluate_success_checks(step.success_checks, facts)
        if contract is None or contract.verdict != "pass":
            return False
    return True


def _step_allows_tool(step: PlanStep, tool_name: str) -> bool:
    return any(tool == tool_name or tool.endswith(f"__{tool_name}") for tool in step.allowed_tools)


def _latest_fact_passed(facts: list[dict], kind: str, *, require_framing: bool = False) -> bool:
    for fact in reversed(facts):
        if fact.get("kind") != kind:
            continue
        if fact.get("ok") is not True:
            return False
        return not (require_framing and fact.get("framing_ok") is False)
    return False


def _carry_forward_facts(facts: list[dict]) -> list[dict]:
    """跨 attempt/step 复用已经成功的客观事实；失败事实只留在当前现场。"""
    return [fact for fact in facts if fact.get("ok") is not False]


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
        registry = _AutoScreenshotFocusRegistry(registry, tee)
        max_iterations = self._step_max_iterations
        wall_seconds = self._wall_seconds_for_step(step)
        budget = step.step_budget or {}
        try:
            if budget.get("max_turns"):
                max_iterations = min(int(budget["max_turns"]), max_iterations)
        except (TypeError, ValueError):
            pass  # 预算字段不合法时沿用 runner 默认值
        return AgentLoop(
            self._llm,
            registry,
            system_prompt=self._system_prompt,
            max_iterations=max_iterations,
            max_wall_seconds=wall_seconds,
            session_log=tee,
            stop_after_tool=lambda: self._early_contract_pass_summary(step, tee),
        )

    def _wall_seconds_for_step(self, step: PlanStep) -> float:
        wall_seconds = self._step_wall_seconds
        budget = step.step_budget or {}
        try:
            if budget.get("max_seconds"):
                wall_seconds = min(float(budget["max_seconds"]), wall_seconds)
        except (TypeError, ValueError):
            pass
        return wall_seconds

    def _early_contract_pass_summary(self, step: PlanStep, tee: _EvidenceTee) -> str | None:
        """工具事实已满足步骤契约时，直接结束本步，避免为一句总结再打 LLM。"""
        if _visual_step_ready_for_review(step, tee.facts):
            return "[工具证据已满足视觉审查前置] 白盒搭建、校验和截图已完成，进入视觉审查。"
        if step.required_evidence or not step.success_checks:
            return None
        check_kinds = {
            str(check.get("kind", "")).strip()
            for check in step.success_checks
            if isinstance(check, dict)
        }
        if not check_kinds or not check_kinds <= _EARLY_CONTRACT_PASS_KINDS:
            return None
        decisive = deterministic_verdict(tee.facts)
        if decisive is not None and decisive.verdict == "fail":
            return None
        contract = evaluate_success_checks(step.success_checks, tee.facts)
        if contract is None or contract.verdict != "pass":
            return None
        return f"[工具证据已满足步骤契约] {contract.reason}"

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

    def _contract_pass_summary_from_facts(self, step: PlanStep, facts: list[dict]) -> str | None:
        """历史客观 facts 已满足纯契约步骤时，直接本地收口，避免重复问模型。"""
        if step.required_evidence or not step.success_checks:
            return None
        check_kinds = {
            str(check.get("kind", "")).strip()
            for check in step.success_checks
            if isinstance(check, dict)
        }
        if not check_kinds or not check_kinds <= _EARLY_CONTRACT_PASS_KINDS:
            return None
        decisive = deterministic_verdict(facts)
        if decisive is not None and decisive.verdict == "fail":
            return None
        contract = evaluate_success_checks(step.success_checks, facts)
        if contract is None or contract.verdict != "pass":
            return None
        return f"[已有工具证据满足步骤契约] {contract.reason}"

    async def _run_vision_review(
        self, step: PlanStep, goal: str, tee: _EvidenceTee, *, new_fact_start: int = 0
    ) -> VisionReviewResult | None:
        """A4 子任务3：对本步产出的截图做视觉审查，结果并入 tee.facts 驱动验收。

        触发条件：注入了 vision_reviewer（已配 vision 角色）且本次 attempt 实际产出了截图
        （viewport_screenshot 落地的 screenshot 事实）。两者缺一则跳过——视觉审查是
        增量证据，绝不改变"没截图任务"的既有行为。审查结果以 vision_review 事实并入
        证据通道：存在 high 问题或解析失败 → ok=False，被 deterministic_verdict 判 fail。
        审查链路本身故障（vision 模型不可用等）只记 trace、不炸步骤验收。
        """
        if self._vision_reviewer is None:
            return None
        new_facts = tee.facts[new_fact_start:]
        shots = [
            str(f["path"]) for f in new_facts if f.get("kind") == "screenshot" and f.get("path")
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
        try:
            plan_timeout = min(self._step_wall_seconds, max(0.01, deadline - time.monotonic()))
            session.task_class, session.plan = await asyncio.wait_for(
                make_plan(
                    self._llm,
                    goal,
                    tool_names=self._registry.names(),
                ),
                timeout=plan_timeout,
            )
        except asyncio.CancelledError as exc:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise
            timeout = TimeoutError("LLM 请求被取消（可能是底层超时）")
            return self._abort_during_plan(timeout, original=exc)
        except TimeoutError as exc:
            if exc.args and str(exc).strip():
                timeout = exc
            else:
                timeout = TimeoutError(f"LLM 请求超时（计划阶段墙钟预算 {plan_timeout:.2f}s）")
            return self._abort_during_plan(timeout, original=exc)
        except Exception as exc:
            return self._abort_during_plan(exc)
        except BaseException as exc:
            task = asyncio.current_task()
            if isinstance(exc, KeyboardInterrupt) or (task is not None and task.cancelling()):
                raise
            wrapped = RuntimeError(f"LLM 请求异常：{type(exc).__name__}: {exc}")
            return self._abort_during_plan(wrapped, original=exc)
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
        session_facts: list[dict] = []
        history: list[dict[str, Any]] = [{"role": "system", "content": system_content}]
        summaries: dict[str, str] = {}
        aborted = False

        for index, step in enumerate(session.plan):
            if aborted:
                step.status = "skipped"
                continue
            session.current_step = index
            if _is_report_only_step(step) and summaries:
                step.status = "done"
                step.attempts += 1
                summaries[step.id] = _report_only_summary()
                self._writer.event("phase_enter", phase="execute", step_id=step.id)
                self._writer.event("phase_exit", phase="execute", step_id=step.id)
                self._writer.event(
                    "verify_result",
                    step_id=step.id,
                    verdict="pass",
                    reason="报告汇总步骤复用前序验证证据",
                    mode="local",
                )
                continue
            step.status = "running"
            step_facts: list[dict] = []
            while True:
                if time.monotonic() >= deadline:
                    step.status = "failed"
                    aborted = True
                    summaries.setdefault(step.id, "[会话总预算耗尽]")
                    self._writer.event("budget_warning", step_id=step.id, reason="total_wall_clock")
                    break
                step.attempts += 1
                tee.reset()
                tee.facts.extend(session_facts)
                tee.facts.extend(step_facts)
                seeded_fact_count = len(tee.facts)
                pre_satisfied = self._contract_pass_summary_from_facts(step, tee.facts)
                if pre_satisfied is not None:
                    summaries[step.id] = pre_satisfied
                    self._writer.event("phase_enter", phase="execute", step_id=step.id)
                    self._writer.event("phase_exit", phase="execute", step_id=step.id)
                    self._writer.event(
                        "verify_result",
                        step_id=step.id,
                        verdict="pass",
                        reason=pre_satisfied,
                        mode="contract_cached",
                    )
                    session_facts.extend(step_facts)
                    step.status = "done"
                    break
                self._writer.event("phase_enter", phase="execute", step_id=step.id)
                prompt = (
                    f"{self._progress_line(session.plan, index)}\n"
                    f"总目标：{goal}\n当前步骤（{step.id}）：{step.intent}\n"
                    f"验收标准：{step.acceptance or '无'}"
                )
                if _is_whitebox_build_step(step):
                    prompt += _WHITEBOX_BUILD_PROMPT_SUFFIX
                precondition_hint = await self._check_preconditions(step)
                if precondition_hint:
                    prompt += f"\n[前置条件未满足] {precondition_hint}——请先恢复环境再做本步骤。"
                loop = self._build_step_loop(step, tee)
                execution_verdict: VerifyResult | None = None
                execution_failure_type: str | None = None
                try:
                    step_timeout = min(
                        self._wall_seconds_for_step(step),
                        max(0.01, deadline - time.monotonic()),
                    )
                    result = await asyncio.wait_for(
                        loop.run(prompt, role="coder", history=history),
                        timeout=step_timeout,
                    )
                    summaries[step.id] = result.final_text
                except BudgetExhausted as exc:
                    execution_failure_type = _execution_failure_type(exc)
                    summaries[step.id] = f"[步内预算耗尽] {exc}"
                    execution_verdict = VerifyResult("fail", f"步内预算耗尽：{exc}")
                    self._writer.event(
                        "run_error",
                        step_id=step.id,
                        failure_type=execution_failure_type,
                        error=str(exc),
                    )
                except TimeoutError as exc:
                    if exc.args and str(exc).strip():
                        step_exc = exc
                    else:
                        step_exc = TimeoutError(f"LLM 请求超时（步内墙钟预算 {step_timeout:.2f}s）")
                    execution_failure_type = _execution_failure_type(step_exc)
                    reason = f"步骤执行异常：{type(step_exc).__name__}: {step_exc}"
                    if execution_failure_type != "execution_error":
                        reason = f"{execution_failure_type}: {reason}"
                    summaries[step.id] = f"[{reason}]"
                    execution_verdict = VerifyResult("fail", reason)
                    self._writer.event(
                        "run_error",
                        step_id=step.id,
                        failure_type=execution_failure_type,
                        error=f"{type(step_exc).__name__}: {step_exc}",
                    )
                except Exception as exc:  # LLM/工具底层故障：记为步骤失败，不炸整个会话
                    execution_failure_type = _execution_failure_type(exc)
                    reason = f"步骤执行异常：{type(exc).__name__}: {exc}"
                    if execution_failure_type != "execution_error":
                        reason = f"{execution_failure_type}: {reason}"
                    summaries[step.id] = f"[{reason}]"
                    execution_verdict = VerifyResult("fail", reason)
                    self._writer.event(
                        "run_error",
                        step_id=step.id,
                        failure_type=execution_failure_type,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                self._writer.event("phase_exit", phase="execute", step_id=step.id)

                # A4：对本步截图做视觉审查，结果并入 tee.facts（驱动下方确定性验收）
                vision_result = None
                if execution_verdict is None:
                    vision_result = await self._run_vision_review(
                        step,
                        goal,
                        tee,
                        new_fact_start=seeded_fact_count,
                    )
                new_carry_facts = _carry_forward_facts(tee.facts[seeded_fact_count:])

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
                    session_facts.extend(step_facts)
                    session_facts.extend(new_carry_facts)
                    step.status = "done"
                    break
                if mode == "execution":
                    if (
                        execution_failure_type == "llm_timeout"
                        and step.attempts < 2
                        and step.attempts < self._max_step_attempts
                    ):
                        self._writer.event(
                            "recover_action",
                            step_id=step.id,
                            action="retry",
                            reason=verdict.reason,
                        )
                        retry_note = (
                            f"步骤 {step.id} 执行时发生 llm_timeout：{verdict.reason}。"
                            "请压缩输出，直接调用必要工具后再总结。"
                        )
                        history = _compact_llm_timeout_retry_history(
                            system_content=system_content,
                            goal=goal,
                            step=step,
                            retry_note=retry_note,
                        )
                        continue
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
                step_facts.extend(new_carry_facts)
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
                retry_note += _whitebox_recovery_hint(step, tee.evidence())
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
                    history = _compact_vision_retry_history(
                        history,
                        goal=goal,
                        facts=tee.facts,
                        vision_result=vision_result,
                        retry_note=retry_note,
                    )
                else:
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

    def _abort_during_plan(
        self, exc: Exception, *, original: BaseException | None = None
    ) -> RunOutcome:
        session = self._session
        session.status = "aborted"
        failure_type = _execution_failure_type(exc)
        error = f"{type(exc).__name__}: {exc}"
        if original is not None and original is not exc:
            error += f"（original {type(original).__name__}: {original}）"
        reason = f"{failure_type}: 规划阶段异常：{error}"
        self._writer.event(
            "run_error",
            phase="plan",
            failure_type=failure_type,
            error=error,
        )
        self._writer.event("phase_exit", phase="plan", error=reason)
        self._writer.event("phase_enter", phase="final_report")
        report = build_report(session, {}, final_answer=reason)
        self._writer.write_report(report)
        self._writer.save_session()
        self._writer.event("run_end", turns=0, tool_calls=0)
        return RunOutcome(
            success=False,
            report=report,
            session_id=session.id,
            final_answer=reason,
        )
