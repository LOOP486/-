"""白盒 compiler 级本地预览渲染。

本模块不依赖 UE：读取 layout DSL，经 compile_layout 得到 placements，再用 Pillow
把 AABB 渲染成多角度 contact sheet，供 agent 的 vision_review 使用。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from ue5agent.whitebox.asset_preview_cache import AssetPreview, AssetPreviewCache
from ue5agent.whitebox.compiler import LayoutError, Placement, compile_layout, layout_from_dict
from ue5agent.whitebox.manifest import Manifest
from ue5agent.whitebox.wall_topology import WallTopologyReport, analyze_wall_topology

_CANVAS = (1024, 768)
_HEADER_H = 26
_MARGIN = 34
_BG = (246, 246, 242)
_GRID = (225, 226, 220)
_FLOOR = (210, 210, 204)
_WALL = (74, 77, 82)
_WALL_EDGE = (238, 158, 22)
_STRUCTURE = (140, 146, 154)
_STAIR = (118, 145, 174)
_TEXT = (48, 52, 58)
_ISO_X_SCALE = math.cos(math.radians(30))
_ISO_Y_SCALE = 0.6
_ISO_Z_SCALE = 0.35


@dataclass(frozen=True)
class RenderedView:
    name: str
    path: Path


@dataclass(frozen=True)
class PreviewRenderResult:
    ok: bool
    layout_name: str
    output_dir: Path
    views: list[RenderedView]
    contact_sheet: Path
    placement_count: int
    bounds: tuple[float, float, float, float, float, float]
    wall_topology: WallTopologyReport | None = None
    preview_cache_assets: int = 0
    silhouette_proxy_count: int = 0
    mesh_proxy_count: int = 0

    def facts(self) -> dict[str, Any]:
        has_proxy = self.silhouette_proxy_count > 0 or self.mesh_proxy_count > 0
        geometry_fidelity = "aabb"
        if self.mesh_proxy_count > 0:
            geometry_fidelity = "mesh_proxy"
        elif self.silhouette_proxy_count > 0:
            geometry_fidelity = "silhouette"
        facts: dict[str, Any] = {
            "kind": "render_preview",
            "ok": self.ok,
            "source": "compiler",
            "geometry_fidelity": geometry_fidelity,
            "mesh_fidelity": "proxy" if has_proxy else "none",
            "asset_shape_exact": False,
            "layout_name": self.layout_name,
            "path": str(self.contact_sheet),
            "paths": [str(view.path) for view in self.views],
            "view_count": len(self.views),
            "placement_count": self.placement_count,
            "bounds": [round(value, 3) for value in self.bounds],
            "preview_cache_assets": self.preview_cache_assets,
            "silhouette_proxy_count": self.silhouette_proxy_count,
            "mesh_proxy_count": self.mesh_proxy_count,
        }
        if self.wall_topology is not None:
            facts["wall_topology"] = self.wall_topology.facts()
        return facts


@dataclass(frozen=True)
class _Box:
    name: str
    kind: str
    min_x: float
    min_y: float
    min_z: float
    max_x: float
    max_y: float
    max_z: float
    asset_key: str = ""
    rotation_yaw: float = 0.0
    top_silhouette: tuple[tuple[float, float], ...] = ()
    mesh_vertices: tuple[tuple[float, float, float], ...] = ()
    mesh_faces: tuple[tuple[int, ...], ...] = ()

    @property
    def center_sort(self) -> float:
        return self.max_x + self.max_y + self.max_z


@dataclass(frozen=True)
class _IsoFace:
    box: _Box
    name: str
    points: list[tuple[float, float, float]]
    normal: tuple[float, float, float]
    depth: float


def render_layout_preview(
    layout: dict[str, Any],
    output_dir: str | Path,
    *,
    manifest: Manifest | None = None,
    preview_cache: AssetPreviewCache | None = None,
    canvas: tuple[int, int] = _CANVAS,
) -> PreviewRenderResult:
    """把 layout 编译成 placements 并渲染 top/iso_ne/iso_sw/contact sheet。"""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    manifest = manifest or Manifest(grid=100.0, assets={})
    spec = layout_from_dict(layout)
    wall_topology = analyze_wall_topology(spec.walls) if spec.walls else None
    placements = compile_layout(spec, manifest)
    preview_cache = preview_cache or AssetPreviewCache(items={})
    boxes = [_box_from_placement(placement, preview_cache) for placement in placements]
    if not boxes:
        raise LayoutError("layout 编译后没有任何 placement，无法生成预览")

    bounds = _bounds(boxes)
    view_specs = (
        ("top", _render_top_view),
        ("iso_ne", lambda bs, b, size: _render_iso_view(bs, b, size, flip=False)),
        ("iso_sw", lambda bs, b, size: _render_iso_view(bs, b, size, flip=True)),
    )
    views: list[RenderedView] = []
    images: list[Image.Image] = []
    for name, renderer in view_specs:
        image = renderer(boxes, bounds, canvas)
        _draw_header(image, name)
        path = output / f"{name}.png"
        image.save(path)
        views.append(RenderedView(name=name, path=path))
        images.append(image)

    contact = _contact_sheet(images, [view.name for view in views])
    contact_path = output / "contact_sheet.png"
    contact.save(contact_path)
    return PreviewRenderResult(
        ok=wall_topology.ok if wall_topology is not None else True,
        layout_name=spec.name,
        output_dir=output,
        views=views,
        contact_sheet=contact_path,
        placement_count=len(placements),
        bounds=bounds,
        wall_topology=wall_topology,
        preview_cache_assets=len(preview_cache.items),
        silhouette_proxy_count=sum(1 for box in boxes if box.top_silhouette),
        mesh_proxy_count=sum(1 for box in boxes if box.mesh_vertices and box.mesh_faces),
    )


def _box_from_placement(
    placement: Placement, preview_cache: AssetPreviewCache | None = None
) -> _Box:
    if placement.target_min is not None and placement.target_size is not None:
        min_x, min_y, min_z = placement.target_min
        size_x, size_y, size_z = placement.target_size
    else:
        size_x = abs(placement.scale[0]) * 100.0
        size_y = abs(placement.scale[1]) * 100.0
        size_z = abs(placement.scale[2]) * 100.0
        min_x = placement.location[0] - size_x / 2
        min_y = placement.location[1] - size_y / 2
        min_z = placement.location[2] - size_z / 2
    preview = _preview_for_placement(placement, preview_cache)
    return _Box(
        name=placement.name,
        kind=placement.kind or "structure",
        min_x=float(min_x),
        min_y=float(min_y),
        min_z=float(min_z),
        max_x=float(min_x + size_x),
        max_y=float(min_y + size_y),
        max_z=float(min_z + size_z),
        asset_key=placement.asset_key,
        rotation_yaw=float(placement.rotation[1]) if len(placement.rotation) >= 2 else 0.0,
        top_silhouette=preview.top_silhouette if preview is not None else (),
        mesh_vertices=preview.mesh_vertices if preview is not None else (),
        mesh_faces=preview.mesh_faces if preview is not None else (),
    )


def _preview_for_placement(
    placement: Placement, preview_cache: AssetPreviewCache | None
) -> AssetPreview | None:
    if preview_cache is None or not placement.asset_key:
        return None
    return preview_cache.items.get(placement.asset_key)


def _bounds(boxes: list[_Box]) -> tuple[float, float, float, float, float, float]:
    return (
        min(box.min_x for box in boxes),
        min(box.min_y for box in boxes),
        min(box.min_z for box in boxes),
        max(box.max_x for box in boxes),
        max(box.max_y for box in boxes),
        max(box.max_z for box in boxes),
    )


def _render_top_view(
    boxes: list[_Box],
    bounds: tuple[float, float, float, float, float, float],
    canvas: tuple[int, int],
) -> Image.Image:
    image = Image.new("RGB", canvas, _BG)
    draw = ImageDraw.Draw(image)
    min_x, min_y, _min_z, max_x, max_y, _max_z = bounds
    scale = _fit_scale(max_x - min_x, max_y - min_y, canvas)

    def map_xy(x: float, y: float) -> tuple[float, float]:
        px = _MARGIN + (x - min_x) * scale
        py = canvas[1] - _MARGIN - (y - min_y) * scale
        return px, py

    _draw_grid(draw, canvas)
    for box in sorted(boxes, key=_top_order):
        x0, y1 = map_xy(box.min_x, box.min_y)
        x1, y0 = map_xy(box.max_x, box.max_y)
        fill, outline, width = _style_for_box(box)
        polygon = _box_top_polygon(box)
        if polygon:
            screen_polygon = [map_xy(x, y) for x, y in polygon]
            draw.polygon(screen_polygon, fill=fill, outline=outline)
            if width > 1:
                draw.line(screen_polygon + screen_polygon[:1], fill=outline, width=width)
        else:
            draw.rectangle((x0, y0, x1, y1), fill=fill, outline=outline, width=width)
    return image


def _render_iso_view(
    boxes: list[_Box],
    bounds: tuple[float, float, float, float, float, float],
    canvas: tuple[int, int],
    *,
    flip: bool,
) -> Image.Image:
    image = Image.new("RGB", canvas, _BG)
    draw = ImageDraw.Draw(image)
    projected = [_project_corner(point, flip=flip) for box in boxes for point in _corners(box)]
    min_u = min(point[0] for point in projected)
    max_u = max(point[0] for point in projected)
    min_v = min(point[1] for point in projected)
    max_v = max(point[1] for point in projected)
    scale = _fit_scale(max_u - min_u, max_v - min_v, canvas)

    def project(point: tuple[float, float, float]) -> tuple[float, float]:
        u, v = _project_corner(point, flip=flip)
        return (
            _MARGIN + (u - min_u) * scale,
            canvas[1] - _MARGIN - (v - min_v) * scale,
        )

    for face in _iso_visible_faces(boxes, flip=flip):
        fill, outline, width = _style_for_box(face.box)
        color = _iso_face_color(face.name, fill)
        draw.polygon([project(point) for point in face.points], fill=color, outline=outline)
        if face.box.kind == "wall" and face.name == "z_max":
            draw.line(
                [project(point) for point in face.points + face.points[:1]],
                fill=_WALL_EDGE,
                width=width,
            )
    return image


def _iso_visible_faces(boxes: list[_Box], *, flip: bool) -> list[_IsoFace]:
    view = _iso_view_vector(flip=flip)
    faces: list[_IsoFace] = []
    for box in boxes:
        proxy_faces = _iso_proxy_faces(box, view)
        if proxy_faces:
            faces.extend(proxy_faces)
            continue
        for name, points, normal in _iso_box_faces(box):
            if _dot(normal, view) <= 0:
                continue
            faces.append(
                _IsoFace(
                    box=box,
                    name=name,
                    points=points,
                    normal=normal,
                    depth=sum(_dot(point, view) for point in points) / len(points),
                )
            )
    return sorted(faces, key=lambda face: (face.depth, face.box.name, face.name))


def _iso_proxy_faces(box: _Box, view: tuple[float, float, float]) -> list[_IsoFace]:
    if box.mesh_vertices and box.mesh_faces:
        return _iso_mesh_faces(box, view)
    if box.top_silhouette:
        return _iso_silhouette_faces(box, view)
    return []


def _iso_mesh_faces(box: _Box, view: tuple[float, float, float]) -> list[_IsoFace]:
    faces: list[_IsoFace] = []
    vertices = [_mesh_vertex_to_world(box, vertex) for vertex in box.mesh_vertices]
    for index, face in enumerate(box.mesh_faces):
        points = [vertices[i] for i in face]
        faces.append(_make_iso_face(box, f"mesh_{index}", points, view))
    return faces


def _iso_silhouette_faces(box: _Box, view: tuple[float, float, float]) -> list[_IsoFace]:
    bottom = [(x, y, box.min_z) for x, y in _box_top_polygon(box)]
    if len(bottom) < 3:
        return []
    top = [(x, y, box.max_z) for x, y, _z in bottom]
    faces = [_make_iso_face(box, "z_max", top, view)]
    for index, (a, b) in enumerate(zip(bottom, bottom[1:] + bottom[:1], strict=False)):
        top_a = (a[0], a[1], box.max_z)
        top_b = (b[0], b[1], box.max_z)
        faces.append(_make_iso_face(box, f"side_{index}", [a, b, top_b, top_a], view))
    return faces


def _make_iso_face(
    box: _Box,
    name: str,
    points: list[tuple[float, float, float]],
    view: tuple[float, float, float],
) -> _IsoFace:
    normal = _face_normal(points)
    return _IsoFace(
        box=box,
        name=name,
        points=points,
        normal=normal,
        depth=sum(_dot(point, view) for point in points) / len(points),
    )


def _face_normal(points: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    if len(points) < 3:
        return (0.0, 0.0, 1.0)
    ax, ay, az = points[0]
    bx, by, bz = points[1]
    cx, cy, cz = points[2]
    ux, uy, uz = bx - ax, by - ay, bz - az
    vx, vy, vz = cx - ax, cy - ay, cz - az
    return (
        uy * vz - uz * vy,
        uz * vx - ux * vz,
        ux * vy - uy * vx,
    )


def _iso_box_faces(
    box: _Box,
) -> list[
    tuple[
        str,
        list[tuple[float, float, float]],
        tuple[float, float, float],
    ]
]:
    x0, x1 = box.min_x, box.max_x
    y0, y1 = box.min_y, box.max_y
    z0, z1 = box.min_z, box.max_z
    return [
        ("x_min", [(x0, y0, z0), (x0, y1, z0), (x0, y1, z1), (x0, y0, z1)], (-1, 0, 0)),
        ("x_max", [(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)], (1, 0, 0)),
        ("y_min", [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)], (0, -1, 0)),
        ("y_max", [(x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1)], (0, 1, 0)),
        ("z_min", [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0)], (0, 0, -1)),
        ("z_max", [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)], (0, 0, 1)),
    ]


def _iso_view_vector(*, flip: bool) -> tuple[float, float, float]:
    # 与 _project_corner 的屏幕基向量保持一致：cross(screen_right, screen_up)
    # 是物体指向相机的方向，用它同时做可见面判断和 depth 排序。
    xy = _ISO_X_SCALE * _ISO_Z_SCALE
    z = 2 * _ISO_X_SCALE * _ISO_Y_SCALE
    return (xy, xy, z) if flip else (-xy, -xy, z)


def _iso_face_color(face_name: str, base: tuple[int, int, int]) -> tuple[int, int, int]:
    if face_name == "z_max":
        return base
    if face_name in {"x_max", "x_min"}:
        return _shade(base, 0.9)
    return _shade(base, 0.74)


def _dot(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> float:
    return left[0] * right[0] + left[1] * right[1] + left[2] * right[2]


def _project_corner(point: tuple[float, float, float], *, flip: bool) -> tuple[float, float]:
    x, y, z = point
    if flip:
        x, y = -x, -y
    return ((x - y) * _ISO_X_SCALE, (x + y) * _ISO_Y_SCALE + z * _ISO_Z_SCALE)


def _box_top_polygon(box: _Box) -> list[tuple[float, float]]:
    if box.top_silhouette:
        return [_normalized_xy_to_world(box, x, y) for x, y in box.top_silhouette]
    if box.mesh_vertices:
        return [_normalized_xy_to_world(box, x, y) for x, y in _mesh_xy_hull(box.mesh_vertices)]
    return []


def _mesh_vertex_to_world(
    box: _Box, vertex: tuple[float, float, float]
) -> tuple[float, float, float]:
    x, y = _normalized_xy_to_world(box, vertex[0], vertex[1])
    z = box.min_z + vertex[2] * (box.max_z - box.min_z)
    return (x, y, z)


def _normalized_xy_to_world(box: _Box, x: float, y: float) -> tuple[float, float]:
    rx, ry = _rotate_normalized_xy(x, y, box.rotation_yaw)
    return (
        box.min_x + rx * (box.max_x - box.min_x),
        box.min_y + ry * (box.max_y - box.min_y),
    )


def _rotate_normalized_xy(x: float, y: float, yaw: float) -> tuple[float, float]:
    yaw = round(yaw) % 360
    if yaw == 90:
        return (1.0 - y, x)
    if yaw == 180:
        return (1.0 - x, 1.0 - y)
    if yaw == 270:
        return (y, 1.0 - x)
    return (x, y)


def _mesh_xy_hull(
    vertices: tuple[tuple[float, float, float], ...],
) -> tuple[tuple[float, float], ...]:
    points = sorted({(x, y) for x, y, _z in vertices})
    if len(points) < 3:
        return ()

    def cross(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for point in points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper: list[tuple[float, float]] = []
    for point in reversed(points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return tuple(lower[:-1] + upper[:-1])


def _corners(box: _Box) -> list[tuple[float, float, float]]:
    return [
        (x, y, z)
        for x in (box.min_x, box.max_x)
        for y in (box.min_y, box.max_y)
        for z in (box.min_z, box.max_z)
    ]


def _fit_scale(width: float, height: float, canvas: tuple[int, int]) -> float:
    width = max(width, 1.0)
    height = max(height, 1.0)
    return min((canvas[0] - 2 * _MARGIN) / width, (canvas[1] - 2 * _MARGIN - _HEADER_H) / height)


def _top_order(box: _Box) -> int:
    if box.kind in {"floor", "nav_proxy"}:
        return 0
    if box.kind == "wall":
        return 2
    return 1


def _style_for_box(box: _Box) -> tuple[tuple[int, int, int], tuple[int, int, int], int]:
    if box.kind == "floor":
        return _FLOOR, (168, 168, 160), 1
    if box.kind == "wall":
        return _WALL, _WALL_EDGE, 3
    if box.kind in {"stair", "stairwell"}:
        return _STAIR, (72, 93, 118), 2
    return _STRUCTURE, (92, 97, 105), 2


def _shade(color: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    return (
        max(0, min(255, round(color[0] * factor))),
        max(0, min(255, round(color[1] * factor))),
        max(0, min(255, round(color[2] * factor))),
    )


def _draw_grid(draw: ImageDraw.ImageDraw, canvas: tuple[int, int]) -> None:
    for x in range(_MARGIN, canvas[0] - _MARGIN + 1, 40):
        draw.line((x, _HEADER_H, x, canvas[1] - _MARGIN), fill=_GRID)
    for y in range(_HEADER_H, canvas[1] - _MARGIN + 1, 40):
        draw.line((_MARGIN, y, canvas[0] - _MARGIN, y), fill=_GRID)


def _draw_header(image: Image.Image, label: str) -> None:
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, image.width, _HEADER_H), fill=(232, 233, 228))
    draw.text((10, 6), label, fill=_TEXT, font=ImageFont.load_default())


def _contact_sheet(images: list[Image.Image], labels: list[str]) -> Image.Image:
    width = sum(image.width for image in images)
    height = max(image.height for image in images)
    sheet = Image.new("RGB", (width, height), _BG)
    x = 0
    for image, label in zip(images, labels, strict=True):
        sheet.paste(image, (x, 0))
        ImageDraw.Draw(sheet).text((x + 10, 6), label, fill=_TEXT, font=ImageFont.load_default())
        x += image.width
    return sheet


def facts_json(result: PreviewRenderResult) -> str:
    return json.dumps(result.facts(), ensure_ascii=False)


__all__ = [
    "PreviewRenderResult",
    "RenderedView",
    "facts_json",
    "render_layout_preview",
]
