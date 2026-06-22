"""用平面图门宽候选校准 SVG→DSL 尺度。"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Any, Literal

from ue5agent.floorplan.wall_extractor import (
    WallExtractionError,
    convert_wall_svg_to_grid_dsl,
)

DoorAxis = Literal["h", "v"]


@dataclass(frozen=True)
class DoorCandidate:
    id: str
    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float = 1.0

    @property
    def width_svg(self) -> float:
        return math.hypot(self.x2 - self.x1, self.y2 - self.y1)

    @property
    def axis(self) -> DoorAxis:
        return "h" if abs(self.x2 - self.x1) >= abs(self.y2 - self.y1) else "v"

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["axis"] = self.axis
        data["width_svg"] = _clean_number(self.width_svg)
        return data


@dataclass(frozen=True)
class DoorScaleCalibrationResult:
    ok: bool
    line_svg: Path
    output_dir: Path
    layout_json: Path
    snap_report_json: Path
    calibration_report_json: Path
    units_per_grid: float
    target_door_width_grid: float
    used_door_count: int
    rejected_door_count: int
    applied_opening_count: int
    unmatched_opening_count: int

    def facts(self) -> dict[str, Any]:
        return {
            "kind": "floorplan_door_scale_calibration",
            "ok": self.ok,
            "line_svg": str(self.line_svg),
            "layout_json": str(self.layout_json),
            "snap_report_json": str(self.snap_report_json),
            "calibration_report_json": str(self.calibration_report_json),
            "units_per_grid": _clean_number(self.units_per_grid),
            "target_door_width_grid": _clean_number(self.target_door_width_grid),
            "used_door_count": self.used_door_count,
            "rejected_door_count": self.rejected_door_count,
            "applied_opening_count": self.applied_opening_count,
            "unmatched_opening_count": self.unmatched_opening_count,
        }


def calibrate_wall_svg_to_grid_dsl(
    line_svg: str | Path,
    output_dir: str | Path,
    *,
    door_candidates: list[dict[str, Any]],
    target_door_width_grid: float = 1.0,
    min_confidence: float = 0.5,
    outlier_ratio: float = 0.35,
    apply_openings: bool = False,
    wall_match_tolerance_grid: int = 0,
) -> DoorScaleCalibrationResult:
    """用标准门宽反推 `units_per_grid`，再生成整数格 walls DSL。

    `door_candidates` 由视觉或图像算法提供，坐标必须与 `wall_lines.svg` 同处 SVG
    坐标系。每个候选至少包含 `x1/y1/x2/y2`，表示门洞两侧端点。
    """
    if target_door_width_grid <= 0:
        raise WallExtractionError("target_door_width_grid 必须大于 0")
    if not door_candidates:
        raise WallExtractionError("door_candidates 不能为空")

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    parsed_doors = _parse_door_candidates(door_candidates)
    used_doors, rejected_doors = _select_scale_doors(
        parsed_doors,
        min_confidence=min_confidence,
        outlier_ratio=outlier_ratio,
    )
    if not used_doors:
        raise WallExtractionError("没有可用于标定的门候选")

    units_per_grid = median([door.width_svg for door in used_doors]) / target_door_width_grid
    conversion = convert_wall_svg_to_grid_dsl(
        line_svg,
        output,
        units_per_grid=units_per_grid,
    )

    layout = json.loads(conversion.layout_json.read_text(encoding="utf-8"))
    calibration_payload = {
        "method": "door_width",
        "target_door_width_grid": _clean_number(target_door_width_grid),
        "units_per_grid": _clean_number(units_per_grid),
        "used_door_count": len(used_doors),
        "rejected_door_count": len(rejected_doors),
    }
    layout.setdefault("source", {})["calibration"] = calibration_payload

    applied_openings: list[dict[str, Any]] = []
    unmatched_openings: list[dict[str, Any]] = []
    if apply_openings:
        applied_openings, unmatched_openings = _apply_openings_to_walls(
            layout,
            used_doors,
            origin=conversion.origin,
            units_per_grid=units_per_grid,
            target_door_width_grid=target_door_width_grid,
            wall_match_tolerance_grid=wall_match_tolerance_grid,
        )

    conversion.layout_json.write_text(
        json.dumps(layout, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    calibration_report_json = output / "door_calibration_report.json"
    result = DoorScaleCalibrationResult(
        ok=True,
        line_svg=Path(line_svg),
        output_dir=output,
        layout_json=conversion.layout_json,
        snap_report_json=conversion.snap_report_json,
        calibration_report_json=calibration_report_json,
        units_per_grid=units_per_grid,
        target_door_width_grid=target_door_width_grid,
        used_door_count=len(used_doors),
        rejected_door_count=len(rejected_doors),
        applied_opening_count=len(applied_openings),
        unmatched_opening_count=len(unmatched_openings),
    )
    _write_calibration_report(
        result,
        parsed_doors=parsed_doors,
        used_doors=used_doors,
        rejected_doors=rejected_doors,
        applied_openings=applied_openings,
        unmatched_openings=unmatched_openings,
        min_confidence=min_confidence,
        outlier_ratio=outlier_ratio,
    )
    _merge_calibration_into_snap_report(result)
    return result


def _parse_door_candidates(raw_doors: list[dict[str, Any]]) -> list[DoorCandidate]:
    doors: list[DoorCandidate] = []
    for index, raw in enumerate(raw_doors, start=1):
        x1: Any
        y1: Any
        x2: Any
        y2: Any
        if {"x1", "y1", "x2", "y2"}.issubset(raw):
            x1, y1, x2, y2 = raw["x1"], raw["y1"], raw["x2"], raw["y2"]
        else:
            start = raw.get("from", raw.get("start"))
            end = raw.get("to", raw.get("end"))
            if start is None or end is None:
                jambs = raw.get("jambs")
                if isinstance(jambs, list) and len(jambs) == 2:
                    start, end = jambs
            if not _is_xy(start) or not _is_xy(end):
                raise WallExtractionError(
                    f"door_candidates[{index}] 必须包含 x1/y1/x2/y2 或 from/to"
                )
            x1, y1 = _xy_pair(start)
            x2, y2 = _xy_pair(end)
        door = DoorCandidate(
            id=str(raw.get("id") or raw.get("name") or f"door_{index:03d}"),
            x1=float(x1),
            y1=float(y1),
            x2=float(x2),
            y2=float(y2),
            confidence=float(raw.get("confidence", 1.0)),
        )
        doors.append(door)
    return doors


def _is_xy(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and len(value) == 2


def _xy_pair(value: Any) -> tuple[Any, Any]:
    if not _is_xy(value):
        raise WallExtractionError("坐标必须是 [x, y]")
    return value[0], value[1]


def _select_scale_doors(
    doors: list[DoorCandidate],
    *,
    min_confidence: float,
    outlier_ratio: float,
) -> tuple[list[DoorCandidate], list[dict[str, Any]]]:
    rejected: list[dict[str, Any]] = []
    confident: list[DoorCandidate] = []
    for door in doors:
        if door.confidence < min_confidence:
            rejected.append({**door.as_dict(), "reason": "low_confidence"})
        elif door.width_svg <= 0:
            rejected.append({**door.as_dict(), "reason": "zero_width"})
        else:
            confident.append(door)
    if not confident:
        return [], rejected

    base_width = median([door.width_svg for door in confident])
    min_width = base_width * (1.0 - outlier_ratio)
    max_width = base_width * (1.0 + outlier_ratio)
    used: list[DoorCandidate] = []
    for door in confident:
        if min_width <= door.width_svg <= max_width:
            used.append(door)
        else:
            rejected.append({**door.as_dict(), "reason": "outlier_width"})
    return used, rejected


def _apply_openings_to_walls(
    layout: dict[str, Any],
    doors: list[DoorCandidate],
    *,
    origin: tuple[float, float],
    units_per_grid: float,
    target_door_width_grid: float,
    wall_match_tolerance_grid: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    applied: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    walls = layout.get("walls", [])
    for door in doors:
        opening_width = max(1, round(target_door_width_grid))
        match = _find_wall_for_door(
            walls,
            door,
            origin=origin,
            units_per_grid=units_per_grid,
            opening_width=opening_width,
            wall_match_tolerance_grid=wall_match_tolerance_grid,
        )
        if match is None:
            unmatched.append({**door.as_dict(), "reason": "no_covering_wall"})
            continue
        wall, opening = match
        wall_openings = wall.setdefault("openings", [])
        if opening not in wall_openings:
            wall_openings.append(opening)
            wall_openings.sort(key=lambda item: item["at"])
        applied.append(
            {
                "door_id": door.id,
                "wall": wall.get("name"),
                "opening": opening,
            }
        )
    return applied, unmatched


def _find_wall_for_door(
    walls: list[dict[str, Any]],
    door: DoorCandidate,
    *,
    origin: tuple[float, float],
    units_per_grid: float,
    opening_width: int,
    wall_match_tolerance_grid: int,
) -> tuple[dict[str, Any], dict[str, int]] | None:
    if door.axis == "h":
        coord = round((((door.y1 + door.y2) / 2.0) - origin[1]) / units_per_grid)
        lo = round((min(door.x1, door.x2) - origin[0]) / units_per_grid)
    else:
        coord = round((((door.x1 + door.x2) / 2.0) - origin[0]) / units_per_grid)
        lo = round((min(door.y1, door.y2) - origin[1]) / units_per_grid)
    hi = lo + opening_width

    best: tuple[int, dict[str, Any], dict[str, int]] | None = None
    for wall in walls:
        start = wall.get("from", wall.get("start"))
        end = wall.get("to", wall.get("end"))
        if not _is_xy(start) or not _is_xy(end):
            continue
        start_x, start_y = _xy_pair(start)
        end_x, end_y = _xy_pair(end)
        wall_horizontal = start_y == end_y
        if (door.axis == "h") != wall_horizontal:
            continue
        wall_coord = int(start_y if wall_horizontal else start_x)
        if abs(wall_coord - coord) > wall_match_tolerance_grid:
            continue
        if wall_horizontal:
            wall_lo = min(int(start_x), int(end_x))
            wall_hi = max(int(start_x), int(end_x))
        else:
            wall_lo = min(int(start_y), int(end_y))
            wall_hi = max(int(start_y), int(end_y))
        if lo < wall_lo or hi > wall_hi:
            continue
        score = abs(wall_coord - coord)
        opening = {"at": lo - wall_lo, "width": opening_width}
        if best is None or score < best[0]:
            best = (score, wall, opening)
    if best is None:
        return None
    return best[1], best[2]


def _write_calibration_report(
    result: DoorScaleCalibrationResult,
    *,
    parsed_doors: list[DoorCandidate],
    used_doors: list[DoorCandidate],
    rejected_doors: list[dict[str, Any]],
    applied_openings: list[dict[str, Any]],
    unmatched_openings: list[dict[str, Any]],
    min_confidence: float,
    outlier_ratio: float,
) -> None:
    payload = {
        "ok": result.ok,
        "source": str(result.line_svg),
        "layout_json": str(result.layout_json),
        "snap_report_json": str(result.snap_report_json),
        "method": "door_width",
        "target_door_width_grid": _clean_number(result.target_door_width_grid),
        "units_per_grid": _clean_number(result.units_per_grid),
        "min_confidence": _clean_number(min_confidence),
        "outlier_ratio": _clean_number(outlier_ratio),
        "door_candidates": [door.as_dict() for door in parsed_doors],
        "used_doors": [door.as_dict() for door in used_doors],
        "rejected_doors": rejected_doors,
        "used_door_count": result.used_door_count,
        "rejected_door_count": result.rejected_door_count,
        "applied_openings": applied_openings,
        "unmatched_openings": unmatched_openings,
        "applied_opening_count": result.applied_opening_count,
        "unmatched_opening_count": result.unmatched_opening_count,
    }
    result.calibration_report_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _merge_calibration_into_snap_report(result: DoorScaleCalibrationResult) -> None:
    snap = json.loads(result.snap_report_json.read_text(encoding="utf-8"))
    snap["calibration"] = {
        "method": "door_width",
        "calibration_report_json": str(result.calibration_report_json),
        "target_door_width_grid": _clean_number(result.target_door_width_grid),
        "units_per_grid": _clean_number(result.units_per_grid),
        "used_door_count": result.used_door_count,
    }
    result.snap_report_json.write_text(
        json.dumps(snap, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _clean_number(value: float) -> int | float:
    rounded = round(value, 6)
    nearest = round(rounded)
    return nearest if abs(rounded - nearest) < 1e-6 else rounded


__all__ = [
    "DoorCandidate",
    "DoorScaleCalibrationResult",
    "calibrate_wall_svg_to_grid_dsl",
]
