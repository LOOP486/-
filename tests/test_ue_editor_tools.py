"""ue_editor 关卡验证三工具（A1）：fake bridge，不碰真编辑器。"""

import inspect
import json
from pathlib import Path

import ue5agent.mcp_servers.ue_editor.server as ed_server


def _record_bridge(monkeypatch, response=None):
    """替换 send_command，记录 (command, params) 序列并返回固定响应。"""
    calls: list[tuple[str, dict]] = []

    def fake_send(command, params=None, **_kwargs):
        calls.append((command, params or {}))
        return response or {"status": "success", "result": {"ok": True}}

    monkeypatch.setattr(ed_server, "send_command", fake_send)
    return calls


def test_screenshot_default_path_is_absolute_png(monkeypatch):
    calls = _record_bridge(monkeypatch)
    ed_server.viewport_screenshot()
    command, params = calls[0]
    assert command == "viewport_screenshot"
    file_path = Path(params["file_path"])
    assert file_path.is_absolute(), "C++ 侧进程 cwd 不同，必须传绝对路径"
    assert file_path.suffix == ".png"
    assert "screenshots" in file_path.parts
    assert "location" not in params and "rotation" not in params


def test_screenshot_camera_params_passthrough(monkeypatch):
    calls = _record_bridge(monkeypatch)
    ed_server.viewport_screenshot(
        file_path="shot.png", location=[100, 200, 3000], rotation=[-90, 0, 0]
    )
    _, params = calls[0]
    assert params["location"] == [100, 200, 3000]
    assert params["rotation"] == [-90, 0, 0]
    assert Path(params["file_path"]).is_absolute()


def test_screenshot_success_emits_screenshot_facts(monkeypatch):
    """A4：截图成功须落 screenshot 事实，path=实际落盘绝对路径（runner 据此触发视觉审查）。"""
    _record_bridge(monkeypatch)
    out = ed_server.viewport_screenshot(file_path="shot.png")
    assert "[facts]" in out
    facts = json.loads(out.split("[facts]", 1)[1].strip())
    assert facts["kind"] == "screenshot"
    assert facts["ok"] is True
    assert Path(facts["path"]) == Path("shot.png").resolve()


def test_screenshot_bridge_error_emits_no_facts(monkeypatch):
    """桥报错时不得伪造 screenshot 事实（否则会审查到不存在的截图）。"""
    _record_bridge(monkeypatch, response={"status": "error", "error": "viewport busy"})
    out = ed_server.viewport_screenshot(file_path="shot.png")
    assert out.startswith("[error]")
    assert "[facts]" not in out


def test_screenshot_env_unready_emits_no_facts(monkeypatch):
    def fake_send(command, params=None, **_kwargs):
        raise ConnectionRefusedError("no editor")

    monkeypatch.setattr(ed_server, "send_command", fake_send)
    out = ed_server.viewport_screenshot(file_path="shot.png")
    assert ed_server.is_env_unready(out)
    assert "[facts]" not in out


def test_navmesh_rebuild_composes_ensure_bounds(monkeypatch):
    calls = _record_bridge(monkeypatch)
    ed_server.navmesh_rebuild(bounds_center=[0, 0, 0], bounds_extent=[3000, 3000, 500])
    command, params = calls[0]
    assert command == "navmesh_rebuild"
    assert params["ensure_bounds"] == {"center": [0, 0, 0], "extent": [3000, 3000, 500]}


def test_navmesh_rebuild_without_bounds_sends_empty(monkeypatch):
    calls = _record_bridge(monkeypatch)
    ed_server.navmesh_rebuild()
    assert calls[0] == ("navmesh_rebuild", {})


def test_navmesh_rebuild_half_bounds_rejected_before_bridge(monkeypatch):
    calls = _record_bridge(monkeypatch)
    out = ed_server.navmesh_rebuild(bounds_center=[0, 0, 0])
    assert out.startswith("[error]")
    assert not calls, "参数不完整不应触发桥调用"


def test_path_test_passes_endpoints(monkeypatch):
    calls = _record_bridge(monkeypatch)
    ed_server.path_test(start=[0, 0, 0], end=[1000, 0, 0])
    command, params = calls[0]
    assert command == "path_test"
    assert params == {"start": [0, 0, 0], "end": [1000, 0, 0]}


def test_bridge_error_mapped_to_error_text(monkeypatch):
    _record_bridge(
        monkeypatch,
        response={"status": "error", "error": "No navmesh built; run navmesh_rebuild first"},
    )
    out = ed_server.path_test(start=[0, 0, 0], end=[1, 1, 1])
    assert out.startswith("[error]")
    assert "navmesh_rebuild" in out


# ---------- C1 (P1.2) 裁剪与分级：瘦桥只暴露挑选过的只读命令 ----------

# 注册进瘦桥的工具（蓝图只读 + 场景读取 + 截图 + 导航验证）。增删工具时同步本清单与
# docs/phase1-bridge-plan.md 的分级表——这是 ADR-0003"蓝图只读"的执行边界。
_EXPECTED_TOOLS = {
    "editor_status",
    "editor_actors",
    "actor_find",
    "bp_read",
    "bp_analyze",
    "bp_overview",
    "bp_pseudocode",
    "bp_find_usages",
    "viewport_screenshot",
    "navmesh_rebuild",
    "path_test",
}

# 编辑/批量构建类桥命令一律不得在瘦桥源码中出现（防止未来误暴露写能力）。
_FORBIDDEN_BRIDGE_COMMANDS = (
    "spawn_actor",
    "delete_actor",
    "set_actor_transform",
    "set_actor_property",
    "add_component",
    "compile_blueprint",
    "create_blueprint",
    "connect_blueprint_nodes",
    "create_town",
    "spawn_blueprint_actor",
)


def test_thin_bridge_registers_exactly_vetted_tools():
    """瘦桥暴露的工具集恰好是审定过的只读集（+ navmesh 写）。"""
    tool_names = {
        name
        for name, obj in vars(ed_server).items()
        if inspect.isfunction(obj)
        and not name.startswith("_")
        and name not in {"main"}
        and obj.__module__ == ed_server.__name__
    }
    assert tool_names == _EXPECTED_TOOLS


def test_no_edit_or_batch_bridge_commands_forwarded():
    """瘦桥源码不出现任何编辑/批量构建类桥命令（ADR-0003 蓝图只读的硬边界）。"""
    src = inspect.getsource(ed_server)
    for command in _FORBIDDEN_BRIDGE_COMMANDS:
        assert command not in src, f"瘦桥不应转发编辑类命令：{command}"


def test_blueprint_tools_are_readonly_calls(monkeypatch):
    """bp_read/bp_analyze 只发只读查询命令，不触发任何写操作。"""
    calls = _record_bridge(monkeypatch, response={"status": "success", "result": {}})
    ed_server.bp_read("/Game/BP_X")
    ed_server.bp_analyze("/Game/BP_X", graph_name="Move")
    commands = [c for c, _ in calls]
    assert commands == ["read_blueprint_content", "analyze_blueprint_graph"]
