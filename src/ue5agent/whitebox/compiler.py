"""白盒布局 DSL 与编译器（ADR-0004：模型出拓扑，确定性程序出坐标）。

v1 范围：矩形房间 + 四向墙 + 门洞。墙体/地板全部用 cube 缩放实现，
单位为格（grid uu），编译输出世界坐标放置指令，spawn 前完成程序化校验。
坐标约定：rect=(x, y, 宽, 深) 格；墙在房间内侧；门洞 v1 为全高开口。
相邻房间共享边时各自生成一面墙，编译末尾 _dedupe_shared_walls 会去除重合的另一面，
共享边只保留一面（v2）；共用门洞仍需两个房间各开一个对齐的门。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ue5agent.whitebox.manifest import AssetDef, Manifest

WALLS = ("north", "south", "east", "west")

_FLOOR_THICKNESS = 20.0
"""地板板厚（uu）：顶面贴 z=0，向下 20uu。cube 基准 100uu 时即旧逻辑的 scale.z=0.2。"""


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
    doors: list[Door] = field(default_factory=list)


@dataclass
class LayoutSpec:
    name: str
    rooms: list[Room]
    wall_height: float = 300.0
    wall_thickness: float = 20.0
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0)


@dataclass
class Placement:
    name: str
    asset_path: str
    location: tuple[float, float, float]
    scale: tuple[float, float, float]


def layout_from_dict(data: dict) -> LayoutSpec:
    """模型产出的 JSON → LayoutSpec（结构错误转为可读的 LayoutError）。"""
    try:
        rooms = []
        for raw in data["rooms"]:
            rect = tuple(int(v) for v in raw["rect"])
            if len(rect) != 4:
                raise LayoutError(f"房间 {raw.get('name')} 的 rect 必须是 [x, y, 宽, 深]")
            rooms.append(
                Room(
                    name=str(raw["name"]),
                    rect=rect,
                    doors=[
                        Door(wall=str(d["wall"]), at=int(d["at"]), width=int(d.get("width", 1)))
                        for d in raw.get("doors", [])
                    ],
                )
            )
        raw_origin = data.get("origin", (0, 0, 0))
        origin = (float(raw_origin[0]), float(raw_origin[1]), float(raw_origin[2]))
        return LayoutSpec(
            name=str(data.get("name", "layout")),
            rooms=rooms,
            wall_height=float(data.get("wall_height", 300)),
            wall_thickness=float(data.get("wall_thickness", 20)),
            origin=origin,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LayoutError(f"布局 JSON 结构不合法：{exc}") from exc


def compile_layout(spec: LayoutSpec, manifest: Manifest) -> list[Placement]:
    _validate(spec)
    # 按结构角色取件（v1 清单无 roles → 回退 cube，行为不变）。
    floor_asset = manifest.asset_for_role("floor")
    wall_asset = manifest.asset_for_role("wall")
    g = manifest.grid
    placements: list[Placement] = []
    for room in spec.rooms:
        placements += _compile_room(room, spec, floor_asset, wall_asset, g)
    return _dedupe_shared_walls(placements)


def _fit_placement(
    name: str,
    asset: AssetDef,
    tmin: tuple[float, float, float],
    tsize: tuple[float, float, float],
) -> Placement:
    """把资产缩放填满目标世界 AABB（tmin..tmin+tsize），按 pivot 补偿出 UE 落地点。

    UE 把资产原点放在 location；原点在 AABB 内的归一化位置即 pivot，
    故 location = tmin + pivot * tsize，scale = tsize / 资产基准尺寸。
    cube（pivot=[.5,.5,.5]、base=100）代入即还原升级前的"中心放置"逻辑（逐字节一致）。
    """
    base = asset.size
    scale = (tsize[0] / base[0], tsize[1] / base[1], tsize[2] / base[2])
    location = (
        tmin[0] + asset.pivot[0] * tsize[0],
        tmin[1] + asset.pivot[1] * tsize[1],
        tmin[2] + asset.pivot[2] * tsize[2],
    )
    return Placement(name=name, asset_path=asset.path, location=location, scale=scale)


def _dedupe_shared_walls(placements: list[Placement]) -> list[Placement]:
    """去除相邻房间共享墙产生的重叠薄墙（v2：共享边只保留一面）。

    相邻房间各自生成自己的墙，共享边上会出现两面几乎重合的薄墙（仅差墙厚 20uu），
    视觉上像"多放了一块板"。此处按墙的 (location, scale) 近似重合判定，
    重复的只保留第一面、丢弃其余——门洞段因位置/长度不同不会被误删。
    """
    kept: list[Placement] = []
    seen: list[tuple[float, float, float, float, float, float]] = []
    for p in placements:
        if p.name.endswith("_floor"):  # 地板从不参与去重
            kept.append(p)
            continue
        key = (
            round(p.location[0], 1),
            round(p.location[1], 1),
            round(p.location[2], 1),
            round(p.scale[0], 2),
            round(p.scale[1], 2),
            round(p.scale[2], 2),
        )
        if any(_walls_coincide(key, s) for s in seen):
            continue  # 与已保留的某面墙重合 → 这是共享墙的另一面，丢弃
        seen.append(key)
        kept.append(p)
    return kept


def _walls_coincide(
    a: tuple[float, float, float, float, float, float],
    b: tuple[float, float, float, float, float, float],
) -> bool:
    """两面墙是否在共享边上几乎重合：同朝向、同尺寸，且中心距 <= 一个墙厚级别。

    共享墙两面只在垂直于墙的方向相差约一个墙厚（典型 20uu），其余轴完全一致。
    """
    if (a[3], a[4], a[5]) != (b[3], b[4], b[5]):  # 尺寸/朝向不同，不是同一道墙
        return False
    dx, dy, dz = abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2])
    if dz > 0.1:  # 高度必须一致
        return False
    # 沿墙方向必须重合，垂直方向允许约一个墙厚（<=40uu）的偏移
    near = sorted((dx, dy))
    return near[0] <= 0.1 and near[1] <= 40.0


def _validate(spec: LayoutSpec) -> None:
    if not spec.rooms:
        raise LayoutError("布局至少要有一个房间")
    for room in spec.rooms:
        _x, _y, w, d = room.rect
        if w < 2 or d < 2:
            raise LayoutError(f"房间 {room.name} 太小（{w}x{d}），至少 2x2 格")
        for door in room.doors:
            if door.wall not in WALLS:
                raise LayoutError(f"房间 {room.name} 的门朝向非法：{door.wall}")
            length = w if door.wall in ("north", "south") else d
            if door.width < 1 or door.at < 0 or door.at + door.width > length:
                raise LayoutError(
                    f"房间 {room.name} 的门超出墙体范围：at={door.at} width={door.width}"
                    f"（墙长 {length} 格）"
                )
    for i, a in enumerate(spec.rooms):
        for b in spec.rooms[i + 1 :]:
            if _interiors_overlap(a.rect, b.rect):
                raise LayoutError(f"房间 {a.name} 与 {b.name} 内部重叠")
    _validate_connectivity(spec)


def _validate_connectivity(spec: LayoutSpec) -> None:
    """门图连通性：多房间布局必须通过对齐的门洞连成一体（设计 §6.3 校验环）。"""
    if len(spec.rooms) < 2:
        return
    segments = {room.name: _door_world_segments(room) for room in spec.rooms}
    adjacency: dict[str, set[str]] = {room.name: set() for room in spec.rooms}
    names = list(adjacency)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            for axis_a, coord_a, lo_a, hi_a in segments[a]:
                for axis_b, coord_b, lo_b, hi_b in segments[b]:
                    if axis_a != axis_b or coord_a != coord_b:
                        continue
                    if min(hi_a, hi_b) - max(lo_a, lo_b) >= 1:  # 重叠至少 1 格才走得过去
                        adjacency[a].add(b)
                        adjacency[b].add(a)
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
    x, y, w, d = room.rect
    out = []
    for door in room.doors:
        if door.wall == "south":
            out.append(("y", y, x + door.at, x + door.at + door.width))
        elif door.wall == "north":
            out.append(("y", y + d, x + door.at, x + door.at + door.width))
        elif door.wall == "west":
            out.append(("x", x, y + door.at, y + door.at + door.width))
        else:  # east
            out.append(("x", x + w, y + door.at, y + door.at + door.width))
    return out


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
    out = [
        _fit_placement(  # 地板：顶面在 z=0，向下 _FLOOR_THICKNESS
            f"{room.name}_floor",
            floor_asset,
            tmin=(ox + x * g, oy + y * g, oz - _FLOOR_THICKNESS),
            tsize=(w * g, d * g, _FLOOR_THICKNESS),
        )
    ]
    doors_by_wall: dict[str, list[tuple[int, int]]] = {wall: [] for wall in WALLS}
    for door in room.doors:
        doors_by_wall[door.wall].append((door.at, door.at + door.width))

    def add_wall(wall: str, length: int) -> None:
        for index, (s, e) in enumerate(_segments(length, doors_by_wall[wall], room.name, wall)):
            run = (e - s) * g
            if wall == "south":
                tmin = (ox + (x + s) * g, oy + y * g, oz)
                tsize = (run, t, h)
            elif wall == "north":
                tmin = (ox + (x + s) * g, oy + (y + d) * g - t, oz)
                tsize = (run, t, h)
            elif wall == "west":
                tmin = (ox + x * g, oy + (y + s) * g, oz)
                tsize = (t, run, h)
            else:  # east
                tmin = (ox + (x + w) * g - t, oy + (y + s) * g, oz)
                tsize = (t, run, h)
            out.append(_fit_placement(f"{room.name}_{wall}_{index}", wall_asset, tmin, tsize))

    add_wall("south", w)
    add_wall("north", w)
    add_wall("west", d)
    add_wall("east", d)
    return out


def _segments(
    length: int, doors: list[tuple[int, int]], room: str, wall: str
) -> list[tuple[int, int]]:
    """墙长按门洞切分为实体段；门洞重叠即报错。"""
    cursor = 0
    segments: list[tuple[int, int]] = []
    for start, end in sorted(doors):
        if start < cursor:
            raise LayoutError(f"房间 {room} 的 {wall} 墙门洞重叠")
        if start > cursor:
            segments.append((cursor, start))
        cursor = end
    if cursor < length:
        segments.append((cursor, length))
    return segments
