"""平面图墙线算法：黑色粗墙体 → SVG + walls DSL。"""

from __future__ import annotations

import json

from PIL import Image, ImageDraw

from ue5agent.floorplan.wall_extractor import (
    convert_wall_svg_to_grid_dsl,
    extract_floorplan_walls,
)
from ue5agent.whitebox.compiler import compile_layout, layout_from_dict
from ue5agent.whitebox.manifest import Manifest


def _make_wall_plan(tmp_path):
    path = tmp_path / "plan.png"
    image = Image.new("RGB", (180, 140), "white")
    draw = ImageDraw.Draw(image)
    # 细线模拟家具/标注，颜色足够深但厚度不应通过墙体筛选。
    draw.line((10, 15, 170, 15), fill=(40, 40, 40), width=1)
    # 6px 粗墙体：L 形转角 + 一个短隔墙，短隔墙不能因为长度短被误删。
    draw.rectangle((20, 50, 120, 55), fill=(0, 0, 0))
    draw.rectangle((115, 50, 120, 110), fill=(0, 0, 0))
    draw.rectangle((50, 84, 76, 89), fill=(0, 0, 0))
    image.save(path)
    return path


def test_extract_floorplan_walls_filters_by_thickness_and_keeps_short_walls(tmp_path):
    image = _make_wall_plan(tmp_path)

    result = extract_floorplan_walls(image, tmp_path / "out", grid_px=10)

    assert result.ok is True
    assert result.wall_thickness_mode_px == 6
    assert result.uniform_stroke_width_px == 6
    assert len(result.lines) >= 3
    assert not any(abs(line.y1 - 15) <= 1 and line.axis == "h" for line in result.lines)
    assert any(
        line.axis == "h" and 48 <= line.y1 <= 91 and line.length_px <= 35 for line in result.lines
    )
    assert result.body_svg.exists()
    assert result.line_svg.exists()
    assert result.line_overlay.exists()
    assert result.layout_json.exists()

    svg = result.line_svg.read_text(encoding="utf-8")
    assert 'stroke-width="6"' in svg
    assert 'data-detected-thickness="1' not in svg

    layout = json.loads(result.layout_json.read_text(encoding="utf-8"))
    assert layout["structure_mode"] == "slab"
    assert layout["walls"]
    assert all("from" in wall and "to" in wall for wall in layout["walls"])


def test_extract_floorplan_walls_accepts_crop_but_keeps_source_pixel_coordinates(tmp_path):
    image = _make_wall_plan(tmp_path)

    result = extract_floorplan_walls(image, tmp_path / "out", crop=(10, 40, 130, 120), grid_px=10)

    assert result.ok is True
    assert result.crop == (10, 40, 130, 120)
    assert min(line.x1 for line in result.lines) >= 20
    assert min(line.y1 for line in result.lines) >= 50


def test_default_grid_preserves_nearby_parallel_walls_in_dsl(tmp_path):
    path = tmp_path / "parallel.png"
    image = Image.new("RGB", (140, 100), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 35, 110, 40), fill=(0, 0, 0))
    draw.rectangle((20, 51, 110, 56), fill=(0, 0, 0))
    image.save(path)

    result = extract_floorplan_walls(path, tmp_path / "out")
    layout = json.loads(result.layout_json.read_text(encoding="utf-8"))
    placements = compile_layout(layout_from_dict(layout), Manifest(grid=100.0, assets={}))

    assert len(result.lines) == 2
    assert len(layout["walls"]) == 2
    assert sum(placement.kind == "wall" for placement in placements) == 2


def test_convert_wall_svg_to_grid_dsl_reads_svg_lines_and_auto_selects_grid(tmp_path):
    svg = tmp_path / "wall_lines.svg"
    svg.write_text(
        """<svg xmlns="http://www.w3.org/2000/svg" width="160" height="120" viewBox="0 0 160 120">
  <g stroke="#ffa500" stroke-width="5">
    <line id="wall_001" x1="20.00" y1="35.00" x2="110.00" y2="35.00" data-axis="h"/>
    <line id="wall_002" x1="20.00" y1="51.00" x2="110.00" y2="51.00" data-axis="h"/>
    <line id="wall_003" x1="110.00" y1="35.00" x2="110.00" y2="90.00" data-axis="v"/>
  </g>
</svg>
""",
        encoding="utf-8",
    )

    result = convert_wall_svg_to_grid_dsl(svg, tmp_path / "grid")
    layout = json.loads(result.layout_json.read_text(encoding="utf-8"))
    report = json.loads(result.snap_report_json.read_text(encoding="utf-8"))
    placements = compile_layout(layout_from_dict(layout), Manifest(grid=100.0, assets={}))

    assert result.units_per_grid == 15
    assert report["source"] == str(svg)
    assert report["wall_count_before"] == 3
    assert report["wall_count_after"] == 3
    assert report["duplicate_wall_count"] == 0
    assert layout["coordinate_space"] == "grid"
    assert layout["source"]["line_svg"] == str(svg)
    assert layout["source"]["units_per_grid"] == 15
    assert sum(placement.kind == "wall" for placement in placements) == 3


def test_convert_wall_svg_to_grid_dsl_accepts_manual_units_per_grid(tmp_path):
    svg = tmp_path / "wall_lines.svg"
    svg.write_text(
        """<svg xmlns="http://www.w3.org/2000/svg" width="100" height="80">
  <line id="wall_001" x1="10" y1="20" x2="70" y2="20" data-axis="h"/>
</svg>
""",
        encoding="utf-8",
    )

    result = convert_wall_svg_to_grid_dsl(svg, tmp_path / "grid", units_per_grid=20)
    layout = json.loads(result.layout_json.read_text(encoding="utf-8"))

    assert result.units_per_grid == 20
    assert layout["walls"] == [{"name": "wall_001", "from": [0, 0], "to": [3, 0]}]
