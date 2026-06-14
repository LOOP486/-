"""manifest v2：loader 向后兼容、角色解析、pivot/footprint 解析，以及编译器 fit 补偿。"""

from pathlib import Path

import pytest

from ue5agent.whitebox.compiler import LayoutSpec, Room, compile_layout
from ue5agent.whitebox.manifest import AssetDef, Manifest, load_manifest

_CONFIG = Path(__file__).parent.parent / "config" / "whitebox"
V1 = load_manifest(_CONFIG / "levelprototyping.yaml")  # 老清单（cube 兜底，无 roles）
# 编译器/loader 单测用冻结的 ArchKit 样例清单，而非随用户重扫而变的 config/whitebox/kit.yaml。
KIT = load_manifest(Path(__file__).parent / "data" / "kit_archkit_sample.yaml")


def by_name(placements, name):
    return next(p for p in placements if p.name == name)


class TestLoaderBackCompat:
    def test_v1_defaults(self):
        """v1 清单无 version/roles/pivot/footprint → loader 全给默认值。"""
        assert V1.version == 1
        assert V1.roles == {}
        cube = V1.require("cube")
        assert cube.pivot == (0.5, 0.5, 0.5)  # cube 等价默认
        assert cube.footprint == (1, 1)
        assert cube.tags == ()
        assert cube.needs_review is False

    def test_v2_fields_parsed(self):
        assert KIT.version == 2
        assert KIT.roles["floor"] == "floor_n"
        assert KIT.roles["wall"] == "wall1_4"
        wall = KIT.require("wall8_4")
        assert wall.size == (800.0, 20.0, 400.0)
        assert wall.pivot == (0.0, 1.0, 0.0)  # 反推：原点在 X 左端/+Y 面/底
        assert wall.snap_box == ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
        assert wall.footprint == (8, 1)
        assert "structure" in wall.tags
        assert wall.source_fbx.endswith("Wall8_4.fbx")

    def test_needs_review_flag(self):
        assert KIT.require("irongrilledoor").needs_review is True
        assert KIT.require("wall8_4").needs_review is False

    def test_default_archkit_path_uses_ue_calibrated_bounds(self):
        for key, pivot in {
            "floor_n": (0.0, 0.0, 0.0),
            "floor2_2": (0.0, 0.0, 0.0),
            "floor4_4": (0.0, 0.0, 0.0),
            "wall1_4": (0.0, 0.0, 0.0),
            "stair_2": (0.0, 1.0, 0.0),
        }.items():
            asset = KIT.require(key)
            assert asset.calibrated is True
            assert asset.pivot == pivot
            assert asset.local_bounds_min is not None
            assert asset.local_bounds_max is not None

    def test_ue_calibrated_bounds_fields_parsed(self, tmp_path):
        manifest_path = tmp_path / "kit.yaml"
        manifest_path.write_text(
            """
version: 2
grid: 100
assets:
  wall:
    path: /Game/Kit/Wall
    size: [100, 20, 400]
    category: wall
    pivot: [0, 1, 0]
    local_bounds_min: [0, 0, 0]
    local_bounds_max: [100, 20, 400]
    calibrated: true
""",
            encoding="utf-8",
        )

        wall = load_manifest(manifest_path).require("wall")

        assert wall.local_bounds_min == (0.0, 0.0, 0.0)
        assert wall.local_bounds_max == (100.0, 20.0, 400.0)
        assert wall.calibrated is True


class TestAssetForRole:
    def test_role_mapping(self):
        assert KIT.asset_for_role("floor").key == "floor_n"
        assert KIT.asset_for_role("wall").key == "wall1_4"

    def test_fallback_to_cube_when_role_absent(self):
        """v1 清单无 roles，按角色取件应回退到 cube。"""
        assert V1.asset_for_role("floor").key == "cube"
        assert V1.asset_for_role("wall").key == "cube"

    def test_missing_role_and_no_fallback_raises(self):
        m = Manifest(
            grid=100,
            assets={"only": AssetDef("only", "/X", (100, 100, 100), "prop")},
            version=2,
            roles={},
        )
        with pytest.raises(KeyError, match="未配置角色"):
            m.asset_for_role("floor")


