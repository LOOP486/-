# 路线图

阶段定义与验收标准的完整版见 [architecture/design.md §11](architecture/design.md)。本页是工作清单，随进展勾选。

## Phase 0：核心骨架 + C++ 编译闭环（进行中）

- [x] 仓库工程化：uv + ruff + pytest + mypy，setup/check 脚本
- [x] 配置体系：models.yaml（角色路由）/ agent.yaml / .env，校验与 CLI
- [x] agent 主循环：tool-calling、工具错误回传、迭代预算、会话 JSONL 日志
- [x] 权限网关：read/write/dangerous 三级 + 白名单 + CLI 确认
- [x] MCP 客户端：stdio 挂载、工具前缀、按 server 配授权
- [x] ue_build MCP server：UBT 编译 + 结构化诊断解析（含单测）
- [x] 对真实 UE 工程实测 ubt_compile（UE5.7 + VS2026：成功/工具链缺失/注入错误三种形态均验证，真机样本固化为测试）
- [x] chat 会话内多轮记忆
- [x] 历史压缩 compact_history 实现（确定性摘要版）
- [x] 验收：任一模型完成「加一个 gameplay 功能并编译通过」（2026-06-11，DeepSeek 自主完成 BlueprintFunctionLibrary：5 轮 7 工具调用，编译 7.51s 零错误，全程自动 checkpoint——**Phase 0 完成**）

## Phase 1：编辑器桥（让 agent 看见蓝图）

- [ ] fork flopperam/unreal-engine-mcp 进 unreal/，跑通最小链路（见 ADR-0005）
- [ ] 砍蓝图编辑工具，保留场景/资产/截图/日志
- [ ] 自研蓝图只读导出：bp_overview / bp_pseudocode / bp_graph / bp_find_usages
- [ ] 验收：agent 能回答「这个蓝图做了什么、谁在用它」

## Phase 2：白盒搭建子系统

- [x] 资产 manifest v1（LevelPrototyping 套件，config/whitebox/；自动扫描草稿待资产包到位后做）
- [x] 布局 DSL 与编译器 v1（矩形房间/四向墙/门洞，几何与校验有单测，见 ADR-0004）
- [x] 批量 spawn + 前缀整批回滚（已对真实编辑器落地 12 件双房间布局验证；崩溃根因根治后三房间 agent 端到端核验通过）
- [x] 校验器：重叠/封闭/连通 + 关卡 metrics 表 + NavMesh 可达性（A1+A2 完成 2026-06-12：
  wb_validate 期望/实测对照 + navmesh_rebuild/path_test 真机验证）
- [ ] 视觉迭代：俯视/漫游截图 → vision 审查 → 局部重生成（development-plan A4，需 vision key；
  截图链路已就绪，缺 vision 审查与局部重生成）
- [ ] 验收：文字需求 + 模块资产库 → 可走通的白盒关卡 + 截图证据

## Phase 3：行为闭环与编排

- [ ] 自动化测试闭环（Functional Test 生成与运行）
- [ ] 子代理体系（上下文隔离 + 按角色配模型）
- [ ] 完整评测基准工程与跑分（一次通过率/迭代次数/人工干预次数）
- [ ] CI（GitHub Actions：ruff + pytest）

## 横切：agent 工程化（贯穿各阶段，按优先级排序）

agent 开发自身的复杂度清单。共同特征：不动架构，往既有接缝加模块。

- [x] 弱模型容错：参数 schema 校验、坏 JSON 修复、幻觉工具名纠正（registry.dispatch 链）
- [x] 迷你评测集：10 任务 smoke + `ue5agent eval`（通过率/工具错误率/token/成本）；DeepSeek 基线已归档（evals/baselines/）
- [x] 可观测性：trace 事件（耗时/token/预览）+ `ue5agent trace` 回放
- [x] API 故障面：指数退避重试、超时、角色级 fallback 链
- [ ] 流式输出与任务中断（Phase 2）
- [ ] 会话持久化与恢复（Phase 2）

## 横切：生产级强化（2026-06-12 外部评审吸收，细案见 development-plan.md Stage B/D）

- [x] 证据信封 v1：ToolOutcome.facts + verifier 两段式（确定性规则先行，LLM judge 兜底）（A3，2026-06-12）
- [x] PlanStep 契约 v2：allowed_tools / preconditions / success_checks / rollback_policy / 步级预算（B1，2026-06-12）
- [ ] 工具效果声明：幂等性 / requires_checkpoint / rollback_tool / 非幂等工具重试治理（B2）
- [ ] 错误分类与恢复策略表：bridge_down / partial_side_effect / evidence_missing 等差异化恢复（B3）
- [ ] 上下文工程 v1：工程状态摘要注入、progress 文件、按工具类型的结果摘要器（B4）
- [ ] 桥与凭据安全：TCP token 鉴权 + 协议版本握手 + trace secret redaction + 外部内容围栏（D1）
- [ ] 运行锁与清理：同工程单 runner 文件锁、runs prune、CI 离线门禁（D2）
