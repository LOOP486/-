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

from ue5agent.core.errors import is_env_unready, mark_env_unready
from ue5agent.mcp_servers.ue_editor.bridge import DEFAULT_PORT, probe_editor, send_command

mcp = FastMCP("ue-editor")


def _call(command: str, params: dict[str, Any] | None = None) -> str:
    try:
        response = send_command(command, params)
    except ConnectionRefusedError:
        return mark_env_unready("连不上编辑器桥：请先打开 UE 编辑器（UnrealMCP 插件随工程加载）")
    except (OSError, ConnectionError, TimeoutError) as exc:
        return f"[error] 编辑器桥通信失败：{exc}"
    if response.get("status") == "error":
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
def bp_analyze(blueprint_path: str, function_name: str = "") -> str:
    """分析蓝图图表（节点与连接关系）。只读，token 较大，先用 bp_read。"""
    params: dict[str, Any] = {"blueprint_path": blueprint_path}
    if function_name:
        params["function_name"] = function_name
    return _call("analyze_blueprint_graph", params)


@mcp.tool()
def viewport_screenshot(
    file_path: str = "",
    location: list[float] | None = None,
    rotation: list[float] | None = None,
) -> str:
    """对编辑器视口截图存为 PNG，返回保存路径与尺寸。

    俯视白盒布局：location=[中心x, 中心y, 高度]、rotation=[-90, 0, 0]（pitch/yaw/roll，
    高度建议为布局对角线长度量级）。不传相机参数则按当前视口视角截图。

    Args:
        file_path: 保存路径（.png）；留空自动存到 runs/screenshots/ 下的时间戳文件
        location: 可选，截图前把视口相机移到此位置 [x, y, z]
        rotation: 可选，视口相机旋转 [pitch, yaw, roll]
    """
    if not file_path:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        file_path = str(Path("runs") / "screenshots" / f"viewport_{stamp}.png")
    params: dict[str, Any] = {"file_path": str(Path(file_path).resolve())}
    if location is not None:
        params["location"] = location
    if rotation is not None:
        params["rotation"] = rotation
    out = _call("viewport_screenshot", params)
    if out.startswith("[error]") or is_env_unready(out):
        return out
    # screenshot 事实：runner 据此发现本步截图并触发 A4 视觉审查（路径 = 实际落盘路径）
    facts = {"kind": "screenshot", "ok": True, "path": params["file_path"]}
    return f"{out}\n[facts] {json.dumps(facts, ensure_ascii=False)}"


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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
