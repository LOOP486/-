"""白盒 B+：多层/楼梯/玩法层/真实 PlayerStart 的编译与校验契约。"""

from pathlib import Path

import pytest

import ue5agent.whitebox.spawner as spawner
from ue5agent.whitebox.compiler import (
    LayoutError,
    Placement,
    compile_layout,
    layout_from_dict,
)
from ue5agent.whitebox.manifest import load_manifest
from ue5agent.whitebox.validator import ActorView, validate_layout

_CONFIG = Path(__file__).parent.parent / "config" / "whitebox"
# 编译器单测用冻结的 ArchKit 样例清单，与随用户重扫而变的 config/whitebox/kit.yaml 解耦。
KIT = load_manifest(Path(__file__).parent / "data" / "kit_archkit_sample.yaml")


def _by_name(placements: list[Placement], name: str) -> Placement:
    return next(p for p in placements if p.name == name)


def _perfect_actors(placements: list[Placement]) -> list[ActorView]:
    return [
        ActorView(
            name=f"WB_abc123_{p.name}",
            location=p.location,
            scale=p.scale,
            rotation=p.rotation,
            actor_type=p.actor_type,
        )
        for p in placements
    ]


def _aabb(placement: Placement) -> tuple[tuple[float, float], tuple[float, float]]:
    assert placement.target_min is not None
    assert placement.target_size is not None
    return (
        (placement.target_min[0], placement.target_min[0] + placement.target_size[0]),
        (placement.target_min[1], placement.target_min[1] + placement.target_size[1]),
    )


def _overlaps_xy(
    a: tuple[tuple[float, float], tuple[float, float]],
    b: tuple[tuple[float, float], tuple[float, float]],
) -> bool:
    return min(a[0][1], b[0][1]) > max(a[0][0], b[0][0]) and min(a[1][1], b[1][1]) > max(
        a[1][0], b[1][0]
    )


def test_layout_from_dict_parses_levels_stairs_props_and_gameplay():
    spec = layout_from_dict(
        {
            "name": "vertical",
            "structure_mode": "modular",
            "wall_height": 400,
            "rooms": [
                {
                    "name": "upper",
                    "level": 1,
                    "rect": [0, 0, 6, 6],
                    "props": [
                        {
                            "key": "smallwoodencrate_001",
                            "at": [2, 2],
                            "rotation": 90,
                            "optional": True,
                        }
                    ],
                }
            ],
            "stairs": [
                {
                    "room": "upper",
                    "at": [1, 0],
                    "from_level": 0,
                    "to_level": 1,
                    "facing": "north",
                    "key": "stair_2",
                }
            ],
            "gameplay": {},
        }
    )

    assert spec.level_height == 400.0
    assert spec.structure_mode == "modular"
    assert spec.rooms[0].level == 1
    assert spec.rooms[0].props[0].key == "smallwoodencrate_001"
    assert spec.rooms[0].props[0].rotation == 90.0
    assert spec.stairs[0].to_level == 1
    assert spec.gameplay is not None


def test_default_slab_rejects_upper_room_levels():
    spec = layout_from_dict(
        {
            "name": "slab-rejects-upper-room",
            "rooms": [
                {"name": "Lower", "rect": [0, 0, 6, 6], "level": 0},
                {"name": "Upper", "rect": [0, 0, 6, 6], "level": 1},
            ],
        }
    )

    with pytest.raises(LayoutError, match='structure_mode="modular"'):
        compile_layout(spec, KIT)


def test_default_slab_allows_stair_without_upper_room_and_no_upper_structure():
    spec = layout_from_dict(
        {
            "name": "single-level-stair",
            "rooms": [{"name": "Lower", "rect": [0, 0, 7, 8], "level": 0}],
            "stairs": [
                {
                    "room": "Lower",
                    "at": [1, 1],
                    "from_level": 0,
                    "to_level": 1,
                    "facing": "north",
                    "key": "stair_2",
                }
            ],
        }
    )

    placements = compile_layout(spec, KIT)
    upper_structure = [
        p
        for p in placements
        if p.kind in {"floor", "wall"} and p.target_min is not None and p.target_min[2] >= 400.0
    ]

    assert any(p.kind == "stair" for p in placements)
    assert any(p.kind == "stairwell" for p in placements)
    assert not upper_structure


