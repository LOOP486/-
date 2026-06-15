"""ue_editor 关卡验证三工具（A1）：fake bridge，不碰真编辑器。"""

import inspect
import json
from pathlib import Path

from PIL import Image, ImageDraw

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
    assert params["clean_view"] is True
    assert params["margin"] == 1.25


def test_screenshot_camera_params_passthrough(monkeypatch):
    calls = _record_bridge(monkeypatch)
    ed_server.viewport_screenshot(
        file_path="shot.png", location=[100, 200, 3000], rotation=[-90, 0, 0]
    )
    _, params = calls[0]
    assert params["location"] == [100, 200, 3000]
    assert params["rotation"] == [-90, 0, 0]
    assert Path(params["file_path"]).is_absolute()


def test_screenshot_focus_params_passthrough(monkeypatch):
    """白盒截图可要求 UE 侧隐藏干扰元素，并按命名前缀自动聚焦取景。"""
    calls = _record_bridge(monkeypatch)
    ed_server.viewport_screenshot(
        file_path="shot.png",
        clean_view=False,
        focus_prefix="SPC3V_",
        margin=1.5,
    )
    _, params = calls[0]
    assert params["clean_view"] is False
    assert params["focus_prefix"] == "SPC3V_"
    assert params["margin"] == 1.5


def test_screenshot_success_emits_screenshot_facts(monkeypatch, tmp_path):
    """A4：截图成功须落 screenshot 事实，path=实际落盘绝对路径（runner 据此触发视觉审查）。"""
    _record_bridge(monkeypatch)
    path = tmp_path / "shot.png"
    _make_viewport_shot(path, (70, 40, 170, 120))

    out = ed_server.viewport_screenshot(file_path=str(path))
    assert "[facts]" in out
    facts = json.loads(out.split("[facts]", 1)[1].strip())
    assert facts["kind"] == "screenshot"
    assert facts["ok"] is True
    assert Path(facts["path"]) == path.resolve()


def _make_viewport_shot(path: Path, rect: tuple[int, int, int, int]) -> None:
    img = Image.new("RGB", (240, 160), (55, 90, 135))
    draw = ImageDraw.Draw(img)
    draw.rectangle(rect, fill=(120, 118, 110))
    img.save(path)


