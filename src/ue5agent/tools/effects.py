"""工具副作用声明（B2 Tool Effect System）：工具元数据从"权限级"扩展为副作用语义。

权威在 kernel 侧：MCP 工具的 effects 由本模块的声明表给出，不采信远端自报——
requires_checkpoint 这类安全行为不能由 server 进程说了算。本地工具在定义处
直接挂 ToolEffects（见 fs_tools.py）。未声明的工具按权限级推导保守默认，
与 B2 之前的行为完全等价（WRITE_PROJECT 自动 checkpoint，其余不打快照）。

消费方：
- PermissionGate：requires_checkpoint 驱动前置快照（取代"WRITE_PROJECT 一律打"的硬编码）；
- ToolPipeline：非幂等工具执行失败的熔断阈值降为 2，回传文本禁止原样重试；
- B3 恢复策略表：partial_side_effect 类错误用 rollback_tool 先清理再重试。
"""

from __future__ import annotations

from dataclasses import dataclass

from ue5agent.core.permissions import PermissionLevel


@dataclass(frozen=True)
class ToolEffects:
    idempotent: bool = True
    """重复执行是否与执行一次等价。非幂等工具失败后禁止原样重试（副作用可能已部分发生）。"""
    requires_checkpoint: bool = False
    """执行前是否必须有 git checkpoint 就位（驱动 PermissionGate 的自动快照）。"""
    rollback_tool: str | None = None
    """能撤销本工具副作用的工具名（裸名），无则 None。"""
    supports_dry_run: bool = False
    """是否支持只校验不落地的试运行。"""
    resources: tuple[str, ...] = ()
    """触碰的资源类别：level_actors / source_files / git_index /
    build_artifacts / editor_process。"""


def default_effects(level: PermissionLevel) -> ToolEffects:
    """未声明工具的保守默认：与 B2 之前行为等价——
    WRITE_PROJECT 一律前置 checkpoint，其余级别不打；幂等性按 True（沿用阈值 3）。"""
    return ToolEffects(requires_checkpoint=(level is PermissionLevel.WRITE_PROJECT))


# 自带 MCP server 工具的副作用声明（键为裸名，不含 server 前缀）。
# 注意 requires_checkpoint 在此表中是权威声明：wb_build 不打 git 快照不是疏漏——
# 关卡 actor 不落文件，git checkpoint 保护不了它，回滚靠 rollback_tool=wb_clear。
KNOWN_TOOL_EFFECTS: dict[str, ToolEffects] = {
    # ue_whitebox：spawn 部分失败会留下半截布局 → 非幂等；wb_clear 按前缀整批撤销
    "wb_build": ToolEffects(
        idempotent=False, rollback_tool="wb_clear", resources=("level_actors",)
    ),
    "wb_clear": ToolEffects(idempotent=True, resources=("level_actors",)),
    "wb_validate": ToolEffects(idempotent=True),
    # ue_build：UBT 增量编译可重复执行，产物只在 Binaries/Intermediate
    "ubt_compile": ToolEffects(idempotent=True, resources=("build_artifacts",)),
    "engine_info": ToolEffects(idempotent=True),
    # repo_tools：restore 覆盖未提交改动且不可撤销 → 非幂等（dangerous 级别不变）
    "repo_checkpoint": ToolEffects(idempotent=True, resources=("git_index",)),
    "repo_restore": ToolEffects(idempotent=False, resources=("source_files", "git_index")),
    # ue_editor：navmesh_rebuild 会改编辑器里的导航体积/NavMesh 状态，但 git checkpoint
    # 保护不了这类运行态关卡副作用；权限仍由配置保持 write_project。
    "navmesh_rebuild": ToolEffects(
        idempotent=True, requires_checkpoint=False, resources=("level_actors",)
    ),
    # ue_lifecycle：editor_launch 幂等（已运行即返回）。
    "editor_launch": ToolEffects(idempotent=True, resources=("editor_process",)),
}


def effects_for(bare_name: str, level: PermissionLevel) -> ToolEffects:
    """按裸工具名查声明表，未声明的按权限级推导默认。"""
    return KNOWN_TOOL_EFFECTS.get(bare_name) or default_effects(level)


__all__ = ["KNOWN_TOOL_EFFECTS", "ToolEffects", "default_effects", "effects_for"]
