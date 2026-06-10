# 开发计划

> 本页是施工蓝图：里程碑顺序、任务分解、验收标准。高层阶段定义见 [roadmap.md](roadmap.md)，两者关系：roadmap 管"做什么/做没做"，本页管"按什么顺序怎么做"。
> 制定：2026-06-10 | 修订：2026-06-10（M1–M5 完成；插入 Phase 0.5 Agent Kernel 重构）

## 排序原则

1. **评测与容错先行**：先有分数和容错层，每一步都可度量。
2. **不被外部依赖阻塞**：API key、UE 工程到位前先完成纯代码件。
3. **每个里程碑独立收口**：结束时 lint+测试全绿、文档同步、独立提交。

## 已完成（2026-06-10，66 个单测全绿）

| 里程碑 | 交付物 | 提交 |
|---|---|---|
| M1 弱模型容错层 | JSON 机械修复、jsonschema 参数校验、近似工具名提示、CI workflow | 2e9726c |
| M2 可观测性 | trace 事件（耗时/token/预览）、LoopResult token 汇总、`ue5agent trace` 回放 | 02fbafe |
| M3 API 故障面 | 瞬态错误指数退避、角色 fallback 链、错误分类 | bf65500 |
| M4 会话与上下文 | chat 多轮记忆、compact_history（配对边界安全） | 2f2b01d |
| M5 迷你评测集 | 沙盒工具组、6 种检查器、runner 报告、`ue5agent eval`、首批 10 任务 | cea89a5 |

## 当前阶段：Phase 0.5 Agent Kernel 重构

完整方案见 **[kernel-refactor-plan.md](kernel-refactor-plan.md)**（已批准待施工）。要点：

- 决策：按完整 Agent Kernel 形态重构——TaskSession 状态机、Planner/Verifier/Recovery、runs/ 产物目录、权限 4 级、judge 角色；**先真模型基线（K0）再细化 kernel**。
- 里程碑：K0 真模型基线 → K1 数据结构与 trace → K2 工具管线 → K3 权限与 repo_tools → K4 Runner 状态机 → K5 切换清理 → K6 能力注册表。
- **K0 阻塞于用户提供 API key**（建议先 DeepSeek）。

## 后续阶段（Phase 0.5 之后）

### M6 真实 UE 闭环验证（需：UE 引擎根目录 + .uproject）

- [ ] ubt_compile 对真实工程实测，按实际输出补解析样本与测试
- [ ] 端到端验收：「加一个 gameplay 功能并编译通过」= Phase 0 完成
- [ ] UE 依赖类 eval case 起步（fix_compile_error 等，目录式案例）

### M7 编辑器桥（Phase 1，开工前单独细化）

fork flopperam → 裁剪 → 蓝图只读导出四件套（见 ADR-0005）。不等 K6 收尾，K5 后即可并行启动调研。

## 当前等待用户提供

| 事项 | 阻塞 |
|---|---|
| 至少一个模型 API key（建议先 DeepSeek；最好加一个对照模型） | K0 |
| UE 引擎根目录 + 测试用 .uproject | M6、M7 |
| GitHub/Gitee 远端仓库（CI 生效与备份） | 仅 CI |
| 模块化资产包 + 关卡 metrics 表 | Phase 2（白盒搭建） |
