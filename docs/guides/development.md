# 开发工作流

## 日常循环

```powershell
git checkout -b feat/xxx
# ... 开发 ...
.\scripts\check.ps1          # ruff + pytest，提交前必须全绿
git commit -m "feat: xxx"
```

提交信息用 Conventional Commits（`feat:` `fix:` `docs:` `refactor:` `test:` `chore:`），描述中文。

## 代码规范

- ruff 管 lint 与格式（规则见 pyproject.toml），`uv run ruff check --fix` + `uv run ruff format`。
- 新代码尽量带类型注解；`uv run mypy src` 保持无新增报错。
- 注释与 docstring 用中文，写"为什么"而不是复述代码。
- 模块职责边界（违反即架构腐蚀，参考 CLAUDE.md 硬性约定）：
  - `core/` 不依赖 litellm/mcp 等外部 SDK，只依赖协议类型；
  - MCP server 的纯逻辑独立成可单测模块，server.py 只接线；
  - 新工具必须声明 PermissionLevel。

## 测试约定

- 外部依赖（LLM、子进程、网络）一律用替身：`ChatModel` 协议有 FakeModel 先例（tests/test_loop.py）。
- 解析器类代码（如 ubt.py）用真实样本输出做用例；遇到线上解析遗漏，先把样本加进测试再修。
- 跑法：`uv run pytest -q`；单个文件 `uv run pytest tests/test_loop.py -q`。

## 文档维护

| 变化 | 动作 |
|---|---|
| 架构级决策 | docs/architecture/decisions/ 新增 ADR（旧 ADR 不改写） |
| 操作步骤变化 | 更新 docs/guides/ |
| 任务进展 | 勾选/调整 docs/roadmap.md |
| 对外行为变化 | CHANGELOG.md [未发布] 段加一行 |

## 依赖管理

加依赖：`uv add <pkg>`（dev 依赖 `uv add --dev <pkg>`）；uv.lock 由 uv 维护、随提交入库，不手改。
