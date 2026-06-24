"""显式 walls DSL 的墙图拓扑检查。

本模块只做 compiler 级几何检查，不依赖 UE、不依赖视觉模型。输入是整数格墙段，
输出墙图 metrics 与可阻断的硬问题，用于在视觉审查前发现断角、孤立墙等确定性错误。
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

from ue5agent.whitebox.compiler import WallSegment

Point = tuple[int, int]
Edge = tuple[Point, Point]


@dataclass(frozen=True)
class _Segment:
    index: int
    name: str
    start: Point
    end: Point

    @property
    def horizontal(self) -> bool:
        return self.start[1] == self.end[1]

    @property
    def vertical(self) -> bool:
        return self.start[0] == self.end[0]


@dataclass
class WallTopologyReport:
    ok: bool
    wall_count: int
    node_count: int
    edge_count: int
    component_count: int
    closed_loop_count: int
    dangling_endpoint_count: int
    near_miss_count: int
    isolated_segment_count: int
    overlap_count: int
    t_junction_count: int
    cross_junction_count: int
    issues: list[dict[str, Any]] = field(default_factory=list)

    def facts(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "wall_count": self.wall_count,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "component_count": self.component_count,
            "closed_loop_count": self.closed_loop_count,
            "dangling_endpoint_count": self.dangling_endpoint_count,
            "near_miss_count": self.near_miss_count,
            "isolated_segment_count": self.isolated_segment_count,
            "overlap_count": self.overlap_count,
            "t_junction_count": self.t_junction_count,
            "cross_junction_count": self.cross_junction_count,
            "issues": self.issues[:8],
        }


def analyze_wall_topology(
    walls: list[WallSegment], *, near_miss_tolerance: int = 1
) -> WallTopologyReport:
    """把显式墙段拆成图并检查断角/孤立段等拓扑问题。"""
    segments = [_Segment(i, wall.name, wall.start, wall.end) for i, wall in enumerate(walls)]
    points_by_segment: dict[int, set[Point]] = {
        segment.index: {segment.start, segment.end} for segment in segments
    }
    issues: list[dict[str, Any]] = []
    overlap_count = 0

    for left_index, left in enumerate(segments):
        for right in segments[left_index + 1 :]:
            intersection = _axis_aligned_intersection(left, right)
            if intersection is not None:
                points_by_segment[left.index].add(intersection)
                points_by_segment[right.index].add(intersection)
                continue
            if _collinear_overlap(left, right):
                overlap_count += 1
                issues.append(
                    {
                        "code": "overlapping_wall",
                        "severity": "high",
                        "message": f"墙段 {left.name} 与 {right.name} 共线重叠",
                    }
                )

    adjacency: dict[Point, set[Point]] = defaultdict(set)
    edges: set[Edge] = set()
    for segment in segments:
        ordered = sorted(
            points_by_segment[segment.index],
            key=(lambda point: point[0]) if segment.horizontal else (lambda point: point[1]),
        )
        for start, end in pairwise(ordered):
            if start == end:
                continue
            edge = _edge(start, end)
            edges.add(edge)
            adjacency[start].add(end)
            adjacency[end].add(start)

    components = _components(adjacency)
    component_by_node = {
        node: component_index
        for component_index, component in enumerate(components)
        for node in component
    }
    edge_counts_by_component: dict[int, int] = defaultdict(int)
    for start, _end in edges:
        edge_counts_by_component[component_by_node[start]] += 1

    isolated_segment_count = 0
    if len(components) > 1:
        for component_index in range(len(components)):
            if edge_counts_by_component[component_index] == 1:
                isolated_segment_count += 1
        for component_index, component in enumerate(components):
            if edge_counts_by_component[component_index] != 1:
                continue
            node_names = sorted(component)
            issues.append(
                {
                    "code": "isolated_segment",
                    "severity": "high",
                    "message": f"存在孤立墙段组件：{node_names}",
                }
            )

    near_miss_count = _append_near_miss_issues(
        segments,
        issues,
        tolerance=max(0, near_miss_tolerance),
    )

    node_count = len(adjacency)
    edge_count = len(edges)
    component_count = len(components)
    closed_loop_count = max(edge_count - node_count + component_count, 0)
    degrees = [len(neighbors) for neighbors in adjacency.values()]
    ok = not any(issue.get("severity") == "high" for issue in issues)
    return WallTopologyReport(
        ok=ok,
        wall_count=len(walls),
        node_count=node_count,
        edge_count=edge_count,
        component_count=component_count,
        closed_loop_count=closed_loop_count,
        dangling_endpoint_count=sum(1 for degree in degrees if degree == 1),
        near_miss_count=near_miss_count,
        isolated_segment_count=isolated_segment_count,
        overlap_count=overlap_count,
        t_junction_count=sum(1 for degree in degrees if degree == 3),
        cross_junction_count=sum(1 for degree in degrees if degree >= 4),
        issues=issues,
    )


def _axis_aligned_intersection(left: _Segment, right: _Segment) -> Point | None:
    if left.horizontal and right.vertical:
        h, v = left, right
    elif left.vertical and right.horizontal:
        h, v = right, left
    else:
        return None
    hx0, hx1 = sorted((h.start[0], h.end[0]))
    vy0, vy1 = sorted((v.start[1], v.end[1]))
    x = v.start[0]
    y = h.start[1]
    if hx0 <= x <= hx1 and vy0 <= y <= vy1:
        return (x, y)
    return None


def _collinear_overlap(left: _Segment, right: _Segment) -> bool:
    if left.horizontal and right.horizontal and left.start[1] == right.start[1]:
        return (
            _interval_overlap_length((left.start[0], left.end[0]), (right.start[0], right.end[0]))
            > 0
        )
    if left.vertical and right.vertical and left.start[0] == right.start[0]:
        return (
            _interval_overlap_length((left.start[1], left.end[1]), (right.start[1], right.end[1]))
            > 0
        )
    return False


def _interval_overlap_length(left: tuple[int, int], right: tuple[int, int]) -> int:
    left_lo, left_hi = sorted(left)
    right_lo, right_hi = sorted(right)
    return min(left_hi, right_hi) - max(left_lo, right_lo)


def _edge(start: Point, end: Point) -> Edge:
    return (start, end) if start <= end else (end, start)


def _components(adjacency: dict[Point, set[Point]]) -> list[set[Point]]:
    unseen = set(adjacency)
    components: list[set[Point]] = []
    while unseen:
        first = unseen.pop()
        component = {first}
        queue: deque[Point] = deque([first])
        while queue:
            current = queue.popleft()
            for neighbor in adjacency[current]:
                if neighbor not in unseen:
                    continue
                unseen.remove(neighbor)
                component.add(neighbor)
                queue.append(neighbor)
        components.append(component)
    return components


def _append_near_miss_issues(
    segments: list[_Segment], issues: list[dict[str, Any]], *, tolerance: int
) -> int:
    if tolerance <= 0:
        return 0
    seen: set[tuple[Point, Point, int, int]] = set()
    for segment in segments:
        for endpoint in (segment.start, segment.end):
            for other in segments:
                if other.index == segment.index:
                    continue
                if _same_axis(segment, other):
                    continue
                distance, closest = _point_segment_distance(endpoint, other)
                if distance == 0 or distance > tolerance:
                    continue
                point_a, point_b = sorted((endpoint, closest))
                segment_a, segment_b = sorted((segment.index, other.index))
                key = (point_a, point_b, segment_a, segment_b)
                if key in seen:
                    continue
                seen.add(key)
                issues.append(
                    {
                        "code": "near_miss_endpoint",
                        "severity": "high",
                        "message": (
                            f"墙段 {segment.name} 端点 {endpoint} 距墙段 {other.name} "
                            f"仅 {distance} 格但未连接"
                        ),
                    }
                )
    return len(seen)


def _same_axis(left: _Segment, right: _Segment) -> bool:
    return (left.horizontal and right.horizontal) or (left.vertical and right.vertical)


def _point_segment_distance(point: Point, segment: _Segment) -> tuple[int, Point]:
    px, py = point
    if segment.horizontal:
        x0, x1 = sorted((segment.start[0], segment.end[0]))
        closest_x = min(max(px, x0), x1)
        closest = (closest_x, segment.start[1])
        return abs(py - closest[1]) + abs(px - closest[0]), closest
    if segment.vertical:
        y0, y1 = sorted((segment.start[1], segment.end[1]))
        closest_y = min(max(py, y0), y1)
        closest = (segment.start[0], closest_y)
        return abs(px - closest[0]) + abs(py - closest[1]), closest
    return 1_000_000_000, point


__all__ = ["WallTopologyReport", "analyze_wall_topology"]