def test_multilevel_rooms_use_z_offset_and_upper_stairwell_has_no_floor():
    spec = layout_from_dict(
        {
            "name": "two-level",
            "structure_mode": "modular",
            "wall_height": 400,
            "rooms": [
                {"name": "Lower", "rect": [0, 0, 6, 8], "level": 0},
                {"name": "Upper", "rect": [0, 0, 6, 8], "level": 1},
            ],
            "stairs": [
                {
                    "room": "Lower",
                    "at": [1, 1],
                    "from_level": 0,
                    "to_level": 1,
                    "facing": "north",
                }
            ],
        }
    )

    placements = compile_layout(spec, KIT)

    lower_floor = next(p for p in placements if p.name.startswith("Lower_floor"))
    upper_wall = _by_name(placements, "Upper_south_0_0")
    stair = next(p for p in placements if p.kind == "stair")
    assert lower_floor.target_min is not None and lower_floor.target_min[2] < 0
    assert upper_wall.target_min is not None and upper_wall.target_min[2] == 400.0
    assert stair.scale == (1.0, 1.0, 1.0)

    hole_xy = ((100.0, 400.0), (100.0, 650.0))
    upper_floors = [p for p in placements if p.name.startswith("Upper_floor")]
    assert upper_floors
    assert not [_aabb(p) for p in upper_floors if _overlaps_xy(_aabb(p), hole_xy)]


def test_stair_generates_stairwell_guards_and_metrics():
    spec = layout_from_dict(
        {
            "name": "stairwell",
            "structure_mode": "modular",
            "wall_height": 400,
            "rooms": [
                {"name": "Lower", "rect": [0, 0, 6, 8], "level": 0},
                {"name": "Upper", "rect": [0, 0, 6, 8], "level": 1},
            ],
            "stairs": [
                {
                    "room": "Lower",
                    "at": [1, 1],
                    "from_level": 0,
                    "to_level": 1,
                    "facing": "north",
                }
            ],
        }
    )

    placements = compile_layout(spec, KIT)
    guards = [p for p in placements if p.kind == "stairwell"]
    report = validate_layout(spec, KIT, _perfect_actors(placements))

    assert len(guards) >= 2
    assert all(p.target_min is not None and p.target_size is not None for p in guards)
    assert report.metrics["stairwell_count"] == len(guards)


def test_multilevel_structural_laps_are_legal_but_stairs_still_cannot_cross_walls():
    good = layout_from_dict(
        {
            "name": "legal-structural-laps",
            "structure_mode": "modular",
            "wall_height": 400,
            "rooms": [
                {"name": "Lower", "rect": [0, 0, 6, 8], "level": 0},
                {"name": "Upper", "rect": [0, 0, 6, 8], "level": 1},
            ],
            "stairs": [
                {
                    "room": "Lower",
                    "at": [1, 1],
                    "from_level": 0,
                    "to_level": 1,
                    "facing": "north",
                }
            ],
        }
    )
    good_placements = compile_layout(good, KIT)
    good_report = validate_layout(good, KIT, _perfect_actors(good_placements))

    assert good_report.ok, good_report.violations

    bad = layout_from_dict(
        {
            "name": "stair-crosses-wall",
            "structure_mode": "modular",
            "wall_height": 400,
            "rooms": [
                {"name": "Lower", "rect": [0, 0, 6, 8], "level": 0},
                {"name": "Upper", "rect": [0, 0, 6, 8], "level": 1},
            ],
            "stairs": [
                {
                    "room": "Lower",
                    "at": [1, 0],
                    "from_level": 0,
                    "to_level": 1,
                    "facing": "north",
                }
            ],
        }
    )
    with pytest.raises(LayoutError, match="穿墙"):
        compile_layout(bad, KIT)


