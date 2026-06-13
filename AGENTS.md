# AGENTS.md

本仓库是 ue5agent——面向 UE5 游戏开发的 agent 工具。AI 协作开发时遵循以下约定。

## 常用命令

```powershell
uv sync                  # 同步依赖（含 dev 组）
uv run pytest -q         # 跑测试
uv run ruff check src tests --fix
uv run ruff format src tests
uv run mypy src          # 类型检查（宽松模式，新代码尽量带注解）
uv run ue5agent check-config
.\scripts\check.ps1      # lint + 测试一键跑
```

## 架构速览

四层：Agent Kernel（`src/ue5agent/agent/runner.py` 阶段状态机，`core/loop.py` 为步内微循环引擎，见 ADR-0006）→ MCP 工具层（`tools/mcp_client.py` 挂载外部 server，`mcp_servers/` 是自带 server）→ UE5 工程 → 知识层。模型路由按角色（planner/coder/vision）配置在 `config/models.yaml`，经 LiteLLM 适配任意 OpenAI 兼容 API。

完整设计在 docs/architecture/design.md；动架构前先读它和 docs/architecture/decisions/ 下的 ADR。

## 硬性约定

- **loop 不依赖 litellm**：`core/loop.py` 只依赖 `llm/types.py` 的 `ChatModel` 协议，测试用替身。不要在 core 里 import litellm。
- **工具失败回传模型**：registry.dispatch 把异常转成 `[error] ...` 文本返回，不抛出中断循环。
- **权限分级**：新工具必须标 PermissionLevel（read/write/dangerous），危险操作默认拒绝。
- **MCP server 里的纯逻辑独立成模块**（如 `ue_build/ubt.py`），保证可单测；server.py 只做接线。
- 注释与文档用中文；标识符用英文；ruff 规则见 pyproject.toml。

## 文档维护规则

- 改了架构级决策 → 在 docs/architecture/decisions/ 新增 ADR（已接受的 ADR 不改写，用新 ADR 取代）。
- 改了操作步骤 → 更新 docs/guides/。
- 完成/新增任务 → 更新 docs/roadmap.md 的勾选项。
- 对外可见的行为变化 → 记入 CHANGELOG.md 的 [未发布] 段。

## 提交约定

Conventional Commits：`feat:` / `fix:` / `docs:` / `refactor:` / `test:` / `chore:`，描述用中文。不要提交 config/models.yaml、config/agent.yaml、.env（已在 .gitignore）。
