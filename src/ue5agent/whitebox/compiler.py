"""白盒布局 DSL 与编译器（ADR-0004：模型出拓扑，确定性程序出坐标）。

v1 范围：矩形房间 + 四向墙 + 门洞。墙体/地板全部用 cube 缩放实现，
单位为格（grid uu），编译输出世界坐标放置指令，spawn 前完成程序化校验。
坐标约定：rect=(x, y, 宽, 深) 格；墙在房间内侧；门洞 v1 为全高开口。
相邻房间共享边时各自生成一面墙，编译末尾 _dedupe_shared_walls 会去除重合的另一面，
共享边只保留一面（v2）；共用门洞仍需两个房间各开一个对齐的门。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from itertools import pairwise, product
from typing import Any

from ue5agent.whitebox.manifest import AssetDef, Manifest

WALLS = ("north", "south", "east", "west")
_NAV_PROXY_TOP_OFFSET = 2.0
_NAV_PROXY_THICKNESS = 2.0
_NAV_PROXY_ASSET = AssetDef(
    key="_engine_nav_proxy",
    path="/Engine/BasicShapes/Cube.Cube",
    size=(100.0, 100.0, 100.0),
    category="nav_proxy",
    pivot=(0.5, 0.5, 0.5),
    footprint=(1, 1),
    tags=("nav",),
)
_ROUTE_MARKER_ASSET = AssetDef(
    key="_engine_route_marker",
    path="/Engine/BasicShapes/Cube.Cube",
    size=(20.0, 20.0, 20.0),
    category="route",
    pivot=(0.5, 0.5, 0.0),
    footprint=(1, 1),
    tags=("route",),
)
_FACING_YAW = {"north": 0.0, "east": 90.0, "south": 180.0, "west": 270.0}
_SPAWN_Z_OFFSET = 88.0
_ROUTE_Z_OFFSET = 8.0
_ROUTE_CORRIDOR_HALF_WIDTH = 45.0

_FLOOR_THICKNESS = 20.0
"""地板板厚（uu）：顶面贴 z=0，向下 20uu。cube 基准 100uu 时即旧逻辑的 scale.z=0.2。"""
_STRUCTURE_MODES = {"slab", "modular"}
_ENGINE_SLAB_ASSET = AssetDef(
    key="_engine_slab",
    path="/Engine/BasicShapes/Cube.Cube",
    size=(100.0, 100.0, 100.0),
    category="slab",
    pivot=(0.5, 0.5, 0.5),
    footprint=(1, 1),
    tags=("structure", "slab"),
)


class LayoutError(Exception):
    """布局规格不合法（编译前拦截，绝不让坏布局落进编辑器）。"""


@dataclass
class Door:
    wall: str
    at: int
    """沿墙方向的起始格（从墙的低坐标端数起）"""
    width: int = 1


@dataclass
class Room:
    name: str
    rect: tuple[int, int, int, int]
    """(x, y, 宽, 深)，单位格"""
    level: int = 0
    doors: list[Door] = field(default_factory=list)
    windows: list[Door] = field(default_factory=list)
    props: list[PropSpec] = field(default_factory=list)


@dataclass
class PropSpec:
    at: tuple[int, int]
    key: str | None = None
    category: str | None = None
    rotation: float = 0.0
    optional: bool = False


@dataclass
class StairSpec:
    room: str
    at: tuple[int, int]
    from_level: int
    to_level: int
    facing: str
    key: str | None = None


@dataclass
class GameplaySpec:
    spawn_points: list[dict[str, Any]] | None = None
    routes: list[dict[str, Any]] | None = None
    auto_cover: bool | None = None


@dataclass
class LayoutSpec:
    name: str
    rooms: list[Room]
    structure_mode: str = "slab"
    wall_height: float = 300.0
    level_height: float = 0.0
    wall_thickness: float = 20.0
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
    stairs: list[StairSpec] = field(default_factory=list)
    gameplay: GameplaySpec | None = None

    def __post_init__(self) -> None:
        self.structure_mode = self.structure_mode.strip().lower()
        if self.structure_mode not in _STRUCTURE_MODES:
            modes = "、".join(sorted(_STRUCTURE_MODES))
            raise LayoutError(f"structure_mode 只支持 {modes}，收到：{self.structure_mode}")
        if self.level_height <= 0:
            self.level_height = self.wall_height


@dataclass
class Placement:
    name: str
    asset_path: str
    location: tuple[float, float, float]
    scale: tuple[float, float, float]
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    target_min: tuple[float, float, float] | None = None
    target_size: tuple[float, float, float] | None = None
    asset_key: str = ""
    visual_min: tuple[float, float, float] | None = None
    visual_size: tuple[float, float, float] | None = None
    asset_calibrated: bool = False
    snap_box_default: bool = True
    actor_type: str = "StaticMeshActor"
    kind: str = "structure"
    metadata: dict[str, Any] = field(default_factory=dict)


def layout_from_dict(data: dict) -> LayoutSpec:
    """模型产出的 JSON → LayoutSpec（结构错误转为可读的 LayoutError）。"""
    try:
        structure_mode = str(data.get("structure_mode", "slab")).strip().lower()
        if structure_mode not in _STRUCTURE_MODES:
            modes = "、".join(sorted(_STRUCTURE_MODES))
            raise LayoutError(f"structure_mode 只支持 {modes}，收到：{structure_mode}")
        rooms = []
        for raw in data["rooms"]:
            rect = tuple(int(v) for v in raw["rect"])
            if len(rect) != 4:
                raise LayoutError(f"房间 {raw.get('name')} 的 rect 必须是 [x, y, 宽, 深]")
            rooms.append(
                Room(
                    name=str(raw["name"]),
                    rect=rect,
                    level=int(raw.get("level", 0)),
                    doors=[
                        Door(wall=str(d["wall"]), at=int(d["at"]), width=int(d.get("width", 1)))
                        for d in raw.get("doors", [])
                    ],
                    windows=[
                        Door(wall=str(w["wall"]), at=int(w["at"]), width=int(w.get("width", 1)))
                        for w in raw.get("windows", [])
                    ],
                    props=[
                        PropSpec(
                            key=str(p["key"]) if p.get("key") is not None else None,
                            category=(
                                str(p["category"]) if p.get("category") is not None else None
                            ),
                            at=_xy_int(p["at"]),
                            rotation=float(p.get("rotation", 0)),
                            optional=bool(p.get("optional", False)),
                        )
                        for p in raw.get("props", [])
                    ],
                )
            )
        raw_origin = data.get("origin", (0, 0, 0))
        origin = (float(raw_origin[0]), float(raw_origin[1]), float(raw_origin[2]))
        gameplay = None
        if "gameplay" in data:
            raw_gameplay = data.get("gameplay") or {}
            if not isinstance(raw_gameplay, dict):
                raise LayoutError("gameplay 必须是对象")
            gameplay = GameplaySpec(
                spawn_points=(
                    list(raw_gameplay["spawn_points"])
                    if raw_gameplay.get("spawn_points") is not None
                    else None
                ),
                routes=list(raw_gameplay["routes"])
                if raw_gameplay.get("routes") is not None
                else None,
                auto_cover=(
                    bool(raw_gameplay["auto_cover"])
                    if raw_gameplay.get("auto_cover") is not None
                    else None
                ),
            )
        return LayoutSpec(
            name=str(data.get("name", "layout")),
            rooms=rooms,
            structure_mode=structure_mode,
            wall_height=float(data.get("wall_height", 400)),
            level_height=float(data.get("level_height", data.get("wall_height", 400))),
            wall_thickness=float(data.get("wall_thickness", 20)),
            origin=origin,
            stairs=[
                StairSpec(
                    room=str(s["room"]),
                    at=_xy_int(s["at"]),
                    from_level=int(s["from_level"]),
                    to_level=int(s["to_level"]),
                    facing=str(s["facing"]),
                    key=str(s["key"]) if s.get("key") is not None else None,
                )
                for s in data.get("stairs", [])
            ],
            gameplay=gameplay,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LayoutError(f"布局 JSON 结构不合法：{exc}") from exc


def _xy_int(raw: object) -> tuple[int, int]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        raise LayoutError("坐标必须是 [x, y]")
    return (int(raw[0]), int(raw[1]))


def compile_layout(spec: LayoutSpec, manifest: Manifest) -> list[Placement]:
    _validate(spec, manifest)
    if spec.structure_mode == "slab":
        return _compile_layout_slab(spec, manifest)
    return _compile_layout_modular_or_legacy(spec, manifest)


def _compile_layout_slab(spec: LayoutSpec, manifest: Manifest) -> list[Placement]:
    g = manifest.grid
    placements: list[Placement] = []
    for room in spec.rooms:
        placements += _compile_room(room, spec, _ENGINE_SLAB_ASSET, _ENGINE_SLAB_ASSET, g)
    placements = _dedupe_shared_walls(placements)
    placements += _compile_native_layers(spec, manifest)
    return placements


def _compile_layout_modular_or_legacy(spec: LayoutSpec, manifest: Manifest) -> list[Placement]:
    stairwells = _stairwell_cells(spec, manifest)
    if _uses_modular_kit(manifest):
        modular_placements = _compile_layout_modular(spec, manifest, stairwells)
        modular_placements += _compile_native_layers(spec, manifest)
        return modular_placements
    # 按结构角色取件（v1 清单无 roles → 回退 cube，行为不变）。
    floor_asset = manifest.asset_for_role("floor")
    wall_asset = manifest.asset_for_role("wall")
    g = manifest.grid
    legacy_placements: list[Placement] = []
    for room in spec.rooms:
        legacy_placements += _compile_room(room, spec, floor_asset, wall_asset, g)
    legacy_placements = _dedupe_shared_walls(legacy_placements)
    legacy_placements += _compile_native_layers(spec, manifest)
    return legacy_placements


def _fit_placement(
    name: str,
    asset: AssetDef,
    tmin: tuple[float, float, float],
    tsize: tuple[float, float, float],
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0),
    *,
    kind: str = "structure",
    actor_type: str = "StaticMeshActor",
    metadata: dict[str, Any] | None = None,
) -> Placement:
    """把资产缩放填满目标世界 AABB（tmin..tmin+tsize），按 pivot 补偿出 UE 落地点。

    UE 把资产原点放在 location；原点在 AABB 内的归一化位置即 pivot，
    故 location = tmin + pivot * tsize，scale = tsize / 资产基准尺寸。
    cube（pivot=[.5,.5,.5]、base=100）代入即还原升级前的"中心放置"逻辑（逐字节一致）。
    """
    base = asset.size
    snap_min, snap_max = asset.snap_box
    snap_size = tuple((snap_max[i] - snap_min[i]) * base[i] for i in range(3))
    if any(size <= 0 for size in snap_size):
        raise LayoutError(f"资产 {asset.key} 的 snap_box 非法：对齐盒尺寸必须大于 0")
    yaw = round(rotation[1]) % 360
    if yaw in (90, 270):
        scale = (
            _clean_float(tsize[1] / snap_size[0]),
            _clean_float(tsize[0] / snap_size[1]),
            _clean_float(tsize[2] / snap_size[2]),
        )
    else:
        scale = (
            _clean_float(tsize[0] / snap_size[0]),
            _clean_float(tsize[1] / snap_size[1]),
            _clean_float(tsize[2] / snap_size[2]),
        )

    local_min = tuple((snap_min[i] - asset.pivot[i]) * base[i] * scale[i] for i in range(3))
    local_max = tuple((snap_max[i] - asset.pivot[i]) * base[i] * scale[i] for i in range(3))
    corners = [
        _rotate_yaw((x, y, z), yaw)
        for x, y, z in product(
            (local_min[0], local_max[0]),
            (local_min[1], local_max[1]),
            (local_min[2], local_max[2]),
        )
    ]
    rel_min = tuple(min(c[i] for c in corners) for i in range(3))
    location = (tmin[0] - rel_min[0], tmin[1] - rel_min[1], tmin[2] - rel_min[2])
    visual_min, visual_size = _world_aabb_from_local_bounds(
        asset.visual_bounds,
        scale,
        yaw,
        location,
    )
    return Placement(
        name=name,
        asset_path=asset.path,
        location=location,
        scale=scale,
        rotation=rotation,
        target_min=tmin,
        target_size=tsize,
        asset_key=asset.key,
        visual_min=visual_min,
        visual_size=visual_size,
        asset_calibrated=asset.calibrated,
        snap_box_default=_is_default_snap_box(asset.snap_box),
        actor_type=actor_type,
        kind=kind,
        metadata=metadata or {},
    )


def _clean_float(value: float) -> float:
    rounded = round(value, 8)
    nearest_int = round(rounded)
    if abs(rounded - nearest_int) < 1e-8:
        return float(nearest_int)
    return rounded


def _rotate_yaw(point: tuple[float, float, float], yaw: int) -> tuple[float, float, float]:
    x, y, z = point
    yaw = yaw % 360
    if yaw == 90:
        return (-y, x, z)
    if yaw == 180:
        return (-x, -y, z)
    if yaw == 270:
        return (y, -x, z)
    return (x, y, z)


def _world_aabb_from_local_bounds(
    bounds: tuple[tuple[float, float, float], tuple[float, float, float]],
    scale: tuple[float, float, float],
    yaw: int,
    location: tuple[float, float, float],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    lo, hi = bounds
    local_min = tuple(lo[i] * scale[i] for i in range(3))
    local_max = tuple(hi[i] * scale[i] for i in range(3))
    corners = [
        _rotate_yaw((x, y, z), yaw)
        for x, y, z in product(
            (local_min[0], local_max[0]),
            (local_min[1], local_max[1]),
            (local_min[2], local_max[2]),
        )
    ]
    world_min = (
        location[0] + min(c[0] for c in corners),
        location[1] + min(c[1] for c in corners),
        location[2] + min(c[2] for c in corners),
    )
    world_max = (
        location[0] + max(c[0] for c in corners),
        location[1] + max(c[1] for c in corners),
        location[2] + max(c[2] for c in corners),
    )
    visual_size = (
        world_max[0] - world_min[0],
        world_max[1] - world_min[1],
        world_max[2] - world_min[2],
    )
    return world_min, visual_size


def _is_default_snap_box(
    snap_box: tuple[tuple[float, float, float], tuple[float, float, float]],
) -> bool:
    return snap_box == ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))


def _uses_modular_kit(manifest: Manifest) -> bool:
    categories = {a.category for a in manifest.assets.values()}
    return manifest.version >= 2 and {"floor", "wall", "wall_door"}.issubset(categories)


def _compile_layout_modular(
    spec: LayoutSpec,
    manifest: Manifest,
    stairwells: dict[str, set[tuple[int, int]]],
) -> list[Placement]:
    g = manifest.grid
    floor_assets = _assets_by_category(manifest, "floor")
    wall_assets = _assets_by_category(manifest, "wall")
    door_assets = _assets_by_category(manifest, "wall_door")
    window_assets = _assets_by_category(manifest, "window", "glass_wall")
    shared_doors = _shared_door_keys(spec)
    placements: list[Placement] = []
    for room in spec.rooms:
        blocked = stairwells.get(room.name, set())
        placements += _compile_nav_proxies(room, spec, g, blocked)
        placements += _compile_floor_tiles(room, spec, floor_assets, g, blocked)
        placements += _compile_wall_modules(
            room, spec, wall_assets, door_assets, window_assets, shared_doors, g
        )
    return _dedupe_shared_walls(placements)


def _assets_by_category(manifest: Manifest, *categories: str) -> list[AssetDef]:
    wanted = set(categories)
    assets = [a for a in manifest.assets.values() if a.category in wanted and not a.needs_review]
    return sorted(
        assets,
        key=lambda a: (a.footprint[0] * a.footprint[1], a.footprint),
        reverse=True,
    )


def _compile_nav_proxies(
    room: Room, spec: LayoutSpec, g: float, blocked_cells: set[tuple[int, int]]
) -> list[Placement]:
    if not blocked_cells:
        return [_compile_nav_proxy(room, spec, g)]
    x, y, w, d = room.rect
    ox, oy, oz = spec.origin
    base_z = oz + room.level * spec.level_height
    out: list[Placement] = []
    for cy in range(d):
        for cx in range(w):
            if (cx, cy) in blocked_cells:
                continue
            out.append(
                _fit_placement(
                    f"{room.name}_navproxy_{cx}_{cy}",
                    _NAV_PROXY_ASSET,
                    tmin=(
                        ox + (x + cx) * g,
                        oy + (y + cy) * g,
                        base_z - _NAV_PROXY_TOP_OFFSET - _NAV_PROXY_THICKNESS,
                    ),
                    tsize=(g, g, _NAV_PROXY_THICKNESS),
                    kind="nav_proxy",
                    metadata={"room": room.name},
                )
            )
    return out


def _compile_nav_proxy(room: Room, spec: LayoutSpec, g: float) -> Placement:
    """ArchKit floor 资产当前不产出可走 NavMesh，薄代理藏在地板下承载导航。"""
    x, y, w, d = room.rect
    ox, oy, oz = spec.origin
    base_z = oz + room.level * spec.level_height
    return _fit_placement(
        f"{room.name}_navproxy",
        _NAV_PROXY_ASSET,
        tmin=(
            ox + x * g,
            oy + y * g,
            base_z - _NAV_PROXY_TOP_OFFSET - _NAV_PROXY_THICKNESS,
        ),
        tsize=(w * g, d * g, _NAV_PROXY_THICKNESS),
        kind="nav_proxy",
        metadata={"room": room.name},
    )


def _compile_floor_tiles(
    room: Room,
    spec: LayoutSpec,
    candidates: list[AssetDef],
    g: float,
    blocked_cells: set[tuple[int, int]] | None = None,
) -> list[Placement]:
    if not candidates:
        return []
    x, y, w, d = room.rect
    ox, oy, oz = spec.origin
    base_z = oz + room.level * spec.level_height
    blocked_cells = blocked_cells or set()
    occupied = [[False for _ in range(d)] for _ in range(w)]
    for cx, cy in blocked_cells:
        if 0 <= cx < w and 0 <= cy < d:
            occupied[cx][cy] = True
    out: list[Placement] = []
    for cy in range(d):
        for cx in range(w):
            if occupied[cx][cy]:
                continue
            asset = _pick_footprint(candidates, w - cx, d - cy, occupied, cx, cy)
            fw, fd = _fit_footprint(asset, w - cx, d - cy)
            for ix in range(cx, min(cx + fw, w)):
                for iy in range(cy, min(cy + fd, d)):
                    occupied[ix][iy] = True
            full_room = cx == 0 and cy == 0 and fw == w and fd == d
            name = f"{room.name}_floor" if full_room else f"{room.name}_floor_{cx}_{cy}"
            out.append(
                _fit_placement(
                    name,
                    asset,
                    tmin=(ox + (x + cx) * g, oy + (y + cy) * g, base_z - asset.size[2]),
                    tsize=(fw * g, fd * g, asset.size[2]),
                    kind="floor",
                    metadata={"room": room.name},
                )
            )
    return out


def _pick_footprint(
    candidates: list[AssetDef],
    max_w: int,
    max_d: int,
    occupied: list[list[bool]],
    cx: int,
    cy: int,
) -> AssetDef:
    for asset in candidates:
        fw, fd = asset.footprint
        if fw <= max_w and fd <= max_d and _area_empty(occupied, cx, cy, fw, fd):
            return asset
    return candidates[-1]


def _fit_footprint(asset: AssetDef, max_w: int, max_d: int) -> tuple[int, int]:
    return (max(1, min(asset.footprint[0], max_w)), max(1, min(asset.footprint[1], max_d)))


def _area_empty(occupied: list[list[bool]], x: int, y: int, w: int, d: int) -> bool:
    if x + w > len(occupied) or y + d > len(occupied[0]):
        return False
    return all(not occupied[ix][iy] for ix in range(x, x + w) for iy in range(y, y + d))


def _compile_wall_modules(
    room: Room,
    spec: LayoutSpec,
    wall_assets: list[AssetDef],
    door_assets: list[AssetDef],
    window_assets: list[AssetDef],
    shared_doors: set[tuple[str, str, int, int]],
    g: float,
) -> list[Placement]:
    _x, _y, w, d = room.rect
    out: list[Placement] = []
    doors_by_wall = _openings_by_wall(room.doors)
    windows_by_wall = _openings_by_wall(room.windows)
    for wall in WALLS:
        length = w if wall in ("north", "south") else d
        blockers = doors_by_wall[wall] + windows_by_wall[wall]
        for s, e in _segments(length, blockers, room.name, wall):
            out += _compile_wall_run(room, spec, wall, s, e, wall_assets, g)
        for door in [d for d in room.doors if d.wall == wall]:
            s, e = door.at, door.at + door.width
            if (room.name, wall, s, e) in shared_doors:
                continue
            out.append(_compile_opening(room, spec, wall, s, e, door_assets, "door", g))
        for s, e in windows_by_wall[wall]:
            out.append(_compile_opening(room, spec, wall, s, e, window_assets, "window", g))
    return out


def _openings_by_wall(openings: list[Door]) -> dict[str, list[tuple[int, int]]]:
    by_wall: dict[str, list[tuple[int, int]]] = {wall: [] for wall in WALLS}
    for opening in openings:
        by_wall[opening.wall].append((opening.at, opening.at + opening.width))
    return by_wall


def _shared_door_keys(spec: LayoutSpec) -> set[tuple[str, str, int, int]]:
    """房间间连通门只保留开口，不放门框，避免门框简单碰撞阻断 NavMesh。"""
    doors: list[tuple[Room, Door, tuple[str, int, int, int]]] = []
    for room in spec.rooms:
        for door in room.doors:
            doors.append((room, door, _door_world_segment(room, door)))
    shared: set[tuple[str, str, int, int]] = set()
    for i, (room_a, door_a, seg_a) in enumerate(doors):
        for room_b, door_b, seg_b in doors[i + 1 :]:
            if room_a.name == room_b.name:
                continue
            if room_a.level != room_b.level:
                continue
            axis_a, coord_a, lo_a, hi_a = seg_a
            axis_b, coord_b, lo_b, hi_b = seg_b
            if axis_a == axis_b and coord_a == coord_b and min(hi_a, hi_b) - max(lo_a, lo_b) >= 1:
                shared.add((room_a.name, door_a.wall, door_a.at, door_a.at + door_a.width))
                shared.add((room_b.name, door_b.wall, door_b.at, door_b.at + door_b.width))
    return shared


def _compile_wall_run(
    room: Room,
    spec: LayoutSpec,
    wall: str,
    start: int,
    end: int,
    candidates: list[AssetDef],
    g: float,
) -> list[Placement]:
    if end <= start or not candidates:
        return []
    asset = _best_scaled_wall_asset(candidates, spec.wall_height)
    tmin, tsize, rotation = _wall_target(room, spec, wall, start, end, g)
    return [
        _fit_placement(
            f"{room.name}_{wall}_{start}_0",
            asset,
            tmin,
            tsize,
            rotation,
            kind="wall",
            metadata={"room": room.name},
        )
    ]


def _best_scaled_wall_asset(candidates: list[AssetDef], desired_height: float) -> AssetDef:
    """墙体主路径优先用 1m 基础件拉伸，减少多段拼接缝和 ArchKit 外沿错位。"""
    return min(
        candidates,
        key=lambda a: (
            0 if a.footprint[0] == 1 else 1,
            abs(a.size[2] - desired_height),
            abs(a.size[0] - 100.0),
            a.key,
        ),
    )


def _compile_opening(
    room: Room,
    spec: LayoutSpec,
    wall: str,
    start: int,
    end: int,
    candidates: list[AssetDef],
    kind: str,
    g: float,
) -> Placement:
    asset = _best_opening_asset(candidates, end - start)
    tmin, tsize, rotation = _wall_target(
        room,
        spec,
        wall,
        start,
        end,
        g,
        thickness=spec.wall_thickness,
    )
    return _fit_placement(
        f"{room.name}_{wall}_{kind}_{start}",
        asset,
        tmin,
        tsize,
        rotation,
        kind="wall",
        metadata={"room": room.name},
    )


def _best_opening_asset(candidates: list[AssetDef], width: int) -> AssetDef:
    if not candidates:
        raise LayoutError("manifest 缺少门/窗模块资产，无法放置开口")
    return min(candidates, key=lambda a: (abs(a.footprint[0] - width), -a.footprint[0]))


def _wall_target(
    room: Room,
    spec: LayoutSpec,
    wall: str,
    start: int,
    end: int,
    g: float,
    *,
    thickness: float | None = None,
) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    x, y, w, d = room.rect
    h = spec.wall_height
    t = thickness if thickness is not None else spec.wall_thickness
    ox, oy, oz = spec.origin
    base_z = oz + room.level * spec.level_height
    run = (end - start) * g
    if wall == "south":
        center_y = oy + y * g + spec.wall_thickness / 2
        return (ox + (x + start) * g, center_y - t / 2, base_z), (run, t, h), (0.0, 0.0, 0.0)
    if wall == "north":
        center_y = oy + (y + d) * g - spec.wall_thickness / 2
        return (ox + (x + start) * g, center_y - t / 2, base_z), (run, t, h), (0.0, 0.0, 0.0)
    if wall == "west":
        run_start = start * g + (spec.wall_thickness if start == 0 else 0.0)
        run_end = end * g - (spec.wall_thickness if end == d else 0.0)
        run = run_end - run_start
        if run <= 0:
            raise LayoutError(f"房间 {room.name} 的 west 墙段过短，无法缩进墙角")
        center_x = ox + x * g + spec.wall_thickness / 2
        return (center_x - t / 2, oy + y * g + run_start, base_z), (t, run, h), (0.0, 90.0, 0.0)
    run_start = start * g + (spec.wall_thickness if start == 0 else 0.0)
    run_end = end * g - (spec.wall_thickness if end == d else 0.0)
    run = run_end - run_start
    if run <= 0:
        raise LayoutError(f"房间 {room.name} 的 east 墙段过短，无法缩进墙角")
    center_x = ox + (x + w) * g - spec.wall_thickness / 2
    return (center_x - t / 2, oy + y * g + run_start, base_z), (t, run, h), (0.0, 90.0, 0.0)


def _compile_native_layers(spec: LayoutSpec, manifest: Manifest) -> list[Placement]:
    placements: list[Placement] = []
    occupied = _reserved_cells_for_stairs(spec, manifest)
    placements += _compile_stairs(spec, manifest)
    spawn_points: list[tuple[Room, tuple[float, float], float]] = []
    route_points: list[list[tuple[Room, tuple[float, float]]]] = []
    route_cells: set[tuple[int, int, int]] = set()
    if spec.gameplay is not None:
        spawn_points = _gameplay_spawn_points(spec)
        route_points = _gameplay_routes(spec, spawn_points)
        route_cells = _route_cells(route_points)

    prop_placements, prop_cells = _compile_explicit_props(
        spec,
        manifest,
        occupied,
        route_cells,
    )
    placements += prop_placements
    occupied |= prop_cells
    if spec.gameplay is None:
        return placements

    spawn_cells = {_world_cell_for_point(room, local) for room, local, _rotation in spawn_points}
    placements += _compile_spawn_points(spec, manifest.grid, spawn_points)
    placements += _compile_route_markers(spec, manifest.grid, route_points)
    if spec.gameplay.auto_cover is not False:
        placements += _compile_auto_cover(
            spec,
            manifest,
            occupied=occupied | spawn_cells,
            route_cells=route_cells,
        )
    return placements


def _compile_stairs(spec: LayoutSpec, manifest: Manifest) -> list[Placement]:
    out: list[Placement] = []
    rooms = {room.name: room for room in spec.rooms}
    wall_asset = _stairwell_wall_asset(manifest, spec)
    for index, stair in enumerate(spec.stairs):
        room = rooms[stair.room]
        asset = _stair_asset(manifest, stair)
        rotation = _rotation_from_facing(stair.facing)
        base_level = min(stair.from_level, stair.to_level)
        tmin = _local_tmin(spec, room, stair.at, base_level, manifest.grid)
        out.append(
            _place_native(
                f"{room.name}_stair_{stair.key or index}",
                asset,
                tmin,
                rotation,
                kind="stair",
                metadata={
                    "from_level": stair.from_level,
                    "to_level": stair.to_level,
                    "room": room.name,
                },
            )
        )
        if wall_asset is not None:
            out += _compile_stairwell_guards(
                spec, room, stair, asset, wall_asset, index, rotation, manifest.grid
            )
    return out


def _stairwell_wall_asset(manifest: Manifest, spec: LayoutSpec) -> AssetDef | None:
    if spec.structure_mode == "slab":
        return _ENGINE_SLAB_ASSET
    wall_assets = _assets_by_category(manifest, "wall")
    if not wall_assets:
        return None
    return _best_scaled_wall_asset(wall_assets, spec.wall_height)


def _compile_stairwell_guards(
    spec: LayoutSpec,
    room: Room,
    stair: StairSpec,
    stair_asset: AssetDef,
    wall_asset: AssetDef,
    stair_index: int,
    rotation: tuple[float, float, float],
    g: float,
) -> list[Placement]:
    """为楼梯洞生成两侧楼梯井护墙；两端保持开口，避免挡住上下楼路径。"""
    fw, fd = _rotated_footprint(stair_asset.footprint, rotation)
    return _compile_stairwell_guards_with_grid(
        spec, room, stair, wall_asset, stair_index, fw, fd, g
    )


def _compile_stairwell_guards_with_grid(
    spec: LayoutSpec,
    room: Room,
    stair: StairSpec,
    wall_asset: AssetDef,
    stair_index: int,
    fw: int,
    fd: int,
    g: float,
) -> list[Placement]:
    ox, oy, oz = spec.origin
    room_x0 = ox + room.rect[0] * g
    room_y0 = oy + room.rect[1] * g
    room_x1 = ox + (room.rect[0] + room.rect[2]) * g
    room_y1 = oy + (room.rect[1] + room.rect[3]) * g
    x0 = ox + (room.rect[0] + stair.at[0]) * g
    y0 = oy + (room.rect[1] + stair.at[1]) * g
    x1 = x0 + fw * g
    y1 = y0 + fd * g
    z = oz + min(stair.from_level, stair.to_level) * spec.level_height
    h = spec.level_height
    t = spec.wall_thickness
    facing = stair.facing.lower()
    segments: list[
        tuple[
            str,
            tuple[float, float, float],
            tuple[float, float, float],
            tuple[float, float, float],
        ]
    ] = []
    if facing in {"north", "south"}:
        if x0 - t >= room_x0:
            segments.append(("west", (x0 - t, y0, z), (t, fd * g, h), (0.0, 90.0, 0.0)))
        if x1 + t <= room_x1:
            segments.append(("east", (x1, y0, z), (t, fd * g, h), (0.0, 90.0, 0.0)))
    else:
        if y0 - t >= room_y0:
            segments.append(("south", (x0, y0 - t, z), (fw * g, t, h), (0.0, 0.0, 0.0)))
        if y1 + t <= room_y1:
            segments.append(("north", (x0, y1, z), (fw * g, t, h), (0.0, 0.0, 0.0)))

    out: list[Placement] = []
    for side, tmin, tsize, wall_rotation in segments:
        out.append(
            _fit_placement(
                f"{room.name}_stairwell_{stair_index}_{side}",
                wall_asset,
                tmin,
                tsize,
                wall_rotation,
                kind="stairwell",
                metadata={
                    "room": room.name,
                    "from_level": stair.from_level,
                    "to_level": stair.to_level,
                    "side": side,
                },
            )
        )
    return out


def _compile_explicit_props(
    spec: LayoutSpec,
    manifest: Manifest,
    occupied: set[tuple[int, int, int]],
    route_cells: set[tuple[int, int, int]] | None = None,
) -> tuple[list[Placement], set[tuple[int, int, int]]]:
    out: list[Placement] = []
    prop_cells: set[tuple[int, int, int]] = set()
    for room in spec.rooms:
        for index, prop in enumerate(room.props):
            try:
                asset = _prop_asset(manifest, prop)
                rotation = (0.0, prop.rotation, 0.0)
                cells = _prop_cells(room, prop, asset, rotation)
                reason = _prop_block_reason(room, cells, occupied | prop_cells, route_cells)
            except (KeyError, LayoutError) as exc:
                if prop.optional:
                    continue
                raise LayoutError(f"房间 {room.name} 的道具放置失败：{exc}") from exc
            if reason:
                if prop.optional:
                    continue
                raise LayoutError(f"房间 {room.name} 的道具 {prop.key or prop.category} {reason}")
            out.append(
                _place_native(
                    f"{room.name}_prop_{prop.key or prop.category or index}",
                    asset,
                    _local_tmin(spec, room, prop.at, room.level, manifest.grid),
                    rotation,
                    kind="prop",
                    metadata={"room": room.name, "optional": prop.optional},
                )
            )
            prop_cells |= cells
    return out, prop_cells


def _compile_spawn_points(
    spec: LayoutSpec,
    g: float,
    spawn_points: list[tuple[Room, tuple[float, float], float]],
) -> list[Placement]:
    out: list[Placement] = []
    for index, (room, local, rotation) in enumerate(spawn_points):
        x, y, z = _world_point(spec, room, local, g, z_offset=_SPAWN_Z_OFFSET)
        out.append(
            Placement(
                name=f"game_spawn_{index}",
                asset_path="",
                location=(x, y, z),
                scale=(1.0, 1.0, 1.0),
                rotation=(0.0, rotation, 0.0),
                actor_type="PlayerStart",
                kind="spawn",
                metadata={"room": room.name},
            )
        )
    return out


def _compile_route_markers(
    spec: LayoutSpec, g: float, route_points: list[list[tuple[Room, tuple[float, float]]]]
) -> list[Placement]:
    out: list[Placement] = []
    for route_index, points in enumerate(route_points):
        for point_index, (room, local) in enumerate(points):
            x, y, z = _world_point(spec, room, local, g, z_offset=_ROUTE_Z_OFFSET)
            out.append(
                _place_native(
                    f"game_route_{route_index}_{point_index}",
                    _ROUTE_MARKER_ASSET,
                    (x - 10.0, y - 10.0, z),
                    (0.0, 0.0, 0.0),
                    kind="route",
                    metadata={"route_id": route_index, "room": room.name},
                )
            )
    return out


def _compile_auto_cover(
    spec: LayoutSpec,
    manifest: Manifest,
    *,
    occupied: set[tuple[int, int, int]],
    route_cells: set[tuple[int, int, int]],
) -> list[Placement]:
    out: list[Placement] = []
    cover_asset = _smallest_asset(manifest, "cover")
    pillar_asset = _smallest_asset(manifest, "pillar")
    if cover_asset is None and pillar_asset is None:
        return out

    used = set(occupied) | set(route_cells)
    for room in spec.rooms:
        if cover_asset is not None:
            cell = _first_free_cell(room, cover_asset, used)
            if cell is not None:
                out.append(
                    _place_native(
                        f"{room.name}_cover_0",
                        cover_asset,
                        _local_tmin(spec, room, cell, room.level, manifest.grid),
                        (0.0, 0.0, 0.0),
                        kind="cover",
                        metadata={"room": room.name},
                    )
                )
                used |= _asset_cells(room, cell, cover_asset, (0.0, 0.0, 0.0))
        if pillar_asset is not None and room.rect[2] * room.rect[3] >= 36:
            cell = _last_free_cell(room, pillar_asset, used)
            if cell is not None:
                out.append(
                    _place_native(
                        f"{room.name}_pillar_0",
                        pillar_asset,
                        _local_tmin(spec, room, cell, room.level, manifest.grid),
                        (0.0, 0.0, 0.0),
                        kind="pillar",
                        metadata={"room": room.name},
                    )
                )
                used |= _asset_cells(room, cell, pillar_asset, (0.0, 0.0, 0.0))
    return out


def _place_native(
    name: str,
    asset: AssetDef,
    tmin: tuple[float, float, float],
    rotation: tuple[float, float, float],
    *,
    kind: str,
    metadata: dict[str, Any] | None = None,
) -> Placement:
    return _fit_placement(
        name,
        asset,
        tmin=tmin,
        tsize=_native_target_size(asset, rotation),
        rotation=rotation,
        kind=kind,
        metadata=metadata,
    )


def _native_target_size(
    asset: AssetDef, rotation: tuple[float, float, float]
) -> tuple[float, float, float]:
    snap_min, snap_max = asset.snap_box
    snap_size = (
        (snap_max[0] - snap_min[0]) * asset.size[0],
        (snap_max[1] - snap_min[1]) * asset.size[1],
        (snap_max[2] - snap_min[2]) * asset.size[2],
    )
    yaw = round(rotation[1]) % 360
    if yaw in (90, 270):
        return (snap_size[1], snap_size[0], snap_size[2])
    return snap_size


def _rotation_from_facing(facing: str) -> tuple[float, float, float]:
    key = facing.lower()
    if key not in _FACING_YAW:
        raise LayoutError(f"楼梯 facing 非法：{facing}")
    return (0.0, _FACING_YAW[key], 0.0)


def _stair_asset(manifest: Manifest, stair: StairSpec) -> AssetDef:
    if stair.key:
        return manifest.require(stair.key)
    assets = _assets_by_category(manifest, "stair")
    if not assets:
        raise LayoutError("manifest 缺少 stair 资产")
    # 具体高度匹配在 _validate_stairs 中按 spec.level_height 检查；这里优先取最高可用件。
    return max(
        assets,
        key=lambda asset: (asset.size[2], asset.footprint[0] * asset.footprint[1], asset.key),
    )


def _prop_asset(manifest: Manifest, prop: PropSpec) -> AssetDef:
    if prop.key:
        return manifest.require(prop.key)
    category = prop.category or "prop"
    asset = _smallest_asset(manifest, category)
    if asset is None:
        raise LayoutError(f"manifest 缺少 {category} 道具资产")
    return asset


def _smallest_asset(manifest: Manifest, category: str) -> AssetDef | None:
    assets = _assets_by_category(manifest, category)
    if not assets:
        return None
    return min(
        assets,
        key=lambda a: (a.footprint[0] * a.footprint[1], a.size[0] * a.size[1], a.key),
    )


def _local_tmin(
    spec: LayoutSpec,
    room: Room,
    local: tuple[int, int],
    level: int,
    g: float,
) -> tuple[float, float, float]:
    ox, oy, oz = spec.origin
    return (
        ox + (room.rect[0] + local[0]) * g,
        oy + (room.rect[1] + local[1]) * g,
        oz + level * spec.level_height,
    )


def _world_point(
    spec: LayoutSpec,
    room: Room,
    local: tuple[float, float],
    g: float,
    *,
    z_offset: float = 0.0,
) -> tuple[float, float, float]:
    ox, oy, oz = spec.origin
    return (
        ox + (room.rect[0] + local[0]) * g,
        oy + (room.rect[1] + local[1]) * g,
        oz + room.level * spec.level_height + z_offset,
    )


def _asset_cells(
    room: Room,
    local: tuple[int, int],
    asset: AssetDef,
    rotation: tuple[float, float, float],
) -> set[tuple[int, int, int]]:
    fw, fd = _rotated_footprint(asset.footprint, rotation)
    return {
        (room.level, room.rect[0] + local[0] + dx, room.rect[1] + local[1] + dy)
        for dx in range(fw)
        for dy in range(fd)
    }


def _prop_cells(
    room: Room,
    prop: PropSpec,
    asset: AssetDef,
    rotation: tuple[float, float, float],
) -> set[tuple[int, int, int]]:
    return _asset_cells(room, prop.at, asset, rotation)


def _rotated_footprint(
    footprint: tuple[int, int], rotation: tuple[float, float, float]
) -> tuple[int, int]:
    yaw = round(rotation[1]) % 360
    if yaw in (90, 270):
        return (footprint[1], footprint[0])
    return footprint


def _prop_block_reason(
    room: Room,
    cells: set[tuple[int, int, int]],
    occupied: set[tuple[int, int, int]],
    route_cells: set[tuple[int, int, int]] | None = None,
) -> str:
    for _level, wx, wy in cells:
        if not _room_contains_world_cell(room, wx, wy):
            return "越界"
    if cells & _door_reserved_cells(room):
        return "堵门"
    if cells & _door_to_door_route_cells(room):
        return "堵门到门路线"
    if route_cells and cells & route_cells:
        return "堵主路线"
    if cells & occupied:
        return "重叠"
    return ""


def _door_reserved_cells(room: Room) -> set[tuple[int, int, int]]:
    x, y, w, d = room.rect
    out: set[tuple[int, int, int]] = set()
    for door in room.doors:
        for offset in range(door.width):
            if door.wall == "south":
                out.add((room.level, x + door.at + offset, y))
            elif door.wall == "north":
                out.add((room.level, x + door.at + offset, y + d - 1))
            elif door.wall == "west":
                out.add((room.level, x, y + door.at + offset))
            else:
                out.add((room.level, x + w - 1, y + door.at + offset))
    return out


def _door_to_door_route_cells(
    room: Room, *, through_only: bool = False
) -> set[tuple[int, int, int]]:
    if len(room.doors) < 2:
        return set()
    out: set[tuple[int, int, int]] = set()
    for left_index, left_door in enumerate(room.doors):
        for right_door in room.doors[left_index + 1 :]:
            if through_only and not _doors_are_opposite(left_door, right_door):
                continue
            left_cells = _door_anchor_cells(room, left_door)
            right_cells = _door_anchor_cells(room, right_door)
            for left_cell in left_cells:
                for right_cell in right_cells:
                    out |= _grid_line_cells(left_cell, right_cell)
    return out


def _doors_are_opposite(left: Door, right: Door) -> bool:
    return {left.wall, right.wall} in ({"west", "east"}, {"north", "south"})


def _door_anchor_cells(room: Room, door: Door) -> set[tuple[int, int, int]]:
    x, y, w, d = room.rect
    cells: set[tuple[int, int, int]] = set()
    for offset in range(door.width):
        if door.wall == "south":
            cells.add((room.level, x + door.at + offset, y))
        elif door.wall == "north":
            cells.add((room.level, x + door.at + offset, y + d - 1))
        elif door.wall == "west":
            cells.add((room.level, x, y + door.at + offset))
        else:
            cells.add((room.level, x + w - 1, y + door.at + offset))
    return cells


def _grid_line_cells(
    start: tuple[int, int, int], end: tuple[int, int, int]
) -> set[tuple[int, int, int]]:
    level, ax, ay = start
    _end_level, bx, by = end
    steps = max(1, max(abs(ax - bx), abs(ay - by)))
    cells: set[tuple[int, int, int]] = set()
    for step in range(steps + 1):
        t = step / steps
        cells.add((level, round(ax + (bx - ax) * t), round(ay + (by - ay) * t)))
    return cells


def _room_contains_world_cell(room: Room, wx: int, wy: int) -> bool:
    x, y, w, d = room.rect
    return x <= wx < x + w and y <= wy < y + d


def _stairwell_cells(spec: LayoutSpec, manifest: Manifest) -> dict[str, set[tuple[int, int]]]:
    wells: dict[str, set[tuple[int, int]]] = {}
    for stair in spec.stairs:
        room = _room_by_name(spec, stair.room)
        asset = _stair_asset(manifest, stair)
        rotation = _rotation_from_facing(stair.facing)
        fw, fd = _rotated_footprint(asset.footprint, rotation)
        upper_level = max(stair.from_level, stair.to_level)
        world_cells = {
            (room.rect[0] + stair.at[0] + dx, room.rect[1] + stair.at[1] + dy)
            for dx in range(fw)
            for dy in range(fd)
        }
        for upper_room in spec.rooms:
            if upper_room.level != upper_level:
                continue
            for wx, wy in world_cells:
                if _room_contains_world_cell(upper_room, wx, wy):
                    wells.setdefault(upper_room.name, set()).add(
                        (wx - upper_room.rect[0], wy - upper_room.rect[1])
                    )
    return wells


def _reserved_cells_for_stairs(spec: LayoutSpec, manifest: Manifest) -> set[tuple[int, int, int]]:
    cells: set[tuple[int, int, int]] = set()
    for stair in spec.stairs:
        room = _room_by_name(spec, stair.room)
        asset = _stair_asset(manifest, stair)
        rotation = _rotation_from_facing(stair.facing)
        fw, fd = _rotated_footprint(asset.footprint, rotation)
        footprint = {
            (room.rect[0] + stair.at[0] + dx, room.rect[1] + stair.at[1] + dy)
            for dx in range(fw)
            for dy in range(fd)
        }
        # 自动玩法件需要给楼梯和楼梯井 guard 留出一圈安全边界，避免 cover/pillar
        # 贴到井壁或楼梯侧边形成视觉/碰撞阻塞。
        for target_room in spec.rooms:
            if target_room.level not in {stair.from_level, stair.to_level}:
                continue
            cells |= _expanded_world_cells_within_room(
                target_room,
                footprint,
                level=target_room.level,
            )
    return cells


def _expanded_world_cells_within_room(
    room: Room,
    world_cells: set[tuple[int, int]],
    *,
    level: int,
) -> set[tuple[int, int, int]]:
    out: set[tuple[int, int, int]] = set()
    for wx, wy in world_cells:
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                mx, my = wx + dx, wy + dy
                if _room_contains_world_cell(room, mx, my):
                    out.add((level, mx, my))
    return out


def _room_by_name(spec: LayoutSpec, name: str) -> Room:
    for room in spec.rooms:
        if room.name == name:
            return room
    raise LayoutError(f"找不到房间：{name}")


def _gameplay_spawn_points(spec: LayoutSpec) -> list[tuple[Room, tuple[float, float], float]]:
    assert spec.gameplay is not None
    if spec.gameplay.spawn_points is not None:
        out = []
        for raw in spec.gameplay.spawn_points:
            room = _room_by_name(spec, str(raw.get("room", spec.rooms[0].name)))
            local = raw.get("at")
            at = _xy_float(local) if local is not None else _room_center_local(room)
            out.append((room, at, float(raw.get("rotation", 0.0))))
        return out

    if len(spec.rooms) == 1:
        room = spec.rooms[0]
        w, d = room.rect[2], room.rect[3]
        return [(room, (0.75, 0.75), 0.0), (room, (w - 0.75, d - 0.75), 180.0)]

    graph = _room_graph(spec)
    start = spec.rooms[0]
    farthest = _farthest_room(spec, start, graph)
    return [
        (start, _room_center_local(start), 0.0),
        (farthest, _room_center_local(farthest), 180.0),
    ]


def _gameplay_routes(
    spec: LayoutSpec,
    spawn_points: list[tuple[Room, tuple[float, float], float]],
) -> list[list[tuple[Room, tuple[float, float]]]]:
    assert spec.gameplay is not None
    if spec.gameplay.routes is not None:
        routes: list[list[tuple[Room, tuple[float, float]]]] = []
        for raw_route in spec.gameplay.routes:
            explicit_points = []
            for raw_point in raw_route.get("points", []):
                room = _room_by_name(spec, str(raw_point.get("room", spec.rooms[0].name)))
                explicit_points.append(
                    (room, _xy_float(raw_point.get("at", _room_center_local(room))))
                )
            if explicit_points:
                routes.append(explicit_points)
        return routes

    if len(spawn_points) < 2:
        return []
    start_room = spawn_points[0][0]
    end_room = spawn_points[1][0]
    graph = _room_graph(spec)
    path = _shortest_room_path(spec, start_room, end_room, graph)
    route_points: list[tuple[Room, tuple[float, float]]] = [(start_room, spawn_points[0][1])]
    for left, right in pairwise(path):
        door = _shared_door_center(left, right)
        if door is not None:
            route_points.append((left, door[0]))
            route_points.append((right, door[1]))
        elif stair_points := _shared_stair_center(spec, left, right):
            route_points.append((left, stair_points[0]))
            route_points.append((right, stair_points[1]))
        else:
            route_points.append((right, _room_center_local(right)))
    route_points.append((end_room, spawn_points[1][1]))
    return [route_points]


def _shared_stair_center(
    spec: LayoutSpec, left: Room, right: Room
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    for stair in spec.stairs:
        base_room = _room_by_name(spec, stair.room)
        target_room = _stair_target_room(spec, stair)
        if target_room is None:
            continue
        asset = _stair_asset_for_validation(spec, stair)
        fw, fd = _rotated_footprint(asset.footprint, _rotation_from_facing(stair.facing))
        world_center = (
            base_room.rect[0] + stair.at[0] + fw / 2,
            base_room.rect[1] + stair.at[1] + fd / 2,
        )
        base_local = (
            world_center[0] - base_room.rect[0],
            world_center[1] - base_room.rect[1],
        )
        target_local = (
            world_center[0] - target_room.rect[0],
            world_center[1] - target_room.rect[1],
        )
        if left.name == base_room.name and right.name == target_room.name:
            return base_local, target_local
        if left.name == target_room.name and right.name == base_room.name:
            return target_local, base_local
    return None


def _xy_float(raw: object) -> tuple[float, float]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        raise LayoutError("坐标必须是 [x, y]")
    return (float(raw[0]), float(raw[1]))


def _room_center_local(room: Room) -> tuple[float, float]:
    return (room.rect[2] / 2, room.rect[3] / 2)


def _world_cell_for_point(room: Room, local: tuple[float, float]) -> tuple[int, int, int]:
    return (room.level, room.rect[0] + int(local[0]), room.rect[1] + int(local[1]))


def _route_cells(
    route_points: list[list[tuple[Room, tuple[float, float]]]],
) -> set[tuple[int, int, int]]:
    cells: set[tuple[int, int, int]] = set()
    for points in route_points:
        for room, local in points:
            cells.add(_world_cell_for_point(room, local))
        for (room_a, local_a), (room_b, local_b) in pairwise(points):
            if room_a.level != room_b.level:
                continue
            ax = room_a.rect[0] + local_a[0]
            ay = room_a.rect[1] + local_a[1]
            bx = room_b.rect[0] + local_b[0]
            by = room_b.rect[1] + local_b[1]
            steps = max(1, int(max(abs(ax - bx), abs(ay - by))))
            for step in range(steps + 1):
                t = step / steps
                wx = round(ax + (bx - ax) * t)
                wy = round(ay + (by - ay) * t)
                cells.add((room_a.level, wx, wy))
    return cells


def _first_free_cell(
    room: Room, asset: AssetDef, occupied: set[tuple[int, int, int]]
) -> tuple[int, int] | None:
    candidates = [
        (x, y)
        for y in range(1, max(1, room.rect[3] - 1))
        for x in range(1, max(1, room.rect[2] - 1))
    ] or [(0, 0)]
    return _first_cell_from_candidates(room, asset, occupied, candidates)


def _last_free_cell(
    room: Room, asset: AssetDef, occupied: set[tuple[int, int, int]]
) -> tuple[int, int] | None:
    candidates = [
        (x, y)
        for y in range(max(0, room.rect[3] - 2), 0, -1)
        for x in range(max(0, room.rect[2] - 2), 0, -1)
    ] or [(0, 0)]
    return _first_cell_from_candidates(room, asset, occupied, candidates)


def _first_cell_from_candidates(
    room: Room,
    asset: AssetDef,
    occupied: set[tuple[int, int, int]],
    candidates: list[tuple[int, int]],
) -> tuple[int, int] | None:
    rotation = (0.0, 0.0, 0.0)
    for cell in candidates:
        cells = _asset_cells(room, cell, asset, rotation)
        if _prop_block_reason(room, cells, occupied):
            continue
        return cell
    return None


def _room_graph(spec: LayoutSpec) -> dict[str, set[str]]:
    adjacency: dict[str, set[str]] = {room.name: set() for room in spec.rooms}
    for room_a, room_b in _door_connected_pairs(spec):
        adjacency[room_a.name].add(room_b.name)
        adjacency[room_b.name].add(room_a.name)
    for stair in spec.stairs:
        room = _room_by_name(spec, stair.room)
        upper_room = _stair_target_room(spec, stair)
        if upper_room is not None:
            adjacency[room.name].add(upper_room.name)
            adjacency[upper_room.name].add(room.name)
    return adjacency


def _door_connected_pairs(spec: LayoutSpec) -> list[tuple[Room, Room]]:
    pairs: list[tuple[Room, Room]] = []
    segments = {room.name: _door_world_segments(room) for room in spec.rooms}
    for i, room_a in enumerate(spec.rooms):
        for room_b in spec.rooms[i + 1 :]:
            if room_a.level != room_b.level:
                continue
            for axis_a, coord_a, lo_a, hi_a in segments[room_a.name]:
                for axis_b, coord_b, lo_b, hi_b in segments[room_b.name]:
                    if axis_a != axis_b or coord_a != coord_b:
                        continue
                    if min(hi_a, hi_b) - max(lo_a, lo_b) >= 1:
                        pairs.append((room_a, room_b))
    return pairs


def _stair_target_room(spec: LayoutSpec, stair: StairSpec) -> Room | None:
    room = _room_by_name(spec, stair.room)
    asset = _stair_asset_for_validation(spec, stair)
    rotation = _rotation_from_facing(stair.facing)
    fw, fd = _rotated_footprint(asset.footprint, rotation)
    target_level = stair.to_level
    world_cells = {
        (room.rect[0] + stair.at[0] + dx, room.rect[1] + stair.at[1] + dy)
        for dx in range(fw)
        for dy in range(fd)
    }
    for candidate in spec.rooms:
        if candidate.level != target_level:
            continue
        if any(_room_contains_world_cell(candidate, wx, wy) for wx, wy in world_cells):
            return candidate
    return None


def _stair_asset_for_validation(spec: LayoutSpec, stair: StairSpec) -> AssetDef:
    # _validate_stairs 只需要 footprint；运行时会用 manifest 的真实资产覆盖。
    return AssetDef(
        key=stair.key or "_stair_validation",
        path="",
        size=(300.0, 600.0, spec.level_height),
        category="stair",
        pivot=(0.0, 0.0, 0.0),
        footprint=(3, 6),
    )


def _farthest_room(spec: LayoutSpec, start: Room, graph: dict[str, set[str]]) -> Room:
    distances = _room_distances(start.name, graph)
    name = max(distances, key=lambda n: (distances[n], n))
    return _room_by_name(spec, name)


def _room_distances(start_name: str, graph: dict[str, set[str]]) -> dict[str, int]:
    distances = {start_name: 0}
    queue: deque[str] = deque([start_name])
    while queue:
        current = queue.popleft()
        for neighbor in graph[current]:
            if neighbor in distances:
                continue
            distances[neighbor] = distances[current] + 1
            queue.append(neighbor)
    return distances


def _shortest_room_path(
    spec: LayoutSpec,
    start: Room,
    end: Room,
    graph: dict[str, set[str]],
) -> list[Room]:
    parents: dict[str, str | None] = {start.name: None}
    queue: deque[str] = deque([start.name])
    while queue:
        current = queue.popleft()
        if current == end.name:
            break
        for neighbor in sorted(graph[current]):
            if neighbor in parents:
                continue
            parents[neighbor] = current
            queue.append(neighbor)
    if end.name not in parents:
        return [start, end]
    names = [end.name]
    while parents[names[-1]] is not None:
        parent = parents[names[-1]]
        assert parent is not None
        names.append(parent)
    names.reverse()
    return [_room_by_name(spec, name) for name in names]


def _shared_door_center(
    room_a: Room,
    room_b: Room,
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    if room_a.level != room_b.level:
        return None
    for door_a in room_a.doors:
        seg_a = _door_world_segment(room_a, door_a)
        for door_b in room_b.doors:
            seg_b = _door_world_segment(room_b, door_b)
            axis_a, coord_a, lo_a, hi_a = seg_a
            axis_b, coord_b, lo_b, hi_b = seg_b
            if axis_a != axis_b or coord_a != coord_b:
                continue
            lo, hi = max(lo_a, lo_b), min(hi_a, hi_b)
            if hi - lo < 1:
                continue
            mid = (lo + hi) / 2
            return (
                _door_local_point(room_a, axis_a, coord_a, mid),
                _door_local_point(room_b, axis_b, coord_b, mid),
            )
    return None


def _door_local_point(room: Room, axis: str, coord: int, mid: float) -> tuple[float, float]:
    x, y, w, d = room.rect
    if axis == "y":
        local_y = 0.5 if coord == y else d - 0.5
        return (mid - x, local_y)
    local_x = 0.5 if coord == x else w - 0.5
    return (local_x, mid - y)


def _dedupe_shared_walls(placements: list[Placement]) -> list[Placement]:
    """去除相邻房间共享墙产生的重叠薄墙（v2：共享边只保留一面）。

    相邻房间各自生成自己的墙，共享边上会出现两面几乎重合的薄墙（仅差墙厚 20uu），
    视觉上像"多放了一块板"。此处按墙的 (location, scale) 近似重合判定，
    重复的只保留第一面、丢弃其余——门洞段因位置/长度不同不会被误删。
    """
    kept: list[Placement] = []
    seen: list[tuple[float, float, float, float, float, float, float]] = []
    for p in placements:
        if p.kind != "wall":  # 只有结构墙/门窗参与共享墙去重
            kept.append(p)
            continue
        key = (
            round(p.location[0], 1),
            round(p.location[1], 1),
            round(p.location[2], 1),
            round(p.scale[0], 2),
            round(p.scale[1], 2),
            round(p.scale[2], 2),
            round(p.rotation[1] % 360, 1),
        )
        if any(_walls_coincide(key, s) for s in seen):
            continue  # 与已保留的某面墙重合 → 这是共享墙的另一面，丢弃
        seen.append(key)
        kept.append(p)
    return kept


def _walls_coincide(
    a: tuple[float, float, float, float, float, float, float],
    b: tuple[float, float, float, float, float, float, float],
) -> bool:
    """两面墙是否在共享边上几乎重合：同朝向、同尺寸，且中心距 <= 一个墙厚级别。

    共享墙两面只在垂直于墙的方向相差约一个墙厚（典型 20uu），其余轴完全一致。
    """
    if (a[3], a[4], a[5]) != (b[3], b[4], b[5]):  # 尺寸不同，不是同一道墙
        return False
    if abs((a[6] - b[6] + 180.0) % 360.0 - 180.0) > 0.1:  # 朝向不同，不是共享墙
        return False
    dx, dy, dz = abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2])
    if dz > 0.1:  # 高度必须一致
        return False
    # 沿墙方向必须重合，垂直方向允许约一个墙厚（<=40uu）的偏移
    near = sorted((dx, dy))
    return near[0] <= 0.1 and near[1] <= 40.0


def _validate(spec: LayoutSpec, manifest: Manifest) -> None:
    if spec.structure_mode not in _STRUCTURE_MODES:
        modes = "、".join(sorted(_STRUCTURE_MODES))
        raise LayoutError(f"structure_mode 只支持 {modes}，收到：{spec.structure_mode}")
    if not spec.rooms:
        raise LayoutError("布局至少要有一个房间")
    names = [room.name for room in spec.rooms]
    if len(names) != len(set(names)):
        raise LayoutError("房间 name 必须唯一")
    for room in spec.rooms:
        if spec.structure_mode == "slab" and room.level != 0:
            raise LayoutError(
                f"默认 slab 模式只支持 room.level=0；房间 {room.name} 的 level={room.level}。"
                '如需旧多层 room，请显式设置 structure_mode="modular"'
            )
        _x, _y, w, d = room.rect
        if w < 2 or d < 2:
            raise LayoutError(f"房间 {room.name} 太小（{w}x{d}），至少 2x2 格")
        openings_by_wall: dict[str, list[tuple[int, int, str]]] = {wall: [] for wall in WALLS}
        for label, openings in (("门", room.doors), ("窗", room.windows)):
            for opening in openings:
                if opening.wall not in WALLS:
                    raise LayoutError(f"房间 {room.name} 的{label}朝向非法：{opening.wall}")
                length = w if opening.wall in ("north", "south") else d
                if opening.width < 1 or opening.at < 0 or opening.at + opening.width > length:
                    raise LayoutError(
                        f"房间 {room.name} 的{label}超出墙体范围："
                        f"at={opening.at} width={opening.width}（墙长 {length} 格）"
                    )
                openings_by_wall[opening.wall].append(
                    (opening.at, opening.at + opening.width, label)
                )
        for wall, spans in openings_by_wall.items():
            cursor = 0
            for start, end, _label in sorted(spans):
                if start < cursor:
                    labels = {label for _s, _e, label in spans}
                    word = "门洞" if labels == {"门"} else "开口"
                    raise LayoutError(f"房间 {room.name} 的 {wall} 墙{word}重叠")
                cursor = end
    _validate_stairs(spec, manifest)
    for i, a in enumerate(spec.rooms):
        for b in spec.rooms[i + 1 :]:
            if a.level == b.level and _interiors_overlap(a.rect, b.rect):
                raise LayoutError(f"房间 {a.name} 与 {b.name} 内部重叠")
    _validate_props(spec, manifest)
    _validate_connectivity(spec)


def _validate_stairs(spec: LayoutSpec, manifest: Manifest) -> None:
    for stair in spec.stairs:
        room = _room_by_name(spec, stair.room)
        if abs(stair.to_level - stair.from_level) != 1:
            raise LayoutError("楼梯只允许连接相邻楼层")
        if room.level != stair.from_level:
            raise LayoutError(f"楼梯 {stair.room} 的 from_level 必须等于所在房间 level")
        asset = _stair_asset(manifest, stair)
        expected_height = abs(stair.to_level - stair.from_level) * spec.level_height
        if abs(asset.size[2] - expected_height) > 1.0:
            raise LayoutError(
                f"楼梯 {asset.key} 高度 {asset.size[2]:.0f}uu 与层高差 "
                f"{expected_height:.0f}uu 不匹配"
            )
        rotation = _rotation_from_facing(stair.facing)
        cells = _asset_cells(room, stair.at, asset, rotation)
        for _level, wx, wy in cells:
            if not _room_contains_world_cell(room, wx, wy):
                raise LayoutError(f"房间 {room.name} 的楼梯井越界")
        if cells & _door_to_door_route_cells(room, through_only=True):
            raise LayoutError(f"房间 {room.name} 的楼梯 {asset.key} 堵门到门路线")
        if _native_piece_crosses_wall(spec, room, stair.at, asset, rotation, manifest.grid):
            raise LayoutError(f"房间 {room.name} 的楼梯 {asset.key} 穿墙")
        if spec.structure_mode == "modular" and _stair_target_room(spec, stair) is None:
            raise LayoutError(f"楼梯 {room.name} 的上端没有可衔接房间")


def _validate_props(spec: LayoutSpec, manifest: Manifest) -> None:
    occupied = _reserved_cells_for_stairs(spec, manifest)
    route_cells = _gameplay_route_cells(spec)
    prop_cells: set[tuple[int, int, int]] = set()
    for room in spec.rooms:
        for prop in room.props:
            try:
                asset = _prop_asset(manifest, prop)
                rotation = (0.0, prop.rotation, 0.0)
                cells = _prop_cells(room, prop, asset, rotation)
                reason = _prop_block_reason(room, cells, occupied | prop_cells, route_cells)
                if not reason and _native_piece_crosses_wall(
                    spec, room, prop.at, asset, rotation, manifest.grid
                ):
                    reason = "穿墙"
            except (KeyError, LayoutError) as exc:
                if prop.optional:
                    continue
                raise LayoutError(f"房间 {room.name} 的道具放置失败：{exc}") from exc
            if reason:
                if prop.optional:
                    continue
                raise LayoutError(f"房间 {room.name} 的道具 {prop.key or prop.category} {reason}")
            prop_cells |= cells


def _native_piece_crosses_wall(
    spec: LayoutSpec,
    room: Room,
    local: tuple[int, int],
    asset: AssetDef,
    rotation: tuple[float, float, float],
    grid: float,
) -> bool:
    """原生尺寸件的目标 AABB 不能侵入外墙厚度占用的边界带。"""
    tmin = _local_tmin(spec, room, local, room.level, grid)
    tsize = _native_target_size(asset, rotation)
    x0 = spec.origin[0] + room.rect[0] * grid
    y0 = spec.origin[1] + room.rect[1] * grid
    x1 = spec.origin[0] + (room.rect[0] + room.rect[2]) * grid
    y1 = spec.origin[1] + (room.rect[1] + room.rect[3]) * grid
    return (
        tmin[0] < x0 + spec.wall_thickness
        or tmin[1] < y0 + spec.wall_thickness
        or tmin[0] + tsize[0] > x1 - spec.wall_thickness
        or tmin[1] + tsize[1] > y1 - spec.wall_thickness
    )


def _gameplay_route_cells(spec: LayoutSpec) -> set[tuple[int, int, int]]:
    if spec.gameplay is None:
        return set()
    spawn_points = _gameplay_spawn_points(spec)
    route_points = _gameplay_routes(spec, spawn_points)
    return _route_cells(route_points)


def _validate_connectivity(spec: LayoutSpec) -> None:
    """门图连通性：多房间布局必须通过对齐的门洞连成一体（设计 §6.3 校验环）。"""
    if len(spec.rooms) < 2:
        return
    adjacency = _room_graph(spec)
    names = list(adjacency)
    visited = {names[0]}
    queue = [names[0]]
    while queue:
        for neighbor in adjacency[queue.pop()]:
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
    orphans = [n for n in names if n not in visited]
    if orphans:
        raise LayoutError(
            f"房间不连通：{('、'.join(orphans))} 无法从 {names[0]} 到达——"
            "相邻房间需要在共享墙的同一位置各开一个对齐的门"
        )


def _door_world_segments(room: Room) -> list[tuple[str, int, int, int]]:
    """门洞的世界格区间：(边轴, 边坐标, 起, 止)，用于判断两房间的门是否对穿。"""
    return [_door_world_segment(room, door) for door in room.doors]


def _door_world_segment(room: Room, door: Door) -> tuple[str, int, int, int]:
    """单个门洞的世界格区间。"""
    x, y, w, d = room.rect
    if door.wall == "south":
        return ("y", y, x + door.at, x + door.at + door.width)
    if door.wall == "north":
        return ("y", y + d, x + door.at, x + door.at + door.width)
    if door.wall == "west":
        return ("x", x, y + door.at, y + door.at + door.width)
    return ("x", x + w, y + door.at, y + door.at + door.width)


def _interiors_overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    ax, ay, aw, ad = a
    bx, by, bw, bd = b
    return ax < bx + bw and bx < ax + aw and ay < by + bd and by < ay + ad


def _compile_room(
    room: Room, spec: LayoutSpec, floor_asset: AssetDef, wall_asset: AssetDef, g: float
) -> list[Placement]:
    x, y, w, d = room.rect
    h, t = spec.wall_height, spec.wall_thickness
    ox, oy, oz = spec.origin
    base_z = oz + room.level * spec.level_height
    out = [
        _fit_placement(  # 地板：顶面在 z=0，向下 _FLOOR_THICKNESS
            f"{room.name}_floor",
            floor_asset,
            tmin=(ox + x * g, oy + y * g, base_z - _FLOOR_THICKNESS),
            tsize=(w * g, d * g, _FLOOR_THICKNESS),
            kind="floor",
            metadata={"room": room.name},
        )
    ]
    openings_by_wall: dict[str, list[tuple[int, int]]] = {wall: [] for wall in WALLS}
    for door in room.doors:
        openings_by_wall[door.wall].append((door.at, door.at + door.width))
    for window in room.windows:
        openings_by_wall[window.wall].append((window.at, window.at + window.width))

    def add_wall(wall: str, length: int) -> None:
        for index, (s, e) in enumerate(_segments(length, openings_by_wall[wall], room.name, wall)):
            run = (e - s) * g
            if wall == "south":
                tmin = (ox + (x + s) * g, oy + y * g, base_z)
                tsize = (run, t, h)
            elif wall == "north":
                tmin = (ox + (x + s) * g, oy + (y + d) * g - t, base_z)
                tsize = (run, t, h)
            elif wall == "west":
                tmin = (ox + x * g, oy + (y + s) * g, base_z)
                tsize = (t, run, h)
            else:  # east
                tmin = (ox + (x + w) * g - t, oy + (y + s) * g, base_z)
                tsize = (t, run, h)
            out.append(
                _fit_placement(
                    f"{room.name}_{wall}_{index}",
                    wall_asset,
                    tmin,
                    tsize,
                    kind="wall",
                    metadata={"room": room.name},
                )
            )

    add_wall("south", w)
    add_wall("north", w)
    add_wall("west", d)
    add_wall("east", d)
    return out


def _segments(
    length: int, openings: list[tuple[int, int]], room: str, wall: str
) -> list[tuple[int, int]]:
    """墙长按门/窗开口切分为实体段；开口重叠即报错。"""
    cursor = 0
    segments: list[tuple[int, int]] = []
    for start, end in sorted(openings):
        if start < cursor:
            raise LayoutError(f"房间 {room} 的 {wall} 墙开口重叠")
        if start > cursor:
            segments.append((cursor, start))
        cursor = end
    if cursor < length:
        segments.append((cursor, length))
    return segments