def test_stair_must_connect_adjacent_levels_and_match_level_height():
    bad_jump = layout_from_dict(
        {
            "name": "bad-jump",
            "structure_mode": "modular",
            "wall_height": 400,
            "rooms": [
                {"name": "L0", "rect": [0, 0, 6, 6], "level": 0},
                {"name": "L2", "rect": [0, 0, 6, 6], "level": 2},
            ],
            "stairs": [
                {
                    "room": "L0",
                    "at": [1, 0],
                    "from_level": 0,
                    "to_level": 2,
                    "facing": "north",
                }
            ],
        }
    )
    with pytest.raises(LayoutError, match="相邻楼层"):
        compile_layout(bad_jump, KIT)

    bad_height = layout_from_dict(
        {
            "name": "bad-height",
            "structure_mode": "modular",
            "wall_height": 300,
            "level_height": 300,
            "rooms": [
                {"name": "L0", "rect": [0, 0, 6, 6], "level": 0},
                {"name": "L1", "rect": [0, 0, 6, 6], "level": 1},
            ],
            "stairs": [
                {
                    "room": "L0",
                    "at": [1, 0],
                    "from_level": 0,
                    "to_level": 1,
                    "facing": "north",
                }
            ],
        }
    )
    with pytest.raises(LayoutError, match="高度"):
        compile_layout(bad_height, KIT)


def test_stair_cannot_block_straight_door_to_door_route():
    spec = layout_from_dict(
        {
            "name": "stair-blocks-through-route",
            "structure_mode": "modular",
            "rooms": [
                {
                    "name": "Lower",
                    "rect": [0, 0, 8, 8],
                    "level": 0,
                    "doors": [
                        {"wall": "west", "at": 4, "width": 1},
                        {"wall": "east", "at": 4, "width": 1},
                    ],
                },
                {"name": "Upper", "rect": [0, 0, 8, 8], "level": 1},
            ],
            "stairs": [
                {
                    "room": "Lower",
                    "at": [2, 1],
                    "from_level": 0,
                    "to_level": 1,
                    "facing": "north",
                    "key": "stair_2",
                }
            ],
        }
    )

    with pytest.raises(LayoutError, match="门到门"):
        compile_layout(spec, KIT)


def test_explicit_props_use_native_scale_and_required_conflicts_fail():
    spec = layout_from_dict(
        {
            "name": "props",
            "rooms": [
                {
                    "name": "A",
                    "rect": [0, 0, 6, 6],
                    "props": [{"key": "smallwoodencrate_001", "at": [2, 2], "rotation": 90}],
                }
            ],
        }
    )

    prop = next(p for p in compile_layout(spec, KIT) if p.kind == "prop")

    assert prop.scale == (1.0, 1.0, 1.0)
    assert prop.rotation == (0.0, 90.0, 0.0)

    out_of_bounds = layout_from_dict(
        {
            "name": "bad-prop",
            "rooms": [
                {
                    "name": "A",
                    "rect": [0, 0, 4, 4],
                    "props": [{"key": "smallwoodencrate_001", "at": [4, 1]}],
                }
            ],
        }
    )
    with pytest.raises(LayoutError, match="越界"):
        compile_layout(out_of_bounds, KIT)

    blocks_door = layout_from_dict(
        {
            "name": "bad-door-prop",
            "rooms": [
                {
                    "name": "A",
                    "rect": [0, 0, 4, 4],
                    "doors": [{"wall": "south", "at": 1, "width": 1}],
                    "props": [{"key": "smallwoodencrate_001", "at": [1, 0]}],
                }
            ],
        }
    )
    with pytest.raises(LayoutError, match="堵门"):
        compile_layout(blocks_door, KIT)

    crosses_wall = layout_from_dict(
        {
            "name": "wall-prop",
            "rooms": [
                {
                    "name": "A",
                    "rect": [0, 0, 4, 4],
                    "props": [{"key": "smallwoodencrate_001", "at": [0, 1]}],
                }
            ],
        }
    )
    with pytest.raises(LayoutError, match="穿墙"):
        compile_layout(crosses_wall, KIT)


