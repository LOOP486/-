"""工具分级授权网关：只读放行，写操作需确认，危险操作默认拒绝。

设计依据见 docs/architecture/design.md §10（安全与回滚）。
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Any


class PermissionLevel(StrEnum):
    READ = "read"
    WRITE = "write"
    DANGEROUS = "dangerous"


class ToolDenied(Exception):
    """工具调用被授权网关拒绝。"""


Confirmer = Callable[[str, dict[str, Any]], bool]
"""确认回调：(tool_name, arguments) -> 是否放行。CLI 下接交互式确认。"""


class PermissionGate:
    def __init__(self, confirmer: Confirmer | None = None, allowlist: set[str] | None = None):
        self._confirmer = confirmer
        self._allowlist = allowlist or set()

    def check(self, tool_name: str, level: PermissionLevel, arguments: dict[str, Any]) -> None:
        """放行则静默返回，拒绝则抛 ToolDenied。"""
        if level is PermissionLevel.READ or tool_name in self._allowlist:
            return
        if level is PermissionLevel.DANGEROUS:
            raise ToolDenied(f"{tool_name} 属于危险操作，默认禁止（如确需使用请加入白名单）")
        if self._confirmer is None or not self._confirmer(tool_name, arguments):
            raise ToolDenied(f"{tool_name} 是写操作，未获用户确认")
