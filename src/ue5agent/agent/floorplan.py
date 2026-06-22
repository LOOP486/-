"""平面图输入识别：本地图片 → 白盒布局 DSL。

第一版只做拓扑优先识别：房间、相邻关系、门洞/开口连通。尺寸按现有
slab + realistic + 整数格 DSL 收敛；不从平面图生成 gameplay/props。
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ue5agent.agent.events import RunWriter
from ue5agent.agent.vision_review import image_to_data_url
from ue5agent.floorplan.wall_extractor import (
    WallExtractionError,
    WallExtractionResult,
    extract_floorplan_walls,
)
from ue5agent.llm.types import ChatModel
from ue5agent.whitebox.compiler import LayoutError, compile_layout, layout_from_dict
from ue5agent.whitebox.manifest import Manifest

_SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_VALIDATION_MANIFEST = Manifest(grid=100.0, assets={})
_REPAIRABLE_LAYOUT_ERROR_MARKERS = ("内部重叠", "不连通", "门洞", "窗只能开在外墙")
_ALLOWED_LAYOUT_KEYS = {
    "name",
    "structure_mode",
    "scale_profile",
    "rooms",
    "stairs",
    "walls",
    "origin",
    "wall_height",
    "level_height",
    "wall_thickness",
}
_OUTDOOR_ROOM_TOKENS = (
    "piscina",
    "pool",
    "varanda",
    "varnanda",
    "veranda",
    "terrace",
    "terraco",
    "terraço",
    "patio",
    "jardim",
    "garden",
    "deck",
)

FLOORPLAN_PROMPT = """\
你是 UE5 白盒关卡的平面图识别器。根据输入平面图输出现有白盒 layout_json。
只输出 JSON（不要其它文字）：
{"ok": true, "confidence": 0.0,
 "layout": {"name": "floorplan_blockout", "structure_mode": "slab",
            "scale_profile": "realistic", "rooms": []},
 "assumptions": [], "warnings": []}

规则：
- 拓扑优先：优先识别房间、相邻关系、门洞/开口连通；不要追求像素级尺寸复刻；
- layout 必须使用现有白盒 DSL：rooms[].rect=[x,y,width,depth]，单位为整数格；
- 坐标必须归一化为紧凑整数网格，主体建筑通常落在 x/y 约 -30..30 的范围；
  不要直接使用图片像素坐标或图纸标尺数值；
