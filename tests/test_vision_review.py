"""A4 视觉审查模块：消息构造、问题解析、证据信封、降级。"""

from __future__ import annotations

import base64
import io

from ue5agent.agent.vision_review import (
    VisionReviewResult,
    build_review_messages,
    image_to_data_url,
    parse_review,
    review_screenshots,
)
from ue5agent.llm.types import AssistantTurn


def _make_png(tmp_path, name="shot.png"):
    # 最小合法 PNG（1x1），仅验证读取与 base64 编码链路
    raw = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC"
    )
    p = tmp_path / name
    p.write_bytes(raw)
    return p


class _FakeLLM:
    """记录收到的 messages/role，按脚本返回回答。"""

    def __init__(self, content: str):
        self._content = content
        self.calls: list[tuple[str, list]] = []

    async def acomplete(self, role, messages, tools=None):
        self.calls.append((role, messages))
        return AssistantTurn(content=self._content)


def test_image_to_data_url(tmp_path):
    p = _make_png(tmp_path)
    url = image_to_data_url(p)
    assert url.startswith("data:image/png;base64,")
    # 小图（1x1）不触发降采样，原样返回，可解码回原字节
    payload = url.split(",", 1)[1]
    assert base64.b64decode(payload) == p.read_bytes()


def _make_big_png(tmp_path, name="big.png", size=(3000, 1200)):
    from PIL import Image

    p = tmp_path / name
    Image.new("RGB", size, (10, 20, 30)).save(p, format="PNG")
    return p


def test_large_image_downscaled_to_jpeg(tmp_path):
    """大截图（长边 > 1280）降采样并重压为 JPEG，请求体显著缩小（真机 e2e 修复）。"""
    p = _make_big_png(tmp_path)
    url = image_to_data_url(p)
    assert url.startswith("data:image/jpeg;base64,")
    payload = base64.b64decode(url.split(",", 1)[1])
    # 重压后远小于原 PNG，且能被 Pillow 正常解码、长边收敛到 1280
    assert len(payload) < p.stat().st_size
    from PIL import Image

    with Image.open(io.BytesIO(payload)) as img:
        assert max(img.size) <= 1280


async def test_review_caps_image_count(tmp_path):
    """一步连截多张时只送最近 max_images 张，避免请求体随重试膨胀。"""
    paths = [_make_png(tmp_path, f"s{i}.png") for i in range(5)]
    llm = _FakeLLM('{"issues": []}')
    await review_screenshots(llm, requirement="需求", screenshot_paths=paths, max_images=2)
    _, messages = llm.calls[0]
    image_blocks = [b for b in messages[1]["content"] if b["type"] == "image_url"]
    assert len(image_blocks) == 2  # 只送了最后两张


