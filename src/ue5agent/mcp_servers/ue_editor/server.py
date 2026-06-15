"""ue_editor MCP server：编辑器桥的只读工具集（ADR-0003：蓝图只读）。

启动：uv run python -m ue5agent.mcp_servers.ue_editor（stdio）
前置：UE 编辑器开启且 UnrealMCP 插件已加载（TCP 55557）。
写类命令（spawn/delete/set_*）刻意不暴露，待 P1.2 按 write_project 级单独开放。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from ue5agent.blueprint import format_overview, format_pseudocode, format_usages
from ue5agent.core.errors import ErrorCategory, is_env_unready, mark_env_unready, mark_error
from ue5agent.mcp_servers.ue_editor.bridge import DEFAULT_PORT, probe_editor, send_command

mcp = FastMCP("ue-editor")


def _call(command: str, params: dict[str, Any] | None = None) -> str:
    try:
        response = send_command(command, params)
    except ConnectionRefusedError:
        return mark_env_unready("连不上编辑器桥：请先打开 UE 编辑器（UnrealMCP 插件随工程加载）")
    except (OSError, ConnectionError, TimeoutError) as exc:
        # 区别于 ConnectionRefused（从未开）：连上后又断/超时 = 桥中途掉线，
        # 标 bridge_down，由 runner 探活后决定重连或快速终止（避免对死桥空转重试）。
        return mark_error(ErrorCategory.BRIDGE_DOWN, f"编辑器桥通信中断：{exc}")
    if response.get("status") == "error":
        error = str(response.get("error", response))
        if command == "viewport_screenshot" and "No active editor viewport" in error:
            return mark_env_unready(
                "编辑器当前没有活动视口，无法截图；请打开/激活 Level Editor 视口后重试"
            )
        return f"[error] {response.get('error', response)}"
    return json.dumps(response.get("result", response), ensure_ascii=False)


@mcp.tool()
def editor_status() -> str:
    """探测 UE 编辑器桥是否在线。做任何编辑器相关操作前先调本工具确认环境就绪。

    返回 online/offline——offline 是正常答复而非错误，表示需要先启动编辑器。
    """
    host = os.environ.get("UE_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("UE_MCP_PORT", DEFAULT_PORT))
    if probe_editor():
        return f"online：编辑器桥可达（{host}:{port}）"
    return (
        f"offline：编辑器桥不可达（{host}:{port}）。需要先启动 UE 编辑器并加载工程"
        "（UnrealMCP 插件随工程加载）；若挂载了 ue_lifecycle，可用 editor_launch 启动。"
    )


@mcp.tool()
def editor_actors() -> str:
    """列出当前关卡的全部 Actor（名称与类型）。"""
    return _call("get_actors_in_level")


@mcp.tool()
def actor_find(pattern: str) -> str:
    """按名称模式查找 Actor。"""
    return _call("find_actors_by_name", {"pattern": pattern})


@mcp.tool()
def bp_read(blueprint_path: str) -> str:
    """读取蓝图内容（组件、变量、函数、图表概览）。只读。

    Args:
        blueprint_path: 蓝图资产路径或名字，如 /Game/ThirdPerson/Blueprints/BP_ThirdPersonCharacter
    """
    return _call("read_blueprint_content", {"blueprint_path": blueprint_path})


@mcp.tool()
def bp_analyze(blueprint_path: str, graph_name: str = "") -> str:
    """分析蓝图某张图的节点与连接（只读，token 较大，先用 bp_read / bp_overview）。

    graph_name 缺省=EventGraph；传函数名（如 Move）可取该函数图。返回含 connections
    （{from_node,from_pin,to_node,to_pin}）。
    """
    params: dict[str, Any] = {"blueprint_path": blueprint_path}
    if graph_name:
        params["graph_name"] = graph_name
    return _call("analyze_blueprint_graph", params)


@mcp.tool()
def bp_overview(blueprint_path: str) -> str:
    """蓝图概览（C2，默认视图）：父类/组件/接口/变量/函数/事件图分类，token 远小于原始 JSON。

    适合"这个蓝图是什么、响应哪些输入/事件"的首问；要精确节点图再用 bp_analyze 下钻。
    """
    out = _call("read_blueprint_content", {"blueprint_path": blueprint_path})
    if out.startswith("[error]") or is_env_unready(out):
        return out
    try:
        return format_overview(json.loads(out))
    except (json.JSONDecodeError, AttributeError, TypeError):
        return out  # 解析失败则回退原始文本，不丢信息


@mcp.tool()
def bp_pseudocode(blueprint_path: str, graph_name: str = "") -> str:
    """蓝图某张图的控制流伪代码（C2，token 高效默认视图）。

    graph_name 缺省=EventGraph；传函数名取该函数图。基于节点 exec 连接重建执行流，
    从事件/输入入口缩进列出执行顺序；无连接信息时退回结构化摘要。
    """
    params: dict[str, Any] = {"blueprint_path": blueprint_path}
    if graph_name:
        params["graph_name"] = graph_name
    out = _call("analyze_blueprint_graph", params)
    if out.startswith("[error]") or is_env_unready(out):
        return out
    try:
        return format_pseudocode(json.loads(out))
    except (json.JSONDecodeError, AttributeError, TypeError):
        return out


@mcp.tool()
def bp_find_usages(blueprint_path: str) -> str:
    """查找谁引用了这个蓝图（C2，AssetRegistry 依赖图，只读）。

    回答"谁在用它"——返回引用该蓝图的资产 package 列表（已过滤引擎/自身）。
    """
    out = _call("find_blueprint_references", {"blueprint_path": blueprint_path})
    if out.startswith("[error]") or is_env_unready(out):
        return out
    try:
        return format_usages(json.loads(out))
    except (json.JSONDecodeError, AttributeError, TypeError):
        return out


@mcp.tool()
def viewport_screenshot(
    file_path: str = "",
    location: list[float] | None = None,
    rotation: list[float] | None = None,
    clean_view: bool = True,
    focus_prefix: str | None = None,
    margin: float = 1.25,
) -> str:
    """对编辑器视口截图存为 PNG，返回保存路径与尺寸。

    俯视白盒布局：location=[中心x, 中心y, 高度]、rotation=[-90, 0, 0]（pitch/yaw/roll，
    高度建议为布局对角线长度量级）。不传相机参数则按当前视口视角截图。
    clean_view=True 时由 UE 侧临时隐藏坐标轴、选中描边等编辑器干扰；focus_prefix 用于
    让 UE 侧按 Actor 命名前缀计算目标 bbox 并自动聚焦，margin 为 bbox 外扩倍率。

    Args:
        file_path: 保存路径（.png）；留空自动存到 runs/screenshots/ 下的时间戳文件
        location: 可选，截图前把视口相机移到此位置 [x, y, z]
        rotation: 可选，视口相机旋转 [pitch, yaw, roll]
        clean_view: 截图时是否隐藏编辑器辅助显示元素
        focus_prefix: 可选，按 Actor 名称前缀自动聚焦取景
        margin: focus_prefix 自动取景时的 bbox 外扩倍率
    """
    if not file_path:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        file_path = str(Path("runs") / "screenshots" / f"viewport_{stamp}.png")
    params: dict[str, Any] = {
        "file_path": str(Path(file_path).resolve()),
        "clean_view": clean_view,
        "margin": margin,
    }
    if location is not None:
        params["location"] = location
    if rotation is not None:
        params["rotation"] = rotation
    if focus_prefix:
        params["focus_prefix"] = focus_prefix
    out = _call("viewport_screenshot", params)
    if out.startswith("[error]") or is_env_unready(out):
        return out
    crop = _crop_screenshot_to_primary_component(Path(params["file_path"])) if focus_prefix else {}
    # screenshot 事实：runner 据此发现本步截图并触发 A4 视觉审查（路径 = 实际落盘路径）
    frame = _analyze_screenshot_frame(Path(params["file_path"]))
    facts = {
        "kind": "screenshot",
        "ok": frame["framing_ok"],
        "path": params["file_path"],
        **crop,
        **frame,
    }
    verdict = "PASS" if frame["framing_ok"] else "FAIL"
    note = f"截图取景{verdict}：{frame['framing_reason']}"
    return f"{out}\n{note}\n[facts] {json.dumps(facts, ensure_ascii=False)}"


def _crop_screenshot_to_primary_component(path: Path) -> dict[str, Any]:
    """focus_prefix 只负责 UE 侧取景，宽屏视口仍可能拍到邻近旧测试结构。

    本地按前景连通域做一次保守裁剪：多个主体簇同时出现时，保留最靠近画面中心的簇。
    这能把并排 eval 旧批次裁掉，避免视觉模型把邻近结构当作当前白盒缺陷。
    """
    if not path.exists():
        return {}
    try:
        from PIL import Image
    except ImportError:
        return {}
    try:
        with Image.open(path) as opened:
            original = opened.convert("RGB")
            analyzed = original.copy()
            analyzed.thumbnail((768, 768), Image.Resampling.BILINEAR)
            components = _foreground_components(analyzed)
            selected = _select_primary_component(components, analyzed.size)
            if selected is None:
                return {}
            union = _bbox_union([component["bbox"] for component in components])
            if union is None:
                return {}
            if not _component_crop_worthwhile(selected["bbox"], union, analyzed.size):
                return {}
            scaled_box = _scale_bbox(selected["bbox"], analyzed.size, original.size)
            crop_box = _expand_crop_box(scaled_box, original.size, margin_ratio=0.12)
            cropped = original.crop(crop_box)
            cropped.save(path)
    except Exception:
        return {}
    return {"crop_applied": True, "crop_bbox": list(crop_box)}


def _analyze_screenshot_frame(path: Path) -> dict[str, Any]:
    """本地截图可用性快检：文件存在、画面中有足量且居中的非背景内容。

    这不是替代 vision_review 的语义审查，只防止“截到了天空/边角/空图”也进入
    硬证据通道。背景色取四角均值，前景取与背景差异足够大的像素。
    """
    if not path.exists():
        return _screenshot_frame_fail("截图文件不存在", foreground_ratio=0.0)
    try:
        from PIL import Image
    except ImportError:
        return {"framing_ok": True, "framing_reason": "未安装 Pillow，跳过本地取景快检"}
    try:
        with Image.open(path) as opened:
            original_size = opened.size
            img = opened.convert("RGB")
            if max(img.size) > 512:
                img.thumbnail((512, 512), Image.Resampling.BILINEAR)
            bbox, ratio, centroid = _foreground_stats(img)
    except Exception as exc:
        return _screenshot_frame_fail(f"截图无法解码：{type(exc).__name__}", foreground_ratio=0.0)

    if bbox is None:
        return _screenshot_frame_fail("截图未检测到主体", foreground_ratio=ratio)

    width, height = img.size
    x0, y0, x1, y1 = bbox
    bbox_w = (x1 - x0 + 1) / max(width, 1)
    bbox_h = (y1 - y0 + 1) / max(height, 1)
    center_x, center_y = centroid
    original_bbox = _scale_bbox(bbox, img.size, original_size)
    centroid_out = [round(center_x, 3), round(center_y, 3)]

    if ratio < 0.02:
        return _screenshot_frame_fail(
            "截图主体占比过小",
            foreground_ratio=ratio,
            foreground_bbox=original_bbox,
            foreground_centroid=centroid_out,
        )
    if bbox_w < 0.12 or bbox_h < 0.12:
        return _screenshot_frame_fail(
            "截图主体尺寸过小",
            foreground_ratio=ratio,
            foreground_bbox=original_bbox,
            foreground_centroid=centroid_out,
        )
    if not (0.22 <= center_x <= 0.78 and 0.22 <= center_y <= 0.78):
        return _screenshot_frame_fail(
            "截图主体不在画面中心",
            foreground_ratio=ratio,
            foreground_bbox=original_bbox,
            foreground_centroid=centroid_out,
        )
    return {
        "framing_ok": True,
        "framing_reason": "主体取景正常",
        "foreground_ratio": round(ratio, 4),
        "foreground_bbox": original_bbox,
        "foreground_centroid": centroid_out,
    }


def _foreground_stats(img: Any) -> tuple[list[int] | None, float, tuple[float, float]]:
    width, height = img.size
    pixels = img.load()
    bg = _corner_background_rgb(img)
    threshold = 70
    x0, y0 = width, height
    x1 = y1 = -1
    count = 0
    sum_x = 0
    sum_y = 0
    for y in range(height):
        for x in range(width):
            rgb = pixels[x, y]
            delta = abs(rgb[0] - bg[0]) + abs(rgb[1] - bg[1]) + abs(rgb[2] - bg[2])
            if _looks_like_editor_blue_background(rgb) or delta <= threshold:
                continue
            count += 1
            sum_x += x
            sum_y += y
            x0 = min(x0, x)
            y0 = min(y0, y)
            x1 = max(x1, x)
            y1 = max(y1, y)
    ratio = count / max(width * height, 1)
    if count == 0:
        return None, ratio, (0.0, 0.0)
    centroid = (
        sum_x / count / max(width - 1, 1),
        sum_y / count / max(height - 1, 1),
    )
    return [x0, y0, x1, y1], ratio, centroid


def _foreground_components(img: Any) -> list[dict[str, Any]]:
    width, height = img.size
    pixels = img.load()
    bg = _corner_background_rgb(img)
    threshold = 70
    mask = bytearray(width * height)
    for y in range(height):
        row = y * width
        for x in range(width):
            rgb = pixels[x, y]
            delta = abs(rgb[0] - bg[0]) + abs(rgb[1] - bg[1]) + abs(rgb[2] - bg[2])
            if not _looks_like_editor_blue_background(rgb) and delta > threshold:
                mask[row + x] = 1

    visited = bytearray(width * height)
    components: list[dict[str, Any]] = []
    min_pixels = max(24, int(width * height * 0.002))
    for index, present in enumerate(mask):
        if not present or visited[index]:
            continue
        stack = [index]
        visited[index] = 1
        count = 0
        sum_x = 0
        sum_y = 0
        x0, y0 = width, height
        x1 = y1 = -1
        while stack:
            current = stack.pop()
            x = current % width
            y = current // width
            count += 1
            sum_x += x
            sum_y += y
            x0 = min(x0, x)
            y0 = min(y0, y)
            x1 = max(x1, x)
            y1 = max(y1, y)
            for neighbor in _mask_neighbors(x, y, width, height):
                if mask[neighbor] and not visited[neighbor]:
                    visited[neighbor] = 1
                    stack.append(neighbor)
        if count >= min_pixels:
            components.append(
                {
                    "bbox": [x0, y0, x1, y1],
                    "count": count,
                    "centroid": (sum_x / count, sum_y / count),
                }
            )
    return components


def _mask_neighbors(x: int, y: int, width: int, height: int) -> tuple[int, ...]:
    out: list[int] = []
    if x > 0:
        out.append(y * width + x - 1)
    if x + 1 < width:
        out.append(y * width + x + 1)
    if y > 0:
        out.append((y - 1) * width + x)
    if y + 1 < height:
        out.append((y + 1) * width + x)
    return tuple(out)


def _select_primary_component(
    components: list[dict[str, Any]], size: tuple[int, int]
) -> dict[str, Any] | None:
    if len(components) < 2:
        return None
    width, height = size
    cx, cy = (width - 1) / 2, (height - 1) / 2

    def score(component: dict[str, Any]) -> float:
        x, y = component["centroid"]
        distance = (((x - cx) / max(width, 1)) ** 2 + ((y - cy) / max(height, 1)) ** 2) ** 0.5
        return float(component["count"]) / (0.08 + distance)

    return max(components, key=score)


def _bbox_union(boxes: list[list[int]]) -> list[int] | None:
    if not boxes:
        return None
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def _component_crop_worthwhile(
    selected: list[int], union: list[int], size: tuple[int, int]
) -> bool:
    width, height = size
    selected_area = max(selected[2] - selected[0] + 1, 1) * max(selected[3] - selected[1] + 1, 1)
    union_area = max(union[2] - union[0] + 1, 1) * max(union[3] - union[1] + 1, 1)
    if union_area < selected_area * 1.35:
        return False
    selected_width = (selected[2] - selected[0] + 1) / max(width, 1)
    selected_height = (selected[3] - selected[1] + 1) / max(height, 1)
    return selected_width >= 0.12 and selected_height >= 0.12


def _looks_like_editor_blue_background(rgb: tuple[int, int, int]) -> bool:
    r, g, b = rgb
    return b > 70 and b > r + 20 and b > g + 5


def _corner_background_rgb(img: Any) -> tuple[int, int, int]:
    width, height = img.size
    pixels = img.load()
    sample = max(1, min(width, height) // 20)
    points: list[tuple[int, int, int]] = []
    for xs, ys in (
        (range(sample), range(sample)),
        (range(width - sample, width), range(sample)),
        (range(sample), range(height - sample, height)),
        (range(width - sample, width), range(height - sample, height)),
    ):
        for x in xs:
            for y in ys:
                points.append(pixels[x, y])
    return (
        round(sum(rgb[0] for rgb in points) / len(points)),
        round(sum(rgb[1] for rgb in points) / len(points)),
        round(sum(rgb[2] for rgb in points) / len(points)),
    )


def _scale_bbox(
    bbox: list[int] | tuple[int, int, int, int],
    analyzed_size: tuple[int, int],
    original_size: tuple[int, int],
) -> list[int]:
    if analyzed_size == original_size:
        return list(bbox)
    sx = original_size[0] / analyzed_size[0]
    sy = original_size[1] / analyzed_size[1]
    return [
        round(bbox[0] * sx),
        round(bbox[1] * sy),
        round(bbox[2] * sx),
        round(bbox[3] * sy),
    ]


def _expand_crop_box(
    bbox: list[int], image_size: tuple[int, int], *, margin_ratio: float
) -> tuple[int, int, int, int]:
    width, height = image_size
    x0, y0, x1, y1 = bbox
    margin = max(12, round(max(x1 - x0 + 1, y1 - y0 + 1) * margin_ratio))
    return (
        max(0, x0 - margin),
        max(0, y0 - margin),
        min(width, x1 + margin + 1),
        min(height, y1 + margin + 1),
    )


def _screenshot_frame_fail(
    reason: str,
    *,
    foreground_ratio: float,
    foreground_bbox: list[int] | None = None,
    foreground_centroid: list[float] | None = None,
) -> dict[str, Any]:
    return {
        "framing_ok": False,
        "framing_reason": reason,
        "foreground_ratio": round(foreground_ratio, 4),
        "foreground_bbox": foreground_bbox or [],
        "foreground_centroid": foreground_centroid or [],
    }


@mcp.tool()
def navmesh_rebuild(
    bounds_center: list[float] | None = None,
    bounds_extent: list[float] | None = None,
) -> str:
    """重建导航网格（NavMesh），供 path_test 做可达性验证。会修改关卡。

    白盒场景默认没有 NavMeshBoundsVolume：传 bounds_center+bounds_extent 自动生成
    （或调整）一个覆盖体积再构建。extent 是半尺寸，如 [3000, 3000, 500] 覆盖
    60m×60m×10m 区域。已有覆盖体积时可不传参直接重建。
    """
    params: dict[str, Any] = {}
    if bounds_center is not None and bounds_extent is not None:
        params["ensure_bounds"] = {"center": bounds_center, "extent": bounds_extent}
    elif bounds_center is not None or bounds_extent is not None:
        return "[error] bounds_center 与 bounds_extent 必须同时提供"
    return _call("navmesh_rebuild", params)


@mcp.tool()
def path_test(start: list[float], end: list[float]) -> str:
    """测试两点间导航可达性（需先 navmesh_rebuild）。

    返回 reachable（完整路径存在）、partial（只能走到一半）、path_length（路径长度）。
    传地板坐标即可，起终点会自动投影到导航网格上。

    Args:
        start: 起点 [x, y, z]
        end: 终点 [x, y, z]
    """
    out = _call("path_test", {"start": start, "end": end})
    if out.startswith("[error]"):
        return out
    try:
        result = json.loads(out)
    except json.JSONDecodeError:
        return out
    facts: dict[str, Any] = {
        "kind": "path_test",
        "ok": bool(result.get("reachable")),
        "reachable": bool(result.get("reachable")),
        "partial": bool(result.get("partial")),
    }
    if "path_length" in result:
        facts["path_length"] = round(float(result["path_length"]), 1)
    return f"{out}\n[facts] {json.dumps(facts, ensure_ascii=False)}"


@mcp.tool()
def output_log_tail(lines: int = 100, severity: str = "all") -> str:
    """读取 Output Log 尾部（编译/PIE 后查错）。只读。

    Args:
        lines: 返回最近多少行（1–2000，默认 100）
        severity: 过滤级别——"error" 仅错误 / "warning" 错误+警告 / "all" 全部（默认）
    """
    out = _call("output_log_tail", {"lines": lines, "severity": severity})
    if out.startswith("[error]") or is_env_unready(out):
        return out
    try:
        result = json.loads(out)
    except json.JSONDecodeError:
        return out
    facts = {
        "kind": "output_log",
        "ok": True,
        "total_errors": int(result.get("total_errors", 0)),
        "total_warnings": int(result.get("total_warnings", 0)),
    }
    return f"{out}\n[facts] {json.dumps(facts, ensure_ascii=False)}"


@mcp.tool()
def pie_smoke(seconds: float = 3.0) -> str:
    """运行期冒烟（E1）：在编辑器里启动 PIE 跑若干秒，结束后返回期间新增 Error/Warning 计数。

    用于改完蓝图/关卡后验证"能跑起来、运行期不报错"（不止编译/几何/导航）。播放当前关卡，
    不切换地图。期间编辑器正常 tick PIE（本工具在 MCP 进程里等待，不阻塞编辑器主线程）。

    Args:
        seconds: PIE 运行秒数（默认 3，自动钳到 [1, 30]）
    """
    duration = max(1.0, min(float(seconds), 30.0))
    started = _call("pie_start", {})
    if started.startswith("[error]") or is_env_unready(started):
        return started
    time.sleep(duration)
    out = _call("pie_stop", {})
    if out.startswith("[error]") or is_env_unready(out):
        return out
    try:
        result = json.loads(out)
    except json.JSONDecodeError:
        return out
    errors = int(result.get("error_count", 0))
    warnings = int(result.get("warning_count", 0))
    facts = {
        "kind": "pie",
        "ok": errors == 0,
        "error_count": errors,
        "warning_count": warnings,
        "seconds": duration,
    }
    return f"{out}\n[facts] {json.dumps(facts, ensure_ascii=False)}"


@mcp.tool()
def run_functional_test(test_name: str, timeout: float = 60.0, poll_interval: float = 1.0) -> str:
    """运行期功能测试（E1）：触发一个 UE Functional/Automation Test，轮询至完成后返回结果。

    用于"改完蓝图/关卡后验证一段具体行为是否正确"（比编译/几何/导航/PIE 冒烟更精确）。
    Automation Test 在编辑器主线程异步执行（自身可能进出 PIE），故拆成插件
    `functest_start`（触发）+ `functest_poll`（查进度）两命令，由本工具在 MCP 进程里
    轮询等待，不阻塞编辑器 GameThread（同 pie_smoke 的拆分理由）。

    Args:
        test_name: 要运行的测试名（Automation 测试全名或 Functional Test 资产名）
        timeout: 最长等待秒数（自动钳到 [5, 600]）；超时不伪造结果，按未完成报告
        poll_interval: 轮询间隔秒（自动钳到 [0.2, 10]）
    """
    if not test_name or not test_name.strip():
        return "[error] test_name 不能为空"
    budget = max(5.0, min(float(timeout), 600.0))
    interval = max(0.2, min(float(poll_interval), 10.0))
    started = _call("functest_start", {"test_name": test_name})
    if started.startswith("[error]") or is_env_unready(started):
        return started
    elapsed = 0.0
    last = ""
    while elapsed < budget:
        time.sleep(interval)
        elapsed += interval
        last = _call("functest_poll", {})
        if last.startswith("[error]") or is_env_unready(last):
            return last
        try:
            result = json.loads(last)
        except json.JSONDecodeError:
            continue  # 进度未返回完整 JSON，继续轮询
        if not result.get("finished"):
            continue
        passed = bool(result.get("passed"))
        errors = int(result.get("error_count", 0))
        warnings = int(result.get("warning_count", 0))
        facts = {
            "kind": "functional_test",
            "ok": passed,
            "test_name": test_name,
            "passed": passed,
            "error_count": errors,
            "warning_count": warnings,
        }
        return f"{last}\n[facts] {json.dumps(facts, ensure_ascii=False)}"
    # 超时：绝不伪造通过事实（缺证据应判 fail，而非假成功）。
    return mark_error(
        ErrorCategory.TRANSIENT,
        f"功能测试 {test_name} 在 {budget:.0f}s 内未完成（已轮询 {elapsed:.0f}s）。"
        "可增大 timeout，或检查测试是否卡住。",
    )


@mcp.tool()
def functest_list(filter: str = "", max: int = 200) -> str:
    """列出已注册的 Automation/Functional 测试名（供发现可用 run_functional_test 目标）。只读。

    Args:
        filter: 可选子串过滤（大小写不敏感），如 "Functional" 只看功能测试
        max: 返回上限（默认 200）
    """
    params: dict[str, Any] = {"max": max}
    if filter:
        params["filter"] = filter
    return _call("functest_list", params)


@mcp.tool()
def import_fbx(
    tasks: list[dict[str, Any]] | None = None,
    import_materials: bool = False,
    replace_existing: bool = True,
    save: bool = True,
    import_uniform_scale: float = 1.0,
    transform_vertex_to_absolute: bool = True,
    bake_pivot_in_vertex: bool = False,
    convert_scene_unit: bool = False,
    timeout: float = 600.0,
) -> str:
    """批量把 FBX 文件导入为 StaticMesh 资产（WB-1 资产库地基）。会写工程（生成 .uasset）。

    走 legacy FBX 工厂，确定性地按参数控制是否导材质——白盒只关心几何，
    默认 import_materials=False，导入的网格用引擎默认材质。一个 FBX（含多网格的
    collection）合并成单个 StaticMesh。单件失败不影响其余件，结果逐件回报。

    缩放与原点（关键）：FBX 若按"米"建模，UE 需 import_uniform_scale=100 才能得到正确
    real-world 尺寸（米→uu）；transform_vertex_to_absolute=False 可避免把 DCC 里物体的
    世界位置烘进顶点（否则网格会偏离资产原点很远），让网格留在局部原点。模块化建筑套件
    推荐 import_uniform_scale=100 + transform_vertex_to_absolute=False。

    Args:
        tasks: 导入任务列表，每项 {"filename": FBX 绝对/相对路径,
            "destination_path": 目标内容路径如 /Game/LevelPrototyping/Meshes/ArchKit/wall,
            "asset_name": 可选，导入后的资产名（缺省用文件名）}
        import_materials: 是否一并导入材质/贴图（默认 False=用默认材质）
        replace_existing: 同名资产是否覆盖（默认 True）
        save: 导入后是否落盘保存（默认 True）
        import_uniform_scale: 几何统一缩放（默认 1.0；米制源资产用 100 换算成 uu）
        transform_vertex_to_absolute: 是否把节点世界变换烘入顶点（默认 True=UE 默认；
            模块化资产传 False 让网格回到局部原点，避免原点远离几何）
        bake_pivot_in_vertex: 是否把 DCC 旋转 pivot 作为网格原点（仅当
            transform_vertex_to_absolute=False 生效，默认 False）
        convert_scene_unit: 是否把 FBX 场景单位换算到 UE 厘米（米制源资产传 True 得 ×100
            正确尺寸；独立于顶点变换烘焙，可与 transform_vertex_to_absolute=False 共存）
        timeout: 整批导入最长等待秒数（自动钳到 [30, 3600]，90 件留足余量）
    """
    if not tasks:
        return "[error] tasks 不能为空（每项需含 filename 与 destination_path）"
    normalized: list[dict[str, Any]] = []
    for task in tasks:
        if not isinstance(task, dict):
            return "[error] tasks 每项必须是对象 {filename, destination_path, asset_name?}"
        filename = str(task.get("filename", "")).strip()
        destination = str(task.get("destination_path", "")).strip()
        if not filename or not destination:
            return "[error] tasks 每项必须含非空 filename 与 destination_path"
        # C++ 侧在编辑器进程 cwd 下解析，必须传绝对路径（截图工具同理）
        entry: dict[str, Any] = {
            "filename": Path(filename).resolve().as_posix(),
            "destination_path": destination,
        }
        if task.get("asset_name"):
            entry["asset_name"] = str(task["asset_name"])
        normalized.append(entry)
    params: dict[str, Any] = {
        "tasks": normalized,
        "import_materials": bool(import_materials),
        "replace_existing": bool(replace_existing),
        "save": bool(save),
        "import_uniform_scale": float(import_uniform_scale),
        "transform_vertex_to_absolute": bool(transform_vertex_to_absolute),
        "bake_pivot_in_vertex": bool(bake_pivot_in_vertex),
        "convert_scene_unit": bool(convert_scene_unit),
    }
    budget = max(30.0, min(float(timeout), 3600.0))
    try:
        response = send_command("import_fbx", params, timeout=budget)
    except ConnectionRefusedError:
        return mark_env_unready("连不上编辑器桥：请先打开 UE 编辑器（UnrealMCP 插件随工程加载）")
    except (OSError, ConnectionError, TimeoutError) as exc:
        return mark_error(ErrorCategory.BRIDGE_DOWN, f"编辑器桥通信中断：{exc}")
    if response.get("status") == "error":
        return f"[error] {response.get('error', response)}"
    result = response.get("result", response)
    imported = int(result.get("imported", 0))
    failed = int(result.get("failed", 0))
    facts = {"kind": "import_fbx", "ok": failed == 0, "imported": imported, "failed": failed}
    body = json.dumps(result, ensure_ascii=False)
    return f"{body}\n[facts] {json.dumps(facts, ensure_ascii=False)}"


@mcp.tool()
def get_mesh_bounds(asset_path: str) -> str:
    """读取 StaticMesh 资产 scale=1 时的本地包围盒真实尺寸（uu）。只读。

    用于实测验证导入缩放是否正确（对照清单期望尺寸，如 Wall8_4 应约 800×20×400）。

    Args:
        asset_path: StaticMesh 资产路径，如 /Game/LevelPrototyping/Meshes/ArchKit/wall/Wall8_4
    """
    if not asset_path or not asset_path.strip():
        return "[error] asset_path 不能为空"
    return _call("get_mesh_bounds", {"asset_path": asset_path.strip()})


@mcp.tool()
def set_mesh_build_scale(asset_path: str, scale: float = 1.0) -> str:
    """设置 StaticMesh 的 BuildScale3D 并重建（几何围绕本地原点缩放，原点不变）。写工程。

    用于把"原点正确但尺寸不对"的网格按比例缩放到正确尺寸——FBX 导入的缩放选项与
    transform_vertex_to_absolute=False（保持局部原点）互斥，故改用 build scale 解耦：
    米制源资产 scale=100 即可在不动原点的前提下放大到正确 uu。BuildScale3D 为绝对值、幂等。

    Args:
        asset_path: StaticMesh 资产路径，如 /Game/LevelPrototyping/Meshes/ArchKit/wall/Wall8_4
        scale: 构建缩放系数（默认 1.0；米制源资产用 100）
    """
    if not asset_path or not asset_path.strip():
        return "[error] asset_path 不能为空"
    return _call("set_mesh_build_scale", {"asset_path": asset_path.strip(), "scale": float(scale)})


@mcp.tool()
def set_static_mesh_material(
    asset_path: str,
    material_path: str,
    material_slot: int = 0,
) -> str:
    """设置 StaticMesh 资产的默认材质 slot。写工程。

    用于把白盒模块件统一刷成项目原型网格材质，例如
    /Game/LevelPrototyping/Materials/MI_PrototypeGrid_Gray。该操作改的是 StaticMesh
    资产默认材质，之后新生成的关卡实例会自动使用它。

    Args:
        asset_path: StaticMesh 资产路径，如 /Game/LevelPrototyping/Meshes/ArchKit/wall/Wall1_4
        material_path: Material/MaterialInstance 资产路径
        material_slot: 要替换的材质槽位（默认 0）
    """
    if not asset_path or not asset_path.strip():
        return "[error] asset_path 不能为空"
    if not material_path or not material_path.strip():
        return "[error] material_path 不能为空"
    return _call(
        "set_static_mesh_material",
        {
            "asset_path": asset_path.strip(),
            "material_path": material_path.strip(),
            "material_slot": int(material_slot),
        },
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
