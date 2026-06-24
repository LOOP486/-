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
from ue5agent.whitebox.asset_preview_cache import (
    AssetPreviewCache,
    preview_cache_from_scan_items,
    preview_cache_path_for_manifest,
    write_asset_preview_cache,
)
from ue5agent.whitebox.compiler import LayoutError, compile_layout, layout_from_dict
from ue5agent.whitebox.level_metrics import load_level_metrics
from ue5agent.whitebox.manifest import AssetDef, Manifest, load_manifest
from ue5agent.whitebox.scanner import (
    build_manifest_dict,
    diff_manifest,
    emit_yaml,
    records_from_bounds_payload,
)
from ue5agent.whitebox.spawner import (
    batch_from_actor_name,
    clear_layout,
    folder_root_from_actor_name,
    spawn_layout,
)
from ue5agent.whitebox.validator import ActorView, validate_layout

mcp = FastMCP("ue-whitebox")

_MANIFEST = Path(os.environ.get("WB_MANIFEST", "config/whitebox/kit.yaml"))
_LEVEL_METRICS = Path(os.environ.get("WB_LEVEL_METRICS", "config/whitebox/level_metrics.yaml"))
_DEFAULT_PROTOTYPE_MATERIAL = "/Game/LevelPrototyping/Materials/MI_PrototypeGrid_Gray"
_ASSET_AUDIT_TOL = 1.0


