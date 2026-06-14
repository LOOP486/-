"""白盒确定性校验器（A2）：期望 vs 实测对照，注入缺陷验证可检出。"""

import json

import ue5agent.mcp_servers.ue_whitebox.server as wb_server
from ue5agent.whitebox.compiler import LayoutSpec, Room, compile_layout, layout_from_dict
from ue5agent.whitebox.manifest import AssetDef, Manifest, load_manifest
from ue5agent.whitebox.validator import (
    ActorView,
    parse_batch_name,
    validate_layout,
)

_LAYOUT = {
    "name": "validator-test",
    "origin": [0, 0, 0],
    "rooms": [
        {"name": "A", "rect": [0, 0, 4, 4], "doors": [{"wall": "east", "at": 1, "width": 2}]},
        {"name": "B", "rect": [4, 0, 4, 4], "doors": [{"wall": "west", "at": 1, "width": 2}]},
    ],
}


def _spec_and_manifest():
    spec = layout_from_dict(_LAYOUT)
    manifest = load_manifest(wb_server._MANIFEST)
    return spec, manifest


def _perfect_actors(spec, manifest, batch="abc123"):
    """按期望放置生成"完美落地"的实测视图。"""
    return [
        ActorView(
            name=f"WB_{batch}_{p.name}",
            location=p.location,
            scale=p.scale,
            rotation=p.rotation,
        )
        for p in compile_layout(spec, manifest)
    ]


def test_parse_batch_name():
    assert parse_batch_name("WB_1a2b3c_Hall_floor", "WB") == ("1a2b3c", "Hall_floor")
    assert parse_batch_name("WB_1a2b_Hall_east_0", "WB") == ("1a2b", "Hall_east_0")
    assert parse_batch_name("PlayerStart", "WB") is None
    assert parse_batch_name("WB_nobatchname", "WB") is None


def test_perfect_layout_passes():
    spec, manifest = _spec_and_manifest()
    report = validate_layout(spec, manifest, _perfect_actors(spec, manifest))
    assert report.ok, report.violations
    assert report.metrics["room_count"] == 2
    assert report.metrics["door_count"] == 2
    assert report.metrics["structure_mode"] == "slab"
    assert report.metrics["wall_fragmentation_score"] > 0
    assert report.metrics["actual_count"] == report.metrics["expected_count"]
    assert report.metrics["floor_area_m2"] == 32.0  # 两个 4x4 格房间 = 2 * 16m²


def test_missing_component_detected():
    spec, manifest = _spec_and_manifest()
    actors = _perfect_actors(spec, manifest)
    removed = next(a for a in actors if a.name.endswith("A_floor"))
    actors.remove(removed)
    report = validate_layout(spec, manifest, actors)
    assert not report.ok
    assert any("缺失构件" in v and "A_floor" in v for v in report.violations)
    assert report.metrics["floor_hole_count"] > 0
    assert any("地板缺口" in v for v in report.violations)


def test_missing_wall_reports_wall_gap_metric():
    spec, manifest = _spec_and_manifest()
    actors = _perfect_actors(spec, manifest)
    removed = next(a for a in actors if a.name.endswith("A_north_0"))
    actors.remove(removed)

    report = validate_layout(spec, manifest, actors)

    assert not report.ok
    assert report.metrics["wall_gap_count"] > 0
    assert any("墙体缺口" in v and "A_north_0" in v for v in report.violations)


def test_extra_component_detected():
    spec, manifest = _spec_and_manifest()
    actors = _perfect_actors(spec, manifest)
    actors.append(
        ActorView(name="WB_abc123_Ghost_floor", location=(9000, 9000, 0), scale=(1, 1, 1))
    )
    report = validate_layout(spec, manifest, actors)
    assert any("多余构件" in v and "Ghost_floor" in v for v in report.violations)


def test_displaced_component_detected():
    spec, manifest = _spec_and_manifest()
    actors = _perfect_actors(spec, manifest)
    target = next(a for a in actors if a.name.endswith("B_floor"))
    loc = target.location
    actors[actors.index(target)] = ActorView(
        name=target.name, location=(loc[0] + 50, loc[1], loc[2]), scale=target.scale
    )
    report = validate_layout(spec, manifest, actors)
    assert any("构件偏差" in v and "B_floor" in v for v in report.violations)


