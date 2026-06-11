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
