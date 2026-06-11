"""ue_editor MCP server：编辑器桥的只读工具集（ADR-0003：蓝图只读）。

启动：uv run python -m ue5agent.mcp_servers.ue_editor（stdio）
前置：UE 编辑器开启且 UnrealMCP 插件已加载（TCP 55557）。
写类命令（spawn/delete/set_*）刻意不暴露，待 P1.2 按 write_project 级单独开放。
"""

from __future__ import annotations

import json
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from ue5agent.core.errors import mark_env_unready
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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