def test_explicit_props_can_select_native_asset_by_category():
    spec = layout_from_dict(
        {
            "name": "category-prop",
            "rooms": [
                {
                    "name": "A",
                    "rect": [0, 0, 6, 6],
                    "props": [{"category": "cover", "at": [2, 2]}],
                }
            ],
        }
    )

    prop = next(p for p in compile_layout(spec, KIT) if p.kind == "prop")

    assert prop.asset_path.endswith("/SmallWoodenCrate_003")
    assert prop.scale == (1.0, 1.0, 1.0)


def test_explicit_prop_overlap_required_fails_and_optional_is_skipped():
    required_overlap = layout_from_dict(
        {
            "name": "prop-overlap-required",
            "rooms": [
                {
                    "name": "A",
                    "rect": [0, 0, 6, 6],
                    "props": [
                        {"key": "smallwoodencrate_001", "at": [2, 2]},
                        {"key": "smallwoodencrate_002", "at": [2, 2]},
                    ],
                }
            ],
        }
    )
    with pytest.raises(LayoutError, match="重叠"):
        compile_layout(required_overlap, KIT)

    optional_overlap = layout_from_dict(
        {
            "name": "prop-overlap-optional",
            "rooms": [
                {
                    "name": "A",
                    "rect": [0, 0, 6, 6],
                    "props": [
                        {"key": "smallwoodencrate_001", "at": [2, 2]},
                        {"key": "smallwoodencrate_002", "at": [2, 2], "optional": True},
                    ],
                }
            ],
        }
    )

    props = [p for p in compile_layout(optional_overlap, KIT) if p.kind == "prop"]

    assert [p.name for p in props] == ["A_prop_smallwoodencrate_001"]


def test_explicit_props_cannot_block_room_door_to_door_route_without_gameplay():
    spec = layout_from_dict(
        {
            "name": "door-route-prop",
            "rooms": [
                {
                    "name": "A",
                    "rect": [0, 0, 7, 5],
                    "doors": [
                        {"wall": "west", "at": 2, "width": 1},
                        {"wall": "east", "at": 2, "width": 1},
                    ],
                    "props": [{"key": "smallwoodencrate_001", "at": [3, 2]}],
                }
            ],
        }
    )

    with pytest.raises(LayoutError, match="门到门"):
        compile_layout(spec, KIT)


def test_explicit_props_conflicting_with_generated_route_respect_optional_flag():
    required_on_route = layout_from_dict(
        {
            "name": "route-prop-conflict",
            "rooms": [
                {
                    "name": "A",
                    "rect": [0, 0, 6, 6],
                    "props": [{"key": "smallwoodencrate_001", "at": [0, 0]}],
                }
            ],
            "gameplay": {},
        }
    )
    with pytest.raises(LayoutError, match="主路线"):
        compile_layout(required_on_route, KIT)

    optional_on_route = layout_from_dict(
        {
            "name": "route-prop-optional",
            "rooms": [
                {
                    "name": "A",
                    "rect": [0, 0, 6, 6],
                    "props": [
                        {
                            "key": "smallwoodencrate_001",
                            "at": [0, 0],
                            "optional": True,
                        }
                    ],
                }
            ],
            "gameplay": {},
        }
    )

    placements = compile_layout(optional_on_route, KIT)

    assert not [p for p in placements if p.name == "A_prop_smallwoodencrate_001"]


def test_empty_gameplay_generates_playerstarts_route_and_native_cover():
    spec = layout_from_dict(
        {
            "name": "gameplay",
            "rooms": [
                {
                    "name": "A",
                    "rect": [0, 0, 6, 6],
                    "doors": [{"wall": "east", "at": 2, "width": 2}],
                },
                {
                    "name": "B",
                    "rect": [6, 0, 6, 6],
                    "doors": [{"wall": "west", "at": 2, "width": 2}],
                },
            ],
            "gameplay": {},
        }
    )

    placements = compile_layout(spec, KIT)
    spawns = [p for p in placements if p.kind == "spawn"]
    routes = [p for p in placements if p.kind == "route"]
    cover = [p for p in placements if p.kind in {"cover", "pillar"}]

    assert len(spawns) == 2
    assert {p.actor_type for p in spawns} == {"PlayerStart"}
    assert all(not p.asset_path for p in spawns)
    assert routes
    assert cover
    assert all(p.scale == (1.0, 1.0, 1.0) for p in cover)


