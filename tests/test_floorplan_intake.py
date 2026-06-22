"""平面图输入识别：本地图片 → 白盒布局 DSL 的前置链路。"""

from __future__ import annotations

import base64
import json

import pytest
from typer.testing import CliRunner

from ue5agent.agent.events import RunWriter, read_events
from ue5agent.agent.floorplan import (
    FloorplanRecognitionError,
    build_floorplan_messages,
    parse_floorplan_response,
    prepare_floorplan_task,
    recognize_floorplan,
    validate_floorplan_image_path,
)
from ue5agent.agent.state import TaskSession
from ue5agent.config import AgentSettings, ModelsConfig
from ue5agent.core.permissions import PermissionGate
from ue5agent.llm.types import AssistantTurn
from ue5agent.tools.registry import ToolRegistry


def _make_png(tmp_path, name: str = "plan.png"):
    raw = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC"
    )
    path = tmp_path / name
    path.write_bytes(raw)
    return path


def _make_wall_png(tmp_path, name: str = "wall_plan.png"):
    from PIL import Image, ImageDraw

    path = tmp_path / name
    image = Image.new("RGB", (160, 120), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 50, 110, 55), fill=(0, 0, 0))
    draw.rectangle((105, 50, 110, 100), fill=(0, 0, 0))
    image.save(path)
    return path


class _FakeLLM:
    def __init__(self, *responses: str):
        self._responses = list(responses)
        self.calls: list[tuple[str, list[dict], object]] = []

    async def acomplete(self, role, messages, tools=None):
        self.calls.append((role, messages, tools))
        return AssistantTurn(content=self._responses.pop(0))


def _valid_payload() -> str:
    return json.dumps(
        {
            "ok": True,
            "confidence": 0.82,
            "layout": {
                "name": "floorplan_blockout",
                "rooms": [
                    {
                        "name": "Entry",
                        "rect": [0, 0, 4, 4],
                        "doors": [{"wall": "east", "at": 1, "width": 2}],
                    },
                    {
                        "name": "Hall",
                        "rect": [4, 0, 6, 4],
                        "doors": [{"wall": "west", "at": 1, "width": 2}],
                    },
                ],
            },
            "assumptions": ["按入口在左侧处理"],
            "warnings": ["图中窗户较模糊，已省略"],
        },
        ensure_ascii=False,
    )


