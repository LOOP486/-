"""MCP server 连接管理：启动 stdio 子进程，把远端工具注册进 ToolRegistry。

用法：
    async with McpManager(settings.mcp_servers) as manager:
        await manager.register_all(registry)
"""

from __future__ import annotations

import os
from contextlib import AsyncExitStack
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError

from ue5agent.config import McpServerConfig
from ue5agent.core.permissions import PermissionLevel
from ue5agent.tools.effects import effects_for
from ue5agent.tools.registry import ToolRegistry, ToolSpec


class McpManager:
    def __init__(self, servers: dict[str, McpServerConfig], *, env: dict[str, str] | None = None):
        self._configs = servers
        self._env = dict(os.environ if env is None else env)
        self._stack = AsyncExitStack()
        self._sessions: dict[str, ClientSession] = {}

    async def __aenter__(self) -> McpManager:
        for name in self._configs:
            await self._open_session(name)
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self._stack.aclose()

    async def _open_session(self, server_name: str) -> ClientSession:
        config = self._configs[server_name]
        # 必须显式传 env：MCP SDK 默认只给子进程一个最小环境，
        # 不含 UE_ENGINE_ROOT/UE_UPROJECT 等自定义变量，否则 ue_build 拿不到工程路径
        params = StdioServerParameters(
            command=config.command[0],
            args=config.command[1:],
            env=self._env,
        )
        read, write = await self._stack.enter_async_context(stdio_client(params))
        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._sessions[server_name] = session
        return session

    async def _restart_session(self, server_name: str) -> ClientSession:
        return await self._open_session(server_name)

    async def _list_tools(self, server_name: str) -> Any:
        try:
            return await self._sessions[server_name].list_tools()
        except Exception as exc:
            if not _is_reconnectable_error(exc):
                raise
            return await (await self._restart_session(server_name)).list_tools()

    async def call_tool_text(
        self, server_name: str, tool_name: str, arguments: dict[str, Any]
    ) -> str:
        try:
            result = await self._sessions[server_name].call_tool(tool_name, arguments)
        except Exception as exc:
            if not _is_reconnectable_error(exc):
                raise
            result = await (await self._restart_session(server_name)).call_tool(
                tool_name, arguments
            )
        parts = [text for block in result.content if (text := getattr(block, "text", None))]
        return "\n".join(parts) or "[空结果]"

    async def register_all(self, registry: ToolRegistry) -> None:
        """工具名加 server 前缀避免跨 server 重名；授权级别取 server 配置，可按工具覆写。

        effects 按裸名查 kernel 侧声明表（tools/effects.py），不采信远端自报——
        checkpoint 等安全行为的权威必须在本进程。
        """
        for server_name in self._sessions:
            config = self._configs[server_name]
            listing = await self._list_tools(server_name)
            for tool in listing.tools:
                level = PermissionLevel(config.tool_permissions.get(tool.name, config.permission))
                registry.register(
                    ToolSpec(
                        name=f"{server_name}__{tool.name}",
                        description=tool.description or "",
                        parameters=tool.inputSchema,
                        level=level,
                        handler=_make_handler(self, server_name, tool.name),
                        effects=effects_for(tool.name, level),
                    )
                )


_RECONNECTABLE_ERRORS = (
    anyio.BrokenResourceError,
    anyio.ClosedResourceError,
    anyio.EndOfStream,
)


def _is_reconnectable_error(exc: Exception) -> bool:
    if isinstance(exc, _RECONNECTABLE_ERRORS):
        return True
    if isinstance(exc, McpError):
        return "connection closed" in str(exc).lower()
    return False


def _make_handler(manager: McpManager, server_name: str, tool_name: str):
    async def handler(**arguments: Any) -> str:
        return await manager.call_tool_text(server_name, tool_name, arguments)

    return handler
