# ADR-0005：编辑器桥基于 flopperam/unreal-engine-mcp fork，不从零自研

- 状态：已接受
- 日期：2026-06-10

## 背景

UE×MCP 开源生态已成熟。flopperam/unreal-engine-mcp（MIT，1000+ star，UE5.5–5.7）的 Python MCP server + C++ 插件（TCP）架构与我们的设计一致，且自带批量场景构建工具；remiphilippe/mcp-unreal（Apache-2.0）的 headless 编译/测试三通道设计完整。

## 决策

Phase 1 fork flopperam 作为编辑器桥基底，放入 `unreal/`：砍掉蓝图编辑类工具（ADR-0003），补三块自研——蓝图只读导出（概览/伪代码）、白盒搭建工具集（ADR-0004）、结构化编译诊断（参考 remiphilippe 的做法，Phase 0 已先行在 `ue_build` 实现）。

## 备选与取舍

- 从零写插件：通信层、GameThread 调度、资产 API 封装全要重做，约多花 4–6 周，否。
- 只用官方 Remote Control API：免插件但能力不够（无蓝图图表访问、无自定义工具面），作为补充通道保留。

## 后果

- 受上游演进影响；MIT 许可 fork 自持，通信协议简单（TCP+JSON），断更可控。
- 需持续跟踪 UE 版本适配（上游已覆盖 5.5–5.7）。