- 不要把整张图纸外框、地形等高线、泳池、剖面线 A/B 或空白区域识别成房间；
- 默认 structure_mode="slab"，scale_profile="realistic"，单层 level=0；
- 多房间必须连通；共享墙门洞必须两侧成对且 at/width 对齐；
- 不生成 gameplay、spawn_points、routes、props、cover 或家具；
- 不确定窗户就省略 windows；窗户不参与连通性；
- 若图像无法可靠识别，返回 ok=false，并在 warnings 写清原因。
"""

_FENCE = re.compile(r"^```(?:json)?\s*(?P<body>.*?)\s*```\s*$", re.DOTALL)


class FloorplanRecognitionError(Exception):
    """平面图识别结果不可用于白盒搭建。"""

    def __init__(
        self,
        message: str,
        *,
        raw: str = "",
        layout: dict[str, Any] | None = None,
        assumptions: list[str] | None = None,
        warnings: list[str] | None = None,
        confidence: float = 0.0,
        parsed: bool = False,
    ):
        super().__init__(message)
        self.raw = raw
        self.layout = layout
        self.assumptions = assumptions or []
        self.warnings = warnings or []
        self.confidence = confidence
        self.parsed = parsed

    def to_facts(self) -> dict[str, Any]:
        rooms = self.layout.get("rooms") if isinstance(self.layout, dict) else None
        return {
            "kind": "floorplan_recognition",
            "ok": False,
            "parsed": self.parsed,
            "confidence": self.confidence,
            "room_count": len(rooms) if isinstance(rooms, list) else 0,
            "warning_count": len(self.warnings),
        }


@dataclass
class FloorplanRecognitionResult:
    ok: bool
    confidence: float
    layout: dict[str, Any]
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    raw: str = ""
    parsed: bool = True

    def to_facts(self) -> dict[str, Any]:
        rooms = self.layout.get("rooms") if isinstance(self.layout, dict) else None
        return {
            "kind": "floorplan_recognition",
            "ok": self.ok,
            "parsed": self.parsed,
            "confidence": self.confidence,
            "room_count": len(rooms) if isinstance(rooms, list) else 0,
            "warning_count": len(self.warnings),
        }


def build_floorplan_messages(image_path: str | Path, *, user_goal: str) -> list[dict[str, Any]]:
    """构造平面图识别的多模态消息。"""
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                f"用户目标：\n{user_goal}\n\n"
                "请按拓扑优先策略识别这张平面图，并输出可直接传给 wb_build 的 "
                'layout_json。layout 顶层必须包含 structure_mode="slab" 和 '
                'scale_profile="realistic"。'
            ),
        },
        {"type": "image_url", "image_url": {"url": image_to_data_url(image_path)}},
    ]
    return [
        {"role": "system", "content": FLOORPLAN_PROMPT},
        {"role": "user", "content": content},
    ]


def parse_floorplan_response(text: str) -> FloorplanRecognitionResult:
    """解析并校验 vision 识别结果。"""
    body = text.strip()
    fence = _FENCE.match(body)
    if fence:
        body = fence.group("body")
    try:
        data = _loads_json_object(body)
    except json.JSONDecodeError as exc:
        raise FloorplanRecognitionError(
            f"平面图识别回答不是合法 JSON：{exc}",
            raw=text,
            warnings=[str(exc)],
            parsed=False,
        ) from exc
    if not isinstance(data, dict):
        raise FloorplanRecognitionError(
            "平面图识别回答必须是 JSON object",
            raw=text,
            warnings=["回答不是 JSON object"],
            parsed=False,
        )

    ok = bool(data.get("ok", False))
    confidence = _confidence(data.get("confidence"))
    assumptions = _string_list(data.get("assumptions"))
    warnings = _string_list(data.get("warnings"))
    layout = data.get("layout", {})
    if not isinstance(layout, dict):
        if not ok:
            layout = {}
        else:
            raise FloorplanRecognitionError(
                "平面图识别回答缺少 layout 对象",
                raw=text,
                assumptions=assumptions,
                warnings=warnings or ["缺少 layout 对象"],
                confidence=confidence,
                parsed=False,
            )
    layout = _normalize_layout(layout)
    if ok:
        try:
            compile_layout(layout_from_dict(layout), _VALIDATION_MANIFEST)
        except LayoutError as exc:
            repaired = _repair_layout_after_validation_error(layout, str(exc))
            if repaired is not None:
                try:
                    compile_layout(layout_from_dict(repaired), _VALIDATION_MANIFEST)
                except LayoutError:
                    repaired = None
                else:
                    layout = repaired
                    confidence = min(confidence, 0.65)
                    warnings = [
                        *warnings,
                        f"原始几何未通过 DSL（{exc}），已回退为拓扑优先安全布局",
                    ]
            if repaired is not None:
                return FloorplanRecognitionResult(
                    ok=ok,
                    confidence=confidence,
                    layout=layout,
                    assumptions=assumptions,
                    warnings=warnings,
                    raw=text,
                    parsed=True,
                )
            raise FloorplanRecognitionError(
                f"平面图 layout 不符合白盒 DSL：{exc}",
                raw=text,
                layout=layout,
                assumptions=assumptions,
                warnings=warnings or [str(exc)],
                confidence=confidence,
                parsed=False,
            ) from exc

    return FloorplanRecognitionResult(
        ok=ok,
        confidence=confidence,
        layout=layout,
        assumptions=assumptions,
        warnings=warnings,
        raw=text,
        parsed=True,
    )


async def recognize_floorplan(
    llm: ChatModel,
    image_path: str | Path,
    *,
    user_goal: str,
    role: str = "vision",
    max_retries: int = 1,
) -> FloorplanRecognitionResult:
    """调用 vision 角色识别平面图；解析失败最多重试 max_retries 次。"""
    messages = build_floorplan_messages(image_path, user_goal=user_goal)
    last_error: FloorplanRecognitionError | None = None
    for attempt in range(max_retries + 1):
        turn = await llm.acomplete(role, messages)
        try:
            return parse_floorplan_response(turn.content or "")
        except FloorplanRecognitionError as exc:
            last_error = exc
            if attempt >= max_retries:
                break
            messages = [
                *messages,
                {"role": "assistant", "content": turn.content or ""},
                {
                    "role": "user",
                    "content": (
                        "上一次回答无法解析或不符合白盒 DSL。"
                        f"错误：{exc}\n"
                        "请只输出合法 JSON，"
                        "并确保 layout.rooms、rect 整数格、共享墙门洞成对。"
                    ),
                },
            ]
    assert last_error is not None
    raise last_error


def validate_floorplan_image_path(path: str | Path) -> Path:
    """校验本地平面图图片路径。"""
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise FloorplanRecognitionError(f"平面图文件不存在：{p}")
    if p.suffix.lower() not in _SUPPORTED_IMAGE_SUFFIXES:
        supported = ", ".join(sorted(_SUPPORTED_IMAGE_SUFFIXES))
        raise FloorplanRecognitionError(f"平面图仅支持 {supported}：{p}")
    return p


async def prepare_floorplan_task(
    llm: ChatModel,
    writer: RunWriter,
    image_path: str | Path,
    *,
    user_goal: str,
) -> str:
    """识别平面图、保存证据，并返回增强后的白盒任务文本。"""
    image = validate_floorplan_image_path(image_path)
    wall_result = _try_prepare_wall_extraction(writer, image)
    if wall_result is not None:
        return _build_wall_extraction_task(
            image,
            wall_result,
            user_goal=user_goal,
        )
    if not getattr(llm, "has_vision", True):
        raise FloorplanRecognitionError(
            "图像算法未提取到可用墙线，且当前模型配置没有 vision 角色，无法回退识别"
        )
    try:
        result = await recognize_floorplan(llm, image, user_goal=user_goal)
    except FloorplanRecognitionError as exc:
        _save_floorplan_evidence(
            writer,
            image,
            raw=exc.raw,
            layout=exc.layout,
            confidence=exc.confidence,
            facts=exc.to_facts(),
            assumptions=exc.assumptions,
            warnings=exc.warnings,
        )
        raise

    layout_artifact = _save_floorplan_evidence(
        writer,
        image,
        raw=result.raw,
        layout=result.layout,
        confidence=result.confidence,
        facts=result.to_facts(),
        assumptions=result.assumptions,
        warnings=result.warnings,
    )
    if not result.ok:
        reason = "；".join(result.warnings) or "vision 未能可靠识别平面图"
        raise FloorplanRecognitionError(
            f"平面图识别未通过：{reason}",
            raw=result.raw,
            layout=result.layout,
            assumptions=result.assumptions,
            warnings=result.warnings,
            confidence=result.confidence,
            parsed=result.parsed,
        )
    if layout_artifact is None:
        raise FloorplanRecognitionError("平面图识别未生成 layout 证据", raw=result.raw)

    layout_json = json.dumps(result.layout, ensure_ascii=False)
    assumptions = "；".join(result.assumptions) if result.assumptions else "无"
    warnings = "；".join(result.warnings) if result.warnings else "无"
    return f"""\
{user_goal}

