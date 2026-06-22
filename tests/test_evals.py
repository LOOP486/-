"""评测 harness：任务加载、检查器、沙盒工具、runner 全链路（mock 模型）。"""

from pathlib import Path

import pytest

from tests.test_loop import FakeModel
from ue5agent.core.loop import LoopResult
from ue5agent.evals.checks import TaskOutcome, evaluate_check
from ue5agent.evals.runner import EvalTask, load_tasks, run_eval
from ue5agent.evals.sandbox import build_sandbox_registry
from ue5agent.llm.types import AssistantTurn, ToolCall

BASIC_TASKS = Path(__file__).parent.parent / "evals" / "tasks" / "basic.yaml"


def outcome(
    final: str = "",
    turns: int = 1,
    calls: list | None = None,
    tool_results: list | None = None,
) -> TaskOutcome:
    return TaskOutcome(
        result=LoopResult(final, turns, len(calls or [])),
        calls=calls or [],
        tool_results=tool_results or [],
    )


class TestChecks:
    def test_tool_called_pass_and_fail(self):
        ok = outcome(calls=[("echo", '{"text": "hi"}')])
        assert evaluate_check({"type": "tool_called", "tool": "echo"}, ok) is None
        assert evaluate_check({"type": "tool_called", "tool": "add"}, ok) is not None

    def test_tool_called_args_contain(self):
        ok = outcome(calls=[("echo", '{"text": "白盒"}')])
        check = {"type": "tool_called", "tool": "echo", "args_contain": "白盒"}
        assert evaluate_check(check, ok) is None
        check_miss = {"type": "tool_called", "tool": "echo", "args_contain": "蓝图"}
        assert evaluate_check(check_miss, ok) is not None

    def test_tool_not_called(self):
        bad = outcome(calls=[("add", "{}")])
        assert evaluate_check({"type": "tool_not_called", "tool": "add"}, bad) is not None

    def test_tool_called_times(self):
        twice = outcome(calls=[("add", "{}"), ("add", "{}")])
        check = {"type": "tool_called_times", "tool": "add", "at_least": 2}
        assert evaluate_check(check, twice) is None
        assert evaluate_check({**check, "at_least": 3}, twice) is not None

    def test_final_contains_and_max_turns(self):
        out = outcome(final="结果是 42", turns=3)
        assert evaluate_check({"type": "final_contains", "text": "42"}, out) is None
        assert evaluate_check({"type": "max_turns", "value": 2}, out) is not None

    def test_no_tool_errors(self):
        bad = outcome(tool_results=["ok", "[error] 未知工具：x"])
        assert evaluate_check({"type": "no_tool_errors"}, bad) is not None

    def test_unknown_check_type_fails_loudly(self):
        assert evaluate_check({"type": "typo_check"}, outcome()) is not None


class TestSandbox:
    async def test_tools_behave(self):
        registry = build_sandbox_registry()
        assert await registry.dispatch("add", '{"a": 17, "b": 25}') == "42"
        assert await registry.dispatch("convert", '{"value_cm": 250, "to": "m"}') == "2.5"
        await registry.dispatch("write_note", '{"key": "k", "content": "v"}')
        assert await registry.dispatch("read_note", '{"key": "k"}') == "v"
        assert "没有名为" in await registry.dispatch("read_note", '{"key": "ghost"}')
        assert registry.calls[0] == ("add", '{"a": 17, "b": 25}')