def test_explicit_empty_gameplay_lists_override_default_generation():
    spec = layout_from_dict(
        {
            "name": "explicit-empty-gameplay",
            "rooms": [
                {
                    "name": "A",
                    "rect": [0, 0, 6, 6],
                    "doors": [{"wall": "east", "at": 2, "width": 2}],
                },
                {
                    "name": "B",
                    "rect": [6, 0, 6, 6],
                    "doors": [{"wall": "west", "at": 2, "width": 2}],
                },
            ],
            "gameplay": {
                "spawn_points": [],
                "routes": [],
                "auto_cover": False,
            },
        }
    )

    placements = compile_layout(spec, KIT)

    assert not [p for p in placements if p.kind == "spawn"]
    assert not [p for p in placements if p.kind == "route"]
    assert not [p for p in placements if p.kind in {"cover", "pillar"}]


def test_explicit_gameplay_fields_take_precedence_over_defaults():
    spec = layout_from_dict(
        {
            "name": "explicit-gameplay",
            "rooms": [{"name": "A", "rect": [0, 0, 6, 6]}],
            "gameplay": {
                "spawn_points": [{"room": "A", "at": [1, 2], "rotation": 45}],
                "routes": [
                    {
                        "points": [
                            {"room": "A", "at": [2, 1]},
                            {"room": "A", "at": [3, 1]},
                        ]
                    }
                ],
                "auto_cover": False,
            },
        }
    )

    placements = compile_layout(spec, KIT)
    spawns = [p for p in placements if p.kind == "spawn"]
    routes = [p for p in placements if p.kind == "route"]

    assert len(spawns) == 1
    assert spawns[0].location == (100.0, 200.0, 88.0)
    assert spawns[0].rotation == (0.0, 45.0, 0.0)
    assert [p.location for p in routes] == [(200.0, 100.0, 8.0), (300.0, 100.0, 8.0)]
    assert not [p for p in placements if p.kind in {"cover", "pillar"}]


def test_default_route_through_stairs_marks_both_stair_landings():
    spec = layout_from_dict(
        {
            "name": "vertical-route",
            "structure_mode": "modular",
            "rooms": [
                {"name": "Lower", "rect": [0, 0, 7, 7], "level": 0},
                {"name": "Upper", "rect": [0, 0, 7, 7], "level": 1},
            ],
            "stairs": [
                {
                    "room": "Lower",
                    "at": [1, 1],
                    "from_level": 0,
                    "to_level": 1,
                    "facing": "north",
                    "key": "stair_2",
                }
            ],
            "gameplay": {},
        }
    )

    placements = compile_layout(spec, KIT)
    route_locations = {
        (round(p.location[0]), round(p.location[1]), round(p.location[2]))
        for p in placements
        if p.kind == "route"
    }

    assert (250, 400, 8) in route_locations
    assert (250, 400, 408) in route_locations


def test_spawn_layout_uses_playerstart_type_without_static_mesh(monkeypatch):
    calls: list[tuple[str, dict]] = []

    def fake_send(command, params=None, **_kwargs):
        params = params or {}
        calls.append((command, params))
        return {"status": "success", "result": {"name": params.get("name")}}

    monkeypatch.setattr(spawner, "send_command", fake_send)
    placement = Placement(
        name="game_spawn_0",
        asset_path="",
        location=(100.0, 100.0, 88.0),
        scale=(1.0, 1.0, 1.0),
        actor_type="PlayerStart",
        kind="spawn",
    )

    spawner.spawn_layout([placement])

    params = next(params for command, params in calls if command == "spawn_actor")
    assert params["type"] == "PlayerStart"
    assert "static_mesh" not in params
    assert params["location"] == [100.0, 100.0, 88.0]


