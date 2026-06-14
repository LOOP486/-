"""ArchKit 结构质感：真实模块件铺排、显式窗、旋转与默认清单。"""

from pathlib import Path

import pytest

import ue5agent.mcp_servers.ue_whitebox.server as wb_server
import ue5agent.whitebox.spawner as spawner
from ue5agent.whitebox.compiler import (
    Door,
    LayoutError,
    LayoutSpec,
    Placement,
    Room,
    compile_layout,
    layout_from_dict,
)
from ue5agent.whitebox.manifest import load_manifest
from ue5agent.whitebox.validator import ActorView, validate_layout

_CONFIG = Path(__file__).parent.parent / "config" / "whitebox"
KIT = load_manifest(_CONFIG / "kit.yaml")
ENGINE_CUBE = "/Engine/BasicShapes/Cube.Cube"


def test_ue_whitebox_defaults_to_archkit_manifest():
    assert wb_server._MANIFEST.name == "kit.yaml"


def test_wb_apply_manifest_material_updates_all_archkit_assets(monkeypatch):
    calls: list[tuple[str, dict]] = []

    def fake_send(command, params=None, **_kwargs):
        calls.append((command, params or {}))
        return {"status": "success", "result": {"saved": True}}

    monkeypatch.setattr(wb_server, "send_command", fake_send)

    out = wb_server.wb_apply_manifest_material()

    expected_paths = sorted({a.path for a in KIT.assets.values() if a.path.startswith("/Game/")})
    assert len(calls) == len(expected_paths)
    assert [params["asset_path"] for command, params in calls] == expected_paths
    assert {command for command, _params in calls} == {"set_static_mesh_material"}
    assert {params["material_path"] for _command, params in calls} == {
        "/Game/LevelPrototyping/Materials/MI_PrototypeGrid_Gray"
    }
    assert {params["material_slot"] for _command, params in calls} == {0}
    assert f"applied={len(expected_paths)}" in out
    assert '"ok": true' in out


def test_layout_from_dict_parses_explicit_windows():
    spec = layout_from_dict(
        {
            "name": "windowed-room",
            "rooms": [
                {
                    "name": "A",
                    "rect": [0, 0, 8, 4],
                    "doors": [{"wall": "south", "at": 3, "width": 2}],
                    "windows": [{"wall": "north", "at": 1, "width": 2}],
                }
            ],
        }
    )

    assert spec.wall_height == 400.0
    assert spec.rooms[0].windows[0].wall == "north"
    assert spec.rooms[0].windows[0].at == 1
    assert spec.rooms[0].windows[0].width == 2


def test_layout_from_dict_parses_structure_mode_default_and_modular():
    default = layout_from_dict({"name": "default", "rooms": [{"name": "A", "rect": [0, 0, 4, 4]}]})
    modular = layout_from_dict(
        {
            "name": "legacy",
            "structure_mode": "modular",
            "rooms": [{"name": "A", "rect": [0, 0, 4, 4]}],
        }
    )

    assert default.structure_mode == "slab"
    assert modular.structure_mode == "modular"

    with pytest.raises(LayoutError, match="structure_mode"):
        layout_from_dict(
            {
                "name": "bad-mode",
                "structure_mode": "kit",
                "rooms": [{"name": "A", "rect": [0, 0, 4, 4]}],
            }
        )


def test_default_kit_compiler_uses_engine_slab_and_omits_modular_opening_actors():
    spec = layout_from_dict(
        {
            "name": "slab-room",
            "rooms": [
                {
                    "name": "A",
                    "rect": [0, 0, 8, 4],
                    "doors": [{"wall": "south", "at": 3, "width": 2}],
                    "windows": [{"wall": "north", "at": 1, "width": 2}],
                }
            ],
        }
    )

    placements = compile_layout(spec, KIT)
    floors = [p for p in placements if p.kind == "floor"]
    walls = [p for p in placements if p.kind == "wall"]

    assert floors and walls
    assert {p.asset_path for p in floors + walls} == {ENGINE_CUBE}
    assert len(floors) == 1
    assert len(walls) == 6
    assert not any("/ArchKit/floor/" in p.asset_path for p in placements)
    assert not any("/ArchKit/wall_door/" in p.asset_path for p in placements)
    assert not any("/ArchKit/window/" in p.asset_path for p in placements)
    assert not any("/ArchKit/glass_wall/" in p.asset_path for p in placements)
    assert not any(p.kind == "nav_proxy" for p in placements)


