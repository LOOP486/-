"""工具调用管线（K2）：吸收原 registry.dispatch 链，补三个增量。

链条：工具名检查（近似纠正）→ JSON 解析（机械修复）→ 参数规范化
→ Schema 校验 → 权限检查 → 执行 → 结果信封 → 失败签名计数。

- 规范化：字符串去空白、按 schema 把数字/布尔字符串温和转型、路径分隔符归一；
- 信封：ToolOutcome 在 trace/recovery 侧保留结构，回传模型仍是紧凑文本；
- 熔断信号：同一工具同类错误连续达到阈值时，在回传文本中升级提示，
  K4 的 recovery 将基于同一计数做策略切换。
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from difflib import get_close_matches
from typing import Any, Protocol

from ue5agent.core.permissions import PermissionGate, PermissionLevel, ToolDenied
from ue5agent.tools.validation import parse_arguments, validate_arguments

_PATH_HINTS = ("path", "file", "dir", "root", "uproject", "folder")


class ToolProvider(Protocol):
    """管线需要的注册表能力（避免与 ToolRegistry 循环依赖）。"""

    def get(self, name: str) -> Any | None: ...
    def names(self) -> list[str]: ...


@dataclass
class ToolOutcome:
    ok: bool
    text: str
    """回传模型的文本"""
    error_kind: str | None = None
    """unknown_tool | bad_json | schema | denied | exception | tool_error"""
    consecutive: int = 0
    """该工具同类错误的连续次数"""


class FailureTracker:
    """失败签名计数：(工具, 错误类别) 连续计数，成功即清零。"""

    def __init__(self, threshold: int = 3):
        self.threshold = threshold
        self._counts: dict[tuple[str, str], int] = {}

    def record(self, tool: str, kind: str) -> int:
        key = (tool, kind)
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key]

    def reset(self, tool: str) -> None:
        for key in [k for k in self._counts if k[0] == tool]:
            del self._counts[key]


class ToolPipeline:
    def __init__(
        self,
        registry: ToolProvider,
        gate: PermissionGate,
        tracker: FailureTracker | None = None,
    ):
        self._registry = registry
        self._gate = gate
        self._tracker = tracker or FailureTracker()

    async def dispatch(self, name: str, arguments_json: str) -> str:
        return (await self.run(name, arguments_json)).text

    async def run(self, name: str, arguments_json: str) -> ToolOutcome:
        tool = self._registry.get(name)
        if tool is None:
            similar = get_close_matches(name, self._registry.names(), n=1)
            hint = f"，是不是想用 {similar[0]}？" if similar else ""
            return self._failed(name, "unknown_tool", f"[error] 未知工具：{name}{hint}")
        try:
            arguments = parse_arguments(arguments_json)
        except ValueError as exc:
            return self._failed(name, "bad_json", f"[error] {exc}")
        arguments = normalize_arguments(tool.parameters, arguments)
        violations = validate_arguments(tool.parameters, arguments)
        if violations:
            return self._failed(
                name, "schema", "[error] 参数不符合 schema：" + "；".join(violations)
            )
        try:
            self._gate.check(name, tool.level, arguments)
        except ToolDenied as exc:
            return self._failed(name, "denied", f"[denied] {exc}")
        try:
            text = await tool.handler(**arguments)
        except Exception as exc:  # 工具异常回传模型，不中断循环
            return self._failed(name, "exception", f"[error] {type(exc).__name__}: {exc}")
        if text.startswith(("[error]", "[denied]")):
            # 工具自身报错（如 MCP 远端工具的业务失败）
            return self._failed(name, "tool_error", text)
        self._tracker.reset(name)
        return ToolOutcome(ok=True, text=text)

    def _failed(self, name: str, kind: str, text: str) -> ToolOutcome:
        consecutive = self._tracker.record(name, kind)
        if consecutive >= self._tracker.threshold:
            text += (
                f"\n[提示] {name} 已连续 {consecutive} 次出现同类错误（{kind}），"
                "不要原样重试——换一种参数或方法。"
            )
        return ToolOutcome(ok=False, text=text, error_kind=kind, consecutive=consecutive)


def normalize_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    """温和规范化：只做无歧义的修正，修不了的留给 schema 校验去报。"""
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    normalized: dict[str, Any] = {}
    for key, value in arguments.items():
        if isinstance(value, str):
            value = value.strip()
            declared = properties.get(key, {}).get("type") if isinstance(properties, dict) else None
            if declared == "integer":
                with contextlib.suppress(ValueError):
                    value = int(value)
            elif declared == "number":
                with contextlib.suppress(ValueError):
                    value = float(value)
            elif declared == "boolean" and value.lower() in ("true", "false"):
                value = value.lower() == "true"
            elif _looks_like_path_key(key):
                value = value.replace("\\", "/")
        normalized[key] = value
    return normalized


def _looks_like_path_key(key: str) -> bool:
    lowered = key.lower()
    return any(hint in lowered for hint in _PATH_HINTS)


__all__ = [
    "FailureTracker",
    "PermissionLevel",
    "ToolOutcome",
    "ToolPipeline",
    "normalize_arguments",
]
