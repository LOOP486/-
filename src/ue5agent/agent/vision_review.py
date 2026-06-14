"""A4 视觉审查：截图 → vision 角色按 checklist 审查 → 结构化问题列表。

设计取舍：
- 问题用房间/构件名定位（不用像素坐标）——便于 runner 把问题回灌 planner 做
  按构件名子集的局部重生成（A4 子任务 3）。
- severity 分级：high 才算阻断性（驱动修正循环），medium/low 仅记录。
- 解析失败时保守处理（parsed=False、不判 pass），绝不把"看不懂的回答"当作通过。
- 本模块只依赖 ChatModel 协议（与 verifier 一致），可用替身单测，不触 litellm。

vision 角色未配置（config.has_vision False）时由调用方降级为"截图存档供人看"，
本模块对空截图列表返回空结果（passed），不报错。
"""

from __future__ import annotations

import base64
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ue5agent.llm.types import ChatModel

VISION_PROMPT = """\
你是白盒关卡的视觉审查员。依据"关卡需求"和"审查清单"检查截图中的布局问题，
只输出 JSON（不要其它文字）：
{"issues": [{"area": "问题所在的房间或构件名", "issue": "具体问题", "severity": "high|medium|low"}]}

规则：
- 没发现问题时返回 {"issues": []}；
- area 必须用房间名/构件名定位，禁止用像素坐标；
- 只报与需求或清单明确相关的问题，不臆测、不报轻微美观问题；
- severity：high=阻断（布局与需求不符/明显穿插或悬空/应连通处不通），
  medium=偏差（比例失调、位置可疑），low=轻微。
"""

DEFAULT_CHECKLIST = """\
1. 布局与需求一致：房间数量、相对位置、连通关系是否符合需求；
2. 空间组织清楚：主空间、开合关系、遮挡、转角与比例是否能支撑需求；
3. 无明显穿插或悬空：构件贴合地面，没有相互穿模、浮空或陷入地面；
4. 无意义孤立墙：不要出现不围合、不遮挡、不导向的孤立墙段；
5. 不要因缺少门框/窗框扣分；默认 slab 白盒的门窗只表现为空间开口。
"""

_FENCE = re.compile(r"^```(?:json)?\s*(?P<body>.*?)\s*```\s*$", re.DOTALL)
_VALID_SEVERITY = ("high", "medium", "low")
_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

# 视口截图常达 3000+px、数 MB；原图直传会让多模态请求体膨胀到 ~10MB、
# 审查耗时数十秒甚至挂起（真机 e2e 实测教训）。审查前统一降采样 + JPEG 重压。
_MAX_IMAGE_SIDE = 1280
"""降采样目标：长边像素上限（布局识别足够，请求体小一两个数量级）。"""
_JPEG_QUALITY = 80
_MAX_REVIEW_IMAGES = 3
"""单次审查最多送几张图：模型一步内可能连截多张，只取最近的几张定布局。"""


@dataclass
class VisionIssue:
    area: str
    """问题所在的房间名/构件名（定位单位，供局部重生成按子集清理）。"""
    issue: str
    severity: str = "medium"  # high | medium | low


@dataclass
class VisionReviewResult:
    issues: list[VisionIssue] = field(default_factory=list)
    raw: str = ""
    """vision 角色的原始回答（trace 留底，便于排查误判）。"""
    parsed: bool = True
    """是否成功解析出结构化结果；False 表示回答不可解析，需人工。"""

    @property
    def high_severity(self) -> list[VisionIssue]:
        return [i for i in self.issues if i.severity == "high"]

    @property
    def passed(self) -> bool:
        """无任何问题且解析成功才算视觉审查通过。"""
        return self.parsed and not self.issues

    def to_facts(self) -> dict:
        """转 A3 证据信封事实：存在 high 问题或解析失败 → ok=False。"""
        return {
            "kind": "vision_review",
            "ok": self.parsed and not self.high_severity,
            "parsed": self.parsed,
            "issue_count": len(self.issues),
            "high_count": len(self.high_severity),
        }

    def summary(self) -> str:
        if not self.parsed:
            return "视觉审查回答不可解析，需人工查看截图"
        if not self.issues:
            return "视觉审查未发现问题"
        parts = [f"{i.area}：{i.issue}（{i.severity}）" for i in self.issues]
        return f"视觉审查发现 {len(self.issues)} 个问题：" + "；".join(parts)


