# 路线图

阶段定义与验收标准的完整版见 [architecture/design.md §11](architecture/design.md)。本页是工作清单，随进展勾选。

## Phase 0：核心骨架 + C++ 编译闭环（进行中）

- [x] 仓库工程化：uv + ruff + pytest + mypy，setup/check 脚本
- [x] 配置体系：models.yaml（角色路由）/ agent.yaml / .env，校验与 CLI
- [x] agent 主循环：tool-calling、工具错误回传、迭代预算、会话 JSONL 日志
- [x] 权限网关：read/write/dangerous 三级 + 白名单 + CLI 确认
- [x] MCP 客户端：stdio 挂载、工具前缀、按 server 配授权
- [x] ue_build MCP server：UBT 编译 + 结构化诊断解析（含单测）
- [ ] 对真实 UE 工程实测 ubt_compile，补齐解析遗漏的报错形态
- [ ] chat 会话内多轮记忆（当前每条输入独立开局）
- [ ] 历史压缩 compact_history 实现（当前直通占位）
- [ ] 验收：任一模型完成「加一个 gameplay 功能并编译通过」

## Phase 1：编辑器桥（让 agent 看见蓝图）

- [ ] fork flopperam/unreal-engine-mcp 进 unreal/，跑通最小链路（见 ADR-0005）
- [ ] 砍蓝图编辑工具，保留场景/资产/截图/日志
- [ ] 自研蓝图只读导出：bp_overview / bp_pseudocode / bp_graph / bp_find_usages
- [ ] 验收：agent 能回答「这个蓝图做了什么、谁在用它」

## Phase 2：白盒搭建子系统

- [ ] 资产 manifest 工具链（自动扫描出草稿 + 人工补语义）
- [ ] 布局 DSL schema 与布局编译器（见 ADR-0004）
- [ ] actors_spawn_batch + 整批回滚
- [ ] 校验器：重叠/封闭/连通 + 关卡 metrics 表 + NavMesh 可达性
- [ ] 视觉迭代：俯视/漫游截图 → vision 审查 → 局部重生成
- [ ] 验收：文字需求 + 模块资产库 → 可走通的白盒关卡 + 截图证据

## Phase 3：行为闭环与编排

- [ ] 自动化测试闭环（Functional Test 生成与运行）
- [ ] 子代理体系（上下文隔离 + 按角色配模型）
- [ ] 完整评测基准工程与跑分（一次通过率/迭代次数/人工干预次数）
- [ ] CI（GitHub Actions：ruff + pytest）

## 横切：agent 工程化（贯穿各阶段，按优先级排序）

agent 开发自身的复杂度清单。共同特征：不动架构，往既有接缝加模块。

- [ ] 弱模型容错（Phase 0–1）：工具参数 schema 校验、坏 JSON 修复、幻觉工具名的纠正回传——加在 registry.dispatch 链上；多模型支持是否成立取决于此
- [ ] 迷你评测集（Phase 1，不等 Phase 3）：10–20 个任务的 smoke eval，独立 harness 调 loop，每次提示词/工具描述改动跑分
- [ ] 可观测性（Phase 1）：session_log 升级为完整 trace（逐轮消息、token、成本），加回放查看命令——加在 loop 的事件点上
- [ ] API 故障面（Phase 1）：限流退避重试、超时、按 provider failover
- [ ] 流式输出与任务中断（Phase 2）
- [ ] 会话持久化与恢复（Phase 2）
