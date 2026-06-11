"""ue_whitebox MCP server：白盒场景搭建（模型出布局 JSON，程序出坐标）。

启动：uv run python -m ue5agent.mcp_servers.ue_whitebox（stdio）
前置：UE 编辑器开启。按设计白盒临时关卡为 write_safe 级。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from ue5agent.whitebox.compiler import LayoutError, compile_layout, layout_from_dict
from ue5agent.whitebox.manifest import load_manifest
from ue5agent.whitebox.spawner import clear_layout, spawn_layout

mcp = FastMCP("ue-whitebox")

_MANIFEST = Path(os.environ.get("WB_MANIFEST", "config/whitebox/levelprototyping.yaml"))


@mcp.tool()
def wb_build(layout_json: str, prefix: str = "WB") -> str:
    """按布局 JSON 在编辑器里搭白盒结构。校验不通过则一件都不落地。

    布局格式（单位=格，1 格=100uu；坐标系：x 东 y 北）：
    {"name": "训练场", "origin": [5000, 5000, 0],
     "rooms": [{"name": "main", "rect": [x, y, 宽, 深],
                "doors": [{"wall": "north|south|east|west", "at": 2, "width": 2}]}]}
    规则：房间至少 2x2 格；多房间必须连通——相邻房间在共享墙同一位置各开一个对齐的门
    （例：A 在 east 墙 at=2 开门，B 紧贴其东侧则在 west 墙的对应位置开门）。
    """
    try:
        data = json.loads(layout_json)
    except json.JSONDecodeError as exc:
        return f"[error] layout_json 不是合法 JSON：{exc}"
    try:
        spec = layout_from_dict(data)
        placements = compile_layout(spec, load_manifest(_MANIFEST))
    except LayoutError as exc:
        return f"[error] 布局校验未通过：{exc}"
    try:
        names = spawn_layout(placements, prefix=prefix)
    except (RuntimeError, OSError, ConnectionError) as exc:
        return f"[error] 落地失败（编辑器开着吗？）：{exc}"
    return (
        f"搭建完成：{len(spec.rooms)} 个房间，{len(names)} 个构件，"
        f"位于 origin={spec.origin}，前缀 {prefix}_（wb_clear 可整批撤销）"
    )


@mcp.tool()
def wb_clear(prefix: str = "WB") -> str:
    """整批删除指定前缀的白盒构件（回滚）。"""
    try:
        removed = clear_layout(prefix=prefix)
    except (OSError, ConnectionError) as exc:
        return f"[error] 编辑器桥通信失败：{exc}"
    return f"已删除 {removed} 个 {prefix}_ 构件"


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
