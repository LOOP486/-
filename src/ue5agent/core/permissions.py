"""工具分级授权网关（K3 起为 4 级，kernel-refactor-plan §5）。

- READ / WRITE_SAFE：自动放行（读取、临时文件、报告、评测沙盒、checkpoint 本身）
- WRITE_PROJECT：前置条件是 checkpoint 钩子成功（自动先打快照），交互模式下再确认
- DANGEROUS：白名单 + 人工确认双条件，缺一即拒
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Any


class PermissionLevel(StrEnum):
    READ = "read"
    WRITE_SAFE = "write_safe"
    WRITE_PROJECT = "write_project"
    DANGEROUS = "dangerous"

    @classmethod
    def _missing_(cls, value: object) -> PermissionLevel | None:
        if value == "write":  # 三级时代的旧配置值，按更严的档位解释
            return cls.WRITE_PROJECT
        return None


_LEVEL_RANK = {
    PermissionLevel.READ: 0,
    PermissionLevel.WRITE_SAFE: 1,
    PermissionLevel.WRITE_PROJECT: 2,
    PermissionLevel.DANGEROUS: 3,
}


def level_rank(level: PermissionLevel) -> int:
    """权限级别的严格序（步骤契约的 permission_ceiling 比较用）。"""
    return _LEVEL_RANK[level]


class ToolDenied(Exception):
    """工具调用被授权网关拒绝。"""


Confirmer = Callable[[str, dict[str, Any]], bool]
"""确认回调：(tool_name, arguments) -> 是否放行。CLI 下接交互式确认。"""

CheckpointHook = Callable[[], bool]
"""WRITE_PROJECT 前置钩子：负责打快照，返回 True 表示 checkpoint 已就位。"""


class PermissionGate:
    def __init__(
        self,
        confirmer: Confirmer | None = None,
        allowlist: set[str] | None = None,
        checkpoint: CheckpointHook | None = None,
    ):
        self._confirmer = confirmer
        self._allowlist = allowlist or set()
        self._checkpoint = checkpoint

    def check(
        self,
        tool_name: str,
        level: PermissionLevel,
        arguments: dict[str, Any],
        requires_checkpoint: bool | None = None,
    ) -> None:
        """放行则静默返回，拒绝则抛 ToolDenied。

        requires_checkpoint 来自工具的副作用声明（B2）：None 表示未声明，
        按旧规则推导（WRITE_PROJECT 一律前置快照）——直接调用方与旧测试不受影响。
        """
        if requires_checkpoint is None:
            requires_checkpoint = level is PermissionLevel.WRITE_PROJECT
        if level is PermissionLevel.DANGEROUS:
            # 白名单先于 checkpoint：不在白名单的工具连快照都不该触发
            if tool_name not in self._allowlist:
                raise ToolDenied(f"{tool_name} 属于危险操作且不在白名单，拒绝")
            if requires_checkpoint:
                self._ensure_checkpoint(tool_name)
            if self._confirmer is None or not self._confirmer(tool_name, arguments):
                raise ToolDenied(f"{tool_name} 属于危险操作，未获人工确认")
            return
        if requires_checkpoint:
            self._ensure_checkpoint(tool_name)
        if level in (PermissionLevel.READ, PermissionLevel.WRITE_SAFE):
            return
        # WRITE_PROJECT
        if self._confirmer is not None and not self._confirmer(tool_name, arguments):
            raise ToolDenied(f"{tool_name} 是工程写操作，未获用户确认")

    def _ensure_checkpoint(self, tool_name: str) -> None:
        if self._checkpoint is None:
            raise ToolDenied(
                f"{tool_name} 会修改工程，但未配置 checkpoint（工程不在 git 管理下？）"
            )
        if not self._checkpoint():
            raise ToolDenied(f"{tool_name} 的前置 checkpoint 失败，拒绝执行")
