"""ue_lifecycle MCP server：编辑器进程生命周期（启动重量级进程，dangerous 级）。

启动：uv run python -m ue5agent.mcp_servers.ue_lifecycle（stdio）
挂载：agent.yaml 里 permission 配 dangerous，并把 ue_lifecycle__editor_launch
加入 permissions.allowlist（白名单 + 人工确认双条件，缺一即拒）。
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from ue5agent.mcp_servers.ue_lifecycle.launcher import LaunchError, launch_editor

mcp = FastMCP("ue-lifecycle")


@mcp.tool()
def editor_launch(timeout_seconds: int = 240) -> str:
    """启动 UE 编辑器并等待桥端口就绪；已在运行则直接返回。

    引擎与工程路径读环境变量 UE_ENGINE_ROOT / UE_UPROJECT（与 ue_build 一致）。
    编辑器冷启动需数分钟，先用 editor_status 确认确实离线再调用。

    Args:
        timeout_seconds: 等待桥端口就绪的上限秒数
    """
    engine = os.environ.get("UE_ENGINE_ROOT")
    project = os.environ.get("UE_UPROJECT")
    if not engine or not project:
        return "[error] 缺少 UE_ENGINE_ROOT / UE_UPROJECT 环境变量，无法定位引擎与工程"
    try:
        return launch_editor(engine, project, timeout=float(timeout_seconds))
    except LaunchError as exc:
        return f"[error] {exc}"


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
