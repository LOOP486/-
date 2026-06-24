# ue5agent

面向 UE5 游戏开发的 agent：用 C++ 实现功能、读懂蓝图工程、搭建 slab-first 白盒场景，并自主完成编译、测试与本地预览/截图验证。支持 GPT / Claude / DeepSeek 及任意 OpenAI 兼容 API。

**当前状态**：Phase 0–3 主线已完成（C++ 编译闭环、蓝图只读理解、白盒搭建、行为验证与子代理编排均已收口）。当前推进白盒能力优化：默认白盒结构已切到 slab-first，使用 Engine Cube 连续地板/片墙表达空间，门窗只作为墙洞；ArchKit 模块化地板/墙/门/窗/navproxy 和旧多层 room 行为保留为显式 `structure_mode="modular"`。B+ 玩法层已支持相邻楼层楼梯、楼梯井 guard、原生尺寸 props/cover/pillar、自动 route markers 与真实 `PlayerStart` 出生点；白盒可靠性底座新增 UE imported bounds 资产审计、visual AABB 校验、结构 metrics 和截图/视觉硬证据门禁。B7 SPC/DST 标准结构 baseline 已 6/6 归档，短期继续聚焦白盒搭建 agent 的自主构型稳定性，随后推进平面图输入。详见 [路线图](docs/roadmap.md)、[工作日志](docs/worklog.md) 与 [CHANGELOG](CHANGELOG.md)。

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
| 阶段计划与任务 | [docs/roadmap.md](docs/roadmap.md) |
| 术语表 | [docs/reference/glossary.md](docs/reference/glossary.md) |
