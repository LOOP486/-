# ADR-0001：自研 agent loop，经 LiteLLM 做多模型适配

- 状态：已接受
- 日期：2026-06-10

## 背景

用户要求 agent 支持自设 API：GPT、Claude、DeepSeek 及自定义中转（base_url）。绑定单一厂商 SDK 或托管框架（如 Claude Code 作为运行时）无法满足。

## 决策

agent 核心是自研的轻量 tool-calling 循环（`core/loop.py`），模型访问统一走 LiteLLM（OpenAI 格式，自动处理各家 function calling 差异）。模型按角色路由（planner/coder/vision）配置在 `models.yaml`，每个 provider 可自定义 base_url 与 api_key 环境变量。

loop 只依赖 `llm/types.py` 的 `ChatModel` 协议，不直接依赖 litellm——保证可测试、可替换。

## 备选与取舍

- Claude Code / Claude Agent SDK 作宿主：上下文管理与权限系统现成，但模型锁定，否。开发期仍可把 MCP 工具挂到 Claude Code 上先行验证（工具层解耦带来的福利）。
- LangGraph / AutoGen 等重框架：抽象层厚、调试成本高，我们的循环本质很简单，否。
- OpenAI Agents SDK + LitellmModel：可行的折中，若自研 loop 维护成本超预期可迁移。

## 后果

- 上下文压缩、会话记忆等需要自己实现（见 roadmap Phase 0 待办）。
- vision 能力依赖所配模型；DeepSeek 主力模型无视觉，配置校验需提示（已实现 has_vision 检查）。
