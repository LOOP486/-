"""ue_whitebox MCP server：白盒场景搭建（模型出布局 JSON，程序出坐标）。

启动：uv run python -m ue5agent.mcp_servers.ue_whitebox（stdio）
前置：UE 编辑器开启。按设计白盒临时关卡为 write_safe 级。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from ue5agent.core.errors import mark_env_unready
from ue5agent.whitebox.compiler import LayoutError, compile_layout, layout_from_dict
from ue5agent.whitebox.manifest import load_manifest
from ue5agent.whitebox.spawner import clear_layout, spawn_layout

mcp = FastMCP("ue-whitebox")

_MANIFEST = Path(os.environ.get("WB_MANIFEST", "config/whitebox/levelprototyping.yaml"))


@mcp.tool()
def wb_build(layout_json: str, prefix: str = "WB") -> str:
    """按布局 JSON 在编辑器里搭白盒结构。校验不通过则一件都不落地。

    重建语义：落地前先清掉同前缀的残留构件（整批回滚旧场景），再落新构件。
    每次落地的 actor 名带运行唯一批次标记，绝不复用旧名——这是因为 UE 的删除是
    "标记销毁 + 延迟 GC"，旧名在 GC 前仍占命名空间，复用同名 spawn 会触发引擎
    Fatal error（"Cannot generate unique name"）直接崩编辑器。唯一名从根上规避。

    布局格式（单位=格，1 格=100uu；坐标系：x 东 y 北）：
    {"name": "训练场", "origin": [5000, 5000, 0],
     "rooms": [{"name": "main", "rect": [x, y, 宽, 深],
                "doors": [{"wall": "north|south|east|west", "at": 2, "width": 2}]}]}
    规则：
    - 房间至少 2x2 格；
    - 多房间必须连通——相邻房间在共享墙同一位置各开一个对齐的门
      （例：A 在 east 墙 at=2 开门，B 紧贴其东侧则在 west 墙的对应位置开门）；
    - 相邻房间的共享边必须完全对齐：贴合的两个房间，其共享墙方向上的范围应一致，
      不要让一个房间在共享边上探出另一个房间之外（否则探出段下方无支撑，地板会悬空错位）。
      例：A=[0,0,8,8] 的东边在 x=8、跨 y[0,8]；若 B 贴其东侧，B 的 y 范围应落在 [0,8] 内
      （如 B=[8,0,4,8] 对齐；而 B=[8,8,6,5] 会在东侧探出 2 格导致悬空）。
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
    # 重建：先清同前缀旧构件（整批回滚），再用唯一名落新构件。
    # 不依赖"清干净才不撞名"——清理只为避免场景堆积；防崩靠 spawn 的运行唯一名
    # （旧名延迟 GC 仍占命名空间，复用即引擎 Fatal）。
    try:
        cleared = clear_layout(prefix=prefix)
    except (OSError, ConnectionError) as exc:
        return f"[error] 落地前清理失败（编辑器开着吗？）：{exc}"
    try:
        names = spawn_layout(placements, prefix=prefix)
    except ConnectionRefusedError:
        return mark_env_unready(
            "落地失败：编辑器桥连接被拒。请先启动 UE 编辑器并加载工程（UnrealMCP 插件随工程加载）"
        )
    except (RuntimeError, OSError, ConnectionError) as exc:
        return f"[error] 落地失败（编辑器开着吗？）：{exc}"
    cleared_note = f"（已先清理 {cleared} 个旧构件）" if cleared else ""
    return (
        f"搭建完成：{len(spec.rooms)} 个房间，{len(names)} 个构件，"
        f"位于 origin={spec.origin}，前缀 {prefix}_{cleared_note}（wb_clear 可整批撤销）"
    )


@mcp.tool()
def wb_clear(prefix: str = "WB") -> str:
    """整批删除指定前缀的白盒构件（回滚）。"""
    try:
        removed = clear_layout(prefix=prefix)
    except ConnectionRefusedError:
        return mark_env_unready(
            "编辑器桥连接被拒。请先启动 UE 编辑器并加载工程（UnrealMCP 插件随工程加载）"
        )
    except (OSError, ConnectionError) as exc:
        return f"[error] 编辑器桥通信失败：{exc}"
    return f"已删除 {removed} 个 {prefix}_ 构件"


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