def test_slab_structure_placements_carry_room_metadata_for_outliner_folders():
    spec = layout_from_dict(
        {
            "name": "slab-room-folders",
            "rooms": [
                {
                    "name": "Alpha",
                    "rect": [0, 0, 4, 4],
                    "doors": [{"wall": "east", "at": 1, "width": 2}],
                },
                {
                    "name": "Beta",
                    "rect": [4, 0, 4, 4],
                    "doors": [{"wall": "west", "at": 1, "width": 2}],
                },
            ],
        }
    )

    placements = compile_layout(spec, KIT)
    structure = [p for p in placements if p.kind in {"floor", "wall"}]

    assert structure
    assert all(p.metadata.get("room") in {"Alpha", "Beta"} for p in structure)


def test_modular_compiler_uses_real_floor_wall_door_and_window_modules_without_corners():
    spec = LayoutSpec(
        name="archkit-room",
        structure_mode="modular",
        wall_height=400,
        rooms=[
            Room(
                name="A",
                rect=(0, 0, 8, 4),
                doors=[Door(wall="south", at=3, width=2)],
                windows=[Door(wall="north", at=1, width=2)],
            )
        ],
    )

    placements = compile_layout(spec, KIT)

    assert any("/ArchKit/floor/" in p.asset_path for p in placements)
    assert any("/ArchKit/wall/" in p.asset_path for p in placements)
    assert any("/ArchKit/wall_door/" in p.asset_path and "_door_" in p.name for p in placements)
    assert any(
        ("/ArchKit/window/" in p.asset_path or "/ArchKit/glass_wall/" in p.asset_path)
        and "_window_" in p.name
        for p in placements
    )
    assert not any("/ArchKit/corner/" in p.asset_path for p in placements)
    assert any(p.rotation == (0.0, 90.0, 0.0) for p in placements), "东西向墙需要 yaw=90"
    for wall in ("north", "south", "east", "west"):
        assert any(p.name.startswith(f"A_{wall}_") for p in placements)

    door = next(
        p for p in placements if "/ArchKit/wall_door/" in p.asset_path and "_door_" in p.name
    )
    window = next(
        p
        for p in placements
        if ("/ArchKit/window/" in p.asset_path or "/ArchKit/glass_wall/" in p.asset_path)
        and "_window_" in p.name
    )
    assert door.target_size[1] == 20.0
    assert door.scale[1] == 1.0
    assert window.target_size[1] == 20.0
    assert window.scale[1] == 1.0


def test_archkit_wall_runs_use_scaled_one_meter_wall_for_alignment():
    spec = LayoutSpec(
        name="scaled-wall",
        structure_mode="modular",
        wall_height=400,
        rooms=[Room(name="A", rect=(0, 0, 4, 4))],
    )

    placements = compile_layout(spec, KIT)
    wall_modules = [p for p in placements if "/ArchKit/wall/" in p.asset_path]

    assert wall_modules
    assert all("/Wall1_4" in p.asset_path for p in wall_modules)
    assert all(p.scale[2] == 1.0 for p in wall_modules)
    assert {p.name for p in wall_modules} == {
        "A_north_0_0",
        "A_south_0_0",
        "A_east_0_0",
        "A_west_0_0",
    }
    by_name = {p.name: p for p in wall_modules}
    assert by_name["A_north_0_0"].scale[0] == 4.0
    assert by_name["A_south_0_0"].scale[0] == 4.0
    assert by_name["A_east_0_0"].scale[0] == 3.6
    assert by_name["A_west_0_0"].scale[0] == 3.6
    assert by_name["A_east_0_0"].target_min == (380.0, 20.0, 0.0)
    assert by_name["A_east_0_0"].target_size == (20.0, 360.0, 400.0)
    assert by_name["A_west_0_0"].target_min == (0.0, 20.0, 0.0)
    assert by_name["A_west_0_0"].target_size == (20.0, 360.0, 400.0)


