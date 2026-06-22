"""平面图门宽标定：视觉门候选 -> SVG 比例 -> walls DSL。"""

from __future__ import annotations

import importlib
import json

import pytest

from ue5agent.whitebox.compiler import compile_layout, layout_from_dict
from ue5agent.whitebox.manifest import Manifest


def _door_calibration_module():
    try:
        return importlib.import_module("ue5agent.floorplan.door_calibration")
    except ModuleNotFoundError as exc:
        pytest.fail(f"缺少门宽标定模块：{exc}")


def _write_svg(tmp_path, body: str):
    svg = tmp_path / "wall_lines.svg"
    svg.write_text(
        f"""<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80">
  {body}
</svg>
""",
        encoding="utf-8",
    )
    return svg


def test_calibrate_wall_svg_to_grid_dsl_uses_standard_door_width(tmp_path):
    module = _door_calibration_module()
    svg = _write_svg(
        tmp_path,
        '<line id="wall_001" x1="0" y1="0" x2="84" y2="0" data-axis="h"/>',
    )

    result = module.calibrate_wall_svg_to_grid_dsl(
        svg,
        tmp_path / "out",
        door_candidates=[
            {"id": "door_a", "x1": 24, "y1": 0, "x2": 36, "y2": 0},
            {"id": "door_b", "x1": 60, "y1": 0, "x2": 72, "y2": 0},
        ],
        target_door_width_grid=1,
    )

    layout = json.loads(result.layout_json.read_text(encoding="utf-8"))
    report = json.loads(result.calibration_report_json.read_text(encoding="utf-8"))

    assert result.units_per_grid == 12
    assert result.used_door_count == 2
    assert layout["source"]["units_per_grid"] == 12
    assert layout["source"]["calibration"]["method"] == "door_width"
    assert layout["walls"] == [{"name": "wall_001", "from": [0, 0], "to": [7, 0]}]
    assert report["used_door_count"] == 2


def test_calibration_rejects_door_width_outliers(tmp_path):
    module = _door_calibration_module()
    svg = _write_svg(
        tmp_path,
        '<line id="wall_001" x1="0" y1="0" x2="96" y2="0" data-axis="h"/>',
    )

    result = module.calibrate_wall_svg_to_grid_dsl(
        svg,
        tmp_path / "out",
        door_candidates=[
            {"id": "door_a", "x1": 0, "y1": 0, "x2": 12, "y2": 0},
            {"id": "door_b", "x1": 24, "y1": 0, "x2": 36, "y2": 0},
            {"id": "double_door", "x1": 48, "y1": 0, "x2": 72, "y2": 0},
        ],
        target_door_width_grid=1,
    )

    report = json.loads(result.calibration_report_json.read_text(encoding="utf-8"))

    assert result.units_per_grid == 12
    assert result.used_door_count == 2
    assert report["rejected_doors"][0]["id"] == "double_door"
    assert report["rejected_doors"][0]["reason"] == "outlier_width"


def test_calibrated_conversion_projects_door_candidate_to_wall_opening(tmp_path):
    module = _door_calibration_module()
    svg = _write_svg(
        tmp_path,
        '<line id="wall_001" x1="0" y1="0" x2="60" y2="0" data-axis="h"/>',
    )

    result = module.calibrate_wall_svg_to_grid_dsl(
        svg,
        tmp_path / "out",
        door_candidates=[{"id": "door_a", "x1": 24, "y1": 0, "x2": 36, "y2": 0}],
        target_door_width_grid=1,
        apply_openings=True,
    )
    layout = json.loads(result.layout_json.read_text(encoding="utf-8"))
    placements = compile_layout(layout_from_dict(layout), Manifest(grid=100.0, assets={}))

    assert layout["walls"] == [
        {
            "name": "wall_001",
            "from": [0, 0],
            "to": [5, 0],
            "openings": [{"at": 2, "width": 1}],
        }
    ]
    assert result.applied_opening_count == 1
    assert sum(placement.kind == "wall" for placement in placements) == 2