[平面图识别结果]
- 输入图：{image}
- 识别置信度：{result.confidence:.2f}
- assumptions：{assumptions}
- warnings：{warnings}
- layout_json 已保存到 {layout_artifact.path}，也在下方给出。

[执行要求]
1. 使用下面的 layout_json 调用 wb_build，默认 prefix="WB"；如需修复，只允许小幅调整门洞对齐、
   房间整数格尺寸和外墙窗，不能改成与平面图拓扑不一致的新方案。
2. 不生成 gameplay、props、cover、spawn_points、routes。
3. wb_build 后必须调用 wb_validate；再调用 viewport_screenshot 生成居中完整截图：
   使用 focus_prefix="WB"、margin=6.0、clean_view=true，不手写 location/rotation；
   截图后触发 vision_review；最后调用 navmesh_rebuild 和 path_test 验证主要空间可达。
4. 最终简短报告平面图识别、白盒校验、截图视觉审查和导航验证结果。

layout_json:
```json
{layout_json}
```
"""


def _try_prepare_wall_extraction(
    writer: RunWriter,
    image: Path,
) -> WallExtractionResult | None:
    output_dir = writer.dir / "_generated" / "floorplan_wall_extraction"
    try:
        result = extract_floorplan_walls(image, output_dir)
    except (WallExtractionError, OSError, SyntaxError, ValueError):
        return None
    if len(result.lines) < 2:
        return None
    artifacts = _save_wall_extraction_evidence(writer, image, result)
    facts = dict(result.facts())
    facts.update(
        {
            "line_svg_artifact": artifacts["line_svg"],
            "body_svg_artifact": artifacts["body_svg"],
            "layout_artifact": artifacts["layout"],
            "snap_report_artifact": artifacts["snap_report"],
        }
    )
    writer.event(
        "floorplan_wall_extraction",
        facts=facts,
        line_svg_artifact=artifacts["line_svg"],
        body_svg_artifact=artifacts["body_svg"],
        line_overlay_artifact=artifacts["line_overlay"],
        layout_artifact=artifacts["layout"],
        snap_report_artifact=artifacts["snap_report"],
        summary_artifact=artifacts["summary"],
    )
    return result


def _save_wall_extraction_evidence(
    writer: RunWriter,
    image: Path,
    result: WallExtractionResult,
) -> dict[str, str]:
    base = "floorplans/wall_extraction"

    def save(kind: str, path: Path, name: str) -> str:
        artifact = writer.save_artifact(
            kind,
            f"{base}/{name}",
            path.read_bytes(),
            source=str(image),
            line_count=len(result.lines),
            wall_thickness_mode_px=result.wall_thickness_mode_px,
        )
        return artifact.path

    return {
        "body_svg": save("floorplan_wall_body_svg", result.body_svg, "wall_body.svg"),
        "body_overlay": save(
            "floorplan_wall_body_overlay",
            result.body_overlay,
            "wall_body_overlay.png",
        ),
        "line_svg": save("floorplan_wall_line_svg", result.line_svg, "wall_lines.svg"),
        "line_overlay": save(
            "floorplan_wall_line_overlay",
            result.line_overlay,
            "wall_lines_overlay.png",
        ),
        "layout": save("floorplan_wall_layout", result.layout_json, "layout_walls.json"),
        "snap_report": save(
            "floorplan_wall_snap_report",
            result.snap_report_json,
            "snap_report.json",
        ),
        "summary": save("floorplan_wall_summary", result.summary_json, "summary.json"),
    }


def _build_wall_extraction_task(
    image: Path,
    result: WallExtractionResult,
    *,
    user_goal: str,
) -> str:
    layout_json = result.layout_json.read_text(encoding="utf-8").strip()
    return f"""\
{user_goal}

