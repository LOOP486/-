# Changelog

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [未发布]

### 新增

- `ue_editor` 新增 `editor_status` 工具：探测编辑器桥在线状态，编辑器相关操作前可先确认环境就绪。
- 新增 `ue_lifecycle` MCP server（dangerous 级）：`editor_launch` 启动 UE 编辑器并等待桥端口就绪，已运行则幂等返回；agent.yaml 新增 `permissions.allowlist` 配置 dangerous 工具白名单（CLI 已接线，此前白名单无配置通路、dangerous 工具必被拒）。

### 修复

- 环境未就绪快速失败：编辑器桥连接被拒时（如 UE 编辑器未启动），runner 不再消耗 3 次步骤重试逐个跳过，而是立即终止并在报告中给出可操作指引（跨进程靠 `[env:unready]` 文本标记传递错误类别）。
- 任务报告的步骤执行小结截断阈值 300 → 2000 字符，截断时带明确标记，不再无声切碎参数 JSON。

- agent 工程化（M1–M5）：工具调用容错（坏 JSON 修复/schema 校验/近似工具名）、trace 与 `ue5agent trace` 回放、API 重试退避与角色降级链、chat 多轮记忆与历史压缩、迷你评测集与 `ue5agent eval`。
- eval 报告新增工具错误率与成本估算，`--out` 导出 JSON；DeepSeek 真模型基线归档（evals/baselines/，basic+hard 两档全满分零方差）。
- Agent Kernel K1：TaskSession/PlanStep/Artifact 数据结构与持久化、类型化 TraceEvent、runs/ 按次产物目录；chat 会话落 runs/，trace 命令兼容新旧目录。
- ubt 解析器捕获无 ERROR: 前缀的 UBT 失败（Result: Failed + 上文回溯），来自 UE5.7 真机样本。
- Agent Kernel K2：工具管线模块化（参数规范化/结果信封/失败签名熔断），registry 保持兼容。
- Agent Kernel K3：权限 4 级（write_safe/write_project 拆分，工程写前置自动 checkpoint，危险操作双条件）；新增 repo_tools MCP server（git 快照与还原）。
- M6 编译闭环实测通过（UE5.7+VS2026）：成功构建、注入错误结构化定位、修复回绿；解析器在已有具体错误时抑制 Result: Failed 冗余汇总。
- Agent Kernel K4/K5：TaskRunner 阶段状态机（计划/执行/judge 验收/恢复/报告，ADR-0006），chat 与 e2e 切换至 runner；移除 session_log 旧路径；真模型门禁通过（eval 两档满分持平基线，e2e 含 judge 打回重试的真实恢复）。

- 项目骨架：agent 主循环（tool-calling）、按角色的多模型路由（LiteLLM）、工具注册表与三级权限网关、MCP 客户端、会话 JSONL 日志。
- 自带 MCP server `ue_build`：UBT 编译调用与结构化诊断解析（MSVC/链接/UBT 错误）。
- CLI：`ue5agent check-config / chat / version`。
- 文档体系：架构设计、ADR×5、上手与开发指南、路线图、术语表。
- 工程化：uv + ruff + pytest + mypy，scripts/setup.ps1 与 scripts/check.ps1。
