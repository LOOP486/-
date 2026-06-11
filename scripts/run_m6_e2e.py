"""M6 端到端验收：agent 自主在 agent_test 工程加 gameplay 功能并编译通过。

跑法：uv run python scripts/run_m6_e2e.py
批模式：写操作不弹确认，但 WRITE_PROJECT 仍强制前置 git checkpoint。
产物：runs/<id>/（trace.jsonl + report.md + session.json）
"""

import asyncio
from pathlib import Path

from dotenv import load_dotenv

from ue5agent.agent.events import RunWriter
from ue5agent.agent.state import TaskSession
from ue5agent.config import load_agent_settings, load_models_config
from ue5agent.core.loop import SYSTEM_PROMPT, AgentLoop
from ue5agent.core.permissions import PermissionGate
from ue5agent.llm.client import LiteLLMClient
from ue5agent.mcp_servers.repo_tools import gitops
from ue5agent.tools.fs_tools import build_fs_tools
from ue5agent.tools.mcp_client import McpManager
from ue5agent.tools.registry import ToolRegistry

TASK = """\
给工程新增一个蓝图函数库：
1. 在 Source/agent_test/ 下创建 UAgentTestUtils 类（继承 UBlueprintFunctionLibrary），
   提供一个 BlueprintCallable 的静态函数 GetAgentGreeting()，返回 FString "Hello from ue5agent"。
2. 用 ue_build__ubt_compile 编译 agent_testEditor 目标，如有错误就修复，直到编译成功。
3. 完成后报告：创建了哪些文件、编译结果摘要。
"""

PROJECT_CONTEXT = """

当前工程信息：
- 工程根目录即文件工具的根；源码在 Source/agent_test/，模块名 agent_test
- 反射代码需要 #include "AgentTestUtils.generated.h"（generated 头必须是最后一个 include）
- 编译工具：ue_build__ubt_compile，只需传 target=agent_testEditor；
  引擎与工程路径已由环境变量提供，不要手动传 engine_root / uproject
- 文件路径一律相对工程根，不要尝试访问工程目录之外
"""


async def main() -> None:
    load_dotenv()
    models = load_models_config(Path("config/models.yaml"))
    settings = load_agent_settings(Path("config/agent.yaml"))
    assert settings.project, "agent.yaml 需要配置 project.uproject"
    project_root = settings.project.uproject.parent

    def checkpoint() -> bool:
        try:
            ref = gitops.checkpoint(project_root, "auto: m6 e2e write")
            print(f"[checkpoint] {ref['ref']}")
            return True
        except RuntimeError as exc:
            print(f"[checkpoint 失败] {exc}")
            return False

    registry = ToolRegistry(PermissionGate(checkpoint=checkpoint))
    for spec in build_fs_tools(project_root):
        registry.register(spec)

    session = TaskSession.new("m6-e2e-blueprint-function-library")
    writer = RunWriter(Path("runs"), session)
    llm = LiteLLMClient(models)
    ue_build_only = {k: v for k, v in settings.mcp_servers.items() if k == "ue_build"}
    async with McpManager(ue_build_only) as manager:
        await manager.register_all(registry)
        loop = AgentLoop(
            llm,
            registry,
            system_prompt=SYSTEM_PROMPT + PROJECT_CONTEXT,
            max_iterations=25,
            session_log=writer,
        )
        result = await loop.run(TASK)
        writer.write_report(result.final_text)
        session.status = "done"
        writer.save_session()
        print(result.final_text)
        print(
            f"\n=== turns={result.turns} tools={result.tool_call_count} "
            f"tokens={result.prompt_tokens}+{result.completion_tokens} ==="
        )
        print(f"trace: {writer.trace_path}")


if __name__ == "__main__":
    asyncio.run(main())
