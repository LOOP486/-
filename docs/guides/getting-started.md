# 上手指南

## 前置条件

- Windows 10/11，git
- [uv](https://docs.astral.sh/uv/)（Python 由 uv 托管，无需单独安装）：`winget install astral-sh.uv`
- 需要工程能力时：本机装有 UE5 引擎（写下引擎根目录备用）

## 安装

```powershell
git clone <repo-url> ue5agent
cd ue5agent
.\scripts\setup.ps1     # 同步依赖 + 生成本机配置模板
```

## 配置

1. `config/models.yaml`：填角色路由。至少配 `planner`；要用截图视觉验证必须配 `vision`（多模态模型）。
2. `.env`：填各 provider 的 API key，以及 `UE_ENGINE_ROOT` / `UE_UPROJECT`。
3. `config/agent.yaml`（可选）：调整 MCP server 挂载与运行限额。

以上三个文件都不入库；模板分别是 `config/models.example.yaml`、`.env.example`、`config/agent.example.yaml`。

## 验证

```powershell
uv run ue5agent check-config   # 配置校验 + 角色路由表
uv run pytest -q               # 单元测试应全绿
uv run ue5agent chat           # 进入交互会话
```

chat 里试一句「编译 MyGameEditor」——agent 会调用 ue_build 的 `ubt_compile` 并回报结构化错误。

## 常见问题

- `uv: 无法识别` → 重开终端（安装后 PATH 需要重载）。
- LLM 报 401 → 检查 `.env` 的 key 与 `models.yaml` 的 `api_key_env` 名称是否对应。
- `ubt_compile` 报「引擎路径不对」→ `UE_ENGINE_ROOT` 应指向含 `Engine/` 的根目录，如 `C:/Program Files/Epic Games/UE_5.5`。