class TestRunner:
    def test_basic_yaml_loads(self):
        tasks = load_tasks(BASIC_TASKS)
        assert len(tasks) == 10
        assert all(task.checks for task in tasks)

    async def test_report_aggregates_pass_and_fail(self):
        tasks = [
            EvalTask(
                name="will_pass",
                prompt="echo hi",
                checks=[{"type": "tool_called", "tool": "echo"}],
            ),
            EvalTask(
                name="will_fail",
                prompt="echo hi",
                checks=[{"type": "final_contains", "text": "不会出现的文本"}],
            ),
        ]
        scripts = iter(
            [
                [
                    AssistantTurn(None, [ToolCall("c1", "echo", '{"text": "hi"}')]),
                    AssistantTurn("done"),
                ],
                [AssistantTurn("done")],
            ]
        )
        report = await run_eval(tasks, lambda: FakeModel(next(scripts)))
        assert report.pass_rate == 0.5
        passed, failed = report.results
        assert passed.passed and passed.tool_calls == 1
        assert passed.tool_errors == 0
        assert report.tool_error_rate == 0.0
        assert not failed.passed
        assert "不会出现的文本" in failed.failures[0]

    async def test_tool_errors_counted(self):
        task = EvalTask(
            name="calls_unknown_tool",
            prompt="x",
            checks=[{"type": "no_tool_errors"}],
        )
        script = [
            AssistantTurn(None, [ToolCall("c1", "ghost_tool", "{}")]),
            AssistantTurn("done"),
        ]
        report = await run_eval([task], lambda: FakeModel(script))
        assert report.results[0].tool_errors == 1
        assert report.tool_error_rate == 1.0
        assert not report.results[0].passed

    async def test_budget_exhausted_counts_as_failure(self):
        task = EvalTask(
            name="loops_forever",
            prompt="x",
            max_turns=2,
            checks=[{"type": "final_contains", "text": "ok"}],
        )
        endless = [AssistantTurn(None, [ToolCall("c1", "echo", '{"text": "x"}')])] * 2
        report = await run_eval([task], lambda: FakeModel(list(endless)))
        assert not report.results[0].passed
        assert report.pass_rate == 0.0


UE_TASKS = Path(__file__).parent.parent / "evals" / "tasks" / "ue.yaml"
UE_SPACE_TASKS = Path(__file__).parent.parent / "evals" / "tasks" / "ue_space.yaml"
UE_SPACE_VISUAL_TASKS = Path(__file__).parent.parent / "evals" / "tasks" / "ue_space_visual.yaml"