def test_build_floorplan_messages_include_image_and_topology_prompt(tmp_path):
    image = _make_png(tmp_path)

    messages = build_floorplan_messages(image, user_goal="根据这张图生成白盒")

    assert messages[0]["role"] == "system"
    assert "只输出 JSON" in messages[0]["content"]
    assert "拓扑优先" in messages[0]["content"]
    assert "紧凑整数网格" in messages[0]["content"]
    assert "不要把整张图纸外框" in messages[0]["content"]
    user = messages[1]
    assert user["role"] == "user"
    text = user["content"][0]["text"]
    assert "根据这张图生成白盒" in text
    assert "structure_mode" in text and "slab" in text
    images = [block for block in user["content"] if block["type"] == "image_url"]
    assert len(images) == 1
    assert images[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_parse_floorplan_response_accepts_fenced_json_and_defaults_layout():
    result = parse_floorplan_response(f"```json\n{_valid_payload()}\n```")

    assert result.ok is True
    assert result.confidence == pytest.approx(0.82)
    assert result.layout["structure_mode"] == "slab"
    assert result.layout["scale_profile"] == "realistic"
    assert "gameplay" not in result.layout
    assert result.assumptions == ["按入口在左侧处理"]
    assert result.warnings == ["图中窗户较模糊，已省略"]
    assert result.to_facts() == {
        "kind": "floorplan_recognition",
        "ok": True,
        "parsed": True,
        "confidence": pytest.approx(0.82),
        "room_count": 2,
        "warning_count": 1,
    }


def test_parse_floorplan_response_drops_unsupported_layout_fields():
    payload = json.dumps(
        {
            "ok": True,
            "confidence": 0.8,
            "layout": {
                "name": "floorplan_blockout",
                "rooms": [
                    {
                        "name": "Entry",
                        "rect": [0, 0, 4, 4],
                        "doors": [{"wall": "east", "at": 1, "width": 2}],
                    },
                    {
                        "name": "Hall",
                        "rect": [4, 0, 6, 4],
                        "doors": [{"wall": "west", "at": 1, "width": 2}],
                    },
                ],
                "corridor": [{"name": "Side passage", "rect": [10, 0, 4, 4]}],
                "spawn_points": [{"room": "Entry"}],
                "cover": [{"room": "Hall"}],
            },
        },
        ensure_ascii=False,
    )

    result = parse_floorplan_response(payload)

    assert "corridor" not in result.layout
    assert "spawn_points" not in result.layout
    assert "cover" not in result.layout


def test_parse_floorplan_response_accepts_first_json_object_with_trailing_text():
    result = parse_floorplan_response(_valid_payload() + "\n模型补充说明：已按拓扑压缩。")

    assert result.ok is True
    assert result.layout["rooms"][0]["name"] == "Entry"
    assert result.raw.endswith("模型补充说明：已按拓扑压缩。")


def test_parse_floorplan_response_normalizes_room_label_to_name():
    payload = json.dumps(
        {
            "ok": True,
            "confidence": 0.8,
            "layout": {
                "rooms": [
                    {
                        "label": "入口",
                        "rect": [0, 0, 4, 4],
                        "doors": [{"wall": "east", "at": 1, "width": 2}],
                    },
                    {
                        "label": "走廊",
                        "rect": [4, 0, 6, 4],
                        "doors": [{"wall": "west", "at": 1, "width": 2}],
                    },
                ]
            },
        },
        ensure_ascii=False,
    )

    result = parse_floorplan_response(payload)

    assert [room["name"] for room in result.layout["rooms"]] == ["入口", "走廊"]
    assert "label" not in result.layout["rooms"][0]


def test_parse_floorplan_response_normalizes_room_id_to_name():
    payload = json.dumps(
        {
            "ok": True,
            "confidence": 0.8,
            "layout": {
                "rooms": [
                    {
                        "id": "1",
                        "rect": [0, 0, 4, 4],
                        "doors": [{"wall": "east", "at": 1, "width": 2}],
                    },
                    {
                        "id": "2",
                        "rect": [4, 0, 6, 4],
                        "doors": [{"wall": "west", "at": 1, "width": 2}],
                    },
                ]
            },
        },
        ensure_ascii=False,
    )

    result = parse_floorplan_response(payload)

    assert [room["name"] for room in result.layout["rooms"]] == ["1", "2"]
    assert "id" not in result.layout["rooms"][0]


def test_parse_floorplan_response_fills_missing_room_names():
    payload = json.dumps(
        {
            "ok": True,
            "confidence": 0.8,
            "layout": {
                "rooms": [
                    {
                        "rect": [0, 0, 4, 4],
                        "doors": [{"wall": "east", "at": 1, "width": 2}],
                    },
                    {
                        "rect": [4, 0, 6, 4],
                        "doors": [{"wall": "west", "at": 1, "width": 2}],
                    },
                ]
            },
        },
        ensure_ascii=False,
    )

    result = parse_floorplan_response(payload)

    assert [room["name"] for room in result.layout["rooms"]] == ["Room_1", "Room_2"]


def test_parse_floorplan_response_rejects_invalid_layout():
    payload = json.dumps(
        {
            "ok": True,
            "confidence": 0.9,
            "layout": {"rooms": [{"name": "Bad", "rect": [0, 0, 4.5, 4]}]},
        }
    )

    with pytest.raises(FloorplanRecognitionError, match="整数格"):
        parse_floorplan_response(payload)


def test_parse_floorplan_response_rejects_geometry_invalid_layout():
    payload = json.dumps(
        {
            "ok": True,
            "confidence": 0.9,
            "layout": {
                "rooms": [
                    {"name": "Outer", "rect": [0, 0, 10, 10]},
                    {"name": "Inner", "rect": [2, 2, 4, 4]},
                ]
            },
        }
    )

    with pytest.raises(FloorplanRecognitionError, match="内部重叠"):
        parse_floorplan_response(payload)


def test_parse_floorplan_response_repairs_multi_room_overlap_to_safe_topology():
    payload = json.dumps(
        {
            "ok": True,
            "confidence": 0.85,
            "layout": {
                "rooms": [
                    {"name": "VARNANDA", "rect": [-10, -10, 25, 12]},
                    {"name": "SALA", "rect": [-8, -8, 7, 6]},
                    {"name": "BANIO", "rect": [-1, -8, 5, 4]},
                    {"name": "CUCINA", "rect": [4, -8, 6, 5]},
                    {"name": "PISCINA", "rect": [0, 8, 12, 5]},
                ]
            },
        },
        ensure_ascii=False,
    )

    result = parse_floorplan_response(payload)

    names = [room["name"] for room in result.layout["rooms"]]
    assert names == ["SALA", "BANIO", "CUCINA"]
    assert "拓扑优先安全布局" in result.warnings[0]
    assert result.to_facts()["room_count"] == 3
    assert all(room.get("doors") for room in result.layout["rooms"])


async def test_recognize_floorplan_retries_once_after_unparseable_response(tmp_path):
    image = _make_png(tmp_path)
    llm = _FakeLLM("不是 JSON", _valid_payload())

    result = await recognize_floorplan(llm, image, user_goal="按图生成白盒")

    assert result.ok is True
    assert len(llm.calls) == 2
    assert [call[0] for call in llm.calls] == ["vision", "vision"]
    retry_prompt = llm.calls[1][1][-1]["content"]
    assert "平面图识别回答不是合法 JSON" in retry_prompt


async def test_recognize_floorplan_stops_on_model_declared_failure(tmp_path):
    image = _make_png(tmp_path)
    llm = _FakeLLM(
        json.dumps(
            {
                "ok": False,
                "confidence": 0.2,
                "layout": {"rooms": [{"name": "Fallback", "rect": [0, 0, 4, 4]}]},
                "warnings": ["平面图过于模糊"],
            },
            ensure_ascii=False,
        )
    )

    result = await recognize_floorplan(llm, image, user_goal="按图生成白盒")

    assert result.ok is False
    assert result.warnings == ["平面图过于模糊"]
    assert len(llm.calls) == 1


def test_validate_floorplan_image_path_accepts_supported_local_image(tmp_path):
    image = _make_png(tmp_path, "plan.PNG")

    assert validate_floorplan_image_path(image) == image


def test_validate_floorplan_image_path_rejects_missing_or_unsupported(tmp_path):
    with pytest.raises(FloorplanRecognitionError, match="不存在"):
        validate_floorplan_image_path(tmp_path / "missing.png")

    text_file = tmp_path / "plan.txt"
    text_file.write_text("not an image", encoding="utf-8")
    with pytest.raises(FloorplanRecognitionError, match="仅支持"):
        validate_floorplan_image_path(text_file)


async def test_prepare_floorplan_task_saves_artifacts_and_trace_fact(tmp_path):
    image = _make_png(tmp_path)
    llm = _FakeLLM(_valid_payload())
    writer = RunWriter(tmp_path / "runs", TaskSession.new("floorplan"))

    enhanced = await prepare_floorplan_task(
        llm,
        writer,
        image,
        user_goal="根据这张平面图生成白盒",
    )

    assert "平面图识别结果" in enhanced
    assert "layout_json" in enhanced
    assert "wb_build" in enhanced
    assert "wb_validate" in enhanced
    assert "vision_review" in enhanced
    assert "margin=6.0" in enhanced
    artifacts = {artifact.kind: artifact for artifact in writer.session.artifacts}
    assert artifacts["floorplan_image"].path.endswith("/plan.png")
    assert artifacts["floorplan_layout"].path.endswith(".json")
    assert artifacts["floorplan_raw"].path.endswith(".txt")
    layout_path = writer.dir / artifacts["floorplan_layout"].path
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    assert layout["rooms"][0]["name"] == "Entry"

    events = read_events(writer.trace_path)
    rec = [event for event in events if event["event"] == "floorplan_recognition"]
    assert len(rec) == 1
    assert rec[0]["facts"]["kind"] == "floorplan_recognition"
    assert rec[0]["facts"]["ok"] is True
    assert rec[0]["layout_artifact"] == artifacts["floorplan_layout"].path


async def test_prepare_floorplan_task_prefers_wall_extraction_without_vision(tmp_path):
    image = _make_wall_png(tmp_path)
    llm = _FakeLLM()
    writer = RunWriter(tmp_path / "runs", TaskSession.new("floorplan"))

    enhanced = await prepare_floorplan_task(
        llm,
        writer,
        image,
        user_goal="只根据墙体生成白盒",
    )

    assert llm.calls == []
    assert "平面图墙线算法结果" in enhanced
    assert "walls" in enhanced
    assert "floorplan_wall_lines" in enhanced
    artifacts = {artifact.kind: artifact for artifact in writer.session.artifacts}
    assert artifacts["floorplan_wall_line_svg"].path.endswith(".svg")
    assert artifacts["floorplan_wall_layout"].path.endswith("layout_walls.json")
    assert artifacts["floorplan_wall_snap_report"].path.endswith("snap_report.json")
    events = read_events(writer.trace_path)
    rec = [event for event in events if event["event"] == "floorplan_wall_extraction"]
    assert len(rec) == 1
    assert rec[0]["facts"]["ok"] is True
    assert rec[0]["facts"]["line_count"] >= 2
    assert rec[0]["facts"]["snap_report_artifact"] == artifacts["floorplan_wall_snap_report"].path
    assert rec[0]["snap_report_artifact"] == artifacts["floorplan_wall_snap_report"].path


async def test_prepare_floorplan_task_saves_failure_artifacts_when_model_says_not_ok(tmp_path):
    image = _make_png(tmp_path)
    llm = _FakeLLM(
        json.dumps(
            {
                "ok": False,
                "confidence": 0.1,
                "layout": {"rooms": [{"name": "Fallback", "rect": [0, 0, 4, 4]}]},
                "warnings": ["看不清墙线"],
            },
            ensure_ascii=False,
        )
    )
    writer = RunWriter(tmp_path / "runs", TaskSession.new("floorplan"))

    with pytest.raises(FloorplanRecognitionError, match="看不清墙线"):
        await prepare_floorplan_task(llm, writer, image, user_goal="根据图生成")

    artifacts = {artifact.kind: artifact for artifact in writer.session.artifacts}
    assert artifacts["floorplan_image"].path.endswith("/plan.png")
    assert artifacts["floorplan_raw"].path.endswith(".txt")
    assert artifacts["floorplan_layout"].path.endswith(".json")
    events = read_events(writer.trace_path)
    rec = [event for event in events if event["event"] == "floorplan_recognition"]
    assert len(rec) == 1
    assert rec[0]["facts"]["ok"] is False
    assert rec[0]["facts"]["parsed"] is True
    assert rec[0]["raw_artifact"] == artifacts["floorplan_raw"].path


async def test_prepare_floorplan_task_saves_raw_when_recognition_is_unparseable(tmp_path):
    image = _make_png(tmp_path)
    llm = _FakeLLM("不是 JSON", "还是不是 JSON")
    writer = RunWriter(tmp_path / "runs", TaskSession.new("floorplan"))

    with pytest.raises(FloorplanRecognitionError, match="合法 JSON"):
        await prepare_floorplan_task(llm, writer, image, user_goal="根据图生成")

    artifacts = {artifact.kind: artifact for artifact in writer.session.artifacts}
    assert artifacts["floorplan_image"].path.endswith("/plan.png")
    assert artifacts["floorplan_raw"].path.endswith(".txt")
    raw_path = writer.dir / artifacts["floorplan_raw"].path
    assert raw_path.read_text(encoding="utf-8") == "还是不是 JSON"
    events = read_events(writer.trace_path)
    rec = [event for event in events if event["event"] == "floorplan_recognition"]
    assert len(rec) == 1
    assert rec[0]["facts"]["ok"] is False
    assert rec[0]["facts"]["parsed"] is False
    assert rec[0]["layout_artifact"] is None


def test_run_command_accepts_floorplan_option(monkeypatch, tmp_path):
    from ue5agent import cli

    image = _make_png(tmp_path)
    captured = {}

    async def fake_run_single(config, settings, task, *, assume_yes=False, floorplan=None):
        captured["task"] = task
        captured["assume_yes"] = assume_yes
        captured["floorplan"] = floorplan

    monkeypatch.setattr(
        cli,
        "_require_models",
        lambda path: ModelsConfig.model_validate(
            {"providers": {"p": {"api_key_env": "K"}}, "roles": {"planner": "p/model"}}
        ),
    )
    monkeypatch.setattr(cli, "load_agent_settings", lambda path: AgentSettings())
    monkeypatch.setattr(cli, "_run_single", fake_run_single)

    result = CliRunner().invoke(
        cli.app,
        ["run", "根据这张平面图生成白盒", "--floorplan", str(image), "--yes"],
    )

    assert result.exit_code == 0, result.output
    assert captured == {
        "task": "根据这张平面图生成白盒",
        "assume_yes": True,
        "floorplan": image,
    }


async def test_execute_task_prepares_floorplan_before_runner(monkeypatch, tmp_path):
    import ue5agent.agent.runner as runner_module
    from ue5agent import cli

    image = _make_png(tmp_path)
    llm = _FakeLLM(_valid_payload())
    captured = {}

    class _Outcome:
        success = True
        final_answer = "done"
        report = "report"

    class FakeTaskRunner:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self, goal):
            captured["goal"] = goal
            return _Outcome()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runner_module, "TaskRunner", FakeTaskRunner)

    await cli._execute_task(
        llm,
        ToolRegistry(PermissionGate()),
        AgentSettings(),
        "根据这张平面图生成白盒",
        config=ModelsConfig.model_validate(
            {
                "providers": {"p": {"api_key_env": "K"}},
                "roles": {"planner": "p/model", "vision": "p/vision"},
            }
        ),
        floorplan=image,
    )

    assert "平面图识别结果" in captured["goal"]
    assert "layout_json" in captured["goal"]
    trace_files = list((tmp_path / "runs").glob("*/trace.jsonl"))
    assert len(trace_files) == 1
    events = read_events(trace_files[0])
    assert any(
        event["event"] == "floorplan_recognition"
        and event["facts"]["kind"] == "floorplan_recognition"
        for event in events
    )
