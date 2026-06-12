"""ue_editor 关卡验证三工具（A1）：fake bridge，不碰真编辑器。"""

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
