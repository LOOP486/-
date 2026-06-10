"""ue_build MCP server：UBT 编译与结构化诊断回传。

启动：uv run python -m ue5agent.mcp_servers.ue_build（stdio）
配置：环境变量 UE_ENGINE_ROOT / UE_UPROJECT，或调用时显式传参。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from ue5agent.mcp_servers.ue_build.ubt import run_build

mcp = FastMCP("ue-build")


@mcp.tool()
def ubt_compile(
    target: str,
    configuration: str = "Development",
    engine_root: str | None = None,
    uproject: str | None = None,
) -> str:
    """编译 UE 工程目标，返回结构化诊断 JSON（错误含文件/行号/错误码）。

    Args:
        target: 构建目标，如 MyGameEditor
        configuration: Development / DebugGame / Shipping
        engine_root: 引擎根目录，缺省读 UE_ENGINE_ROOT 环境变量
        uproject: .uproject 路径，缺省读 UE_UPROJECT 环境变量
    """
    engine = engine_root or os.environ.get("UE_ENGINE_ROOT")
    project = uproject or os.environ.get("UE_UPROJECT")
    if not engine or not project:
        return _error("缺少引擎或工程路径：传入参数或设置 UE_ENGINE_ROOT / UE_UPROJECT")
    build_bat = Path(engine) / "Engine" / "Build" / "BatchFiles" / "Build.bat"
    if not build_bat.exists():
        return _error(f"引擎路径不对：找不到 {build_bat}")
    result = run_build(Path(engine), Path(project), target, configuration)
    return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)


@mcp.tool()
def engine_info(engine_root: str | None = None) -> str:
    """读取引擎版本信息（Engine/Build/Build.version）。"""
    engine = engine_root or os.environ.get("UE_ENGINE_ROOT")
    if not engine:
        return _error("缺少引擎路径：传入参数或设置 UE_ENGINE_ROOT")
    version_file = Path(engine) / "Engine" / "Build" / "Build.version"
    if not version_file.exists():
        return _error(f"找不到 {version_file}")
    return version_file.read_text(encoding="utf-8")


def _error(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
