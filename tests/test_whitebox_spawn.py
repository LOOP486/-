"""白盒落地的崩溃防御契约：spawn 用运行唯一名，绝不复用可能残留的僵尸名。

根因（崩溃日志确诊 LevelActor.cpp:585 "Cannot generate unique name"）：
UE 的 DestroyActor 是"标记销毁 + 延迟 GC"——delete 后 actor 立即从 level 列表移除
（find_actors_by_name 当帧即查不到，看似删净），但其 FName 在 GC 前仍占用命名空间。
因此一切依赖 find 的"删后复查/spawn 前预检"都对僵尸名失效；spawn 复用同名 → 引擎
硬 check → Fatal 崩编辑器。根治：spawn 注入运行唯一批次标记，新名必然空闲，
不给引擎判定重名的机会。clear 仍按前缀子串整批回滚。
"""

from __future__ import annotations

import json

import ue5agent.mcp_servers.ue_whitebox.server as wb_server
import ue5agent.whitebox.spawner as spawner

_LAYOUT = json.dumps(
    {
        "name": "t",
        "origin": [0, 0, 0],
        "rooms": [{"name": "Room", "rect": [0, 0, 2, 2], "doors": []}],
    }
)


def _record_bridge(monkeypatch, *, existing: list[str] | None = None):
    """替换 spawner.send_command，记录命令序列并模拟桥行为。"""
    calls: list[tuple[str, dict]] = []
    present = list(existing or [])

    def fake_send(command, params=None, **_kwargs):
        params = params or {}
        calls.append((command, params))
        if command == "find_actors_by_name":
            pattern = params.get("pattern", "")
            hits = [{"name": n} for n in present if pattern in n]
            return {"status": "success", "result": {"actors": hits}}
        if command == "delete_actor":
            name = params.get("name")
            if name in present:
                present.remove(name)
            return {"status": "success"}
        if command == "spawn_actor":
            name = params.get("name")
            # 模拟引擎重名即崩：重名时这里若不先清就会抛（对应真实 Fatal）
            assert name not in present, f"重名 spawn：{name}（会让 UE 崩溃）"
            present.append(name)
            return {"status": "success", "result": {"name": name}}
        return {"status": "success", "result": {}}

    monkeypatch.setattr(spawner, "send_command", fake_send)
    return calls, present


def test_wb_build_clears_before_spawning(monkeypatch):
    """全新场景：build 仍应先发一次 clear（找不到也无妨），再 spawn。"""
    calls, present = _record_bridge(monkeypatch)
    out = wb_server.wb_build(_LAYOUT)
    assert "搭建完成" in out, out
    commands = [c for c, _ in calls]
    assert commands[0] == "find_actors_by_name", "落地前必须先发现并清理同前缀残留"
    assert "spawn_actor" in commands
    # 至少有地板+四墙落地
    assert len(present) >= 1


def test_wb_build_is_idempotent_no_duplicate_spawn(monkeypatch):
    """关键回归：场景里已有同名残留时，重复 build 不得触发重名 spawn（否则真实 UE 崩溃）。"""
    spec = wb_server.layout_from_dict(json.loads(_LAYOUT))
    placements = wb_server.compile_layout(spec, wb_server.load_manifest(wb_server._MANIFEST))
    existing = [f"WB_{p.name}" for p in placements]  # 上一轮已落地的同名构件
    calls, _present = _record_bridge(monkeypatch, existing=existing)

    # 若 wb_build 不先清就 spawn，fake_send 的 assert 会因重名抛出 → 测试失败
    out = wb_server.wb_build(_LAYOUT)
    assert "搭建完成" in out, out
    assert "已先清理" in out, "重建时应报告清理了旧构件"

    # 校验序列：所有 delete 都发生在第一次 spawn 之前
    first_spawn = next(i for i, (c, _) in enumerate(calls) if c == "spawn_actor")
    deletes_after_spawn = [c for c, _ in calls[first_spawn:] if c == "delete_actor"]
    assert not deletes_after_spawn, "清理必须全部在落地之前完成"


def test_wb_build_rolls_back_partial_batch_when_spawn_loses_response(monkeypatch):
    """桥偶发丢响应：spawn 已在 UE 执行但客户端抛错，wb_build 必须清理半批次。"""
    calls: list[tuple[str, dict]] = []
    present: list[str] = []
    spawn_count = 0

    def fake_send(command, params=None, **_kwargs):
        nonlocal spawn_count
        params = params or {}
        calls.append((command, params))
        if command == "find_actors_by_name":
            pattern = params.get("pattern", "")
            return {
                "status": "success",
                "result": {"actors": [{"name": n} for n in present if pattern in n]},
            }
        if command == "delete_actor":
            name = params.get("name")
            if name in present:
                present.remove(name)
            return {"status": "success"}
        if command == "spawn_actor":
            spawn_count += 1
            name = params["name"]
            present.append(name)
            if spawn_count == 2:
                raise ConnectionError("桥连接关闭但 UE 已创建 actor")
            return {"status": "success", "result": {"name": name}}
        return {"status": "success", "result": {}}

    monkeypatch.setattr(spawner, "send_command", fake_send)

    out = wb_server.wb_build(_LAYOUT, prefix="WB_PARTIAL")

    assert out.startswith("[error] 落地失败"), out
    assert present == [], "spawn 中途失败后必须自动清理同前缀半批次"
    first_spawn = next(i for i, (command, _params) in enumerate(calls) if command == "spawn_actor")
    assert any(command == "delete_actor" for command, _params in calls[first_spawn + 1 :])


