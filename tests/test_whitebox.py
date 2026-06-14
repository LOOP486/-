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

    def test_shared_wall_deduped(self):
        """相邻房间共享边只保留一面墙，不再出现重合的双层薄墙（问题2修复）。"""
        spec = LayoutSpec(
            name="t",
            rooms=[
                Room(name="a", rect=(0, 0, 4, 4), doors=[Door(wall="east", at=1, width=2)]),
                Room(name="b", rect=(4, 0, 4, 4), doors=[Door(wall="west", at=1, width=2)]),
            ],
        )
        placements = compile_layout(spec, MANIFEST)
        # 共享边 x=400 处：a 的 east 段与 b 的 west 段几乎重合，去重后不应同时存在
        shared = [
            p
            for p in placements
            if not p.name.endswith("_floor") and abs(p.location[0] - 400.0) <= 40.0
        ]
        # 门洞把共享墙切成上下两段，去重后该边总段数应 <= 2（而非 a、b 各 2 = 4 段）
        assert len(shared) <= 2, f"共享墙未去重，仍有 {len(shared)} 段：{[p.name for p in shared]}"
        assert {round(p.location[0]) for p in shared} == {400}

    def test_shared_wall_axis_stays_centered_across_adjacent_room_pairs(self):
        """同一条共享墙轴线的不同段不能一段留在南侧、一段留在北侧。"""
        spec = LayoutSpec(
            name="t",
            rooms=[
                Room(
                    name="A",
                    rect=(0, 0, 4, 4),
                    doors=[Door(wall="north", at=1, width=1)],
                ),
                Room(
                    name="B",
                    rect=(0, 4, 4, 4),
                    doors=[Door(wall="south", at=1, width=1), Door(wall="east", at=1, width=1)],
                ),
                Room(
                    name="C",
                    rect=(4, 4, 4, 4),
                    doors=[Door(wall="south", at=1, width=1), Door(wall="west", at=1, width=1)],
                ),
                Room(
                    name="D",
                    rect=(4, 0, 4, 4),
                    doors=[Door(wall="north", at=1, width=1)],
                ),
            ],
        )

        internal_y_walls = [
            p
            for p in compile_layout(spec, MANIFEST)
            if p.kind == "wall"
            and p.target_size
            and p.target_size[0] > p.target_size[1]
            and 360 <= p.location[1] <= 440
        ]

        assert internal_y_walls
        assert {round(p.location[1]) for p in internal_y_walls} == {400}


class TestValidation:
    def test_overlapping_rooms_rejected(self):
        spec = LayoutSpec(
            name="t",
            rooms=[Room(name="a", rect=(0, 0, 4, 4)), Room(name="b", rect=(3, 3, 4, 4))],
        )
        with pytest.raises(LayoutError, match="重叠"):
            compile_layout(spec, MANIFEST)

    def test_touching_rooms_with_aligned_doors_allowed(self):
        spec = LayoutSpec(
            name="t",
            rooms=[
                Room(name="a", rect=(0, 0, 4, 4), doors=[Door(wall="east", at=1, width=2)]),
                Room(name="b", rect=(4, 0, 4, 4), doors=[Door(wall="west", at=1, width=2)]),
            ],
        )
        assert compile_layout(spec, MANIFEST)

    def test_disconnected_rooms_rejected(self):
        spec = LayoutSpec(
            name="t",
            rooms=[Room(name="a", rect=(0, 0, 4, 4)), Room(name="b", rect=(4, 0, 4, 4))],
        )
        with pytest.raises(LayoutError, match="不连通"):
            compile_layout(spec, MANIFEST)

    def test_windows_on_shared_walls_are_rejected(self):
        spec = LayoutSpec(
            name="t",
            rooms=[
                Room(
                    name="a",
                    rect=(0, 0, 4, 4),
                    doors=[Door(wall="east", at=2, width=1)],
                    windows=[Door(wall="east", at=0, width=1)],
                ),
                Room(name="b", rect=(4, 0, 4, 4), doors=[Door(wall="west", at=2, width=1)]),
            ],
        )

        with pytest.raises(LayoutError, match=r"窗.*外墙"):
            compile_layout(spec, MANIFEST)

    def test_misaligned_doors_rejected(self):
        spec = LayoutSpec(
            name="t",
            rooms=[
                Room(name="a", rect=(0, 0, 4, 4), doors=[Door(wall="east", at=0, width=1)]),
                Room(name="b", rect=(4, 0, 4, 4), doors=[Door(wall="west", at=3, width=1)]),
            ],
        )
        with pytest.raises(LayoutError, match="不连通"):
            compile_layout(spec, MANIFEST)

    def test_layout_from_dict_roundtrip(self):
        from ue5agent.whitebox.compiler import layout_from_dict

        spec = layout_from_dict(
            {
                "name": "demo",
                "origin": [5000, 5000, 0],
                "rooms": [
                    {"name": "a", "rect": [0, 0, 4, 4], "doors": [{"wall": "east", "at": 1}]},
                    {"name": "b", "rect": [4, 0, 4, 4], "doors": [{"wall": "west", "at": 1}]},
                ],
            }
        )
        assert len(compile_layout(spec, MANIFEST)) > 0

    def test_layout_from_dict_bad_structure(self):
        from ue5agent.whitebox.compiler import layout_from_dict

        with pytest.raises(LayoutError, match="不合法"):
            layout_from_dict({"rooms": [{"name": "a"}]})

    def test_layout_from_dict_parses_realistic_scale_profile(self):
        from ue5agent.whitebox.compiler import layout_from_dict

        spec = layout_from_dict(
            {
                "name": "scale",
                "scale_profile": "realistic",
                "rooms": [{"name": "a", "rect": [0, 0, 4, 4]}],
            }
        )

        assert spec.scale_profile == "realistic"

        with pytest.raises(LayoutError, match="scale_profile"):
            layout_from_dict(
                {
                    "name": "bad-scale",
                    "scale_profile": "combat",
                    "rooms": [{"name": "a", "rect": [0, 0, 4, 4]}],
                }
            )

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