def image_to_data_url(path: str | Path, *, max_side: int = _MAX_IMAGE_SIDE) -> str:
    """读图片转 base64 data URL（OpenAI 多模态 image_url 格式）。

    大图（长边 > max_side）会降采样并重压为 JPEG，避免请求体过大导致审查极慢/挂起；
    小图原样返回。Pillow 不可用时回退原图（不阻断审查链路，只是请求体偏大）。
    """
    p = Path(path)
    mime = _IMAGE_MIME.get(p.suffix.lower(), "image/png")
    raw = p.read_bytes()
    shrunk = _downscale(raw, max_side)
    if shrunk is not None:
        mime, raw = "image/jpeg", shrunk
    b64 = base64.b64encode(raw).decode()
    return f"data:{mime};base64,{b64}"


def _downscale(raw: bytes, max_side: int) -> bytes | None:
    """长边超过 max_side 时降采样为 JPEG 字节；无需缩放或 Pillow 缺失时返回 None。"""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(io.BytesIO(raw)) as opened:
            if max(opened.size) <= max_side:
                return None
            rgb = opened.convert("RGB")
        ratio = max_side / max(rgb.size)
        new_size = (round(rgb.width * ratio), round(rgb.height * ratio))
        resized = rgb.resize(new_size, Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        resized.save(buf, format="JPEG", quality=_JPEG_QUALITY)
        return buf.getvalue()
    except Exception:
        return None  # 解码/缩放失败不阻断审查，回退原图


def build_review_messages(
    requirement: str,
    screenshot_paths: list[str | Path],
    checklist: str | None = None,
) -> list[dict]:
    """构造多模态审查消息（system + 含图的 user）。"""
    content: list[dict] = [
        {
            "type": "text",
            "text": (
                f"关卡需求：\n{requirement}\n\n"
                f"审查清单：\n{checklist or DEFAULT_CHECKLIST}\n\n"
                f"请审查以下 {len(screenshot_paths)} 张截图并按规则输出 JSON。"
            ),
        }
    ]
    for path in screenshot_paths:
        content.append({"type": "image_url", "image_url": {"url": image_to_data_url(path)}})
    return [
        {"role": "system", "content": VISION_PROMPT},
        {"role": "user", "content": content},
    ]


def parse_review(text: str) -> VisionReviewResult:
    """解析 vision 角色回答为结构化结果；不可解析时 parsed=False（保守）。"""
    body = text.strip()
    fence = _FENCE.match(body)
    if fence:
        body = fence.group("body")
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return VisionReviewResult(issues=[], raw=text, parsed=False)
    if not isinstance(data, dict) or not isinstance(data.get("issues"), list):
        return VisionReviewResult(issues=[], raw=text, parsed=False)
    issues: list[VisionIssue] = []
    for item in data["issues"]:
        if not isinstance(item, dict):
            continue
        area = str(item.get("area", "")).strip() or "未指明区域"
        desc = str(item.get("issue", "")).strip()
        if not desc:
            continue
        severity = str(item.get("severity", "medium")).strip().lower()
        if severity not in _VALID_SEVERITY:
            severity = "medium"
        issues.append(VisionIssue(area=area, issue=desc, severity=severity))
    return VisionReviewResult(issues=issues, raw=text, parsed=True)


async def review_screenshots(
    llm: ChatModel,
    *,
    requirement: str,
    screenshot_paths: list[str | Path],
    checklist: str | None = None,
    role: str = "vision",
    max_images: int = _MAX_REVIEW_IMAGES,
) -> VisionReviewResult:
    """对一组截图做视觉审查。空截图列表直接返回通过（无可审查对象）。

    一步内可能连截多张图，只取最近 max_images 张送审——既定布局靠最新几张即可，
    也避免请求体随重试无限膨胀（真机 e2e 实测教训）。
    """
    if not screenshot_paths:
        return VisionReviewResult(issues=[], raw="", parsed=True)
    selected = screenshot_paths[-max_images:] if max_images > 0 else screenshot_paths
    messages = build_review_messages(requirement, selected, checklist)
    turn = await llm.acomplete(role, messages)
    return parse_review(turn.content or "")
