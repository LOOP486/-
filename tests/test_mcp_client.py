"""MCP client 会话管理。"""

from __future__ import annotations

from types import SimpleNamespace

import anyio
from mcp.shared.exceptions import ErrorData, McpError

from ue5agent.tools.mcp_client import McpManager, _make_handler


def _text_result(text: str) -> SimpleNamespace:
    return SimpleNamespace(content=[SimpleNamespace(text=text)])


class _ClosedSession:
    async def call_tool(self, tool_name: str, arguments: dict[str, object]) -> object:
        raise anyio.ClosedResourceError


class _OkSession:
    async def call_tool(self, tool_name: str, arguments: dict[str, object]) -> object:
        return _text_result(f"{tool_name}:{arguments['value']}")


class _McpClosedSession:
    async def call_tool(self, tool_name: str, arguments: dict[str, object]) -> object:
        raise McpError(ErrorData(code=-32000, message="Connection closed"))


async def test_handler_uses_restarted_session_after_closed_resource() -> None:
    manager = McpManager({})
    manager._sessions["ue_whitebox"] = _ClosedSession()
    restarted: list[str] = []

    async def restart(server_name: str) -> _OkSession:
        restarted.append(server_name)
        session = _OkSession()
        manager._sessions[server_name] = session
        return session

    manager._restart_session = restart  # type: ignore[method-assign]

    handler = _make_handler(manager, "ue_whitebox", "wb_validate")

    assert await handler(value=42) == "wb_validate:42"
    assert restarted == ["ue_whitebox"]


async def test_handler_uses_restarted_session_after_mcp_connection_closed() -> None:
    manager = McpManager({})
    manager._sessions["ue_whitebox"] = _McpClosedSession()
    restarted: list[str] = []

    async def restart(server_name: str) -> _OkSession:
        restarted.append(server_name)
        session = _OkSession()
        manager._sessions[server_name] = session
        return session

    manager._restart_session = restart  # type: ignore[method-assign]

    handler = _make_handler(manager, "ue_whitebox", "wb_build")

    assert await handler(value=7) == "wb_build:7"
    assert restarted == ["ue_whitebox"]