def test_build_messages_includes_images_and_checklist(tmp_path):
    p1 = _make_png(tmp_path, "a.png")
    p2 = _make_png(tmp_path, "b.png")
    messages = build_review_messages("三房间死斗关卡", [p1, p2], checklist="自定义清单")
    assert messages[0]["role"] == "system"
    user = messages[1]
    assert user["role"] == "user"
    text_block = user["content"][0]
    assert "三房间死斗关卡" in text_block["text"]
    assert "自定义清单" in text_block["text"]
    image_blocks = [b for b in user["content"] if b["type"] == "image_url"]
    assert len(image_blocks) == 2
    assert image_blocks[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_default_review_checklist_focuses_on_blockout_space_not_frames(tmp_path):
    p = _make_png(tmp_path)
    messages = build_review_messages("单层三空间白盒", [p])
    text = messages[1]["content"][0]["text"]

    assert "不要因缺少门框/窗框、楼梯踏步/扶手、房间文字标签扣分" in text
    assert "楼梯只需表达可通行体量" in text
    for keyword in ("主空间", "开合", "遮挡", "转角", "比例", "无意义孤立墙"):
        assert keyword in text


def test_parse_no_issues():
    result = parse_review('{"issues": []}')
    assert result.parsed is True
    assert result.passed is True
    assert result.issues == []
    assert result.to_facts() == {
        "kind": "vision_review",
        "ok": True,
        "parsed": True,
        "issue_count": 0,
        "high_count": 0,
    }


def test_parse_issues_with_fence_and_severity():
    text = """```json
{"issues": [
  {"area": "房间A", "issue": "与房间B未连通", "severity": "high"},
  {"area": "走廊", "issue": "比例偏窄", "severity": "low"},
  {"area": "门", "issue": "位置可疑"}
]}
```"""
    result = parse_review(text)
    assert result.parsed is True
    assert result.passed is False
    assert len(result.issues) == 3
    assert result.issues[0].area == "房间A"
    assert result.issues[0].severity == "high"
    # 缺 severity 的条目回退 medium
    assert result.issues[2].severity == "medium"
    assert len(result.high_severity) == 1
    facts = result.to_facts()
    assert facts["ok"] is False
    assert facts["high_count"] == 1
    assert facts["issue_count"] == 3


def test_medium_and_low_issues_are_report_only_not_blocking():
    result = parse_review(
        """{"issues": [
          {"area": "走廊", "issue": "比例偏窄", "severity": "medium"},
          {"area": "角落", "issue": "轻微遮挡", "severity": "low"}
        ]}"""
    )

    assert result.parsed is True
    assert result.passed is True
    facts = result.to_facts()
    assert facts["ok"] is True
    assert facts["high_count"] == 0
    assert facts["issue_count"] == 2


def test_non_visual_path_metrics_are_not_blocking_high_issues():
    """路径长度/NavMesh 这类指标由确定性工具验收，不应被截图审查提前判 high。"""
    result = parse_review(
        """{"issues": [
          {
            "area": "整体布局",
            "issue": "其中至少一条路径长度没有达到至少1500单位的要求。",
            "severity": "high"
          },
          {"area": "入口", "issue": "入口和中心厅之间缺少可见门洞", "severity": "high"}
        ]}"""
    )

    assert result.parsed is True
    assert result.issues[0].severity == "medium"
    assert result.issues[1].severity == "medium"
    assert result.passed is True
    assert result.to_facts()["high_count"] == 0


def test_tool_verified_opening_visibility_is_not_blocking_high_issue():
    """门洞/开口/窗户存在性由 validator/path facts 兜底，截图误判只进报告。"""
    result = parse_review(
        """{"issues": [
          {"area": "turn_corridor", "issue": "缺少门洞", "severity": "high"},
          {"area": "entry", "issue": "缺少门洞", "severity": "high"},
          {"area": "外墙", "issue": "未看到外墙窗户开口", "severity": "high"}
        ]}"""
    )

    assert result.parsed is True
    assert [issue.severity for issue in result.issues] == ["medium", "medium", "medium"]
    assert result.passed is True
    assert result.to_facts()["high_count"] == 0


def test_hard_visual_geometry_still_blocks_when_structure_words_appear():
    """穿插/悬空/重叠/错位仍是视觉 high，不被门洞等关键词误降级。"""
    result = parse_review(
        """{"issues": [
          {"area": "stairwell", "issue": "楼梯开口被墙体穿插堵住", "severity": "high"},
          {"area": "shared_wall", "issue": "共享墙与房间边界明显错位", "severity": "high"}
        ]}"""
    )

    assert result.parsed is True
    assert [issue.severity for issue in result.issues] == ["high", "high"]
    assert result.passed is False
    assert result.to_facts()["high_count"] == 2


def test_non_visual_exact_grid_distance_is_not_blocking_high_issue():
    """中心距/精确格数这类数字约束不应由截图审查作为 high 阻断。"""
    result = parse_review(
        """{"issues": [
          {
            "area": "全体布局",
            "issue": "尽端房间中心距入口小于16格，不符合需求中至少16格的要求。",
            "severity": "high"
          }
        ]}"""
    )

    assert result.parsed is True
    assert result.issues[0].severity == "medium"
    assert result.passed is True
    assert result.to_facts()["high_count"] == 0


def test_review_prompt_excludes_non_visual_metrics(tmp_path):
    p = _make_png(tmp_path)
    messages = build_review_messages("需要 path_test 达到 1500uu", [p])
    system_text = messages[0]["content"]
    user_text = messages[1]["content"][0]["text"]

    assert "不要判断 path_length、NavMesh、path_test" in system_text
    assert "精确格数/中心距/距离阈值" in system_text
    assert "疑似缺少门洞或窗户" in system_text
    assert "不要根据截图判断导航网格、path_test、path_length" in user_text
    assert "不要判断精确格数、中心距、米制/uu 距离阈值" in user_text
    assert "门洞、连通口、窗户、共享墙对齐" in user_text


def test_parse_unparseable_is_conservative():
    result = parse_review("我没看清楚，建议人工检查。")
    assert result.parsed is False
    assert result.passed is False
    assert result.to_facts()["ok"] is False


def test_parse_invalid_severity_falls_back_to_medium():
    result = parse_review('{"issues": [{"area": "x", "issue": "y", "severity": "critical"}]}')
    assert result.issues[0].severity == "medium"


def test_parse_skips_items_without_description():
    payload = '{"issues": [{"area": "x", "issue": ""}, {"area": "y", "issue": "真问题"}]}'
    result = parse_review(payload)
    assert len(result.issues) == 1
    assert result.issues[0].issue == "真问题"


async def test_review_screenshots_calls_vision_role(tmp_path):
    p = _make_png(tmp_path)
    llm = _FakeLLM('{"issues": []}')
    result = await review_screenshots(llm, requirement="需求", screenshot_paths=[p])
    assert result.passed is True
    assert len(llm.calls) == 1
    role, messages = llm.calls[0]
    assert role == "vision"
    image_blocks = [b for b in messages[1]["content"] if b["type"] == "image_url"]
    assert len(image_blocks) == 1


async def test_review_empty_screenshots_passes_without_calling_llm():
    llm = _FakeLLM('{"issues": [{"area":"x","issue":"y","severity":"high"}]}')
    result = await review_screenshots(llm, requirement="需求", screenshot_paths=[])
    assert result.passed is True
    assert llm.calls == []


def test_summary_text():
    assert "未发现问题" in VisionReviewResult().summary()
    bad = parse_review('{"issues": [{"area": "房间A", "issue": "悬空", "severity": "high"}]}')
    assert "房间A" in bad.summary()
    assert "悬空" in bad.summary()
