"""命令行入口：ue5agent check-config / chat / version。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from ue5agent import __version__
from ue5agent.config import (
    AgentSettings,
    ModelsConfig,
    load_agent_settings,
    load_models_config,
)

app = typer.Typer(help="UE5 游戏开发 agent", no_args_is_help=True)
console = Console()

DEFAULT_MODELS = Path("config/models.yaml")
DEFAULT_AGENT = Path("config/agent.yaml")


@app.callback()
def _init() -> None:
    load_dotenv()


@app.command()
def version() -> None:
    """显示版本。"""
    console.print(f"ue5agent {__version__}")


@app.command("check-config")
def check_config(models: Path = DEFAULT_MODELS, agent: Path = DEFAULT_AGENT) -> None:
    """校验配置文件并打印模型角色路由。"""
    config = _require_models(models)
    table = Table(title="模型角色路由")
    table.add_column("角色")
    table.add_column("模型")
    for role, ref in config.roles.items():
        table.add_row(role, ref)
    console.print(table)
    if not config.has_vision:
        console.print("[yellow]警告：未配置 vision 角色，截图视觉验证将不可用[/yellow]")
    if agent.exists():
        settings = load_agent_settings(agent)
        console.print(f"agent.yaml 正常：{len(settings.mcp_servers)} 个 MCP server 待挂载")
    else:
        console.print(f"[dim]未找到 {agent}（可选）：需要工程能力时从 example 复制[/dim]")
    console.print("[green]配置校验通过[/green]")


@app.command()
def chat(models: Path = DEFAULT_MODELS, agent: Path = DEFAULT_AGENT) -> None:
    """交互式会话：连接 MCP 工具后进入 agent 循环。"""
    config = _require_models(models)
    settings = load_agent_settings(agent) if agent.exists() else AgentSettings()
    asyncio.run(_chat(config, settings))


def _require_models(path: Path) -> ModelsConfig:
    if not path.exists():
        console.print(f"[red]未找到 {path}[/red]，请从 config/models.example.yaml 复制并填写")
        raise typer.Exit(1)
    return load_models_config(path)


def _cli_confirm(tool_name: str, arguments: dict[str, Any]) -> bool:
    console.print(f"[yellow]写操作请求：{tool_name}[/yellow] 参数：{arguments}")
    return typer.confirm("允许执行？")


async def _chat(config: ModelsConfig, settings: AgentSettings) -> None:
    # litellm 导入耗时数秒，放到真正需要时再加载
    from ue5agent.core.loop import AgentLoop
    from ue5agent.core.permissions import PermissionGate
    from ue5agent.llm.client import LiteLLMClient
    from ue5agent.session_log import SessionLog
    from ue5agent.tools.mcp_client import McpManager
    from ue5agent.tools.registry import ToolRegistry

    llm = LiteLLMClient(config)
    registry = ToolRegistry(PermissionGate(confirmer=_cli_confirm))
    log = SessionLog(Path("sessions"))
    async with McpManager(settings.mcp_servers) as manager:
        await manager.register_all(registry)
        loop = AgentLoop(
            llm,
            registry,
            max_iterations=settings.limits.max_iterations,
            max_tool_result_chars=settings.limits.max_tool_result_chars,
            session_log=log,
        )
        console.print(f"[dim]已加载 {len(registry)} 个工具；输入 exit 退出[/dim]")
        while True:
            user_input = console.input("[bold cyan]you ›[/bold cyan] ").strip()
            if user_input.lower() in {"exit", "quit"}:
                break
            if not user_input:
                continue
            result = await loop.run(user_input)
            console.print(result.final_text)
            console.print(
                f"[dim]{result.turns} 轮 · {result.tool_call_count} 次工具调用 · "
                f"日志 {log.path}[/dim]"
            )