def test_overlap_detected_but_corner_laps_exempt():
    spec, manifest = _spec_and_manifest()
    # 完美布局自身（含墙角搭接、地板贴边）不应报穿插
    report = validate_layout(spec, manifest, _perfect_actors(spec, manifest))
    assert not [v for v in report.violations if "穿插" in v]
    # 把 B 地板挪到 A 地板上 → 实体穿插
    actors = _perfect_actors(spec, manifest)
    floor_a = next(a for a in actors if a.name.endswith("A_floor"))
    floor_b = next(a for a in actors if a.name.endswith("B_floor"))
    actors[actors.index(floor_b)] = ActorView(
        name=floor_b.name, location=floor_a.location, scale=floor_b.scale
    )
    report = validate_layout(spec, manifest, actors)
    assert any("穿插" in v for v in report.violations)


def test_multiple_batches_flagged():
    spec, manifest = _spec_and_manifest()
    actors = _perfect_actors(spec, manifest, batch="aaa111")
    actors.append(ActorView(name="WB_bbb222_A_floor", location=(0, 0, 0), scale=(1, 1, 1)))
    report = validate_layout(spec, manifest, actors)
    assert any("批次" in v for v in report.violations)


def test_non_wb_actors_ignored():
    spec, manifest = _spec_and_manifest()
    actors = _perfect_actors(spec, manifest)
    actors.append(ActorView(name="PlayerStart", location=(0, 0, 100), scale=(1, 1, 1)))
    actors.append(ActorView(name="Floor", location=(0, 0, 0), scale=(40, 40, 1)))
    report = validate_layout(spec, manifest, actors)
    assert report.ok, report.violations


def test_foreign_prefix_residue_in_layout_area_flagged():
    """异前缀残留回归：旧批次（如 S1_）构件叠在布局区域 → violation 并给清理指引
    （真机 e2e 实测：残留墙堵门导致 path_test 全 partial，被误诊为 agent radius）。"""
    spec, manifest = _spec_and_manifest()
    actors = _perfect_actors(spec, manifest)
    # 残留墙横在布局中央（布局 x[0,800] y[0,400]）
    actors.append(
        ActorView(name="S1_a8b165e_A_north_0", location=(400, 200, 150), scale=(4, 0.2, 3))
    )
    report = validate_layout(spec, manifest, actors)
    assert not report.ok
    assert any("异前缀白盒残留" in v and "S1_" in v and "wb_clear" in v for v in report.violations)


def test_foreign_prefix_residue_far_away_not_flagged():
    spec, manifest = _spec_and_manifest()
    actors = _perfect_actors(spec, manifest)
    # 远离布局区域（布局 x[0,800]，残留在 x=99000）的旧批次不拦验收
    actors.append(
        ActorView(name="S1_a8b165e_B_floor", location=(99000, 99000, -10), scale=(4, 4, 0.2))
    )
    report = validate_layout(spec, manifest, actors)
    assert report.ok, report.violations


def test_visual_bounds_mismatch_detected_even_when_transform_matches():
    """回归：编译器/validator 不能只看 transform 自洽。

    这里 wall 的 manifest pivot 仍按旧 FBX 推断为 (0,+Y,0)，但 UE 校准的真实本地包围盒
    表明原点在另一侧。actor transform 与编译期 placement 完全一致时，旧 validator 会 PASS；
    新 validator 必须用 calibrated visual bounds 发现墙体真实 AABB 没贴住目标墙线。
    """
    floor = AssetDef("floor", "/F", (100, 100, 20), "floor")
    wall = AssetDef(
        "wall",
        "/W",
        (100, 20, 300),
        "wall",
        pivot=(0.0, 1.0, 0.0),
        local_bounds_min=(0.0, 0.0, 0.0),
        local_bounds_max=(100.0, 20.0, 300.0),
        calibrated=True,
    )
    manifest = Manifest(
        grid=100,
        assets={"floor": floor, "wall": wall},
        roles={"floor": "floor", "wall": "wall"},
    )
    spec = LayoutSpec(
        name="visual", structure_mode="modular", rooms=[Room(name="A", rect=(0, 0, 3, 3))]
    )
    actors = _perfect_actors(spec, manifest)

    report = validate_layout(spec, manifest, actors)

    assert not report.ok
    assert any(
        "视觉对齐偏差" in violation and "A_south_0" in violation for violation in report.violations
    )
    assert report.metrics["visual_mismatch_count"] >= 1


