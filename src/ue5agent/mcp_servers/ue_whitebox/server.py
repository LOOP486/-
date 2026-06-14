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
from ue5agent.whitebox.manifest import AssetDef, load_manifest
from ue5agent.whitebox.spawner import clear_layout, spawn_layout
from ue5agent.whitebox.validator import ActorView, validate_layout

mcp = FastMCP("ue-whitebox")

_MANIFEST = Path(os.environ.get("WB_MANIFEST", "config/whitebox/kit.yaml"))
_DEFAULT_PROTOTYPE_MATERIAL = "/Game/LevelPrototyping/Materials/MI_PrototypeGrid_Gray"
_ASSET_AUDIT_TOL = 1.0


@mcp.tool()
def wb_build(layout_json: str, prefix: str = "WB") -> str:
    """按布局 JSON 在编辑器里搭白盒结构。校验不通过则一件都不落地。

    重建语义：落地前先清掉同前缀的残留构件（整批回滚旧场景），再落新构件。
    每次落地的 actor 名带运行唯一批次标记，绝不复用旧名——这是因为 UE 的删除是
    "标记销毁 + 延迟 GC"，旧名在 GC 前仍占命名空间，复用同名 spawn 会触发引擎
    Fatal error（"Cannot generate unique name"）直接崩编辑器。唯一名从根上规避。

    前缀纪律：保持默认 prefix="WB"，不要自创前缀——重建语义只清同前缀旧构件，
    异前缀残留会叠在场景里堵门、断 navmesh（wb_validate 能检出但应避免发生）。

    默认使用 config/whitebox/kit.yaml 作为资产库，但结构层默认走 slab 模式：Engine Cube 连续地板/
    连续片墙，门窗只切墙洞，不放门框/窗框模块；如需旧 ArchKit 模块化结构与多层 room，
    在布局 JSON 顶层显式设置 "structure_mode": "modular"。

    布局格式（单位=格，1 格=100uu；坐标系：x 东 y 北；默认墙高 400uu）：
    {"name": "训练场", "structure_mode": "slab", "origin": [5000, 5000, 0],
     "level_height": 400,
     "rooms": [{"name": "main", "rect": [x, y, 宽, 深],
                "level": 0,
                "doors": [{"wall": "north|south|east|west", "at": 2, "width": 2}],
                "windows": [{"wall": "north|south|east|west", "at": 1, "width": 2}],
                "props": [{"key": "smallwoodencrate_001", "at": [2, 2],
                           "rotation": 0, "optional": false}]}],
     "stairs": [{"room": "main", "at": [1, 0], "from_level": 0, "to_level": 1,
                 "facing": "north", "key": "stair_2"}],
     "gameplay": {}}
    规则：
    - 房间至少 2x2 格；
    - structure_mode 缺省为 slab；slab 只允许 room.level=0。旧多层 room 只能显式
      structure_mode="modular" 使用；
    - slab 下 doors/windows 只参与墙体切分，不生成 wall_door/window/glass_wall actor，也不生成
      navproxy；连续地板、片墙和楼梯井护墙使用 /Engine/BasicShapes/Cube.Cube；
    - stairs 只连接相邻楼层，资产高度必须匹配层高差；slab 允许 from_level=0,to_level=1 且没有
      上层 room 的楼梯，只生成楼梯 mesh + 楼梯间护墙，不生成上层空间；
    - modular 下继续使用 ArchKit 地板/墙/门/窗/navproxy 与旧多层 room 行为；
    - stair/prop/cover/pillar 使用资产原生尺寸落地，scale=(1,1,1)，needs_review 资产不参与自动选择；
    - 只有显式提供 gameplay 时才生成玩法层。gameplay={} 会自动生成两个 PlayerStart、
      route markers 与 cover/pillar；不提供 gameplay 时旧布局输出保持结构层行为；
      `spawn_points`/`routes` 只有缺省时才走默认生成，显式 `[]` 表示不生成对应默认层；
    - props 显式优先；required（optional=false）越界、重叠、堵门或堵同房间门到门路线会报错，
      optional=true 则跳过；stairs 也不能堵同房间对穿门的直通 corridor；
    - spawn 阶段中途失败时会按同前缀自动回滚半批次；若错误文本提示回滚失败，先 wb_clear 再重试；
    - 多房间必须连通——相邻房间在共享墙同一位置各开一个对齐的门
      （例：A 在 east 墙 at=2 开门，B 紧贴其东侧则在 west 墙的对应位置开门）；
    - windows 是显式外墙开窗，不参与房间连通性；
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
        rollback_note = _rollback_after_failed_spawn(prefix)
        return mark_env_unready(
            "落地失败：编辑器桥连接被拒。请先启动 UE 编辑器并加载工程（UnrealMCP 插件随工程加载）"
            f"{rollback_note}"
        )
    except (RuntimeError, OSError, ConnectionError) as exc:
        rollback_note = _rollback_after_failed_spawn(prefix)
        return f"[error] 落地失败（编辑器开着吗？）：{exc}{rollback_note}"
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


def _rollback_after_failed_spawn(prefix: str) -> str:
    """spawn 阶段失败后尽力清理同前缀半批次，避免下轮 validate 看到叠批残留。"""
    try:
        removed = clear_layout(prefix=prefix)
    except (RuntimeError, OSError, ConnectionError) as exc:
        return f"；自动回滚失败：{exc}"
    if removed:
        return f"；已自动回滚 {removed} 个半批次构件"
    return "；未发现需回滚的半批次构件"


@mcp.tool()
def wb_apply_manifest_material(
    material_path: str = _DEFAULT_PROTOTYPE_MATERIAL,
    material_slot: int = 0,
) -> str:
    """把当前白盒 manifest 里的 StaticMesh 资产批量设置为指定默认材质。写工程。

    默认用于把 ArchKit 全部模块件刷成 MI_PrototypeGrid_Gray。该工具改的是 StaticMesh
    资产默认材质，不是当前关卡实例；执行后新生成的白盒 actor 会继承该材质。
    """
    if not material_path or not material_path.strip():
        return "[error] material_path 不能为空"
    manifest = load_manifest(_MANIFEST)
    asset_paths = sorted({a.path for a in manifest.assets.values() if a.path.startswith("/Game/")})
    if not asset_paths:
        return "[error] 当前 manifest 没有可写入材质的 /Game StaticMesh 资产"

    failed: list[str] = []
    applied = 0
    for asset_path in asset_paths:
        try:
            response = send_command(
                "set_static_mesh_material",
                {
                    "asset_path": asset_path,
                    "material_path": material_path.strip(),
                    "material_slot": int(material_slot),
                },
            )
        except ConnectionRefusedError:
            return mark_env_unready(
                "编辑器桥连接被拒。请先启动 UE 编辑器并加载工程（UnrealMCP 插件随工程加载）"
            )
        except (OSError, ConnectionError) as exc:
            return f"[error] 编辑器桥通信失败：{exc}"
        if response.get("status") == "error":
            failed.append(f"{asset_path}: {response.get('error', response)}")
            continue
        applied += 1

    ok = not failed
    facts = {
        "kind": "wb_apply_manifest_material",
        "ok": ok,
        "applied": applied,
        "failed": len(failed),
        "total": len(asset_paths),
        "material_path": material_path.strip(),
    }
    lines = [
        (
            f"manifest 材质批量设置完成：applied={applied}, "
            f"failed={len(failed)}, total={len(asset_paths)}"
        )
    ]
    lines += [f"- {item}" for item in failed[:20]]
    if len(failed) > 20:
        lines.append(f"- ... 另有 {len(failed) - 20} 个失败")
    lines.append(f"[facts] {json.dumps(facts, ensure_ascii=False)}")
    return "\n".join(lines)


@mcp.tool()
def wb_asset_audit(asset_filter: str = "", tolerance: float = _ASSET_AUDIT_TOL) -> str:
    """只读审计白盒 manifest 与 UE 导入后 StaticMesh bounds 是否一致。

    manifest 是编译器输入，但 UE imported mesh bounds 才是视觉落地真值；本工具用于在
    搭建前发现 pivot/尺寸/导入缩放漂移，避免 validator 与错误 manifest 自洽。
    asset_filter 为空时默认审计已 calibrated 的关键资产；传 "*" 或 "all" 可强制全量审计。
    """
    manifest = load_manifest(_MANIFEST)
    needle = asset_filter.strip().lower()
    all_assets = list(manifest.assets.values())
    if needle in {"*", "all"}:
        assets = all_assets
    elif needle:
        assets = [
            asset
            for asset in all_assets
            if needle in asset.key.lower()
            or needle in asset.category.lower()
            or needle in asset.path.lower()
        ]
    else:
        calibrated_assets = [asset for asset in all_assets if asset.calibrated]
        assets = calibrated_assets or all_assets
    if not assets:
        return f"[error] 未找到匹配 asset_filter={asset_filter!r} 的白盒资产"

    violations: list[str] = []
    checked = 0
    calibrated = 0
    for asset in assets:
        try:
            response = send_command("get_mesh_bounds", {"asset_path": asset.path})
        except ConnectionRefusedError:
            return mark_env_unready(
                "编辑器桥连接被拒。请先启动 UE 编辑器并加载工程（UnrealMCP 插件随工程加载）"
            )
        except (OSError, ConnectionError) as exc:
            return f"[error] 编辑器桥通信失败：{exc}"
        if response.get("status") == "error":
            violations.append(f"{asset.key}: UE bounds 读取失败：{response.get('error', response)}")
            continue
        result = response.get("result", response)
        actual_size = _bounds_size(result)
        if actual_size is None:
            violations.append(f"{asset.key}: UE bounds 缺少 size/min/max 字段")
            continue
        checked += 1
        if asset.calibrated:
            calibrated += 1
        expected_size = _asset_expected_size(asset)
        delta = max(abs(a - b) for a, b in zip(actual_size, expected_size, strict=False))
        if delta > tolerance:
            violations.append(
                f"{asset.key}: 尺寸不一致 manifest={_fmt_xyz(expected_size)} "
                f"UE={_fmt_xyz(actual_size)} delta={delta:.1f}uu"
            )

    ok = not violations
    verdict = "PASS" if ok else "FAIL"
    lines = [f"资产审计{verdict}：checked={checked}, calibrated={calibrated}, total={len(assets)}"]
    lines += [f"- {v}" for v in violations]
    facts = {
        "kind": "wb_asset_audit",
        "ok": ok,
        "checked": checked,
        "calibrated": calibrated,
        "violations": len(violations),
    }
    lines.append(f"[facts] {json.dumps(facts, ensure_ascii=False)}")
    return "\n".join(lines)


def _bounds_size(result: object) -> tuple[float, float, float] | None:
    if not isinstance(result, dict):
        return None
    raw_size = result.get("size")
    if isinstance(raw_size, (list, tuple)) and len(raw_size) == 3:
        return (float(raw_size[0]), float(raw_size[1]), float(raw_size[2]))
    raw_min = result.get("min") or result.get("local_min")
    raw_max = result.get("max") or result.get("local_max")
    if (
        isinstance(raw_min, (list, tuple))
        and isinstance(raw_max, (list, tuple))
        and len(raw_min) == 3
        and len(raw_max) == 3
    ):
        return (
            float(raw_max[0]) - float(raw_min[0]),
            float(raw_max[1]) - float(raw_min[1]),
            float(raw_max[2]) - float(raw_min[2]),
        )
    return None


def _asset_expected_size(asset: AssetDef) -> tuple[float, float, float]:
    if asset.local_bounds_min is not None and asset.local_bounds_max is not None:
        return (
            asset.local_bounds_max[0] - asset.local_bounds_min[0],
            asset.local_bounds_max[1] - asset.local_bounds_min[1],
            asset.local_bounds_max[2] - asset.local_bounds_min[2],
        )
    return asset.size


def _fmt_xyz(values: tuple[float, float, float]) -> str:
    return "[" + ", ".join(f"{v:.1f}" for v in values) + "]"


@mcp.tool()
def wb_validate(layout_json: str, prefix: str = "WB") -> str:
    """对照布局 JSON 校验编辑器中已落地的白盒构件（确定性几何检查，只读）。

    回读场景实测坐标，与布局编译出的期望放置对照，检查：缺件（spawn 部分失败）、
    多件（残留/外部添加）、位置漂移、构件穿插、校准资产 visual AABB 偏移、
    主路线被 cover/prop/pillar 堵塞、异前缀白盒残留（旧批次构件叠在布局区域会堵门断 navmesh）。
    metrics 包含 room/door/level/stair/stairwell/prop/spawn/route/wall/floor 面积等计数。
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
    actors = []
    seen_names: set[str] = set()
    # 先精确查询当前 prefix，避免宽查询被桥端返回上限/排序截断后误判缺件；
    # 再宽查询下划线形态 actor，用于发现异前缀残留。
    for pattern in (f"{prefix}_", "_"):
        try:
            response = send_command("find_actors_by_name", {"pattern": pattern})
        except ConnectionRefusedError:
            return mark_env_unready(
                "编辑器桥连接被拒。请先启动 UE 编辑器并加载工程（UnrealMCP 插件随工程加载）"
            )
        except (OSError, ConnectionError) as exc:
            return f"[error] 编辑器桥通信失败：{exc}"
        if response.get("status") == "error":
            return f"[error] {response.get('error', response)}"

        result = response.get("result", response)
        raw_actors = result.get("actors", []) if isinstance(result, dict) else []
        for raw in raw_actors:
            actor = _actor_view_from_raw(raw)
            if actor is None or actor.name in seen_names:
                continue
            seen_names.add(actor.name)
            actors.append(actor)
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


def _actor_view_from_raw(raw: object) -> ActorView | None:
    if isinstance(raw, dict) and "location" in raw and "scale" in raw:
        loc, scl = raw["location"], raw["scale"]
        rot = raw.get("rotation") or [0, 0, 0]
        return ActorView(
            name=str(raw.get("name", "")),
            location=(float(loc[0]), float(loc[1]), float(loc[2])),
            scale=(float(scl[0]), float(scl[1]), float(scl[2])),
            rotation=(float(rot[0]), float(rot[1]), float(rot[2])),
            actor_type=str(raw.get("type", raw.get("actor_type", "StaticMeshActor"))),
        )
    return None


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
