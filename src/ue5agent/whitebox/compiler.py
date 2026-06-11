"""白盒布局 DSL 与编译器（ADR-0004：模型出拓扑，确定性程序出坐标）。

v1 范围：矩形房间 + 四向墙 + 门洞。墙体/地板全部用 cube 缩放实现，
单位为格（grid uu），编译输出世界坐标放置指令，spawn 前完成程序化校验。
坐标约定：rect=(x, y, 宽, 深) 格；墙在房间内侧；门洞 v1 为全高开口。
相邻房间共享边时，两侧各有一面薄墙——共用门洞需两个房间各开一个对齐的门。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ue5agent.whitebox.manifest import Manifest

WALLS = ("north", "south", "east", "west")


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
        origin = tuple(float(v) for v in data.get("origin", (0, 0, 0)))
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
    cube = manifest.require("cube")
    g = manifest.grid
    placements: list[Placement] = []
    for room in spec.rooms:
        placements += _compile_room(room, spec, cube.path, g)
    return placements


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


def _compile_room(room: Room, spec: LayoutSpec, cube: str, g: float) -> list[Placement]:
    x, y, w, d = room.rect
    h, t = spec.wall_height, spec.wall_thickness
    ox, oy, oz = spec.origin
    out = [
        Placement(  # 地板：顶面在 z=0，厚 20uu
            name=f"{room.name}_floor",
            asset_path=cube,
            location=(ox + (x + w / 2) * g, oy + (y + d / 2) * g, oz - 10),
            scale=(w * g / 100, d * g / 100, 0.2),
        )
    ]
    doors_by_wall: dict[str, list[tuple[int, int]]] = {wall: [] for wall in WALLS}
    for door in room.doors:
        doors_by_wall[door.wall].append((door.at, door.at + door.width))

    def add_wall(wall: str, length: int) -> None:
        for index, (s, e) in enumerate(_segments(length, doors_by_wall[wall], room.name, wall)):
            run = (e - s) * g
            mid = (s + e) / 2 * g
            if wall == "south":
                loc, scale = (ox + (x * g) + mid, oy + y * g + t / 2), (run / 100, t / 100)
            elif wall == "north":
                loc, scale = (ox + (x * g) + mid, oy + (y + d) * g - t / 2), (run / 100, t / 100)
            elif wall == "west":
                loc, scale = (ox + x * g + t / 2, oy + (y * g) + mid), (t / 100, run / 100)
            else:  # east
                loc, scale = (ox + (x + w) * g - t / 2, oy + (y * g) + mid), (t / 100, run / 100)
            out.append(
                Placement(
                    name=f"{room.name}_{wall}_{index}",
                    asset_path=cube,
                    location=(loc[0], loc[1], oz + h / 2),
                    scale=(scale[0], scale[1], h / 100),
                )
            )

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
