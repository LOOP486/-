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
- **K0 已完成（2026-06-10）**：DeepSeek 两档（basic+hard）5 次跑分全部满分零方差，基线见 [../evals/baselines/deepseek-chat-2026-06-10.md](../evals/baselines/deepseek-chat-2026-06-10.md)。结论：沙盒尺度已饱和，K4 失败形态输入推迟到 M6 真实工程 trace；K1–K3 纯结构工作先行。
- **K1 已完成（2026-06-10）**：`agent/state.py`（TaskSession/PlanStep/Artifact/Budgets + 持久化）、`agent/events.py`（类型化 TraceEvent + RunWriter + runs/ 产物目录）；loop 经 TraceSink 协议解耦并直写新 trace；chat 落 runs/，trace 命令双目录兼容。
- **M6 已并行起步（同日）**：agent_test 工程（UE5.7）补 C++ 模块脚手架并配置完成；真实 UBT 实测暴露并修复了解析缺口（Result: Failed 无前缀错误）。**当前阻塞：本机无 VS/Windows SDK**，安装后即可跑通完整编译闭环。

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
| 安装 Visual Studio 2022（含 C++ 游戏开发工作负载与 Windows SDK） | M6 完整编译闭环 |
| 对照模型 API key（可选，GPT/Claude 任一，用于多模型对比） | 仅多模型评测 |
| 模块化资产包 + 关卡 metrics 表 | Phase 2（白盒搭建） |

已到位：DeepSeek API key（.env）；GitHub 远端（LOOP486）；UE5.7 引擎 + agent_test 测试工程（已补 C++ 脚手架）。