class TestCubeEquivalence:
    """经新 fit 路径，cube 兜底产出的坐标必须与升级前逐字节一致。"""

    def test_floor_unchanged(self):
        spec = LayoutSpec(name="t", rooms=[Room(name="a", rect=(0, 0, 4, 3))])
        floor = by_name(compile_layout(spec, V1), "a_floor")
        assert floor.location == (200.0, 150.0, -10.0)
        assert floor.scale == (4.0, 3.0, 0.2)

    def test_wall_unchanged(self):
        spec = LayoutSpec(name="t", rooms=[Room(name="a", rect=(0, 0, 4, 3))])
        south = by_name(compile_layout(spec, V1), "a_south_0")
        assert south.location == (200.0, 10.0, 150.0)  # 中心放置：x心200/y心10/高心150
        assert south.scale == (4.0, 0.2, 3.0)


class TestPivotCompensation:
    """非中心 pivot 的真实资产经 fit 补偿后落到正确的世界 AABB。"""

    def _kit(self):
        floor = AssetDef("f", "/F", (100, 100, 20), "floor", pivot=(0.5, 0.5, 0.0))
        wall = AssetDef("w", "/W", (100, 20, 400), "wall", pivot=(0.0, 1.0, 0.0))
        return Manifest(
            grid=100,
            assets={"f": floor, "w": wall},
            version=2,
            roles={"floor": "f", "wall": "w"},
        )

    def test_floor_bottom_pivot_lands_on_ground(self):
        spec = LayoutSpec(
            name="t",
            structure_mode="modular",
            rooms=[Room(name="a", rect=(0, 0, 4, 3))],
        )
        floor = by_name(compile_layout(spec, self._kit()), "a_floor")
        # 底面 pivot(z=0)：location.z=tmin.z=-20，AABB -20..0，顶面贴地 z=0
        assert floor.location == (200.0, 150.0, -20.0)
        assert floor.scale == (4.0, 3.0, 1.0)  # 20uu 厚 / 资产基准 20 = 1.0

    def test_wall_corner_pivot_fills_target_aabb(self):
        spec = LayoutSpec(
            name="t",
            structure_mode="modular",
            rooms=[Room(name="a", rect=(0, 0, 4, 3))],
        )
        placements = compile_layout(spec, self._kit())
        south = by_name(placements, "a_south_0")
        # 南墙目标 AABB: x0..400 / y0..20 / z0..300；pivot=(0,1,0) → 原点在 x左/+Y面/底
        assert south.location == (0.0, 20.0, 0.0)
        assert south.scale == (4.0, 1.0, 0.75)
        north = by_name(placements, "a_north_0")
        # 北墙内侧贴 y=300：AABB y280..300，pivot.y=1 → location.y=300
        assert north.location == (0.0, 300.0, 0.0)

    def test_snap_box_aligns_structure_core_instead_of_full_visual_bounds(self):
        floor = AssetDef("f", "/F", (100, 100, 20), "floor", pivot=(0.5, 0.5, 0.0))
        wall = AssetDef(
            "w",
            "/W",
            (120, 40, 400),
            "wall",
            pivot=(0.5, 0.5, 0.0),
            snap_box=((10 / 120, 10 / 40, 0.0), (110 / 120, 30 / 40, 1.0)),
        )
        door = AssetDef("d", "/D", (100, 20, 400), "wall_door")
        manifest = Manifest(
            grid=100,
            assets={"f": floor, "w": wall, "d": door},
            version=2,
            roles={"floor": "f", "wall": "w", "wall_door": "d"},
        )

        spec = LayoutSpec(
            name="t",
            structure_mode="modular",
            wall_height=400,
            rooms=[Room(name="a", rect=(0, 0, 4, 3))],
        )
        south = by_name(compile_layout(spec, manifest), "a_south_0_0")

        assert south.target_min == (0.0, 0.0, 0.0)
        assert south.target_size == (400.0, 20.0, 400.0)
        assert south.scale == (4.0, 1.0, 1.0)
        assert south.location == (200.0, 10.0, 0.0)