class TestUeSuite:
    """E3/C3 UE 在线档：编排/指标/检查器离线单测（用替身 run_one，不碰真编辑器）。"""

    def test_ue_eval_llm_client_retries_coder_timeout_once(self):
        from ue5agent.cli import _build_ue_eval_llm
        from ue5agent.config import ModelsConfig

        config = ModelsConfig.model_validate(
            {
                "providers": {"deepseek": {"api_key_env": "DEEPSEEK_API_KEY"}},
                "roles": {"planner": "deepseek/deepseek-v4-pro"},
            }
        )

        client = _build_ue_eval_llm(config)

        assert client._max_retries == 2
        assert client._request_timeout == 180.0

    def test_ue_eval_step_budget_covers_timeout_retry(self):
        from ue5agent.cli import UE_EVAL_STEP_WALL_SECONDS

        assert 180.0 < UE_EVAL_STEP_WALL_SECONDS <= 240.0

    def test_ue_eval_builds_vision_reviewer_when_vision_role_exists(self):
        from ue5agent.cli import _build_vision_reviewer

        class DummyLlm:
            has_vision = True

        assert callable(_build_vision_reviewer(DummyLlm()))

    def test_ue_eval_skips_vision_reviewer_without_vision_role(self):
        from ue5agent.cli import _build_vision_reviewer

        class DummyLlm:
            has_vision = False

        assert _build_vision_reviewer(DummyLlm()) is None

    def test_ue_space_task_model_pins_override_eval_roles(self):
        from ue5agent.cli import _apply_ue_task_model_pins
        from ue5agent.config import ModelsConfig
        from ue5agent.evals.ue_suite import UeEvalTask

        config = ModelsConfig.model_validate(
            {
                "providers": {
                    "deepseek": {"api_key_env": "DEEPSEEK_API_KEY"},
                    "moonshot": {
                        "base_url": "https://api.moonshot.cn/v1",
                        "api_key_env": "MOONSHOT_API_KEY",
                    },
                },
                "roles": {
                    "planner": "deepseek/deepseek-chat",
                    "coder": "deepseek/deepseek-chat",
                    "vision": "moonshot/kimi-k2.6",
                },
            }
        )
        task = UeEvalTask(
            name="standard_space",
            prompt="x",
            checks=[{"type": "run_succeeded"}],
            planner_model="deepseek/deepseek-v4-pro",
            vision_model="moonshot/moonshot-v1-8k-vision-preview",
        )

        pinned = _apply_ue_task_model_pins(config, [task], role="planner", model_override=None)

        for role in ("planner", "coder", "judge", "explorer"):
            assert pinned.roles[role] == "deepseek/deepseek-v4-pro"
        assert pinned.roles["vision"] == "moonshot/moonshot-v1-8k-vision-preview"

    def test_ue_space_task_model_pin_rejects_conflicting_cli_override(self):
        from ue5agent.cli import _apply_ue_task_model_pins
        from ue5agent.config import ModelsConfig
        from ue5agent.evals.ue_suite import UeEvalTask

        config = ModelsConfig.model_validate(
            {
                "providers": {"deepseek": {"api_key_env": "DEEPSEEK_API_KEY"}},
                "roles": {"planner": "deepseek/deepseek-chat"},
            }
        )
        task = UeEvalTask(
            name="standard_space",
            prompt="x",
            checks=[{"type": "run_succeeded"}],
            planner_model="deepseek/deepseek-v4-pro",
        )

        with pytest.raises(ValueError, match="固定模型"):
            _apply_ue_task_model_pins(
                config,
                [task],
                role="planner",
                model_override="deepseek/deepseek-chat",
            )

    def test_ue_yaml_loads(self):
        from ue5agent.evals.ue_suite import load_ue_tasks

        tasks = load_ue_tasks(UE_TASKS)
        assert tasks and all(task.checks for task in tasks)
        assert any(t.name == "read_blueprint_and_explain" for t in tasks)

    def test_ue_task_loads_floorplan_image_field(self, tmp_path):
        from ue5agent.evals.ue_suite import load_ue_tasks

        tasks_file = tmp_path / "floorplan_tasks.yaml"
        tasks_file.write_text(
            """
- name: floorplan_smoke
  floorplan_image: test.png
  prompt: 根据这张平面图生成白盒
  checks:
    - { type: run_succeeded }
    - { type: fact_equals, kind: floorplan_recognition, path: ok, equals: true }
""",
            encoding="utf-8",
        )

        tasks = load_ue_tasks(tasks_file)

        assert tasks[0].floorplan_image == str((tmp_path / "test.png").resolve())

    def test_ue_eval_can_check_floorplan_recognition_fact(self):
        from ue5agent.evals.ue_suite import UeRunRecord, evaluate_ue_check

        record = UeRunRecord(
            success=True,
            facts=[
                {
                    "kind": "floorplan_recognition",
                    "ok": True,
                    "room_count": 4,
                    "confidence": 0.8,
                }
            ],
        )

        assert (
            evaluate_ue_check(
                {
                    "type": "fact_equals",
                    "kind": "floorplan_recognition",
                    "path": "ok",
                    "equals": True,
                },
                record,
            )
            is None
        )
        assert (
            evaluate_ue_check(
                {
                    "type": "fact_gte",
                    "kind": "floorplan_recognition",
                    "path": "room_count",
                    "value": 3,
                },
                record,
            )
            is None
        )

    def test_ue_space_yaml_loads(self):
        from ue5agent.evals.ue_suite import load_ue_tasks

        tasks = load_ue_tasks(UE_SPACE_TASKS)
        assert len(tasks) == 6
        assert all("wb_build" in task.prompt for task in tasks)
        assert all(task.prompt_id == "spc-dst-space-v1" for task in tasks)
        assert all(task.prompt_locked is True for task in tasks)
        assert all(task.planner_model == "deepseek/deepseek-v4-pro" for task in tasks)
        assert all(task.vision_model == "moonshot/moonshot-v1-8k-vision-preview" for task in tasks)
        assert all(
            any(check.get("type") == "fact_equals" for check in task.checks) for task in tasks
        )
        expected_prefixes = {
            "slab_branching_training_space": ("SPC1", "[20000,-10000,0]"),
            "slab_loop_gallery_space": ("SPC2", "[25000,-10000,0]"),
            "slab_single_level_stairwell_space": ("SPC3", "[30000,-10000,0]"),
            "slab_arena_space": ("DST1", "[20000,-5000,0]"),
            "slab_crossfire_space": ("DST2", "[25000,-5000,0]"),
            "slab_ring_space": ("DST3", "[30000,-5000,0]"),
        }
        for task in tasks:
            prefix, origin = expected_prefixes[task.name]
            prompt_compact = task.prompt.replace(" ", "")
            assert f'prefix="{prefix}"' in task.prompt
            assert f"origin={origin}" in prompt_compact
            assert "6 个测试" in task.prompt
            assert "不要生成 gameplay" in task.prompt
            assert "props" in task.prompt
            assert "cover" in task.prompt
            assert "spawn_points" in task.prompt
            assert "routes" in task.prompt
            assert "不要调用 viewport_screenshot" in task.prompt
            assert {
                "type": "fact_nonempty",
                "kind": "wb_build",
                "path": "folder_root",
            } in task.checks
            zero_metric_paths = {
                check.get("path")
                for check in task.checks
                if check.get("type") == "fact_lte"
                and check.get("kind") == "wb_validate"
                and check.get("value") == 0
            }
            assert {
                "metrics.prop_count",
                "metrics.spawn_count",
                "metrics.route_count",
                "metrics.parallel_wall_duplicate_count",
            } <= zero_metric_paths
        branch_task = next(task for task in tasks if task.name == "slab_branching_training_space")
        assert "尽端房间中心距入口至少 16 格" in branch_task.prompt
        assert "path_test 必须测试入口到尽端房间" in branch_task.prompt
        assert "稳定训练场模板" in branch_task.prompt
        assert "entry[0,0,6,6]" in branch_task.prompt
        assert "end_room[16,18,6,6]" in branch_task.prompt
        assert "不确定就不要开窗" in branch_task.prompt
        loop_task = next(task for task in tasks if task.name == "slab_loop_gallery_space")
        assert "2x3 环形模板" in loop_task.prompt
        assert "前厅[0,0,6,6]" in loop_task.prompt
        assert "短捷径为长廊 north ↔ 侧厅 south" in loop_task.prompt
        stair_task = next(
            task for task in tasks if task.name == "slab_single_level_stairwell_space"
        )
        assert "稳定楼梯模板" in stair_task.prompt
        assert "main_hall[0,0,14,12]" in stair_task.prompt
        assert "stair_2_001 at=[1,1] facing=north" in stair_task.prompt
        crossfire_task = next(task for task in tasks if task.name == "slab_crossfire_space")
        assert "稳定十字模板" in crossfire_task.prompt
        assert "start_room[-8,0,8,6]" in crossfire_task.prompt
        assert "north_pocket[2,6,4,5]" in crossfire_task.prompt
        assert "south_pocket[2,-5,4,5]" in crossfire_task.prompt
        loop_path_lengths = [
            condition.get("gte")
            for check in loop_task.checks
            if check.get("type") == "fact_any" and check.get("kind") == "path_test"
            for condition in check.get("where", [])
            if condition.get("path") == "path_length"
        ]
        assert loop_path_lengths and max(loop_path_lengths) <= 600

    def test_ue_space_visual_gate_blocks_only_high_vision_issues(self):
        from ue5agent.evals.ue_suite import UeRunRecord, evaluate_ue_check, load_ue_tasks

        tasks = load_ue_tasks(UE_SPACE_VISUAL_TASKS)
        assert tasks and all(task.checks for task in tasks)
        branch_task = next(
            task for task in tasks if task.name == "slab_branching_training_space_visual"
        )
        assert "稳定训练场模板" in branch_task.prompt
        assert "entry[0,0,6,6]" in branch_task.prompt
        assert "end_room[16,18,6,6]" in branch_task.prompt
        assert "不确定就不要开窗" in branch_task.prompt
        crossfire_task = next(task for task in tasks if task.name == "slab_crossfire_space_visual")
        assert "稳定十字模板" in crossfire_task.prompt
        assert "start_room[-8,0,8,6]" in crossfire_task.prompt
        assert "north_pocket[2,6,4,5]" in crossfire_task.prompt
        assert "south_pocket[2,-5,4,5]" in crossfire_task.prompt
        for task in tasks:
            vision_checks = [check for check in task.checks if check.get("kind") == "vision_review"]
            assert {
                "type": "fact_lte",
                "kind": "vision_review",
                "path": "high_count",
                "value": 0,
            } in vision_checks
            assert all(check.get("path") != "issue_count" for check in vision_checks)

        rec = UeRunRecord(
            success=True,
            facts=[
                {
                    "kind": "vision_review",
                    "ok": True,
                    "parsed": True,
                    "high_count": 0,
                    "issue_count": 2,
                }
            ],
        )
        for check in [c for c in tasks[0].checks if c.get("kind") == "vision_review"]:
            assert evaluate_ue_check(check, rec) is None

    def test_checks_cover_success_and_text(self):
        from ue5agent.evals.ue_suite import UeRunRecord, evaluate_ue_check

        ok = UeRunRecord(success=True, final_answer="它继承自 Character，有 Camera 组件")
        assert evaluate_ue_check({"type": "run_succeeded"}, ok) is None
        assert evaluate_ue_check({"type": "final_contains", "text": "Camera"}, ok) is None
        miss = evaluate_ue_check({"type": "final_contains", "text": "蓝图缺失"}, ok)
        assert miss is not None
        any_ok = evaluate_ue_check({"type": "final_contains_any", "texts": ["相机", "Camera"]}, ok)
        assert any_ok is None

    def test_run_failed_check_for_fault_injection(self):
        from ue5agent.evals.ue_suite import UeRunRecord, evaluate_ue_check

        failed = UeRunRecord(success=False, error="env_unready")
        assert evaluate_ue_check({"type": "run_failed"}, failed) is None
        succeeded = UeRunRecord(success=True)
        assert evaluate_ue_check({"type": "run_failed"}, succeeded) is not None

    def test_max_iterations_and_unknown_check(self):
        from ue5agent.evals.ue_suite import UeRunRecord, evaluate_ue_check

        rec = UeRunRecord(success=True, iteration_count=9)
        assert evaluate_ue_check({"type": "max_iterations", "value": 12}, rec) is None
        assert evaluate_ue_check({"type": "max_iterations", "value": 5}, rec) is not None
        assert evaluate_ue_check({"type": "typo"}, rec) is not None

    def test_trace_level_checks_use_tools_and_facts(self):
        from ue5agent.evals.ue_suite import UeRunRecord, evaluate_ue_check

        rec = UeRunRecord(
            success=True,
            tool_calls=[
                "ue_whitebox__wb_build",
                "ue_whitebox__wb_validate",
                "ue_editor__navmesh_rebuild",
                "ue_editor__path_test",
            ],
            facts=[
                {
                    "kind": "wb_validate",
                    "ok": True,
                    "metrics": {
                        "structure_mode": "slab",
                        "scale_warning_count": 0,
                        "wall_fragmentation_score": 0.71,
                    },
                },
                {"kind": "wb_build", "ok": True, "folder_root": "SPC1/abc123"},
                {"kind": "path_test", "ok": True, "reachable": True},
            ],
        )

        assert evaluate_ue_check({"type": "tool_called", "tool": "wb_build"}, rec) is None
        assert (
            evaluate_ue_check(
                {"type": "fact_nonempty", "kind": "wb_build", "path": "folder_root"},
                rec,
            )
            is None
        )
        assert (
            evaluate_ue_check(
                {
                    "type": "fact_equals",
                    "kind": "wb_validate",
                    "path": "metrics.structure_mode",
                    "equals": "slab",
                },
                rec,
            )
            is None
        )
        assert (
            evaluate_ue_check(
                {
                    "type": "fact_lte",
                    "kind": "wb_validate",
                    "path": "metrics.scale_warning_count",
                    "value": 0,
                },
                rec,
            )
            is None
        )
        assert (
            evaluate_ue_check(
                {
                    "type": "fact_lte",
                    "kind": "wb_validate",
                    "path": "metrics.wall_fragmentation_score",
                    "value": 1.0,
                },
                rec,
            )
            is None
        )
        assert evaluate_ue_check({"type": "tool_called", "tool": "viewport_screenshot"}, rec)
        missing_folder = UeRunRecord(success=True, facts=[{"kind": "wb_build", "ok": True}])
        assert "为空或缺失" in evaluate_ue_check(
            {"type": "fact_nonempty", "kind": "wb_build", "path": "folder_root"},
            missing_folder,
        )

    def test_no_tool_errors_check_reads_trace_summary(self):
        from ue5agent.evals.ue_suite import UeRunRecord, evaluate_ue_check

        clean = UeRunRecord(success=True, tool_errors=[])
        dirty = UeRunRecord(success=True, tool_errors=["ue_whitebox__wb_build: [error] bad"])

        assert evaluate_ue_check({"type": "no_tool_errors"}, clean) is None
        assert "工具错误" in evaluate_ue_check({"type": "no_tool_errors"}, dirty)

    def test_no_unrecovered_tool_errors_allows_successful_recovery(self):
        from ue5agent.evals.ue_suite import UeRunRecord, evaluate_ue_check

        recovered = UeRunRecord(
            success=True,
            tool_errors=["ue_whitebox__wb_build: [error] 布局校验未通过"],
        )
        failed = UeRunRecord(
            success=False,
            tool_errors=["ue_whitebox__wb_build: [error] 布局校验未通过"],
        )

        check = {"type": "no_unrecovered_tool_errors"}

        assert evaluate_ue_check(check, recovered) is None
        assert "未恢复工具错误" in evaluate_ue_check(check, failed)

    def test_fact_any_requires_one_fact_to_satisfy_all_conditions(self):
        from ue5agent.evals.ue_suite import UeRunRecord, evaluate_ue_check

        rec = UeRunRecord(
            success=True,
            facts=[
                {"kind": "path_test", "reachable": False, "path_length": 2600.0},
                {"kind": "path_test", "reachable": True, "path_length": 344.0},
                {"kind": "path_test", "reachable": True, "path_length": 1800.0},
            ],
        )
        check = {
            "type": "fact_any",
            "kind": "path_test",
            "where": [
                {"path": "reachable", "equals": True},
                {"path": "path_length", "gte": 1500},
            ],
        }
        assert evaluate_ue_check(check, rec) is None

        too_short = UeRunRecord(
            success=True,
            facts=[
                {"kind": "path_test", "reachable": False, "path_length": 2600.0},
                {"kind": "path_test", "reachable": True, "path_length": 344.0},
            ],
        )
        assert evaluate_ue_check(check, too_short)

    def test_trace_summary_extracts_calls_errors_and_facts(self, tmp_path):
        import json

        from ue5agent.evals.ue_suite import summarize_trace

        trace = tmp_path / "trace.jsonl"
        events = [
            {
                "event": "tool_call",
                "tool": "ue_whitebox__wb_build",
                "result_preview": "ok",
                "facts": {"kind": "wb_build", "ok": True},
            },
            {
                "event": "vision_review",
                "passed": True,
                "facts": {
                    "kind": "vision_review",
                    "ok": True,
                    "parsed": True,
                    "issue_count": 0,
                    "high_count": 0,
                },
            },
            {
                "event": "tool_call",
                "tool": "ue_whitebox__wb_validate",
                "result_preview": "[error] 布局校验未通过",
            },
            {
                "event": "tool_call",
                "tool": "ue_whitebox__wb_build",
                "result_preview": "Error executing tool wb_build: manifest 中没有资产",
            },
            {
                "event": "run_error",
                "failure_type": "llm_timeout",
                "error": "coder TimeoutError",
            },
        ]
        trace.write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in events),
            encoding="utf-8",
        )

        summary = summarize_trace(trace)

        assert summary["tool_calls"] == [
            "ue_whitebox__wb_build",
            "ue_whitebox__wb_validate",
            "ue_whitebox__wb_build",
        ]
        assert summary["facts"] == [
            {"kind": "wb_build", "ok": True},
            {
                "kind": "vision_review",
                "ok": True,
                "parsed": True,
                "issue_count": 0,
                "high_count": 0,
            },
        ]
        assert summary["tool_errors"] == [
            "ue_whitebox__wb_validate: [error] 布局校验未通过",
            "ue_whitebox__wb_build: Error executing tool wb_build: manifest 中没有资产",
        ]
        assert summary["run_errors"] == ["llm_timeout: coder TimeoutError"]

    def test_ue_eval_env_unready_detection_is_specific(self):
        from ue5agent.cli import _ue_record_env_unready
        from ue5agent.evals.ue_suite import UeRunRecord

        bridge_down = UeRunRecord(
            success=False,
            tool_errors=["ue_editor__path_test: [error][env:unready] 连不上编辑器桥"],
        )
        layout_error = UeRunRecord(
            success=False,
            tool_errors=["ue_whitebox__wb_build: [error] 布局校验未通过：楼梯穿墙"],
        )

        assert _ue_record_env_unready(bridge_down)
        assert not _ue_record_env_unready(layout_error)

    def test_ue_eval_llm_timeout_detection_is_specific(self):
        from ue5agent.cli import _ue_record_llm_timeout
        from ue5agent.evals.ue_suite import UeRunRecord

        timeout = UeRunRecord(success=False, error="llm_timeout: 规划阶段异常：CancelledError")
        layout_error = UeRunRecord(
            success=False,
            tool_errors=["ue_whitebox__wb_build: [error] 布局校验未通过：楼梯穿墙"],
        )

        assert _ue_record_llm_timeout(timeout)
        assert not _ue_record_llm_timeout(layout_error)

    async def test_run_ue_suite_aggregates_metrics(self):
        from ue5agent.evals.ue_suite import UeEvalTask, UeRunRecord, run_ue_suite

        tasks = [
            UeEvalTask(name="pass_one_try", prompt="a", checks=[{"type": "run_succeeded"}]),
            UeEvalTask(
                name="fail_text",
                prompt="b",
                checks=[{"type": "final_contains", "text": "缺"}],
            ),
        ]
        records = {
            "pass_one_try": UeRunRecord(
                success=True, final_answer="done", iteration_count=2, max_step_attempts=1
            ),
            "fail_text": UeRunRecord(
                success=True, final_answer="other", iteration_count=5, max_step_attempts=3
            ),
        }

        async def run_one(task):
            return records[task.name]

        report = await run_ue_suite(tasks, run_one)
        assert report.pass_rate == 0.5
        assert report.first_try_pass_rate == 0.5  # 仅 pass_one_try：通过+无重试+零干预
        assert report.avg_iterations == 3.5
        assert report.total_human_intervention == 0

    async def test_run_ue_suite_records_run_error(self):
        from ue5agent.evals.ue_suite import UeEvalTask, UeRunRecord, run_ue_suite

        task = UeEvalTask(name="boom", prompt="x", checks=[{"type": "run_succeeded"}])

        async def run_one(_task):
            return UeRunRecord(success=False, error="RuntimeError: boom")

        report = await run_ue_suite([task], run_one)
        assert not report.results[0].passed
        assert any("boom" in f for f in report.results[0].failures)

    async def test_run_ue_suite_classifies_failure_type_for_report(self):
        from ue5agent.evals.ue_suite import UeEvalTask, UeRunRecord, run_ue_suite

        tasks = [
            UeEvalTask(name="timeout", prompt="x", checks=[{"type": "run_succeeded"}]),
            UeEvalTask(name="viewport_env", prompt="x", checks=[{"type": "run_succeeded"}]),
            UeEvalTask(
                name="vision_high",
                prompt="x",
                checks=[
                    {"type": "fact_lte", "kind": "vision_review", "path": "high_count", "value": 0}
                ],
            ),
            UeEvalTask(
                name="vision_medium_low",
                prompt="x",
                checks=[
                    {"type": "fact_lte", "kind": "vision_review", "path": "issue_count", "value": 0}
                ],
            ),
            UeEvalTask(name="layout_error", prompt="x", checks=[{"type": "no_tool_errors"}]),
        ]
        records = {
            "timeout": UeRunRecord(success=False, error="llm_timeout: coder TimeoutError"),
            "viewport_env": UeRunRecord(
                success=False,
                tool_errors=[
                    "ue_editor__viewport_screenshot: [error][env:unready] 编辑器当前没有活动视口"
                ],
            ),
            "vision_high": UeRunRecord(
                success=True,
                facts=[
                    {
                        "kind": "vision_review",
                        "ok": False,
                        "parsed": True,
                        "issue_count": 1,
                        "high_count": 1,
                    }
                ],
            ),
            "vision_medium_low": UeRunRecord(
                success=True,
                facts=[
                    {
                        "kind": "vision_review",
                        "ok": True,
                        "parsed": True,
                        "issue_count": 2,
                        "high_count": 0,
                    }
                ],
            ),
            "layout_error": UeRunRecord(
                success=False,
                tool_errors=["ue_whitebox__wb_build: [error] 布局校验未通过：楼梯穿墙"],
            ),
        }

        async def run_one(task):
            return records[task.name]

        report = await run_ue_suite(tasks, run_one)

        assert [result.failure_type for result in report.results] == [
            "llm_timeout",
            "env_unready",
            "vision_high",
            "vision_medium_low",
            "layout_error",
        ]
