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
from ue5agent.mcp_servers.ue_editor.bridge import send_command
from ue5agent.whitebox.compiler import LayoutError, compile_layout, layout_from_dict
from ue5agent.whitebox.manifest import load_manifest
from ue5agent.whitebox.spawner import clear_layout, spawn_layout
from ue5agent.whitebox.validator import ActorView, validate_layout

mcp = FastMCP("ue-whitebox")

_MANIFEST = Path(os.environ.get("WB_MANIFEST", "config/whitebox/levelprototyping.yaml"))


@mcp.tool()
def wb_build(layout_json: str, prefix: str = "WB") -> str:
    """按布局 JSON 在编辑器里搭白盒结构。校验不通过则一件都不落地。

    重建语义：落地前先清掉同前缀的残留构件（整批回滚旧场景），再落新构件。
    每次落地的 actor 名带运行唯一批次标记，绝不复用旧名——这是因为 UE 的删除是
    "标记销毁 + 延迟 GC"，旧名在 GC 前仍占命名空间，复用同名 spawn 会触发引擎
    Fatal error（"Cannot generate unique name"）直接崩编辑器。唯一名从根上规避。

    前缀纪律：保持默认 prefix="WB"，不要自创前缀——重建语义只清同前缀旧构件，
    异前缀残留会叠在场景里堵门、断 navmesh（wb_validate 能检出但应避免发生）。

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
    # 回传各房间的世界坐标中心：path_test/截图直接用这些数（单位 uu，1 格=100uu），
    # 不要自己换算——格坐标误当世界坐标是实测高发错误（差 100 倍）
    grid = load_manifest(_MANIFEST).grid
    ox, oy, _oz = spec.origin
    centers = {
        room.name: [
            round(ox + (room.rect[0] + room.rect[2] / 2) * grid),
            round(oy + (room.rect[1] + room.rect[3] / 2) * grid),
        ]
        for room in spec.rooms
    }
    facts = {
        "kind": "wb_build",
        "ok": True,
        "rooms": len(spec.rooms),
        "components": len(names),
        "prefix": prefix,
    }
    return (
        f"搭建完成：{len(spec.rooms)} 个房间，{len(names)} 个构件，"
        f"位于 origin={spec.origin}，前缀 {prefix}_{cleared_note}（wb_clear 可整批撤销）\n"
        f"房间中心（世界坐标 uu，path_test/截图请直接使用）："
        f"{json.dumps(centers, ensure_ascii=False)}"
        f"\n[facts] {json.dumps(facts, ensure_ascii=False)}"
    )


@mcp.tool()
def wb_validate(layout_json: str, prefix: str = "WB") -> str:
    """对照布局 JSON 校验编辑器中已落地的白盒构件（确定性几何检查，只读）。

    回读场景实测坐标，与布局编译出的期望放置对照，检查：缺件（spawn 部分失败）、
    多件（残留/外部添加）、位置漂移、构件穿插、异前缀白盒残留（旧批次构件叠在
    布局区域会堵门断 navmesh）。返回 PASS/FAIL + violations + 关卡 metrics。
    物理可达性请另用 ue_editor 的 navmesh_rebuild + path_test。
    """
    try:
        data = json.loads(layout_json)
    except json.JSONDecodeError as exc:
        return f"[error] layout_json 不是合法 JSON：{exc}"
    try:
        spec = layout_from_dict(data)
        manifest = load_manifest(_MANIFEST)
    except LayoutError as exc:
        return f"[error] 布局校验未通过：{exc}"
    try:
        # 宽查询（任何含下划线的 actor）：异前缀残留必须能被看见，validator 负责过滤
        response = send_command("find_actors_by_name", {"pattern": "_"})
    except ConnectionRefusedError:
        return mark_env_unready(
            "编辑器桥连接被拒。请先启动 UE 编辑器并加载工程（UnrealMCP 插件随工程加载）"
        )
    except (OSError, ConnectionError) as exc:
        return f"[error] 编辑器桥通信失败：{exc}"
    if response.get("status") == "error":
        return f"[error] {response.get('error', response)}"

    actors = []
    result = response.get("result", response)
    for raw in result.get("actors", []) if isinstance(result, dict) else []:
        if isinstance(raw, dict) and "location" in raw and "scale" in raw:
            loc, scl = raw["location"], raw["scale"]
            actors.append(
                ActorView(
                    name=str(raw.get("name", "")),
                    location=(float(loc[0]), float(loc[1]), float(loc[2])),
                    scale=(float(scl[0]), float(scl[1]), float(scl[2])),
                )
            )
    report = validate_layout(spec, manifest, actors, prefix=prefix)
    verdict = "PASS" if report.ok else "FAIL"
    lines = [f"校验{verdict}：实测 {report.metrics.get('actual_count', 0)} 个构件"]
    lines += [f"- {v}" for v in report.violations]
    lines.append(f"metrics: {json.dumps(report.metrics, ensure_ascii=False)}")
    facts = {
        "kind": "wb_validate",
        "ok": report.ok,
        "violations": len(report.violations),
        "room_count": report.metrics.get("room_count"),
        "actual_count": report.metrics.get("actual_count"),
    }
    lines.append(f"[facts] {json.dumps(facts, ensure_ascii=False)}")
    return "\n".join(lines)


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