def test_validator_reports_bplus_metrics_and_excludes_gameplay_from_wall_count():
    spec = layout_from_dict(
        {
            "name": "metrics",
            "rooms": [
                {
                    "name": "A",
                    "rect": [0, 0, 6, 6],
                    "props": [{"key": "smallwoodencrate_001", "at": [4, 1]}],
                }
            ],
            "gameplay": {},
        }
    )
    placements = compile_layout(spec, KIT)

    report = validate_layout(spec, KIT, _perfect_actors(placements))

    assert report.ok, report.violations
    assert report.metrics["level_count"] == 1
    assert report.metrics["prop_count"] >= 1
    assert report.metrics["spawn_count"] == 2
    assert report.metrics["route_count"] == 1
    assert report.metrics["wall_count"] == len([p for p in placements if p.kind == "wall"])


def test_validator_flags_cover_blocking_route_corridor():
    spec = layout_from_dict(
        {
            "name": "blocked-route",
            "rooms": [
                {
                    "name": "A",
                    "rect": [0, 0, 6, 6],
                    "doors": [{"wall": "east", "at": 2, "width": 2}],
                },
                {
                    "name": "B",
                    "rect": [6, 0, 6, 6],
                    "doors": [{"wall": "west", "at": 2, "width": 2}],
                },
            ],
            "gameplay": {},
        }
    )
    placements = compile_layout(spec, KIT)
    actors = _perfect_actors(placements)
    cover = next(p for p in placements if p.kind == "cover")
    marker = next(p for p in placements if p.kind == "route")
    actor = next(a for a in actors if a.name.endswith(cover.name))
    actors[actors.index(actor)] = ActorView(
        name=actor.name,
        location=marker.location,
        scale=actor.scale,
        rotation=actor.rotation,
        actor_type=actor.actor_type,
    )

    report = validate_layout(spec, KIT, actors)

    assert any("主路线" in v for v in report.violations)


def test_auto_cover_keeps_clearance_from_stairwell_guards():
    spec = layout_from_dict(
        {
            "name": "stairwell-clearance",
            "structure_mode": "modular",
            "rooms": [
                {"name": "Lower", "rect": [0, 0, 7, 7], "level": 0},
                {"name": "Upper", "rect": [0, 0, 7, 7], "level": 1},
            ],
            "stairs": [
                {
                    "room": "Lower",
                    "at": [1, 1],
                    "from_level": 0,
                    "to_level": 1,
                    "facing": "north",
                    "key": "stair_2",
                }
            ],
            "gameplay": {},
        }
    )

    placements = compile_layout(spec, KIT)
    guards = [p for p in placements if p.kind == "stairwell"]
    auto = [p for p in placements if p.kind in {"cover", "pillar"}]

    assert guards
    assert auto
    assert not [
        (piece.name, guard.name)
        for piece in auto
        for guard in guards
        if _overlaps_xy(_aabb(piece), _aabb(guard))
    ]


def test_auto_cover_keeps_upper_stairwell_hole_clear():
    spec = layout_from_dict(
        {
            "name": "upper-stairwell-clearance",
            "structure_mode": "modular",
            "rooms": [
                {"name": "Lower", "rect": [0, 0, 7, 7], "level": 0},
                {"name": "Upper", "rect": [0, 0, 7, 7], "level": 1},
            ],
            "stairs": [
                {
                    "room": "Lower",
                    "at": [1, 1],
                    "from_level": 0,
                    "to_level": 1,
                    "facing": "north",
                    "key": "stair_2",
                }
            ],
            "gameplay": {},
        }
    )

    placements = compile_layout(spec, KIT)
    upper_auto = [
        p for p in placements if p.kind in {"cover", "pillar"} and p.metadata.get("room") == "Upper"
    ]
    upper_hole = ((100.0, 400.0), (100.0, 650.0))

    assert upper_auto
    assert not [p.name for p in upper_auto if _overlaps_xy(_aabb(p), upper_hole)]
