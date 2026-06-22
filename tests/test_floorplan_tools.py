"""平面图墙线工具集：给 agent 调用的本地工具包装。"""

from __future__ import annotations

import json

import pytest
from PIL import Image, ImageDraw

from ue5agent.agent.tool_pipeline import extract_facts
from ue5agent.core.permissions import PermissionLevel
from ue5agent.tools.floorplan_tools import build_floorplan_tools


def _make_wall_plan(tmp_path):
    path = tmp_path / "plan.png"
    image = Image.new("RGB", (120, 90), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((15, 30, 80, 35), fill=(0, 0, 0))
    draw.rectangle((75, 30, 80, 75), fill=(0, 0, 0))
    draw.line((5, 10, 110, 10), fill=(20, 20, 20), width=1)
    image.save(path)
    return path


def test_build_floorplan_tools_exposes_wall_extraction_tool(tmp_path):
    specs = {spec.name: spec for spec in build_floorplan_tools(tmp_path)}

    spec = specs["floorplan_extract_walls"]

    assert spec.level is PermissionLevel.WRITE_PROJECT
    assert spec.effects is not None
    assert spec.effects.requires_checkpoint is False
    assert spec.parameters["required"] == ["image_path"]
    assert specs["floorplan_svg_to_grid_dsl"].parameters["required"] == ["line_svg"]


async def test_floorplan_extract_walls_tool_writes_outputs_and_facts(tmp_path):
    image = _make_wall_plan(tmp_path)
    spec = {spec.name: spec for spec in build_floorplan_tools(tmp_path)}["floorplan_extract_walls"]

    text = await spec.handler(
        image_path=str(image),
        output_dir="runs/wall-tool-test",
        threshold=140,
        grid_px=10,
    )

    visible, facts = extract_facts(text)

    assert "已提取" in visible
    assert facts is not None
    assert facts["kind"] == "floorplan_wall_extraction"
    assert facts["ok"] is True
    assert facts["line_count"] >= 2
    layout = tmp_path / "runs" / "wall-tool-test" / "layout_walls.json"
    assert layout.exists()
    assert json.loads(layout.read_text(encoding="utf-8"))["walls"]


async def test_floorplan_extract_walls_tool_rejects_output_dir_escape(tmp_path):
    image = _make_wall_plan(tmp_path)
    spec = {spec.name: spec for spec in build_floorplan_tools(tmp_path)}["floorplan_extract_walls"]

    with pytest.raises(ValueError, match="路径越界"):
        await spec.handler(image_path=str(image), output_dir="../outside")


async def test_floorplan_svg_to_grid_dsl_tool_writes_layout_and_report(tmp_path):
    svg = tmp_path / "wall_lines.svg"
    svg.write_text(
        """<svg xmlns="http://www.w3.org/2000/svg" width="120" height="90">
  <line id="wall_001" x1="15" y1="30" x2="80" y2="30" data-axis="h"/>
  <line id="wall_002" x1="80" y1="30" x2="80" y2="75" data-axis="v"/>
</svg>
""",
        encoding="utf-8",
    )
    spec = {spec.name: spec for spec in build_floorplan_tools(tmp_path)}[
        "floorplan_svg_to_grid_dsl"
    ]

    text = await spec.handler(line_svg=str(svg), output_dir="runs/svg-grid-test")
    visible, facts = extract_facts(text)

    assert "已转换" in visible
    assert facts is not None
    assert facts["kind"] == "floorplan_svg_to_grid_dsl"
    assert facts["ok"] is True
    assert facts["wall_count_after"] == 2
    assert (tmp_path / "runs" / "svg-grid-test" / "layout_walls.json").exists()
    assert (tmp_path / "runs" / "svg-grid-test" / "snap_report.json").exists()
