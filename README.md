# ue5agent

面向 UE5 游戏开发的 agent：用 C++ 实现功能、读懂蓝图工程、搭建 slab-first 白盒场景，并自主完成编译、测试与本地预览/截图验证。支持 GPT / Claude / DeepSeek 及任意 OpenAI 兼容 API。

**当前状态**：Phase 0–3 主线已完成（C++ 编译闭环、蓝图只读理解、白盒搭建、行为验证与子代理编排均已收口）。当前重点是白盒能力优化和平面图输入：默认白盒结构已切到 slab-first；平面图 v1 已打通墙线提取、SVG/grid DSL、门宽标定、本地预览和 UE 在线验收；后续主要补精细识别、真实失败样本回归、资产语义增强，以及流式输出/任务中断/会话恢复等体验项。当前进度以 [路线图](docs/roadmap.md) 和 [CHANGELOG](CHANGELOG.md) 为准；[工作日志](docs/worklog.md) 只保留历史交接与踩坑史。

## 快速开始

```powershell
# 1. 环境部署（安装 uv、同步依赖、生成本机配置）
.\scripts\setup.ps1

# 2. 填写 config/models.yaml（模型与 API key）和 .env，然后校验
uv run ue5agent check-config

# 3. 进入交互会话
uv run ue5agent chat
```

日常开发用 `.\scripts\check.ps1` 一键跑 lint + 测试。

## 仓库结构

```
ue5agent/
├── src/ue5agent/          # agent 核心
│   ├── core/              #   主循环、上下文管理、权限网关
│   ├── llm/               #   LiteLLM 多模型适配（按角色路由）
│   ├── tools/             #   工具注册表、MCP 客户端
│   ├── mcp_servers/       #   自带 MCP server（ue_build：UBT 编译）
│   └── cli.py             #   命令行入口
├── tests/                 # 单元测试（外部依赖一律用替身）
├── config/                # 配置模板（真实配置不入库）
├── docs/                  # 文档体系（见 docs/README.md）
├── unreal/                # UE 编辑器桥插件（Phase 1）
└── scripts/               # setup / check
```

## 文档导航

| 想了解 | 看这里 |
|---|---|
| 整体架构与设计依据 | [docs/architecture/design.md](docs/architecture/design.md) |
| 为什么这么设计（决策记录） | [docs/architecture/decisions/](docs/architecture/decisions/) |
| 环境搭建 | [docs/guides/getting-started.md](docs/guides/getting-started.md) |
| 开发工作流与规范 | [docs/guides/development.md](docs/guides/development.md) |
| 项目作品集展示页 | [docs/portfolio.html](docs/portfolio.html) |
| 阶段计划与任务 | [docs/roadmap.md](docs/roadmap.md) |
| 最新用户可见变化 | [CHANGELOG.md](CHANGELOG.md) |
| 术语表 | [docs/reference/glossary.md](docs/reference/glossary.md) |
