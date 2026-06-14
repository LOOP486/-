"""把放置指令落进编辑器（经 UnrealMCP 桥）。

整批统一命名前缀：clear_layout 按前缀一键回滚，呼应"批量 spawn 可整批撤销"。

崩溃防御（v3，根因确诊）：
UE 引擎在 `LevelActor.cpp:585` 对 SpawnActor 的指定名做硬性 check——
若该 FName 已被占用，触发 `Fatal error: Cannot generate unique name` 直接崩编辑器。

致命点在于 UE 的 DestroyActor 是"标记销毁 + 延迟 GC"：
- delete_actor 调 DestroyActor 后，actor 立即从 level 的 actor 列表移除，
  因此 find_actors_by_name **当帧就查不到它**（返回空，看似已删净）；
- 但该 actor 对象及其 FName 在 GC 真正回收前仍占用命名空间。
所以"先 clear/复查/spawn 前预检"全部失效——它们都依赖 find，而 find 看不到
"已 DestroyActor 但未 GC"的僵尸名字。delete 与 spawn 仅隔几百毫秒，GC 远未发生，
spawn 复用同名 → 命中引擎 check → Fatal。这正是反复崩溃的真实机制
（崩溃日志：delete WB_Hall_east_0→success，find WB_→[]，spawn WB_Hall_floor→Fatal）。

根治：spawn **绝不复用可能残留的名字**。每批 spawn 注入一个运行唯一的批次标记
（`WB_<batch>_<name>`），新名在引擎命名空间里必然空闲，从根上消除重名 Fatal。
clear 仍按 `WB_` 前缀子串清理——唯一名仍以 `WB_` 开头，可被正常整批回滚。
"""

from __future__ import annotations

import time

from ue5agent.mcp_servers.ue_editor.bridge import send_command
from ue5agent.whitebox.compiler import Placement

_CLEAR_MAX_ROUNDS = 4  # 删后复查重试轮数上限


def _find_actor_names(pattern: str) -> list[str]:
    """按子串 pattern 查 actor 名（容错解析桥返回结构）。"""
    response = send_command("find_actors_by_name", {"pattern": pattern})
    result = response.get("result", response)
    actors = result.get("actors", []) if isinstance(result, dict) else []
    names: list[str] = []
    for actor in actors:
        name = actor.get("name") if isinstance(actor, dict) else actor
        if name:
            names.append(name)
    return names


def _batch_token() -> str:
    """本批 spawn 的运行唯一标记：毫秒级时间戳的 base36 短串。

    保证两次 build 之间（即便上一批因 GC 未完成仍残留僵尸名）新名字也不会撞，
    彻底绕开引擎"指定名重名即 Fatal"的硬 check。
    """
    return format(int(time.time() * 1000) & 0xFFFFFFF, "x")


def _folder_segment(value: object, *, fallback: str) -> str:
    """把布局名/房间名压成单层 Outliner 文件夹名，避免斜杠意外拆层。"""
    text = str(value).strip().replace("\\", "/").strip("/")
    parts = [part.strip() for part in text.split("/") if part.strip()]
    return "_".join(parts) if parts else fallback


def _placement_folder_path(prefix: str, placement: Placement) -> str:
    root = _folder_segment(prefix, fallback="WB")
    room = placement.metadata.get("room")
    if room:
        return f"{root}/Rooms/{_folder_segment(room, fallback='UnnamedRoom')}"
    return f"{root}/Misc"


def spawn_layout(placements: list[Placement], *, prefix: str = "WB") -> list[str]:
    """落地一批构件，使用运行唯一名避免与僵尸名（待 GC 的旧 actor）撞名崩溃。

    名字格式 `<prefix>_<batch>_<placement.name>`：仍以 `<prefix>_` 开头，
    可被 clear_layout 子串匹配整批清理；`<batch>` 保证跨批不重名。
    """
    batch = _batch_token()
    spawned: list[str] = []
    for placement in placements:
        name = f"{prefix}_{batch}_{placement.name}"
        params = {
            "type": placement.actor_type,
            "name": name,
            "location": list(placement.location),
            "rotation": list(placement.rotation),
        }
        folder_path = _placement_folder_path(prefix, placement)
        params["folder_path"] = folder_path
        params["folder"] = folder_path
        if placement.actor_type == "StaticMeshActor":
            params["static_mesh"] = placement.asset_path
            params["scale"] = list(placement.scale)
        response = send_command(
            "spawn_actor",
            params,
        )
        if response.get("status") == "error":
            raise RuntimeError(f"spawn {name} 失败：{response.get('error')}")
        spawned.append(name)
    return spawned


def clear_layout(*, prefix: str = "WB") -> int:
    """删除所有该前缀的 Actor，并复查确认从 level 列表移除（整批回滚）。

    插件 pattern 是子串匹配。删完后重新查询，若仍有残留则再删——重复直到查空或
    达到轮数上限。注意：find 查空仅代表 actor 已从 level 列表移除（已标记销毁），
    其 FName 可能尚未 GC——这正是 spawn_layout 改用唯一名而非复用名的原因。
    仍删不净（find 持续非空）则抛 RuntimeError，避免无意义的场景堆积。
    """
    pattern = f"{prefix}_"
    removed = 0
    for _ in range(_CLEAR_MAX_ROUNDS):
        names = _find_actor_names(pattern)
        if not names:
            return removed
        for name in names:
            send_command("delete_actor", {"name": name})
            removed += 1
    remaining = _find_actor_names(pattern)
    if remaining:
        raise RuntimeError(
            f"清理未删净，仍残留 {len(remaining)} 个 {prefix}_ 构件："
            f"{sorted(remaining)[:5]}…（桥可能丢响应/编辑器忙，请重试或检查编辑器）"
        )
    return removed
