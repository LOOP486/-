"""把放置指令落进编辑器（经 UnrealMCP 桥）。

整批统一命名前缀：clear_layout 按前缀一键回滚，呼应"批量 spawn 可整批撤销"。
"""

from __future__ import annotations

from ue5agent.mcp_servers.ue_editor.bridge import send_command
from ue5agent.whitebox.compiler import Placement


def spawn_layout(placements: list[Placement], *, prefix: str = "WB") -> list[str]:
    spawned: list[str] = []
    for placement in placements:
        name = f"{prefix}_{placement.name}"
        response = send_command(
            "spawn_actor",
            {
                "type": "StaticMeshActor",
                "name": name,
                "static_mesh": placement.asset_path,
                "location": list(placement.location),
                "scale": list(placement.scale),
            },
        )
        if response.get("status") == "error":
            raise RuntimeError(f"spawn {name} 失败：{response.get('error')}")
        spawned.append(name)
    return spawned


def clear_layout(*, prefix: str = "WB") -> int:
    """删除所有该前缀的 Actor（整批回滚）。插件的 pattern 是子串匹配。"""
    response = send_command("find_actors_by_name", {"pattern": f"{prefix}_"})
    actors = response.get("result", {}).get("actors", [])
    removed = 0
    for actor in actors:
        name = actor.get("name") if isinstance(actor, dict) else actor
        if name:
            send_command("delete_actor", {"name": name})
            removed += 1
    return removed
