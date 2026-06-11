"""白盒编译器：几何精确性、门洞切分、布局校验。"""

from pathlib import Path

import pytest

from ue5agent.whitebox.compiler import (
    Door,
    LayoutError,
    LayoutSpec,
    Placement,
    Room,
    compile_layout,
)
from ue5agent.whitebox.manifest import load_manifest

MANIFEST = load_manifest(
    Path(__file__).parent.parent / "config" / "whitebox" / "levelprototyping.yaml"
)


def by_name(placements: list[Placement], name: str) -> Placement:
    return next(p for p in placements if p.name == name)


class TestGeometry:
    def test_single_room_counts_and_floor(self):
        spec = LayoutSpec(name="t", rooms=[Room(name="a", rect=(0, 0, 4, 3))])
        placements = compile_layout(spec, MANIFEST)
        # 地板 1 + 四面墙各 1 段
        assert len(placements) == 5
        floor = by_name(placements, "a_floor")
        assert floor.location == (200.0, 150.0, -10.0)
        assert floor.scale == (4.0, 3.0, 0.2)

    def test_door_splits_wall_into_segments(self):
        spec = LayoutSpec(
            name="t",
            rooms=[Room(name="a", rect=(0, 0, 4, 3), doors=[Door(wall="south", at=1, width=2)])],
        )
        placements = compile_layout(spec, MANIFEST)
        assert len(placements) == 6  # 南墙裂成 2 段
        seg0 = by_name(placements, "a_south_0")
        seg1 = by_name(placements, "a_south_1")
        # 段0：格 0..1，段1：格 3..4
        assert seg0.location[0] == 50.0 and seg0.scale[0] == 1.0
        assert seg1.location[0] == 350.0 and seg1.scale[0] == 1.0
        assert seg0.location[2] == 150.0  # 墙心高 = wall_height/2

    def test_door_at_wall_start_no_zero_segment(self):
        spec = LayoutSpec(
            name="t",
            rooms=[Room(name="a", rect=(0, 0, 4, 3), doors=[Door(wall="west", at=0, width=3)])],
        )
        placements = compile_layout(spec, MANIFEST)
        # 西墙整面是门 → 0 段
        assert not [p for p in placements if "_west_" in p.name]

    def test_origin_offset(self):
        spec = LayoutSpec(
            name="t", rooms=[Room(name="a", rect=(0, 0, 2, 2))], origin=(5000, 6000, 0)
        )
        floor = by_name(compile_layout(spec, MANIFEST), "a_floor")
        assert floor.location[:2] == (5100.0, 6100.0)


class TestValidation:
    def test_overlapping_rooms_rejected(self):
        spec = LayoutSpec(
            name="t",
            rooms=[Room(name="a", rect=(0, 0, 4, 4)), Room(name="b", rect=(3, 3, 4, 4))],
        )
        with pytest.raises(LayoutError, match="重叠"):
            compile_layout(spec, MANIFEST)

    def test_touching_rooms_allowed(self):
        spec = LayoutSpec(
            name="t",
            rooms=[Room(name="a", rect=(0, 0, 4, 4)), Room(name="b", rect=(4, 0, 4, 4))],
        )
        assert compile_layout(spec, MANIFEST)

    def test_door_out_of_range_rejected(self):
        spec = LayoutSpec(
            name="t",
            rooms=[Room(name="a", rect=(0, 0, 4, 3), doors=[Door(wall="south", at=3, width=2)])],
        )
        with pytest.raises(LayoutError, match="超出墙体"):
            compile_layout(spec, MANIFEST)

    def test_overlapping_doors_rejected(self):
        spec = LayoutSpec(
            name="t",
            rooms=[
                Room(
                    name="a",
                    rect=(0, 0, 6, 3),
                    doors=[Door(wall="south", at=1, width=2), Door(wall="south", at=2, width=2)],
                )
            ],
        )
        with pytest.raises(LayoutError, match="门洞重叠"):
            compile_layout(spec, MANIFEST)

    def test_tiny_room_rejected(self):
        spec = LayoutSpec(name="t", rooms=[Room(name="a", rect=(0, 0, 1, 5))])
        with pytest.raises(LayoutError, match="太小"):
            compile_layout(spec, MANIFEST)
