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
def trace(
    path: Path | None = typer.Argument(None, help="trace 文件；缺省取 sessions/ 最新一份"),
) -> None:
    """回放查看一次会话：逐轮模型决策、工具调用、耗时与 token。"""
    from ue5agent.session_log import latest_session, read_events

    target = path or latest_session(Path("sessions"))
    if target is None or not target.exists():
        console.print("[red]找不到 trace 文件（sessions/ 为空？）[/red]")
        raise typer.Exit(1)
    console.print(f"[dim]{target}[/dim]")
    for event in read_events(target):
        _render_event(event)


def _render_event(event: dict[str, Any]) -> None:
    kind = event.get("event")
    if kind == "run_start":
        console.rule(f"任务：{str(event.get('user_input', ''))[:80]}")
    elif kind == "llm_turn":
        tokens = f"{event.get('prompt_tokens', 0)}+{event.get('completion_tokens', 0)} tok"
        tools = ", ".join(event.get("tool_names") or []) or "（直接答复）"
        console.print(
            f"[bold]轮 {event.get('turn')}[/bold] "
            f"[dim]{event.get('duration_ms', 0)}ms · {tokens}[/dim] → {tools}"
        )
        if event.get("content_preview"):
            console.print(f"  [dim]{event['content_preview']}[/dim]")
    elif kind == "tool_call":
        console.print(
            f"  ↳ {event.get('tool')} [dim]{event.get('duration_ms', 0)}ms · "
            f"{event.get('result_chars', 0)} 字符[/dim]"
        )
        if event.get("result_preview"):
            console.print(f"    [dim]{event['result_preview']}[/dim]")
    elif kind == "run_end":
        console.print(
            f"[green]完成[/green] {event.get('turns')} 轮 · "
            f"{event.get('tool_calls')} 次工具调用 · "
            f"{event.get('prompt_tokens', 0)}+{event.get('completion_tokens', 0)} tok"
        )


@app.command("eval")
def eval_command(
    tasks: Path = Path("evals/tasks/basic.yaml"),
    models: Path = DEFAULT_MODELS,
    role: str = "planner",
    out: Path | None = typer.Option(None, "--out", help="把报告另存为 JSON（基线归档用）"),
) -> None:
    """跑迷你评测集：每个任务在干净沙盒中运行，输出通过率与失败原因。"""
    config = _require_models(models)
    from ue5agent.evals.runner import load_tasks, run_eval
    from ue5agent.llm.client import LiteLLMClient

    task_list = load_tasks(tasks)
    client = LiteLLMClient(config)
    model_ref = client.model_for(role)
    report = asyncio.run(run_eval(task_list, lambda: client, role=role))

    table = Table(title=f"评测报告（角色：{role}，模型：{model_ref}）")
    table.add_column("任务")
    table.add_column("结果")
    table.add_column("轮数", justify="right")
    table.add_column("工具错误", justify="right")
    table.add_column("token", justify="right")
    table.add_column("失败原因")
    for result in report.results:
        table.add_row(
            result.name,
            "[green]通过[/green]" if result.passed else "[red]失败[/red]",
            str(result.turns),
            str(result.tool_errors),
            str(result.prompt_tokens + result.completion_tokens),
            "；".join(result.failures),
        )
    console.print(table)
    cost = _estimate_cost(model_ref, report.total_prompt_tokens, report.total_completion_tokens)
    cost_text = f" · 估算成本 ${cost:.4f}" if cost is not None else ""
    console.print(
        f"通过率 [bold]{report.pass_rate:.0%}[/bold]"
        f"（{sum(1 for r in report.results if r.passed)}/{len(report.results)}）"
        f" · 工具错误率 {report.tool_error_rate:.0%}"
        f" · 总 token {report.total_tokens}{cost_text}"
    )
    if out:
        _dump_report(out, model_ref, role, report, cost)
        console.print(f"[dim]报告已存 {out}[/dim]")
    if report.pass_rate < 1.0:
        raise typer.Exit(1)


def _estimate_cost(model_ref: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    """按 litellm 价格表估算（美元）；查不到价格时返回 None。"""
    try:
        import litellm

        info = litellm.model_cost.get(model_ref) or litellm.model_cost.get(
            model_ref.split("/", 1)[-1]
        )
        if not info:
            return None
        return prompt_tokens * info.get("input_cost_per_token", 0) + completion_tokens * info.get(
            "output_cost_per_token", 0
        )
    except Exception:
        return None


def _dump_report(out: Path, model_ref: str, role: str, report: Any, cost: float | None) -> None:
    import json
    import time
    from dataclasses import asdict

    payload = {
        "model": model_ref,
        "role": role,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "pass_rate": report.pass_rate,
        "tool_error_rate": report.tool_error_rate,
        "total_tokens": report.total_tokens,
        "estimated_cost_usd": cost,
        "results": [asdict(result) for result in report.results],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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
            compact_budget_chars=settings.limits.compact_budget_chars,
            session_log=log,
        )
        console.print(f"[dim]已加载 {len(registry)} 个工具；输入 exit 退出[/dim]")
        history: list[dict[str, Any]] = []
        while True:
            user_input = console.input("[bold cyan]you ›[/bold cyan] ").strip()
            if user_input.lower() in {"exit", "quit"}:
                break
            if not user_input:
                continue
            result = await loop.run(user_input, history=history)
            console.print(result.final_text)
            console.print(
                f"[dim]{result.turns} 轮 · {result.tool_call_count} 次工具调用 · "
                f"日志 {log.path}[/dim]"
            )