def test_spawn_uses_unique_names_avoiding_zombie_collision(monkeypatch):
    """根治回归：模拟引擎"僵尸名"（delete 后 find 查不到、命名空间仍占用），

    验证 spawn 用唯一批次名绕开，绝不复用旧名 → 不触发引擎重名 Fatal。
    """
    import ue5agent.whitebox.spawner as sp
    from ue5agent.whitebox.compiler import Placement

    # zombie_names: 引擎命名空间仍占用，但已从 level 列表移除（find 查不到）
    zombie = {"WB_Room_floor"}
    spawned_names: list[str] = []

    def fake_send(command, params=None, **_kwargs):
        params = params or {}
        if command == "find_actors_by_name":
            return {"status": "success", "result": {"actors": []}}  # 僵尸名 find 查不到
        if command == "spawn_actor":
            name = params.get("name")
            # 模拟引擎：spawn 复用仍占命名空间的名字即 Fatal
            assert name not in zombie, f"复用僵尸名 {name} 会触发 UE Fatal 崩溃"
            spawned_names.append(name)
            return {"status": "success", "result": {"name": name}}
        return {"status": "success", "result": {}}

    monkeypatch.setattr(sp, "send_command", fake_send)
    # 旧名是 WB_Room_floor（僵尸）；新 spawn 必须用带批次标记的不同名
    p = Placement(name="Room_floor", asset_path="/x", location=(0, 0, 0), scale=(1, 1, 1))
    names = sp.spawn_layout([p], prefix="WB")
    assert names and names[0] not in zombie, "spawn 名必须唯一，不得复用僵尸名"
    assert names[0].startswith("WB_") and names[0].endswith("_Room_floor"), names[0]


def test_spawn_passes_room_folder_to_spawn_actor(monkeypatch):
    """房间构件落地时应进入 World Outliner 的按房间文件夹。"""
    import ue5agent.whitebox.spawner as sp
    from ue5agent.whitebox.compiler import Placement

    calls: list[tuple[str, dict]] = []

    def fake_send(command, params=None, **_kwargs):
        params = params or {}
        calls.append((command, params))
        return {"status": "success", "result": {"name": params.get("name")}}

    monkeypatch.setattr(sp, "send_command", fake_send)
    p = Placement(
        name="Room_floor",
        asset_path="/Engine/BasicShapes/Cube.Cube",
        location=(0, 0, 0),
        scale=(1, 1, 1),
        metadata={"room": "Room"},
    )

    sp.spawn_layout([p], prefix="WB")

    spawn = next(params for command, params in calls if command == "spawn_actor")
    assert spawn["folder_path"] == "WB/Rooms/Room"
    assert spawn["folder"] == "WB/Rooms/Room"


def test_spawn_names_differ_across_batches(monkeypatch):
    """两次 build 的 spawn 名必须不同批次标记，确保跨批不撞名。"""
    import time

    import ue5agent.whitebox.spawner as sp
    from ue5agent.whitebox.compiler import Placement

    def fake_send(command, params=None, **_kwargs):
        return {"status": "success", "result": {"actors": []}}

    monkeypatch.setattr(sp, "send_command", fake_send)
    p = Placement(name="floor", asset_path="/x", location=(0, 0, 0), scale=(1, 1, 1))
    n1 = sp.spawn_layout([p], prefix="WB")[0]
    time.sleep(0.002)  # 拉开毫秒时间戳
    n2 = sp.spawn_layout([p], prefix="WB")[0]
    assert n1 != n2, f"跨批次 spawn 名必须不同：{n1} vs {n2}"


def test_clear_rechecks_and_succeeds_when_first_delete_drops(monkeypatch):
    """桥丢响应：首轮 delete 未生效，clear_layout 应复查并补删，最终删净（崩溃缺陷修复）。"""
    import ue5agent.whitebox.spawner as sp

    present = ["WB_a_floor", "WB_a_wall"]
    state = {"drop_first": True}

    def fake_send(command, params=None, **_kwargs):
        params = params or {}
        if command == "find_actors_by_name":
            pat = params.get("pattern", "")
            actors = [{"name": n} for n in present if pat in n]
            return {"status": "success", "result": {"actors": actors}}
        if command == "delete_actor":
            name = params.get("name")
            if state["drop_first"]:
                state["drop_first"] = False  # 模拟首个 delete 丢响应：不真正删
                return {"status": "success"}
            if name in present:
                present.remove(name)
            return {"status": "success"}
        return {"status": "success", "result": {}}

    monkeypatch.setattr(sp, "send_command", fake_send)
    removed = sp.clear_layout(prefix="WB")
    assert present == [], "复查重试后必须删净，否则后续 spawn 会撞名崩溃"
    assert removed >= 2


def test_clear_raises_when_cannot_delete(monkeypatch):
    """删不掉的残留（编辑器忙/丢响应持续）必须抛错，绝不静默返回让 spawn 撞名。"""
    import pytest

    import ue5agent.whitebox.spawner as sp

    def fake_send(command, params=None, **_kwargs):
        if command == "find_actors_by_name":
            return {"status": "success", "result": {"actors": [{"name": "WB_stuck"}]}}
        return {"status": "success", "result": {}}  # delete 永远不生效

    monkeypatch.setattr(sp, "send_command", fake_send)
    with pytest.raises(RuntimeError, match="未删净"):
        sp.clear_layout(prefix="WB")
