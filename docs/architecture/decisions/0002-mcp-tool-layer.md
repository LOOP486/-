# ADR-0002：工具层统一走 MCP 协议

- 状态：已接受
- 日期：2026-06-10

## 背景

工具能力（编译、编辑器桥、未来的白盒搭建）需要同时服务自研 loop，开发期还想挂到 Claude Code / Cursor 上先行验证；UE 社区现成的桥接项目也都是 MCP 形态。

## 决策

所有工具能力实现为 MCP server（stdio），自研 loop 经 `tools/mcp_client.py` 挂载，工具名加 server 前缀防重名，授权级别（read/write/dangerous）按 server 在 `agent.yaml` 配置。

## 备选与取舍

- 进程内 Python 函数注册：少一层进程通信，但与生态隔绝、无法复用现成 UE 桥，否。注册表仍保留本地工具能力（ToolSpec 直注），两者并存。

## 后果

- 工具层与 loop、模型三者完全解耦；任何 MCP 客户端都能复用我们的 server。
- 多一层 stdio 子进程管理（McpManager 负责生命周期）。
