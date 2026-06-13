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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
