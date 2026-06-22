"""平面图墙体提取：黑色粗墙体像素 → SVG 与白盒 walls DSL。

算法刻意只做确定性图像处理：
- 阈值提取深色像素；
- 从水平/垂直扫描线估计墙体主厚度；
- 只保留短边厚度接近主厚度的墙体矩形；
- 生成统一 stroke-width 的中心线 SVG 和 `walls` DSL。
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw

Axis = Literal["h", "v"]

_SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_ORANGE = "#ffa500"
_AUTO_UNITS_PER_GRID_CANDIDATES = (40.0, 30.0, 25.0, 20.0, 15.0, 12.0, 10.0, 8.0, 5.0)


class WallExtractionError(Exception):
    """平面图墙体算法未能生成可用墙线。"""


@dataclass(frozen=True)
class WallBodyRect:
    id: str
    axis: Axis
    x: int
    y: int
    width: int
    height: int
    detected_thickness: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WallLine:
    id: str
    axis: Axis
    x1: float
    y1: float
    x2: float
    y2: float
    detected_thickness: float

    @property
    def length_px(self) -> float:
        return abs(self.x2 - self.x1) if self.axis == "h" else abs(self.y2 - self.y1)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "axis": self.axis,
            "x1": self.x1,
            "y1": self.y1,
            "x2": self.x2,
            "y2": self.y2,
            "detected_thickness": self.detected_thickness,
            "length_px": self.length_px,
        }


@dataclass(frozen=True)
class SvgGridConversionResult:
    ok: bool
    line_svg: Path
    output_dir: Path
    units_per_grid: float
    origin: tuple[float, float]
    source_line_count: int
    wall_count_after: int
    duplicate_wall_count: int
    zero_length_count: int
    max_snap_error_units: float
    mean_snap_error_units: float
    layout_json: Path
    snap_report_json: Path

    def facts(self) -> dict[str, Any]:
        return {
            "kind": "floorplan_svg_to_grid_dsl",
            "ok": self.ok,
            "line_svg": str(self.line_svg),
            "layout_json": str(self.layout_json),
            "snap_report_json": str(self.snap_report_json),
            "units_per_grid": _clean_number(self.units_per_grid),
            "wall_count_before": self.source_line_count,
            "wall_count_after": self.wall_count_after,
            "duplicate_wall_count": self.duplicate_wall_count,
            "zero_length_count": self.zero_length_count,
            "max_snap_error_units": round(self.max_snap_error_units, 4),
            "mean_snap_error_units": round(self.mean_snap_error_units, 4),
        }


@dataclass(frozen=True)
class WallExtractionResult:
    ok: bool
    image_path: Path
    output_dir: Path
    image_width: int
    image_height: int
    threshold: int
    crop: tuple[int, int, int, int] | None
    wall_thickness_mode_px: int
    thickness_histogram: dict[int, int]
    min_kept_thickness_px: int
    max_kept_thickness_px: int
    uniform_stroke_width_px: int
    lines: list[WallLine]
    body_rects: list[WallBodyRect]
    body_svg: Path
    body_overlay: Path
    line_svg: Path
    line_overlay: Path
    layout_json: Path
    snap_report_json: Path
    summary_json: Path

    def facts(self) -> dict[str, Any]:
        return {
            "kind": "floorplan_wall_extraction",
            "ok": self.ok,
            "line_count": len(self.lines),
            "body_rect_count": len(self.body_rects),
            "wall_thickness_mode_px": self.wall_thickness_mode_px,
            "uniform_stroke_width_px": self.uniform_stroke_width_px,
            "body_svg": str(self.body_svg),
            "line_svg": str(self.line_svg),
            "line_overlay": str(self.line_overlay),
            "layout_json": str(self.layout_json),
            "snap_report_json": str(self.snap_report_json),
            "summary_json": str(self.summary_json),
        }


def convert_wall_svg_to_grid_dsl(
    line_svg: str | Path,
    output_dir: str | Path,
    *,
    units_per_grid: float | str = "auto",
) -> SvgGridConversionResult:
    """从 SVG `<line>` 精确坐标直接映射到整数格 `walls` DSL。"""
    svg_path = Path(line_svg)
    if not svg_path.exists() or not svg_path.is_file():
        raise WallExtractionError(f"SVG 文件不存在：{svg_path}")
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    lines = _read_wall_lines_from_svg(svg_path)
    if not lines:
        raise WallExtractionError(f"SVG 中未找到可用 line 墙段：{svg_path}")
    origin = (
        min(min(line.x1, line.x2) for line in lines),
        min(min(line.y1, line.y2) for line in lines),
    )
    chosen_units = _choose_units_per_grid(lines, origin, units_per_grid)
    layout_json = output / "layout_walls.json"
    snap_report_json = output / "snap_report.json"
    conversion = _grid_walls_from_lines(lines, origin, chosen_units)
    layout = {
        "name": "floorplan_wall_lines",
        "structure_mode": "slab",
        "scale_profile": "realistic",
        "coordinate_space": "grid",
        "source": {
            "line_svg": str(svg_path),
            "units_per_grid": _clean_number(chosen_units),
            "origin": [_clean_number(origin[0]), _clean_number(origin[1])],
            "source_line_count": len(lines),
        },
        "rooms": [],
        "walls": conversion["walls"],
        "wall_thickness": 20,
        "wall_height": 300,
    }
    layout_json.write_text(
        json.dumps(layout, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    result = SvgGridConversionResult(
        ok=True,
        line_svg=svg_path,
        output_dir=output,
        units_per_grid=chosen_units,
        origin=origin,
        source_line_count=len(lines),
        wall_count_after=len(conversion["walls"]),
        duplicate_wall_count=conversion["duplicate_wall_count"],
        zero_length_count=conversion["zero_length_count"],
        max_snap_error_units=conversion["max_snap_error_units"],
        mean_snap_error_units=conversion["mean_snap_error_units"],
        layout_json=layout_json,
        snap_report_json=snap_report_json,
    )
    _write_snap_report(result, lines, conversion["line_reports"])
    return result


def extract_floorplan_walls(
    image_path: str | Path,
    output_dir: str | Path,
    *,
    threshold: int = 140,
    crop: tuple[int, int, int, int] | None = None,
    grid_px: float = 10.0,
    max_thickness_px: int = 30,
    min_wall_run_px: int | None = None,
) -> WallExtractionResult:
    """提取平面图墙线并落地产物。

    `crop` 使用源图像像素坐标 `(x0, y0, x1, y1)`；SVG 与 DSL 坐标仍会回到源图像
    像素坐标，便于和原图叠加审查。
    """
    image = _load_image(image_path)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    crop_box = _normalize_crop(crop, image.size)
    work_image = image.crop(crop_box) if crop_box is not None else image
    offset_x, offset_y = (crop_box[0], crop_box[1]) if crop_box is not None else (0, 0)
    width, height = work_image.size

    dark = _dark_mask(work_image, threshold)
    thickness_histogram = _estimate_thickness_histogram(dark, width, height, max_thickness_px)
    if not thickness_histogram:
        raise WallExtractionError("未找到足够的深色墙体像素，无法估计墙体厚度")
    wall_thickness = _histogram_mode(thickness_histogram)
    min_thickness = max(2, wall_thickness - 2)
    max_thickness = max(wall_thickness + 2, round(wall_thickness * 2.2))
    min_run = min_wall_run_px if min_wall_run_px is not None else max(wall_thickness * 2, 6)

    horizontal_pixels = _axis_run_pixels(dark, width, height, axis="h", min_run=min_run)
    vertical_pixels = _axis_run_pixels(dark, width, height, axis="v", min_run=min_run)
    rects = [
        *_rects_from_components(
            horizontal_pixels,
            width,
            height,
            axis="h",
            offset=(offset_x, offset_y),
            min_thickness=min_thickness,
            max_thickness=max_thickness,
            min_run=min_run,
        ),
        *_rects_from_components(
            vertical_pixels,
            width,
            height,
            axis="v",
            offset=(offset_x, offset_y),
            min_thickness=min_thickness,
            max_thickness=max_thickness,
            min_run=min_run,
        ),
    ]
    rects = _assign_rect_ids(_merge_rects(rects, gap_tolerance=wall_thickness))
    lines = _snap_line_corners(_lines_from_rects(rects), tolerance=max(2.0, wall_thickness * 1.5))
    if not lines:
        raise WallExtractionError("未提取到厚度一致的水平/垂直墙体线段")

    body_svg = output / "wall_body.svg"
    body_overlay = output / "wall_body_overlay.png"
    line_svg = output / "wall_lines.svg"
    line_overlay = output / "wall_lines_overlay.png"
    snap_report_json = output / "snap_report.json"
    summary_json = output / "summary.json"

    _write_body_svg(body_svg, image.size, rects, source=image_path)
    _write_line_svg(line_svg, image.size, lines, stroke_width=wall_thickness, source=image_path)
    _write_body_overlay(body_overlay, image, rects)
    _write_line_overlay(line_overlay, image, lines, stroke_width=wall_thickness)
    grid_result = convert_wall_svg_to_grid_dsl(
        line_svg,
        output,
        units_per_grid=grid_px,
    )

    result = WallExtractionResult(
        ok=True,
        image_path=Path(image_path),
        output_dir=output,
        image_width=image.size[0],
        image_height=image.size[1],
        threshold=threshold,
        crop=crop_box,
        wall_thickness_mode_px=wall_thickness,
        thickness_histogram=dict(sorted(thickness_histogram.items())),
        min_kept_thickness_px=min_thickness,
        max_kept_thickness_px=max_thickness,
        uniform_stroke_width_px=wall_thickness,
        lines=lines,
        body_rects=rects,
        body_svg=body_svg,
        body_overlay=body_overlay,
        line_svg=line_svg,
        line_overlay=line_overlay,
        layout_json=grid_result.layout_json,
        snap_report_json=snap_report_json,
        summary_json=summary_json,
    )
    _write_summary(result)
    return result


def _load_image(image_path: str | Path) -> Image.Image:
    path = Path(image_path)
    if not path.exists() or not path.is_file():
        raise WallExtractionError(f"平面图文件不存在：{path}")
    if path.suffix.lower() not in _SUPPORTED_IMAGE_SUFFIXES:
        supported = ", ".join(sorted(_SUPPORTED_IMAGE_SUFFIXES))
        raise WallExtractionError(f"平面图仅支持 {supported}：{path}")
    return Image.open(path).convert("RGB")


def _normalize_crop(
    crop: tuple[int, int, int, int] | None, image_size: tuple[int, int]
) -> tuple[int, int, int, int] | None:
    if crop is None:
        return None
    if len(crop) != 4:
        raise WallExtractionError("crop 必须是 [x0, y0, x1, y1]")
    x0, y0, x1, y1 = (int(v) for v in crop)
    width, height = image_size
    if x0 < 0 or y0 < 0 or x1 > width or y1 > height or x1 <= x0 or y1 <= y0:
        raise WallExtractionError(f"crop 超出图像范围：{crop}，图像尺寸={image_size}")
    return (x0, y0, x1, y1)


def _dark_mask(image: Image.Image, threshold: int) -> list[bool]:
    gray = image.convert("L")
    return [value <= threshold for value in gray.tobytes()]


def _estimate_thickness_histogram(
    dark: list[bool], width: int, height: int, max_thickness_px: int
) -> Counter[int]:
    histogram: Counter[int] = Counter()
    for y in range(height):
        for run in _runs_in_row(dark, width, y):
            if 2 <= run[1] - run[0] + 1 <= max_thickness_px:
                histogram[run[1] - run[0] + 1] += 1
    for x in range(width):
        for run in _runs_in_column(dark, width, height, x):
            if 2 <= run[1] - run[0] + 1 <= max_thickness_px:
                histogram[run[1] - run[0] + 1] += 1
    return histogram


def _histogram_mode(histogram: Counter[int]) -> int:
    # 频次相同时偏向较粗的值，避免抗锯齿造成 4/5 并列时低估墙宽。
    return max(histogram.items(), key=lambda item: (item[1], item[0]))[0]


def _runs_in_row(dark: list[bool], width: int, y: int) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start: int | None = None
    base = y * width
    for x in range(width):
        if dark[base + x]:
            if start is None:
                start = x
        elif start is not None:
            runs.append((start, x - 1))
            start = None
    if start is not None:
        runs.append((start, width - 1))
    return runs


def _runs_in_column(dark: list[bool], width: int, height: int, x: int) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for y in range(height):
        if dark[y * width + x]:
            if start is None:
                start = y
        elif start is not None:
            runs.append((start, y - 1))
            start = None
    if start is not None:
        runs.append((start, height - 1))
    return runs


def _axis_run_pixels(
    dark: list[bool], width: int, height: int, *, axis: Axis, min_run: int
) -> set[int]:
    pixels: set[int] = set()
    if axis == "h":
        for y in range(height):
            for x0, x1 in _runs_in_row(dark, width, y):
                if x1 - x0 + 1 < min_run:
                    continue
                base = y * width
                pixels.update(base + x for x in range(x0, x1 + 1))
    else:
        for x in range(width):
            for y0, y1 in _runs_in_column(dark, width, height, x):
                if y1 - y0 + 1 < min_run:
                    continue
                pixels.update(y * width + x for y in range(y0, y1 + 1))
    return pixels


def _rects_from_components(
    pixels: set[int],
    width: int,
    height: int,
    *,
    axis: Axis,
    offset: tuple[int, int],
    min_thickness: int,
    max_thickness: int,
    min_run: int,
) -> list[WallBodyRect]:
    rects: list[WallBodyRect] = []
    for component in _connected_components(pixels, width, height):
        xs = [index % width for index in component]
        ys = [index // width for index in component]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        rect_width = max_x - min_x + 1
        rect_height = max_y - min_y + 1
        thickness = rect_height if axis == "h" else rect_width
        run = rect_width if axis == "h" else rect_height
        if thickness < min_thickness or thickness > max_thickness:
            continue
        if run < min_run or run <= thickness:
            continue
        rects.append(
            WallBodyRect(
                id="",
                axis=axis,
                x=min_x + offset[0],
                y=min_y + offset[1],
                width=rect_width,
                height=rect_height,
                detected_thickness=thickness,
            )
        )
    return rects


def _connected_components(pixels: set[int], width: int, height: int) -> list[list[int]]:
    remaining = set(pixels)
    components: list[list[int]] = []
    while remaining:
        first = remaining.pop()
        stack = [first]
        component = [first]
        while stack:
            current = stack.pop()
            x = current % width
            y = current // width
            neighbors: list[int] = []
            if x > 0:
                neighbors.append(current - 1)
            if x < width - 1:
                neighbors.append(current + 1)
            if y > 0:
                neighbors.append(current - width)
            if y < height - 1:
                neighbors.append(current + width)
            for neighbor in neighbors:
                if neighbor not in remaining:
                    continue
                remaining.remove(neighbor)
                stack.append(neighbor)
                component.append(neighbor)
        components.append(component)
    return components


def _merge_rects(rects: list[WallBodyRect], *, gap_tolerance: int) -> list[WallBodyRect]:
    merged: list[WallBodyRect] = []
    for axis in ("h", "v"):
        axis_rects = sorted(
            [rect for rect in rects if rect.axis == axis],
            key=lambda rect: (
                _rect_center_y(rect) if axis == "h" else _rect_center_x(rect),
                rect.x,
                rect.y,
            ),
        )
        for rect in axis_rects:
            if not merged or not _can_merge(merged[-1], rect, gap_tolerance=gap_tolerance):
                merged.append(rect)
                continue
            merged[-1] = _merge_pair(merged[-1], rect)
    return sorted(merged, key=lambda rect: (rect.axis, rect.y, rect.x))


def _can_merge(left: WallBodyRect, right: WallBodyRect, *, gap_tolerance: int) -> bool:
    if left.axis != right.axis:
        return False
    if left.axis == "h":
        same_band = abs(_rect_center_y(left) - _rect_center_y(right)) <= 1.0
        gap = right.x - (left.x + left.width)
        overlap = min(left.y + left.height, right.y + right.height) - max(left.y, right.y)
    else:
        same_band = abs(_rect_center_x(left) - _rect_center_x(right)) <= 1.0
        gap = right.y - (left.y + left.height)
        overlap = min(left.x + left.width, right.x + right.width) - max(left.x, right.x)
    return same_band and 0 <= gap <= gap_tolerance and overlap > 0


def _merge_pair(left: WallBodyRect, right: WallBodyRect) -> WallBodyRect:
    x0 = min(left.x, right.x)
    y0 = min(left.y, right.y)
    x1 = max(left.x + left.width, right.x + right.width)
    y1 = max(left.y + left.height, right.y + right.height)
    thickness = round((left.detected_thickness + right.detected_thickness) / 2)
    return WallBodyRect(
        id="",
        axis=left.axis,
        x=x0,
        y=y0,
        width=x1 - x0,
        height=y1 - y0,
        detected_thickness=thickness,
    )


def _assign_rect_ids(rects: list[WallBodyRect]) -> list[WallBodyRect]:
    return [
        WallBodyRect(
            id=f"wall_body_{index:03d}",
            axis=rect.axis,
            x=rect.x,
            y=rect.y,
            width=rect.width,
            height=rect.height,
            detected_thickness=rect.detected_thickness,
        )
        for index, rect in enumerate(rects, start=1)
    ]


def _lines_from_rects(rects: list[WallBodyRect]) -> list[WallLine]:
    lines: list[WallLine] = []
    for index, rect in enumerate(rects, start=1):
        if rect.axis == "h":
            y = rect.y + (rect.height - 1) / 2
            line = WallLine(
                id=f"wall_{index:03d}",
                axis="h",
                x1=float(rect.x),
                y1=float(y),
                x2=float(rect.x + rect.width - 1),
                y2=float(y),
                detected_thickness=float(rect.detected_thickness),
            )
        else:
            x = rect.x + (rect.width - 1) / 2
            line = WallLine(
                id=f"wall_{index:03d}",
                axis="v",
                x1=float(x),
                y1=float(rect.y),
                x2=float(x),
                y2=float(rect.y + rect.height - 1),
                detected_thickness=float(rect.detected_thickness),
            )
        lines.append(line)
    return lines


def _snap_line_corners(lines: list[WallLine], *, tolerance: float) -> list[WallLine]:
    adjusted = list(lines)
    verticals = [line for line in adjusted if line.axis == "v"]
    horizontals = [line for line in adjusted if line.axis == "h"]
    snapped: list[WallLine] = []
    for line in adjusted:
        if line.axis == "h":
            x1, x2 = sorted((line.x1, line.x2))
            for vertical in verticals:
                vx = vertical.x1
                vy0, vy1 = sorted((vertical.y1, vertical.y2))
                if not (vy0 - tolerance <= line.y1 <= vy1 + tolerance):
                    continue
                if abs(x1 - vx) <= tolerance:
                    x1 = vx
                if abs(x2 - vx) <= tolerance:
                    x2 = vx
            snapped.append(
                WallLine(line.id, "h", x1, line.y1, x2, line.y2, line.detected_thickness)
            )
        else:
            y1, y2 = sorted((line.y1, line.y2))
            for horizontal in horizontals:
                hx0, hx1 = sorted((horizontal.x1, horizontal.x2))
                hy = horizontal.y1
                if not (hx0 - tolerance <= line.x1 <= hx1 + tolerance):
                    continue
                if abs(y1 - hy) <= tolerance:
                    y1 = hy
                if abs(y2 - hy) <= tolerance:
                    y2 = hy
            snapped.append(
                WallLine(line.id, "v", line.x1, y1, line.x2, y2, line.detected_thickness)
            )
    return snapped


def _write_body_svg(
    path: Path, image_size: tuple[int, int], rects: list[WallBodyRect], *, source: str | Path
) -> None:
    width, height = image_size
    lines = [
        _svg_header(width, height),
        f"  <title>{escape(Path(source).name)} wall bodies</title>",
        f'  <g fill="{_ORANGE}" fill-opacity="0.96">',
    ]
    for rect in rects:
        lines.append(
            "    "
            f'<rect id="{rect.id}" x="{rect.x}" y="{rect.y}" '
            f'width="{rect.width}" height="{rect.height}" data-axis="{rect.axis}" '
            f'data-detected-thickness="{rect.detected_thickness}"/>'
        )
    lines.extend(["  </g>", "</svg>", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_line_svg(
    path: Path,
    image_size: tuple[int, int],
    lines_: list[WallLine],
    *,
    stroke_width: int,
    source: str | Path,
) -> None:
    width, height = image_size
    lines = [
        _svg_header(width, height),
        f"  <title>{escape(Path(source).name)} wall centerlines</title>",
        (
            f'  <g fill="none" stroke="{_ORANGE}" stroke-width="{stroke_width}" '
            'stroke-linecap="square" stroke-linejoin="miter">'
        ),
    ]
    for line in lines_:
        lines.append(
            "    "
            f'<line id="{line.id}" x1="{line.x1:.2f}" y1="{line.y1:.2f}" '
            f'x2="{line.x2:.2f}" y2="{line.y2:.2f}" data-axis="{line.axis}" '
            f'data-detected-thickness="{line.detected_thickness:.1f}"/>'
        )
    lines.extend(["  </g>", "</svg>", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def _svg_header(width: int, height: int) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
    )


def _write_body_overlay(path: Path, image: Image.Image, rects: list[WallBodyRect]) -> None:
    overlay = _faded_base(image)
    draw = ImageDraw.Draw(overlay, "RGBA")
    for rect in rects:
        draw.rectangle(
            (rect.x, rect.y, rect.x + rect.width - 1, rect.y + rect.height - 1),
            fill=(255, 165, 0, 245),
        )
    overlay.convert("RGB").save(path)


def _write_line_overlay(
    path: Path, image: Image.Image, lines: list[WallLine], *, stroke_width: int
) -> None:
    overlay = _faded_base(image)
    draw = ImageDraw.Draw(overlay, "RGBA")
    for line in lines:
        draw.line(
            (line.x1, line.y1, line.x2, line.y2),
            fill=(255, 165, 0, 255),
            width=stroke_width,
        )
    overlay.convert("RGB").save(path)


def _faded_base(image: Image.Image) -> Image.Image:
    base = image.convert("RGBA")
    white = Image.new("RGBA", image.size, (255, 255, 255, 255))
    return Image.blend(white, base, 0.28)


def _read_wall_lines_from_svg(path: Path) -> list[WallLine]:
    try:
        root = ElementTree.parse(path).getroot()
    except ElementTree.ParseError as exc:
        raise WallExtractionError(f"SVG 解析失败：{path}：{exc}") from exc
    lines: list[WallLine] = []
    for index, element in enumerate(root.iter(), start=1):
        if _local_name(element.tag) != "line":
            continue
        try:
            x1 = float(element.attrib["x1"])
            y1 = float(element.attrib["y1"])
            x2 = float(element.attrib["x2"])
            y2 = float(element.attrib["y2"])
        except (KeyError, ValueError) as exc:
            raise WallExtractionError(f"SVG line 缺少合法坐标：{element.attrib}") from exc
        axis_raw = element.attrib.get("data-axis")
        axis: Axis = "h" if abs(x2 - x1) >= abs(y2 - y1) else "v"
        if axis_raw == "h":
            axis = "h"
        elif axis_raw == "v":
            axis = "v"
        thickness_raw = element.attrib.get("data-detected-thickness")
        if thickness_raw is None:
            thickness_raw = element.attrib.get("stroke-width", "0")
        try:
            thickness = float(thickness_raw)
        except ValueError:
            thickness = 0.0
        line_id = element.attrib.get("id") or f"wall_{index:03d}"
        lines.append(
            WallLine(
                id=line_id,
                axis=axis,
                x1=x1,
                y1=y1,
                x2=x2,
                y2=y2,
                detected_thickness=thickness,
            )
        )
    return lines


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _choose_units_per_grid(
    lines: list[WallLine],
    origin: tuple[float, float],
    units_per_grid: float | str,
) -> float:
    if isinstance(units_per_grid, str):
        if units_per_grid.strip().lower() != "auto":
            raise WallExtractionError("units_per_grid 只能是正数或 auto")
        for candidate in _AUTO_UNITS_PER_GRID_CANDIDATES:
            conversion = _grid_walls_from_lines(lines, origin, candidate)
            if (
                conversion["zero_length_count"] == 0
                and conversion["duplicate_wall_count"] == 0
                and len(conversion["walls"]) == len(lines)
                and conversion["max_snap_error_units"] <= max(2.0, min(5.0, candidate * 0.35))
            ):
                return candidate
        return _AUTO_UNITS_PER_GRID_CANDIDATES[-1]
    value = float(units_per_grid)
    if value <= 0:
        raise WallExtractionError("units_per_grid 必须大于 0")
    return value


def _grid_walls_from_lines(
    lines: list[WallLine],
    origin: tuple[float, float],
    units_per_grid: float,
) -> dict[str, Any]:
    walls: list[dict[str, Any]] = []
    seen: set[tuple[int, int, int, int]] = set()
    duplicate_count = 0
    zero_length_count = 0
    snap_errors: list[float] = []
    line_reports: list[dict[str, Any]] = []
    for line in lines:
        if line.axis == "h":
            y = _grid_value(line.y1, origin[1], units_per_grid)
            x0 = _grid_value(min(line.x1, line.x2), origin[0], units_per_grid)
            x1 = _grid_value(max(line.x1, line.x2), origin[0], units_per_grid)
            if x1 == x0:
                x1 += 1
                zero_length_count += 1
            start, end = [x0, y], [x1, y]
        else:
            x = _grid_value(line.x1, origin[0], units_per_grid)
            y0 = _grid_value(min(line.y1, line.y2), origin[1], units_per_grid)
            y1 = _grid_value(max(line.y1, line.y2), origin[1], units_per_grid)
            if y1 == y0:
                y1 += 1
                zero_length_count += 1
            start, end = [x, y0], [x, y1]
        key = (start[0], start[1], end[0], end[1])
        if key in seen:
            duplicate_count += 1
        seen.add(key)
        snap_error = _line_snap_error(line, origin, units_per_grid, start, end)
        snap_errors.append(snap_error)
        walls.append({"name": line.id, "from": start, "to": end})
        line_reports.append(
            {
                "id": line.id,
                "axis": line.axis,
                "from_svg": [_clean_number(line.x1), _clean_number(line.y1)],
                "to_svg": [_clean_number(line.x2), _clean_number(line.y2)],
                "from_grid": start,
                "to_grid": end,
                "snap_error_units": round(snap_error, 4),
            }
        )
    return {
        "walls": walls,
        "duplicate_wall_count": duplicate_count,
        "zero_length_count": zero_length_count,
        "max_snap_error_units": max(snap_errors, default=0.0),
        "mean_snap_error_units": (sum(snap_errors) / len(snap_errors) if snap_errors else 0.0),
        "line_reports": line_reports,
    }


def _line_snap_error(
    line: WallLine,
    origin: tuple[float, float],
    units_per_grid: float,
    start: list[int],
    end: list[int],
) -> float:
    snapped_start = (
        origin[0] + start[0] * units_per_grid,
        origin[1] + start[1] * units_per_grid,
    )
    snapped_end = (
        origin[0] + end[0] * units_per_grid,
        origin[1] + end[1] * units_per_grid,
    )
    return max(
        ((line.x1 - snapped_start[0]) ** 2 + (line.y1 - snapped_start[1]) ** 2) ** 0.5,
        ((line.x2 - snapped_end[0]) ** 2 + (line.y2 - snapped_end[1]) ** 2) ** 0.5,
    )


def _write_snap_report(
    result: SvgGridConversionResult,
    lines: list[WallLine],
    line_reports: list[dict[str, Any]],
) -> None:
    payload = {
        "ok": result.ok,
        "source": str(result.line_svg),
        "units_per_grid": _clean_number(result.units_per_grid),
        "origin": [_clean_number(result.origin[0]), _clean_number(result.origin[1])],
        "wall_count_before": result.source_line_count,
        "wall_count_after": result.wall_count_after,
        "duplicate_wall_count": result.duplicate_wall_count,
        "zero_length_count": result.zero_length_count,
        "max_snap_error_units": round(result.max_snap_error_units, 4),
        "mean_snap_error_units": round(result.mean_snap_error_units, 4),
        "input_lines": [line.as_dict() for line in lines],
        "mapped_lines": line_reports,
        "layout_json": str(result.layout_json),
    }
    result.snap_report_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _grid_value(value: float, origin: float, units_per_grid: float) -> int:
    return round((value - origin) / units_per_grid)


def _clean_number(value: float) -> int | float:
    rounded = round(value, 6)
    nearest = round(rounded)
    return nearest if abs(rounded - nearest) < 1e-6 else rounded


def _write_summary(result: WallExtractionResult) -> None:
    payload = {
        "ok": result.ok,
        "image_path": str(result.image_path),
        "image_size": [result.image_width, result.image_height],
        "threshold": result.threshold,
        "crop": result.crop,
        "wall_thickness_mode_px": result.wall_thickness_mode_px,
        "thickness_histogram": result.thickness_histogram,
        "min_kept_thickness_px": result.min_kept_thickness_px,
        "max_kept_thickness_px": result.max_kept_thickness_px,
        "uniform_stroke_width_px": result.uniform_stroke_width_px,
        "line_count": len(result.lines),
        "body_rect_count": len(result.body_rects),
        "body_svg": str(result.body_svg),
        "body_overlay": str(result.body_overlay),
        "line_svg": str(result.line_svg),
        "line_overlay": str(result.line_overlay),
        "layout_json": str(result.layout_json),
        "snap_report_json": str(result.snap_report_json),
        "lines": [line.as_dict() for line in result.lines],
        "body_rects": [rect.as_dict() for rect in result.body_rects],
    }
    result.summary_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _rect_center_x(rect: WallBodyRect) -> float:
    return rect.x + (rect.width - 1) / 2


def _rect_center_y(rect: WallBodyRect) -> float:
    return rect.y + (rect.height - 1) / 2


__all__ = [
    "SvgGridConversionResult",
    "WallBodyRect",
    "WallExtractionError",
    "WallExtractionResult",
    "WallLine",
    "convert_wall_svg_to_grid_dsl",
    "extract_floorplan_walls",
]
