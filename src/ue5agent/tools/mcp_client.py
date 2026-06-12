"""MCP server 连接管理：启动 stdio 子进程，把远端工具注册进 ToolRegistry。

用法：
    async with McpManager(settings.mcp_servers) as manager:
        await manager.register_all(registry)
"""

from __future__ import annotations

import os
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from ue5agent.config import McpServerConfig
from ue5agent.core.permissions import PermissionLevel
from ue5agent.tools.registry import ToolRegistry, ToolSpec


class McpManager:
    def __init__(self, servers: dict[str, McpServerConfig]):
        self._configs = servers
        self._stack = AsyncExitStack()
        self._sessions: dict[str, ClientSession] = {}

    async def __aenter__(self) -> McpManager:
        for name, config in self._configs.items():
            # 必须显式传 env：MCP SDK 默认只给子进程一个最小环境，
            # 不含 UE_ENGINE_ROOT/UE_UPROJECT 等自定义变量，否则 ue_build 拿不到工程路径
            params = StdioServerParameters(
                command=config.command[0],
                args=config.command[1:],
                env=dict(os.environ),
            )
            read, write = await self._stack.enter_async_context(stdio_client(params))
            session = await self._stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            self._sessions[name] = session
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self._stack.aclose()

    async def register_all(self, registry: ToolRegistry) -> None:
        """工具名加 server 前缀避免跨 server 重名；授权级别取 server 配置，可按工具覆写。"""
        for server_name, session in self._sessions.items():
            config = self._configs[server_name]
            listing = await session.list_tools()
            for tool in listing.tools:
                level = PermissionLevel(config.tool_permissions.get(tool.name, config.permission))
                registry.register(
                    ToolSpec(
                        name=f"{server_name}__{tool.name}",
                        description=tool.description or "",
                        parameters=tool.inputSchema,
                        level=level,
                        handler=_make_handler(session, tool.name),
                    )
                )


def _make_handler(session: ClientSession, tool_name: str):
    async def handler(**arguments: Any) -> str:
        result = await session.call_tool(tool_name, arguments)
        parts = [text for block in result.content if (text := getattr(block, "text", None))]
        return "\n".join(parts) or "[空结果]"

    return handler
