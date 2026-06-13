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
    "output_log_tail",
    "pie_smoke",
    "run_functional_test",
    "functest_list",
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


# ---------- E1 运行期验证：output_log_tail / pie_smoke ----------


def test_output_log_tail_emits_facts(monkeypatch):
    """output_log_tail 透传过滤参数并落 output_log 事实（总错误/警告计数）。"""
    calls = _record_bridge(
        monkeypatch,
        response={
            "status": "success",
            "result": {
                "severity": "error",
                "line_count": 1,
                "lines": ["[Error] LogX: boom"],
                "total_errors": 5,
                "total_warnings": 12,
            },
        },
    )
    out = ed_server.output_log_tail(lines=50, severity="error")
    command, params = calls[0]
    assert command == "output_log_tail"
    assert params == {"lines": 50, "severity": "error"}
    facts = json.loads(out.split("[facts]", 1)[1].strip())
    assert facts["kind"] == "output_log"
    assert facts["total_errors"] == 5 and facts["total_warnings"] == 12


def test_pie_smoke_orchestrates_start_sleep_stop(monkeypatch):
    """pie_smoke 串起 pie_start→sleep→pie_stop；零错误时 ok=True。"""
    commands: list[str] = []

    def fake_send(command, params=None, **_kwargs):
        commands.append(command)
        if command == "pie_start":
            return {"status": "success", "result": {"pie_requested": True}}
        return {
            "status": "success",
            "result": {"was_playing": True, "error_count": 0, "warning_count": 1, "errors": []},
        }

    monkeypatch.setattr(ed_server, "send_command", fake_send)
    monkeypatch.setattr(ed_server.time, "sleep", lambda _s: None)  # 别真睡
    out = ed_server.pie_smoke(seconds=2)
    assert commands == ["pie_start", "pie_stop"]
    facts = json.loads(out.split("[facts]", 1)[1].strip())
    assert facts["kind"] == "pie"
    assert facts["ok"] is True and facts["error_count"] == 0


def test_pie_smoke_reports_runtime_errors(monkeypatch):
    """PIE 期间有 Error → 事实 ok=False，带错误计数。"""

    def fake_send(command, params=None, **_kwargs):
        if command == "pie_start":
            return {"status": "success", "result": {}}
        return {"status": "success", "result": {"error_count": 3, "warning_count": 0}}

    monkeypatch.setattr(ed_server, "send_command", fake_send)
    monkeypatch.setattr(ed_server.time, "sleep", lambda _s: None)
    out = ed_server.pie_smoke(seconds=1)
    facts = json.loads(out.split("[facts]", 1)[1].strip())
    assert facts["ok"] is False and facts["error_count"] == 3


def test_pie_smoke_start_failure_short_circuits(monkeypatch):
    """pie_start 失败（编辑器未开）直接返回，不再调 pie_stop。"""
    commands: list[str] = []

    def fake_send(command, params=None, **_kwargs):
        commands.append(command)
        raise ConnectionRefusedError("no editor")

    monkeypatch.setattr(ed_server, "send_command", fake_send)
    monkeypatch.setattr(ed_server.time, "sleep", lambda _s: None)
    out = ed_server.pie_smoke()
    assert ed_server.is_env_unready(out)
    assert commands == ["pie_start"]


# ---------- E1 收尾：run_functional_test（Automation/Functional Test，start + 轮询） ----------


def test_run_functional_test_polls_until_finished_pass(monkeypatch):
    """触发后轮询至 finished；passed 时落 functional_test 事实 ok=True。"""
    commands: list[str] = []
    polls = iter(
        [
            {"status": "success", "result": {"finished": False}},
            {
                "status": "success",
                "result": {"finished": True, "passed": True, "error_count": 0, "warning_count": 2},
            },
        ]
    )

    def fake_send(command, params=None, **_kwargs):
        commands.append(command)
        if command == "functest_start":
            return {"status": "success", "result": {"started": True}}
        return next(polls)

    monkeypatch.setattr(ed_server, "send_command", fake_send)
    monkeypatch.setattr(ed_server.time, "sleep", lambda _s: None)
    out = ed_server.run_functional_test("FT_Combat", timeout=30)
    assert commands == ["functest_start", "functest_poll", "functest_poll"]
    facts = json.loads(out.split("[facts]", 1)[1].strip())
    assert facts["kind"] == "functional_test"
    assert facts["ok"] is True and facts["passed"] is True
    assert facts["test_name"] == "FT_Combat" and facts["warning_count"] == 2


def test_run_functional_test_reports_failure(monkeypatch):
    """测试 finished 但 passed=False → 事实 ok=False，带错误计数。"""

    def fake_send(command, params=None, **_kwargs):
        if command == "functest_start":
            return {"status": "success", "result": {}}
        return {
            "status": "success",
            "result": {"finished": True, "passed": False, "error_count": 4},
        }

    monkeypatch.setattr(ed_server, "send_command", fake_send)
    monkeypatch.setattr(ed_server.time, "sleep", lambda _s: None)
    out = ed_server.run_functional_test("FT_Combat")
    facts = json.loads(out.split("[facts]", 1)[1].strip())
    assert facts["ok"] is False and facts["passed"] is False and facts["error_count"] == 4


def test_run_functional_test_timeout_does_not_fabricate(monkeypatch):
    """始终 finished=False → 超时不落 functional_test 事实（缺证据应判 fail 而非假成功）。"""

    def fake_send(command, params=None, **_kwargs):
        if command == "functest_start":
            return {"status": "success", "result": {}}
        return {"status": "success", "result": {"finished": False}}

    monkeypatch.setattr(ed_server, "send_command", fake_send)
    monkeypatch.setattr(ed_server.time, "sleep", lambda _s: None)
    out = ed_server.run_functional_test("FT_Stuck", timeout=5, poll_interval=1)
    assert "[facts]" not in out
    assert "未完成" in out


def test_run_functional_test_empty_name_rejected(monkeypatch):
    """空 test_name 直接拒绝，不触发任何桥调用。"""
    calls = _record_bridge(monkeypatch)
    out = ed_server.run_functional_test("  ")
    assert out.startswith("[error]")
    assert not calls


def test_run_functional_test_start_failure_short_circuits(monkeypatch):
    """functest_start 失败（编辑器未开）直接返回，不再轮询。"""
    commands: list[str] = []

    def fake_send(command, params=None, **_kwargs):
        commands.append(command)
        raise ConnectionRefusedError("no editor")

    monkeypatch.setattr(ed_server, "send_command", fake_send)
    monkeypatch.setattr(ed_server.time, "sleep", lambda _s: None)
    out = ed_server.run_functional_test("FT_Combat")
    assert ed_server.is_env_unready(out)
    assert commands == ["functest_start"]


def test_functest_list_passes_filter(monkeypatch):
    """functest_list 透传 filter/max 到 functest_list 桥命令。"""
    calls = _record_bridge(
        monkeypatch,
        response={"status": "success", "result": {"total": 1, "returned": 1, "tests": ["X"]}},
    )
    ed_server.functest_list(filter="Functional", max=50)
    command, params = calls[0]
    assert command == "functest_list"
    assert params == {"max": 50, "filter": "Functional"}
