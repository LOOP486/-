"""显式 walls DSL 的 compiler 级墙图拓扑检查。"""

from __future__ import annotations

from ue5agent.whitebox.compiler import WallSegment
from ue5agent.whitebox.wall_topology import analyze_wall_topology


def test_wall_topology_accepts_closed_rectangle():
    report = analyze_wall_topology(
        [
            WallSegment("south", (0, 0), (6, 0)),
            WallSegment("east", (6, 0), (6, 4)),
            WallSegment("north", (6, 4), (0, 4)),
            WallSegment("west", (0, 4), (0, 0)),
        ]
    )

    assert report.ok is True
    assert report.component_count == 1
    assert report.closed_loop_count == 1
    assert report.dangling_endpoint_count == 0
    assert report.near_miss_count == 0


def test_wall_topology_flags_near_miss_corner():
    report = analyze_wall_topology(
        [
            WallSegment("horizontal", (0, 0), (6, 0)),
            WallSegment("vertical", (6, 1), (6, 5)),
        ],
        near_miss_tolerance=1,
    )

    assert report.ok is False
    assert report.near_miss_count == 1
    assert any(issue["code"] == "near_miss_endpoint" for issue in report.issues)


def test_wall_topology_flags_isolated_segment_next_to_main_group():
    report = analyze_wall_topology(
        [
            WallSegment("south", (0, 0), (6, 0)),
            WallSegment("east", (6, 0), (6, 4)),
            WallSegment("north", (6, 4), (0, 4)),
            WallSegment("west", (0, 4), (0, 0)),
            WallSegment("loose", (10, 10), (11, 10)),
        ]
    )

    assert report.ok is False
    assert report.component_count == 2
    assert report.isolated_segment_count == 1
    assert any(issue["code"] == "isolated_segment" for issue in report.issues)