@mcp.tool()
def wb_build(layout_json: str, prefix: str = "WB") -> str:
    """按布局 JSON 在编辑器里搭白盒结构。校验不通过则一件都不落地。

    重建语义：落地前先清掉同前缀的残留构件（整批回滚旧场景），再落新构件。
    每次落地的 actor 名带运行唯一批次标记，绝不复用旧名——这是因为 UE 的删除是
    "标记销毁 + 延迟 GC"，旧名在 GC 前仍占命名空间，复用同名 spawn 会触发引擎
    Fatal error（"Cannot generate unique name"）直接崩编辑器。唯一名从根上规避。

    前缀纪律：普通重建保持默认 prefix="WB"；评测/并排比较可以显式使用稳定前缀
    （如 SPC1/SPC2）并在 layout_json 顶层设置非重叠 origin。一次测试内必须复用同一前缀，
    否则异前缀残留会叠在场景里堵门、断 navmesh（wb_validate 能检出但应避免发生）。
    每次成功落地都会进入独立 Outliner 根文件夹 `<prefix>/<batch>`，`wb_build` facts 会回传
    `folder_root`，评测必须把它作为新测试确已落地的证据。

    默认使用 config/whitebox/kit.yaml 作为资产库，但结构层默认走 slab 模式：Engine Cube 连续地板/
    连续片墙，门窗只切墙洞，不放门框/窗框模块；如需旧 ArchKit 模块化结构与多层 room，
    在布局 JSON 顶层显式设置 "structure_mode": "modular"。

    布局格式（单位=格，1 格=100uu；坐标系：x 东 y 北；默认墙高 400uu）：
    {"name": "训练场", "structure_mode": "slab", "scale_profile": "realistic",
     "origin": [5000, 5000, 0],
     "level_height": 400,
     "rooms": [{"name": "main", "rect": [x, y, 宽, 深],
                "level": 0,
                "doors": [{"wall": "north|south|east|west", "at": 2, "width": 2}],
                "windows": [{"wall": "north|south|east|west", "at": 1, "width": 2}],
                "props": [{"key": "smallwoodencrate_001", "at": [2, 2],
                           "rotation": 0, "optional": false}]}],
    "stairs": [{"room": "main", "at": [1, 0], "from_level": 0, "to_level": 1,
                 "facing": "north", "key": "stair_2_001"}],
     "gameplay": {}}
    规则：
    - 房间至少 2x2 格；
    - 结构层坐标必须使用整数格：room.rect、doors/windows 的 at/width、props/stairs 的 at
      都不接受 1.5 这类半格值；半格/任意线段留待后续 DSL 版本；
    - structure_mode 缺省为 slab；slab 只允许 room.level=0。旧多层 room 只能显式
      structure_mode="modular" 使用；
    - scale_profile 缺省为 realistic；视觉/LLM 负责空间结构，真实米制尺度由
      config/whitebox/level_metrics.yaml 与 wb_validate 的 scale_warnings 诊断收敛；
    - slab 下 doors/windows 只参与墙体切分，不生成 wall_door/window/glass_wall actor，也不生成
      navproxy；连续地板、片墙和楼梯井护墙使用 /Engine/BasicShapes/Cube.Cube；
    - stairs 只连接相邻楼层，资产高度必须匹配层高差；slab 允许 from_level=0,to_level=1 且没有
      上层 room 的楼梯，只生成楼梯 mesh + 楼梯间护墙，不生成上层空间；
      stair_2_001 footprint=3x6 格、高 400uu，at 是 footprint 西南角；north/south 占 3x6，
      east/west 占 6x3，必须完整落在所在 room 内（例：10x8 hall 内 north 可用 at=[2,1]）；
    - modular 下继续使用 ArchKit 地板/墙/门/窗/navproxy 与旧多层 room 行为；
    - stair/prop/cover/pillar 使用资产原生尺寸落地，scale=(1,1,1)，needs_review 资产不参与自动选择；
    - 纯空间结构测试不要提供 gameplay，也不要提供 props/cover/spawn_points/routes；
      只有显式提供 gameplay 时才生成玩法层。gameplay={} 会自动生成两个 PlayerStart、
      route markers 与 cover/pillar；不提供 gameplay 时旧布局输出保持结构层行为；
      `spawn_points`/`routes` 只有缺省时才走默认生成，显式 `[]` 表示不生成对应默认层；
    - props 显式优先；required（optional=false）越界、重叠、堵门或堵同房间门到门路线会报错，
      optional=true 则跳过；stairs 也不能堵门、堵任意同房间门到门路线，或让一圈净空切断门间通路；
    - spawn 阶段中途失败时会按同前缀自动回滚半批次；若错误文本提示回滚失败，先 wb_clear 再重试；
    - 多房间必须连通——相邻房间在共享墙同一位置各开一个对齐的门
      （例：A 在 east 墙 at=2 开门，B 紧贴其东侧则在 west 墙的对应位置开门）；
    - slab+realistic 且 7 个以上房间的训练场，如果存在 entrance/entry/start/spawn/入口/前厅/
      起点/出生等入口房间命名，入口中心到最远房间中心至少 15 格；如果还存在
      end/terminal/final/goal/尽端/终点等尽端房间命名，尽端房间中心到入口也必须至少
      15 格，避免 SPC1 这类多房间训练布局全挤成短团块、path_test 距离不足；
    - windows 是显式外墙开窗，不参与房间连通性；
    - wb_validate 会额外检查近距离同向并列墙，抓出共享墙重复/错轴导致的视觉双墙；
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
    batch_id = batch_from_actor_name(names[0], prefix) if names else None
    folder_root = folder_root_from_actor_name(names[0], prefix) if names else None
    facts = {
        "kind": "wb_build",
        "ok": True,
        "rooms": len(spec.rooms),
        "components": len(names),
        "spawned_count": len(names),
        "prefix": prefix,
        "batch_id": batch_id,
        "folder_root": folder_root,
        "outliner_folder_root": folder_root,
    }
    return (
        f"搭建完成：{len(spec.rooms)} 个房间，{len(names)} 个构件，"
        f"位于 origin={spec.origin}，前缀 {prefix}_{cleared_note}（wb_clear 可整批撤销）\n"
        f"Outliner 根文件夹：{folder_root}\n"
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
    level_metrics = load_level_metrics(_LEVEL_METRICS) if _LEVEL_METRICS.exists() else None
    report = validate_layout(spec, manifest, actors, prefix=prefix, level_metrics=level_metrics)
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
        "scale_profile": report.metrics.get("scale_profile"),
        "scale_warning_count": report.metrics.get("scale_warning_count"),
        "metrics": report.metrics,
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


_BRIDGE_DOWN = "编辑器桥连接被拒。请先启动 UE 编辑器并加载工程（UnrealMCP 插件随工程加载）"


def _gather_scan_records(content_path: str, existing: Manifest | None):
    """取扫描记录：优先桥命令 scan_assets（含新件），不可用则回退 get_mesh_bounds 刷新存量。

    返回 (records_or_error, mode, note, preview_cache)；
    records_or_error 为 str 时表示错误/环境未就绪。
    """
    try:
        response = send_command(
            "scan_assets", {"content_path": content_path, "recursive": True}, timeout=120
        )
    except ConnectionRefusedError:
        return mark_env_unready(_BRIDGE_DOWN), "", "", AssetPreviewCache(items={})
    except (OSError, ConnectionError, TimeoutError) as exc:
        return f"[error] 编辑器桥通信失败：{exc}", "", "", AssetPreviewCache(items={})

    if response.get("status") != "error":
        result = response.get("result", response)
        items = result.get("assets") if isinstance(result, dict) else None
        if isinstance(items, list):
            return (
                records_from_bounds_payload(items),
                "scan_assets",
                f"枚举 {content_path}",
                preview_cache_from_scan_items(items),
            )

    # scan_assets 不可用（旧插件未实现）→ 回退刷新存量清单
    if existing is None or not existing.assets:
        err = response.get("error", response) if response.get("status") == "error" else "返回为空"
        return (
            f"[error] 桥不支持 scan_assets（{err}），且无现有 manifest 可回退刷新；"
            "请重编含 scan_assets 的 UnrealMCP 插件后再扫描",
            "",
            "",
            AssetPreviewCache(items={}),
        )
    records, failures = _refresh_existing_records(existing, content_path)
    if isinstance(records, str):
        return records, "", "", AssetPreviewCache(items={})
    note = "桥未提供 scan_assets，回退按现有清单逐件刷新（发现不了新增资产）"
    if failures:
        note += f"；{len(failures)} 件读取失败：{', '.join(failures[:5])}"
    return records, "get_mesh_bounds 回退", note, AssetPreviewCache(items={})


def _refresh_existing_records(existing: Manifest, content_path: str):
    """回退路径：对现有 manifest 里 content_path 子树的资产逐件 get_mesh_bounds 取真实 bounds。"""
    records = []
    failures: list[str] = []
    for asset in sorted(existing.assets.values(), key=lambda a: a.path):
        if not asset.path.startswith("/Game/") or not asset.path.startswith(content_path):
            continue
        try:
            response = send_command("get_mesh_bounds", {"asset_path": asset.path})
        except ConnectionRefusedError:
            return mark_env_unready(_BRIDGE_DOWN), failures
        except (OSError, ConnectionError, TimeoutError) as exc:
            return f"[error] 编辑器桥通信失败：{exc}", failures
        if response.get("status") == "error":
            failures.append(asset.key)
            continue
        result = response.get("result", response)
        item = dict(result) if isinstance(result, dict) else {}
        item["path"] = asset.path
        records.extend(records_from_bounds_payload([item]))
    return records, failures


@mcp.tool()
def wb_asset_scan(
    content_path: str = "/Game/LevelPrototyping/Meshes/ArchKit",
    apply: bool = False,
    out_path: str = "",
) -> str:
    """扫描 UE 内容目录下的 StaticMesh，按真实 bounds 重建白盒 manifest v2。写工程（写本地清单）。

    解决"重导资产后手工回填 path / 尺寸漂移"：以 UE 导入后 bounds 为真值反推
    size/pivot/footprint（calibrated），命名前缀 + 几何先验混合归类把 unknown 收敛；
    重扫会保留你手调过的 roles 与 desc。默认 apply=False 只预览 diff 不写盘，
    确认后再 apply=True 写出（默认覆盖当前清单 WB_MANIFEST）。

    取数优先调用桥命令 scan_assets 枚举整个目录（含新增件）；插件未提供该命令时，
    回退用 get_mesh_bounds 仅刷新当前 manifest 已登记的资产（发现不了新件，会在结果里提示）。

    Args:
        content_path: 要扫描的 /Game 内容目录（默认 ArchKit）
        apply: True 写出 manifest；False（默认）仅预览 diff
        out_path: 写出路径，缺省用当前 WB_MANIFEST（config/whitebox/kit.yaml）
    """
    content_path = content_path.strip().rstrip("/")
    if not content_path:
        return "[error] content_path 不能为空"

    existing = load_manifest(_MANIFEST) if _MANIFEST.exists() else None
    records, mode, note, preview_cache = _gather_scan_records(content_path, existing)
    if isinstance(records, str):
        return records  # 错误/环境未就绪标记，直接回传
    if not records:
        return "[error] 未扫描到任何 StaticMesh（content_path 是否正确？或先 import_fbx 导入资产）"

    grid = existing.grid if existing is not None else 100.0
    new_manifest = build_manifest_dict(records, grid=grid, existing=existing)
    report = diff_manifest(new_manifest, existing)

    target = Path(out_path.strip()) if out_path.strip() else _MANIFEST
    lines = [f"资产扫描（{mode}）" + (f"：{note}" if note else ""), report.summary()]
    if apply:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(emit_yaml(new_manifest), encoding="utf-8")
        lines.append(f"已写出 manifest → {target}（{report.total} 件）")
        if preview_cache:
            cache_path = preview_cache_path_for_manifest(target)
            write_asset_preview_cache(preview_cache, cache_path)
            lines.append(f"已写出 preview cache → {cache_path}（{len(preview_cache.items)} 件）")
    else:
        lines.append(f"预览模式：未写盘。确认无误后用 apply=true 写出到 {target}")

    facts = {
        "kind": "wb_asset_scan",
        "ok": True,
        "mode": mode,
        "total": report.total,
        "added": len(report.added),
        "removed": len(report.removed),
        "resized": len(report.resized),
        "needs_review": len(report.needs_review),
        "applied": bool(apply),
        "preview_asset_count": len(preview_cache.items),
        "preview_cache_path": str(preview_cache_path_for_manifest(target)) if preview_cache else "",
    }
    lines.append(f"[facts] {json.dumps(facts, ensure_ascii=False)}")
    return "\n".join(lines)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