def _make_gradient_viewport_shot(path: Path, rect: tuple[int, int, int, int]) -> None:
    img = Image.new("RGB", (240, 160), (55, 90, 135))
    pixels = img.load()
    for y in range(img.height):
        for x in range(img.width):
            pixels[x, y] = (40 + y // 2, 80 + y // 3, 130 + y // 4)
    draw = ImageDraw.Draw(img)
    draw.rectangle(rect, fill=(34, 34, 34))
    img.save(path)


def test_screenshot_focus_crops_adjacent_foreground_pollution(monkeypatch, tmp_path):
    """focus_prefix 聚焦后，宽屏视口仍可能拍到旁边旧批次；wrapper 应裁掉邻近主体。"""
    _record_bridge(monkeypatch)
    path = tmp_path / "polluted.png"
    img = Image.new("RGB", (480, 160), (55, 90, 135))
    draw = ImageDraw.Draw(img)
    draw.rectangle((165, 35, 245, 125), fill=(120, 118, 110))
    draw.rectangle((355, 25, 470, 135), fill=(35, 35, 35))
    img.save(path)

    out = ed_server.viewport_screenshot(file_path=str(path), focus_prefix="SPC1V/batch")
    facts = json.loads(out.split("[facts]", 1)[1].strip())

    with Image.open(path) as cropped:
        assert cropped.width < 260
    assert facts["ok"] is True
    assert facts["crop_applied"] is True
    assert facts["crop_bbox"][2] < 320
    assert facts["foreground_bbox"][2] < 260


def test_screenshot_facts_include_frame_quality_for_visible_subject(monkeypatch, tmp_path):
    """截图文件要有本地可检的主体覆盖，避免“只截到天空/边角”也算硬证据。"""
    _record_bridge(monkeypatch)
    path = tmp_path / "framed.png"
    _make_viewport_shot(path, (70, 40, 170, 120))

    out = ed_server.viewport_screenshot(file_path=str(path))
    facts = json.loads(out.split("[facts]", 1)[1].strip())

    assert facts["kind"] == "screenshot"
    assert facts["ok"] is True
    assert facts["framing_ok"] is True
    assert facts["foreground_ratio"] > 0.1
    assert facts["foreground_bbox"] == [70, 40, 170, 120]


def test_screenshot_facts_fail_when_subject_is_out_of_frame(monkeypatch, tmp_path):
    _record_bridge(monkeypatch)
    path = tmp_path / "edge.png"
    _make_viewport_shot(path, (60, 0, 180, 30))

    out = ed_server.viewport_screenshot(file_path=str(path))
    facts = json.loads(out.split("[facts]", 1)[1].strip())

    assert facts["kind"] == "screenshot"
    assert facts["ok"] is False
    assert facts["framing_ok"] is False
    assert "主体不在画面中心" in facts["framing_reason"]


def test_screenshot_facts_fail_when_centered_subject_touches_frame_edge(monkeypatch, tmp_path):
    """主体居中但上下贴边也不可审查，通常表示相机高度太低或裁剪过满。"""
    _record_bridge(monkeypatch)
    path = tmp_path / "tight.png"
    _make_viewport_shot(path, (90, 0, 150, 159))

    out = ed_server.viewport_screenshot(file_path=str(path))
    facts = json.loads(out.split("[facts]", 1)[1].strip())

    assert facts["ok"] is False
    assert facts["framing_ok"] is False
    assert "主体贴近画面边缘" in facts["framing_reason"]


def test_screenshot_frame_quality_ignores_editor_sky_gradient(monkeypatch, tmp_path):
    _record_bridge(monkeypatch)
    path = tmp_path / "gradient-edge.png"
    _make_gradient_viewport_shot(path, (70, 0, 170, 34))

    out = ed_server.viewport_screenshot(file_path=str(path))
    facts = json.loads(out.split("[facts]", 1)[1].strip())

    assert facts["ok"] is False
    assert facts["framing_ok"] is False
    assert "主体不在画面中心" in facts["framing_reason"]


def test_screenshot_facts_fail_when_bridge_reports_success_but_file_missing(monkeypatch, tmp_path):
    _record_bridge(monkeypatch)
    path = tmp_path / "missing.png"

    out = ed_server.viewport_screenshot(file_path=str(path))
    facts = json.loads(out.split("[facts]", 1)[1].strip())

    assert facts["ok"] is False
    assert facts["framing_ok"] is False
    assert "截图文件不存在" in facts["framing_reason"]


def test_screenshot_text_keeps_frame_reason_before_facts(monkeypatch, tmp_path):
    """facts 会被管线剥离；正文也要保留原因，供模型下一轮修正取景。"""
    _record_bridge(monkeypatch)
    path = tmp_path / "missing.png"

    out = ed_server.viewport_screenshot(file_path=str(path))
    visible_text = out.split("[facts]", 1)[0]

    assert "截图取景FAIL" in visible_text
    assert "截图文件不存在" in visible_text


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


def test_screenshot_no_active_viewport_is_env_unready(monkeypatch):
    """编辑器桥在线但没有活动视口时，截图不可由模型换参数恢复，应快速归类为环境未就绪。"""
    _record_bridge(monkeypatch, response={"status": "error", "error": "No active editor viewport"})

    out = ed_server.viewport_screenshot(file_path="shot.png")

    assert ed_server.is_env_unready(out)
    assert "活动视口" in out
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
    "import_fbx",
    "get_mesh_bounds",
    "set_mesh_build_scale",
    "set_static_mesh_material",
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


# ---------- WB-1：import_fbx（批量导入 FBX 为 StaticMesh） ----------


def test_import_fbx_normalizes_tasks_and_passes_flags(monkeypatch):
    """tasks 透传到 import_fbx 桥命令：filename 解析为绝对 posix 路径，flags 一并下发。"""
    calls = _record_bridge(
        monkeypatch,
        response={"status": "success", "result": {"imported": 1, "failed": 0, "results": []}},
    )
    ed_server.import_fbx(
        tasks=[
            {
                "filename": "a/Wall.fbx",
                "destination_path": "/Game/Kit/wall",
                "asset_name": "Wall",
            }
        ],
        import_materials=False,
        import_uniform_scale=100,
        transform_vertex_to_absolute=False,
    )
    command, params = calls[0]
    assert command == "import_fbx"
    assert params["import_materials"] is False
    assert params["replace_existing"] is True and params["save"] is True
    assert params["import_uniform_scale"] == 100.0
    assert params["transform_vertex_to_absolute"] is False
    assert params["bake_pivot_in_vertex"] is False
    task = params["tasks"][0]
    assert Path(task["filename"]).is_absolute(), "C++ 侧 cwd 不同，必须绝对路径"
    assert "\\" not in task["filename"], "应规范成 posix 正斜杠"
    assert task["destination_path"] == "/Game/Kit/wall"
    assert task["asset_name"] == "Wall"


def test_import_fbx_default_scale_and_transform(monkeypatch):
    """默认值：scale=1.0、transform_vertex_to_absolute=True（与 UE 默认一致）。"""
    calls = _record_bridge(
        monkeypatch,
        response={"status": "success", "result": {"imported": 1, "failed": 0, "results": []}},
    )
    ed_server.import_fbx(tasks=[{"filename": "x.fbx", "destination_path": "/Game/K"}])
    _, params = calls[0]
    assert params["import_uniform_scale"] == 1.0
    assert params["transform_vertex_to_absolute"] is True


def test_import_fbx_emits_facts(monkeypatch):
    """导入成功落 import_fbx 事实：failed=0 → ok=True，带 imported/failed 计数。"""
    _record_bridge(
        monkeypatch,
        response={"status": "success", "result": {"imported": 3, "failed": 0, "results": []}},
    )
    out = ed_server.import_fbx(tasks=[{"filename": "x.fbx", "destination_path": "/Game/K"}])
    facts = json.loads(out.split("[facts]", 1)[1].strip())
    assert facts["kind"] == "import_fbx"
    assert facts["ok"] is True and facts["imported"] == 3 and facts["failed"] == 0


def test_import_fbx_partial_failure_marks_not_ok(monkeypatch):
    """有任意一件失败 → ok=False（缺件应暴露，不掩盖）。"""
    _record_bridge(
        monkeypatch,
        response={"status": "success", "result": {"imported": 2, "failed": 1, "results": []}},
    )
    out = ed_server.import_fbx(tasks=[{"filename": "x.fbx", "destination_path": "/Game/K"}])
    facts = json.loads(out.split("[facts]", 1)[1].strip())
    assert facts["ok"] is False and facts["failed"] == 1


def test_import_fbx_empty_tasks_rejected(monkeypatch):
    """空 tasks 直接拒绝，不触发任何桥调用。"""
    calls = _record_bridge(monkeypatch)
    out = ed_server.import_fbx(tasks=[])
    assert out.startswith("[error]")
    assert not calls


def test_import_fbx_missing_fields_rejected(monkeypatch):
    """缺 destination_path 直接拒绝，不触发桥调用。"""
    calls = _record_bridge(monkeypatch)
    out = ed_server.import_fbx(tasks=[{"filename": "x.fbx"}])
    assert out.startswith("[error]")
    assert not calls


def test_import_fbx_bridge_error_emits_no_facts(monkeypatch):
    """桥报错时不得伪造 import_fbx 事实。"""
    _record_bridge(monkeypatch, response={"status": "error", "error": "import failed"})
    out = ed_server.import_fbx(tasks=[{"filename": "x.fbx", "destination_path": "/Game/K"}])
    assert out.startswith("[error]")
    assert "[facts]" not in out


def test_import_fbx_convert_scene_unit_passthrough(monkeypatch):
    """convert_scene_unit 透传到桥命令（米制源 ×100 用）。"""
    calls = _record_bridge(
        monkeypatch,
        response={"status": "success", "result": {"imported": 1, "failed": 0, "results": []}},
    )
    ed_server.import_fbx(
        tasks=[{"filename": "x.fbx", "destination_path": "/Game/K"}], convert_scene_unit=True
    )
    _, params = calls[0]
    assert params["convert_scene_unit"] is True


def test_get_mesh_bounds_calls_bridge(monkeypatch):
    """get_mesh_bounds 透传 asset_path 到 get_mesh_bounds 桥命令。"""
    calls = _record_bridge(
        monkeypatch,
        response={"status": "success", "result": {"size": [800, 20, 400]}},
    )
    ed_server.get_mesh_bounds("/Game/Kit/wall/Wall8_4")
    command, params = calls[0]
    assert command == "get_mesh_bounds"
    assert params == {"asset_path": "/Game/Kit/wall/Wall8_4"}


def test_get_mesh_bounds_empty_rejected(monkeypatch):
    """空 asset_path 直接拒绝，不触发桥调用。"""
    calls = _record_bridge(monkeypatch)
    out = ed_server.get_mesh_bounds("  ")
    assert out.startswith("[error]")
    assert not calls


def test_set_mesh_build_scale_calls_bridge(monkeypatch):
    """set_mesh_build_scale 透传 asset_path/scale 到桥命令。"""
    calls = _record_bridge(
        monkeypatch,
        response={"status": "success", "result": {"size": [800, 20, 400]}},
    )
    ed_server.set_mesh_build_scale("/Game/Kit/wall/Wall8_4", scale=100)
    command, params = calls[0]
    assert command == "set_mesh_build_scale"
    assert params == {"asset_path": "/Game/Kit/wall/Wall8_4", "scale": 100.0}


def test_set_mesh_build_scale_empty_rejected(monkeypatch):
    """空 asset_path 直接拒绝。"""
    calls = _record_bridge(monkeypatch)
    out = ed_server.set_mesh_build_scale("", scale=100)
    assert out.startswith("[error]")
    assert not calls


def test_set_static_mesh_material_calls_bridge(monkeypatch):
    """set_static_mesh_material 透传资产路径、材质路径与 slot 到桥命令。"""
    calls = _record_bridge(monkeypatch)
    ed_server.set_static_mesh_material(
        "/Game/Kit/wall/Wall1_4",
        "/Game/LevelPrototyping/Materials/MI_PrototypeGrid_Gray",
        material_slot=0,
    )

    command, params = calls[0]
    assert command == "set_static_mesh_material"
    assert params == {
        "asset_path": "/Game/Kit/wall/Wall1_4",
        "material_path": "/Game/LevelPrototyping/Materials/MI_PrototypeGrid_Gray",
        "material_slot": 0,
    }


def test_set_static_mesh_material_empty_rejected(monkeypatch):
    """空 asset_path/material_path 直接拒绝。"""
    calls = _record_bridge(monkeypatch)

    out_asset = ed_server.set_static_mesh_material(
        "", "/Game/LevelPrototyping/Materials/MI_PrototypeGrid_Gray"
    )
    out_material = ed_server.set_static_mesh_material("/Game/Kit/wall/Wall1_4", "")

    assert out_asset.startswith("[error]")
    assert out_material.startswith("[error]")
    assert not calls
