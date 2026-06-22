"""平面图工具集：给 agent 调用的本地图像算法工具。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ue5agent.core.permissions import PermissionLevel
from ue5agent.floorplan.wall_extractor import (
    convert_wall_svg_to_grid_dsl,
    extract_floorplan_walls,
)
from ue5agent.tools.effects import ToolEffects
from ue5agent.tools.registry import ToolSpec

_FLOORPLAN_EFFECTS = ToolEffects(
    idempotent=True,
    requires_checkpoint=False,
    supports_dry_run=False,
    resources=("build_artifacts",),
)


def build_floorplan_tools(project_root: Path) -> list[ToolSpec]:
    root = project_root.resolve()

    def _safe_output_dir(raw: str | None, image_path: Path) -> Path:
        if raw:
            candidate = Path(raw)
            path = candidate if candidate.is_absolute() else root / candidate
        else:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            path = root / "runs" / "floorplan_wall_extract" / f"{stamp}_{image_path.stem}"
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"路径越界（必须在工程根 {root} 内）：{raw}")
        return resolved

    async def floorplan_extract_walls(
        image_path: str,
        output_dir: str | None = None,
        threshold: int = 140,
        units_per_grid: float = 10.0,
        grid_px: float | None = None,
        crop: list[int] | None = None,
    ) -> str:
        image = Path(image_path)
        crop_box: tuple[int, int, int, int] | None = None
        if crop is not None:
            if len(crop) != 4:
                raise ValueError("crop 必须是 [x0, y0, x1, y1]")
            crop_box = (int(crop[0]), int(crop[1]), int(crop[2]), int(crop[3]))
        result = extract_floorplan_walls(
            image,
            _safe_output_dir(output_dir, image),
            threshold=threshold,
            grid_px=grid_px if grid_px is not None else units_per_grid,
            crop=crop_box,
        )
        facts = result.facts()
        return (
            f"已提取 {len(result.lines)} 条墙线，墙体主厚度 "
            f"{result.wall_thickness_mode_px}px。\n"
            f"- 统一线宽 SVG：{result.line_svg}\n"
            f"- 完整墙体 SVG：{result.body_svg}\n"
            f"- 叠加预览：{result.line_overlay}\n"
            f"- walls DSL：{result.layout_json}\n"
            f"- SVG→格子 snap 报告：{result.snap_report_json}\n"
            f"[facts] {json.dumps(facts, ensure_ascii=False)}"
        )

    async def floorplan_svg_to_grid_dsl(
        line_svg: str,
        output_dir: str | None = None,
        units_per_grid: float | str = "auto",
    ) -> str:
        svg = Path(line_svg)
        result = convert_wall_svg_to_grid_dsl(
            svg,
            _safe_output_dir(output_dir, svg),
            units_per_grid=units_per_grid,
        )
        facts = result.facts()
        return (
            f"已转换 {result.source_line_count} 条 SVG 墙线为 {result.wall_count_after} 条 "
            f"DSL 网格墙，units_per_grid={result.units_per_grid:g}。\n"
            f"- walls DSL：{result.layout_json}\n"
            f"- snap 报告：{result.snap_report_json}\n"
            f"[facts] {json.dumps(facts, ensure_ascii=False)}"
        )

    return [
        ToolSpec(
            "floorplan_extract_walls",
            (
                "从本地平面图图片中按黑色粗墙体像素提取墙线，生成完整墙体 SVG、"
                "统一线宽中心线 SVG、叠加预览 PNG 与可传给 wb_build 的 walls DSL。"
            ),
            _schema(
                image_path={
                    "type": "string",
                    "description": "本地 png/jpg/jpeg/webp 平面图路径，可为绝对路径",
                    "_required": True,
                },
                output_dir={
                    "type": "string",
                    "description": (
                        "输出目录，缺省写入 runs/floorplan_wall_extract/；必须在工程根内"
                    ),
                },
                threshold={
                    "type": "integer",
                    "description": "深色像素阈值，默认 140；值越大越容易纳入灰色墙线",
                },
                units_per_grid={
                    "type": "number",
                    "description": "导出 walls DSL 时每格对应的 SVG 坐标单位，默认 10",
                },
                grid_px={
                    "type": "number",
                    "description": "兼容旧参数；等同 units_per_grid",
                },
                crop={
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "可选源图裁剪框 [x0,y0,x1,y1]，SVG 坐标仍保持源图坐标",
                },
            ),
            PermissionLevel.WRITE_PROJECT,
            floorplan_extract_walls,
            effects=_FLOORPLAN_EFFECTS,
        ),
        ToolSpec(
            "floorplan_svg_to_grid_dsl",
            (
                "从已有墙线 SVG 的 line 坐标直接映射到整数格 walls DSL，"
                "输出 layout_walls.json 与 snap_report.json。"
            ),
            _schema(
                line_svg={
                    "type": "string",
                    "description": "wall_lines.svg 路径，读取其中 line x1/y1/x2/y2 精确坐标",
                    "_required": True,
                },
                output_dir={
                    "type": "string",
                    "description": (
                        "输出目录，缺省写入 runs/floorplan_wall_extract/；必须在工程根内"
                    ),
                },
                units_per_grid={
                    "anyOf": [{"type": "number"}, {"type": "string"}],
                    "description": "每个 DSL 格对应多少 SVG 坐标单位；传 auto 自动选择",
                },
            ),
            PermissionLevel.WRITE_PROJECT,
            floorplan_svg_to_grid_dsl,
            effects=_FLOORPLAN_EFFECTS,
        ),
    ]


def _schema(**props: dict[str, Any]) -> dict[str, Any]:
    required = [key for key, value in props.items() if value.pop("_required", False)]
    return {"type": "object", "properties": props, "required": required}


__all__ = ["build_floorplan_tools"]