def test_archkit_compiler_adds_nav_proxy_without_polluting_metrics():
    spec = LayoutSpec(
        name="nav-proxy",
        structure_mode="modular",
        wall_height=400,
        rooms=[Room(name="A", rect=(0, 0, 4, 4))],
    )

    placements = compile_layout(spec, KIT)
    proxy = next(p for p in placements if p.name == "A_navproxy")
    actors = [
        ActorView(
            name=f"WB_abc123_{p.name}",
            location=p.location,
            scale=p.scale,
            rotation=p.rotation,
        )
        for p in placements
    ]
    report = validate_layout(spec, KIT, actors)

    assert proxy.asset_path == "/Engine/BasicShapes/Cube.Cube"
    assert proxy.target_min == (0.0, 0.0, -4.0)
    assert proxy.target_size == (400.0, 400.0, 2.0)
    assert report.ok, report.violations
    assert report.metrics["floor_area_m2"] == 16.0
    assert report.metrics["wall_count"] == len(
        [p for p in placements if "_floor" not in p.name and "_navproxy" not in p.name]
    )
    assert report.metrics["visual_mismatch_count"] == 0
    assert report.metrics["calibrated_asset_count"] > 0


def test_archkit_shared_room_doors_remain_open_for_navigation():
    spec = LayoutSpec(
        name="connected",
        structure_mode="modular",
        wall_height=400,
        rooms=[
            Room(name="A", rect=(0, 0, 4, 4), doors=[Door(wall="east", at=1, width=2)]),
            Room(name="B", rect=(4, 0, 4, 4), doors=[Door(wall="west", at=1, width=2)]),
        ],
    )

    placements = compile_layout(spec, KIT)

    assert not [
        p for p in placements if "/ArchKit/wall_door/" in p.asset_path and "_door_" in p.name
    ]
    assert any("/ArchKit/wall/" in p.asset_path for p in placements)


def test_spawn_layout_passes_rotation_to_spawn_actor(monkeypatch):
    calls: list[tuple[str, dict]] = []

    def fake_send(command, params=None, **_kwargs):
        params = params or {}
        calls.append((command, params))
        return {"status": "success", "result": {"actors": []}}

    monkeypatch.setattr(spawner, "send_command", fake_send)
    placement = Placement(
        name="A_east_0",
        asset_path="/Game/LevelPrototyping/Meshes/ArchKit/wall/Wall4_4",
        location=(100.0, 200.0, 0.0),
        scale=(1.0, 1.0, 1.0),
        rotation=(0.0, 90.0, 0.0),
    )

    spawner.spawn_layout([placement])

    spawn = next(params for command, params in calls if command == "spawn_actor")
    assert spawn["rotation"] == [0.0, 90.0, 0.0]


def test_validator_treats_negative_yaw_as_equivalent_wrapped_angle():
    spec = LayoutSpec(
        name="yaw-wrap",
        wall_height=400,
        rooms=[Room(name="A", rect=(0, 0, 4, 4))],
    )
    placements = compile_layout(spec, KIT)
    actors = []
    for p in placements:
        rotation = (0.0, -90.0, 0.0) if p.rotation == (0.0, 270.0, 0.0) else p.rotation
        actors.append(
            ActorView(
                name=f"WB_abc123_{p.name}",
                location=p.location,
                scale=p.scale,
                rotation=rotation,
            )
        )

    report = validate_layout(spec, KIT, actors)

    assert report.ok, report.violations
