"""白盒确定性校验器（A2）：期望 vs 实测对照，注入缺陷验证可检出。"""

import json

import ue5agent.mcp_servers.ue_whitebox.server as wb_server
from ue5agent.whitebox.compiler import compile_layout, layout_from_dict
from ue5agent.whitebox.manifest import load_manifest
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
        ActorView(name=f"WB_{batch}_{p.name}", location=p.location, scale=p.scale)
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


def test_wb_validate_tool_formats_report(monkeypatch):
    """wb_validate 工具：fake bridge 返回完美落地 → PASS 文本。"""
    spec, manifest = _spec_and_manifest()
    actor_dicts = [
        {"name": a.name, "location": list(a.location), "scale": list(a.scale)}
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
        {"name": a.name, "location": list(a.location), "scale": list(a.scale)} for a in actors
    ]
    monkeypatch.setattr(
        wb_server,
        "send_command",
        lambda *_a, **_k: {"status": "success", "result": {"actors": actor_dicts}},
    )
    out = wb_server.wb_validate(json.dumps(_LAYOUT))
    assert "校验FAIL" in out
    assert "缺失构件" in out
