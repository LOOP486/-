"""评测沙盒：内存态工具组，不碰真实工程与文件系统。

度量目标是模型的工具调用能力（选对工具、参数齐全、多步串联、从错误恢复），
所以工具本身刻意简单。
"""

from __future__ import annotations

from ue5agent.core.permissions import PermissionGate, PermissionLevel
from ue5agent.tools.registry import ToolRegistry, ToolSpec


class RecordingRegistry(ToolRegistry):
    """记录每次工具调用与结果，评测检查器的数据来源。"""

    def __init__(self, gate: PermissionGate):
        super().__init__(gate)
        self.calls: list[tuple[str, str]] = []
        self.results: list[str] = []

    async def dispatch(self, name: str, arguments_json: str) -> str:
        result = await super().dispatch(name, arguments_json)
        self.calls.append((name, arguments_json))
        self.results.append(result)
        return result


def build_sandbox_registry() -> RecordingRegistry:
    registry = RecordingRegistry(PermissionGate())
    notes: dict[str, str] = {}

    async def echo(text: str) -> str:
        return text

    async def add(a: float, b: float) -> str:
        return _format_number(a + b)

    async def convert(value_cm: float, to: str) -> str:
        if to == "m":
            return _format_number(value_cm / 100)
        return _format_number(value_cm * 10)

    async def write_note(key: str, content: str) -> str:
        notes[key] = content
        return f"已保存笔记 {key}"

    async def read_note(key: str) -> str:
        if key not in notes:
            return f"没有名为 {key} 的笔记"
        return notes[key]

    registry.register(
        ToolSpec(
            name="echo",
            description="原样返回输入文本",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            level=PermissionLevel.READ,
            handler=echo,
        )
    )
    registry.register(
        ToolSpec(
            name="add",
            description="两数相加",
            parameters={
                "type": "object",
                "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                "required": ["a", "b"],
            },
            level=PermissionLevel.READ,
            handler=add,
        )
    )
    registry.register(
        ToolSpec(
            name="convert",
            description="厘米转换为米或毫米",
            parameters={
                "type": "object",
                "properties": {
                    "value_cm": {"type": "number"},
                    "to": {"type": "string", "enum": ["m", "mm"]},
                },
                "required": ["value_cm", "to"],
            },
            level=PermissionLevel.READ,
            handler=convert,
        )
    )
    registry.register(
        ToolSpec(
            name="write_note",
            description="保存一条笔记",
            parameters={
                "type": "object",
                "properties": {"key": {"type": "string"}, "content": {"type": "string"}},
                "required": ["key", "content"],
            },
            level=PermissionLevel.READ,
            handler=write_note,
        )
    )
    registry.register(
        ToolSpec(
            name="read_note",
            description="读取一条笔记",
            parameters={
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"],
            },
            level=PermissionLevel.READ,
            handler=read_note,
        )
    )
    return registry


def _format_number(value: float) -> str:
    return str(int(value)) if value == int(value) else str(value)