def test_wb_validate_tool_formats_report(monkeypatch):
    """wb_validate 工具：fake bridge 返回完美落地 → PASS 文本。"""
    spec, manifest = _spec_and_manifest()
    actor_dicts = [
        {
            "name": a.name,
            "location": list(a.location),
            "scale": list(a.scale),
            "rotation": list(a.rotation),
        }
        for a in _perfect_actors(spec, manifest)
    ]

    def fake_send(command, params=None, **_kwargs):
        assert command == "find_actors_by_name"
        return {"status": "success", "result": {"actors": actor_dicts}}

    monkeypatch.setattr(wb_server, "send_command", fake_send)
    out = wb_server.wb_validate(json.dumps(_LAYOUT))
    assert "校验PASS" in out
    assert "metrics" in out


def test_wb_validate_tool_reports_fail(monkeypatch):
    spec, manifest = _spec_and_manifest()
    actors = _perfect_actors(spec, manifest)[:-1]  # 丢一件
    actor_dicts = [
        {
            "name": a.name,
            "location": list(a.location),
            "scale": list(a.scale),
            "rotation": list(a.rotation),
        }
        for a in actors
    ]
    monkeypatch.setattr(
        wb_server,
        "send_command",
        lambda *_a, **_k: {"status": "success", "result": {"actors": actor_dicts}},
    )
    out = wb_server.wb_validate(json.dumps(_LAYOUT))
    assert "校验FAIL" in out
    assert "缺失构件" in out


def test_wb_validate_queries_prefix_before_broad_residue_scan(monkeypatch):
    spec, manifest = _spec_and_manifest()
    actor_dicts = [
        {
            "name": a.name,
            "location": list(a.location),
            "scale": list(a.scale),
            "rotation": list(a.rotation),
        }
        for a in _perfect_actors(spec, manifest, batch="abc123")
    ]
    calls: list[str] = []

    def fake_send(command, params=None, **_kwargs):
        assert command == "find_actors_by_name"
        pattern = params["pattern"]
        calls.append(pattern)
        # 回归：桥端宽查询可能被截断；prefix 精确查询必须足以完成当前批次校验。
        actors = actor_dicts if pattern == "WB_" else actor_dicts[:1]
        return {"status": "success", "result": {"actors": actors}}

    monkeypatch.setattr(wb_server, "send_command", fake_send)

    out = wb_server.wb_validate(json.dumps(_LAYOUT), prefix="WB")

    assert calls[:2] == ["WB_", "_"]
    assert "校验PASS" in out


def test_wb_asset_audit_reports_mesh_size_mismatch(monkeypatch, tmp_path):
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
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(wb_server, "_MANIFEST", manifest_path)

    def fake_send(command, params=None, **_kwargs):
        assert command == "get_mesh_bounds"
        assert params == {"asset_path": "/Game/Kit/Wall"}
        return {"status": "success", "result": {"size": [100, 40, 400]}}

    monkeypatch.setattr(wb_server, "send_command", fake_send)

    out = wb_server.wb_asset_audit(asset_filter="wall")

    assert "资产审计FAIL" in out
    assert "wall" in out
    assert "尺寸不一致" in out
    assert '"kind": "wb_asset_audit"' in out
    assert '"ok": false' in out


def test_wb_asset_audit_passes_matching_mesh_size(monkeypatch, tmp_path):
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
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(wb_server, "_MANIFEST", manifest_path)
    monkeypatch.setattr(
        wb_server,
        "send_command",
        lambda *_a, **_k: {"status": "success", "result": {"size": [100, 20, 400]}},
    )

    out = wb_server.wb_asset_audit(asset_filter="wall")

    assert "资产审计PASS" in out
    assert '"ok": true' in out


def test_wb_asset_audit_default_prefers_calibrated_assets(monkeypatch, tmp_path):
    manifest_path = tmp_path / "kit.yaml"
    manifest_path.write_text(
        """
version: 2
grid: 100
assets:
  critical_wall:
    path: /Game/Kit/CriticalWall
    size: [100, 20, 400]
    category: wall
    local_bounds_min: [0, 0, 0]
    local_bounds_max: [100, 20, 400]
    calibrated: true
  draft_prop:
    path: /Game/Kit/DraftProp
    size: [50, 50, 50]
    category: prop
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(wb_server, "_MANIFEST", manifest_path)
    calls: list[str] = []

    def fake_send(_command, params=None, **_kwargs):
        calls.append(params["asset_path"])
        return {"status": "success", "result": {"size": [100, 20, 400]}}

    monkeypatch.setattr(wb_server, "send_command", fake_send)

    out = wb_server.wb_asset_audit()

    assert "资产审计PASS：checked=1, calibrated=1, total=1" in out
    assert calls == ["/Game/Kit/CriticalWall"]
