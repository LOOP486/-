# Changelog

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [未发布]

### 新增

- 项目骨架：agent 主循环（tool-calling）、按角色的多模型路由（LiteLLM）、工具注册表与三级权限网关、MCP 客户端、会话 JSONL 日志。
- 自带 MCP server `ue_build`：UBT 编译调用与结构化诊断解析（MSVC/链接/UBT 错误）。
- CLI：`ue5agent check-config / chat / version`。
- 文档体系：架构设计、ADR×5、上手与开发指南、路线图、术语表。
- 工程化：uv + ruff + pytest + mypy，scripts/setup.ps1 与 scripts/check.ps1。