[平面图墙线算法结果]
- 输入图：{image}
- 提取墙线：{len(result.lines)} 条
- 墙体主厚度：{result.wall_thickness_mode_px}px；导出 SVG 统一
  stroke-width={result.uniform_stroke_width_px}
- 中心线 SVG：{result.line_svg}
- 完整墙体 SVG：{result.body_svg}
- 叠加预览：{result.line_overlay}
- SVG→格子 snap 报告：{result.snap_report_json}
- walls layout_json 已保存到 {result.layout_json}，也在下方给出。

[执行要求]
1. 优先使用下面的 walls layout_json 调用 wb_build，默认 prefix="WB"；
   不要把它改写成视觉猜测的房间矩形。
2. 只生成墙体白盒，不生成 gameplay、props、cover、spawn_points、routes。
3. wb_build 后必须调用 wb_validate；再调用 viewport_screenshot 生成居中完整截图：
   使用 focus_prefix="WB"、margin=6.0、clean_view=true，不手写 location/rotation；
   截图后触发 vision_review；最后调用 navmesh_rebuild 和 path_test 验证主要空间可达。
4. 最终简短报告墙线提取、白盒校验、截图视觉审查和导航验证结果。

layout_json:
```json
{layout_json}
```
"""


def _loads_json_object(body: str) -> Any:
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        if exc.msg != "Extra data":
            raise
        try:
            data, end = json.JSONDecoder().raw_decode(body)
        except json.JSONDecodeError:
            raise exc from None
        if body[end:].strip():
            return data
        raise


def _repair_layout_after_validation_error(
    layout: dict[str, Any], error_text: str
) -> dict[str, Any] | None:
    if not any(marker in error_text for marker in _REPAIRABLE_LAYOUT_ERROR_MARKERS):
        return None
    rooms = _repair_candidate_rooms(layout.get("rooms"))
    if len(rooms) < 3:
        return None
    columns = min(4, max(2, math.ceil(math.sqrt(len(rooms)))))
    room_width = 6
    room_depth = 5
    repaired_rooms: list[dict[str, Any]] = []
    for index, room in enumerate(rooms):
        row = index // columns
        col = index % columns
        repaired = {
            "name": room["name"],
            "rect": [col * room_width, row * room_depth, room_width, room_depth],
            "level": 0,
            "doors": [],
        }
        if col > 0:
            repaired["doors"].append({"wall": "west", "at": 1, "width": 2})
            repaired_rooms[-1]["doors"].append({"wall": "east", "at": 1, "width": 2})
        elif row > 0:
            above = repaired_rooms[(row - 1) * columns]
            repaired["doors"].append({"wall": "south", "at": 2, "width": 2})
            above["doors"].append({"wall": "north", "at": 2, "width": 2})
        repaired_rooms.append(repaired)
    repaired_layout = dict(layout)
    repaired_layout["name"] = str(repaired_layout.get("name", "floorplan_blockout")).strip() or (
        "floorplan_blockout"
    )
    repaired_layout["structure_mode"] = "slab"
    repaired_layout["scale_profile"] = "realistic"
    repaired_layout["rooms"] = repaired_rooms
    repaired_layout.pop("stairs", None)
    repaired_layout.pop("gameplay", None)
    return repaired_layout


def _repair_candidate_rooms(raw_rooms: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_rooms, list):
        return []
    rooms: list[dict[str, Any]] = []
    used: set[str] = set()
    for index, raw in enumerate(raw_rooms, start=1):
        if not isinstance(raw, dict):
            continue
        rect = _valid_rect(raw.get("rect"))
        if rect is None:
            continue
        name = _clean_room_name(raw.get("name"), fallback=f"Room_{index}")
        if _is_outdoor_room_name(name):
            continue
        unique_name = _unique_name(name, used)
        used.add(unique_name)
        rooms.append({"name": unique_name, "rect": rect})
    return rooms


def _valid_rect(raw: Any) -> list[int] | None:
    if not isinstance(raw, list | tuple) or len(raw) != 4:
        return None
    values: list[int] = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        values.append(value)
    if values[2] < 2 or values[3] < 2:
        return None
    return values


def _clean_room_name(raw: Any, *, fallback: str) -> str:
    name = str(raw).strip() if raw is not None else ""
    if not name:
        return fallback
    return " ".join(name.split())[:40] or fallback


def _is_outdoor_room_name(name: str) -> bool:
    lowered = name.casefold()
    return any(token in lowered for token in _OUTDOOR_ROOM_TOKENS)


def _unique_name(name: str, used: set[str]) -> str:
    if name not in used:
        return name
    suffix = 2
    while f"{name}_{suffix}" in used:
        suffix += 1
    return f"{name}_{suffix}"


def _save_floorplan_evidence(
    writer: RunWriter,
    image: Path,
    *,
    raw: str,
    layout: dict[str, Any] | None,
    confidence: float,
    facts: dict[str, Any],
    assumptions: list[str],
    warnings: list[str],
):
    image_artifact = writer.save_artifact(
        "floorplan_image",
        f"floorplans/{image.name}",
        image.read_bytes(),
        source=str(image),
    )
    raw_artifact = writer.save_artifact(
        "floorplan_raw",
        "floorplans/recognition_raw.txt",
        raw,
        source=str(image),
    )
    layout_artifact = None
    if isinstance(layout, dict) and layout:
        layout_text = json.dumps(layout, ensure_ascii=False, indent=2) + "\n"
        layout_artifact = writer.save_artifact(
            "floorplan_layout",
            "floorplans/layout.json",
            layout_text,
            source=str(image),
            confidence=confidence,
        )
    writer.event(
        "floorplan_recognition",
        facts=facts,
        image_artifact=image_artifact.path,
        raw_artifact=raw_artifact.path,
        layout_artifact=layout_artifact.path if layout_artifact is not None else None,
        assumptions=assumptions,
        warnings=warnings,
    )
    return layout_artifact


def _normalize_layout(layout: dict[str, Any]) -> dict[str, Any]:
    normalized = {key: value for key, value in layout.items() if key in _ALLOWED_LAYOUT_KEYS}
    normalized.setdefault("name", "floorplan_blockout")
    normalized["structure_mode"] = str(normalized.get("structure_mode", "slab")).strip() or "slab"
    normalized["scale_profile"] = (
        str(normalized.get("scale_profile", "realistic")).strip() or "realistic"
    )
    rooms = normalized.get("rooms")
    if isinstance(rooms, list):
        normalized["rooms"] = [
            _normalize_room(room, fallback_name=f"Room_{index}")
            for index, room in enumerate(rooms, start=1)
        ]
    return normalized


def _normalize_room(room: Any, *, fallback_name: str) -> Any:
    if not isinstance(room, dict):
        return room
    normalized = dict(room)
    label = normalized.pop("label", None)
    room_id = normalized.pop("id", None)
    for alias in (label, room_id):
        if "name" not in normalized and alias is not None and str(alias).strip():
            normalized["name"] = str(alias).strip()
    if "name" not in normalized:
        normalized["name"] = fallback_name
    normalized.pop("props", None)
    normalized.pop("gameplay", None)
    normalized.setdefault("level", 0)
    return normalized


def _confidence(raw: Any) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, value))


def _string_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


__all__ = [
    "FloorplanRecognitionError",
    "FloorplanRecognitionResult",
    "build_floorplan_messages",
    "parse_floorplan_response",
    "prepare_floorplan_task",
    "recognize_floorplan",
    "validate_floorplan_image_path",
]
