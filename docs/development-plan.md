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
- **K2 已完成（2026-06-10）**：`agent/tool_pipeline.py` 吸收调用链，新增参数规范化（数字/布尔温和转型、路径分隔符归一）、ToolOutcome 结果信封、失败签名熔断（连续同类错误升级提示，K4 recovery 的数据源）；registry 瘦身为注册表。
- **K3 已完成（2026-06-10）**：权限升 4 级（READ/WRITE_SAFE/WRITE_PROJECT/DANGEROUS），WRITE_PROJECT 前置自动 checkpoint、DANGEROUS 白名单+确认双条件；repo_tools MCP server（checkpoint/status/list/restore，write-tree 快照不动工作区）；agent_test 工程已纳入 git。
- **K4 核心已落地（2026-06-11）**：TaskRunner 阶段机（planner/verifier/recovery/report），trivial fast path，步内微循环复用 AgentLoop，judge 三态验收（只看工具证据），失败重试带理由回灌、超限放弃跳步。六条状态路径单测全绿。**K4/K5 已完成（2026-06-11）**：真模型门禁通过——eval 两档 18 任务满分持平基线；e2e 走 TaskRunner 全程成功（planner 拆 3 步、s3 被 judge 打回后重试通过=恢复路径真实触发）。K5 切换：chat/e2e 接 runner、session_log 删除、ADR-0006 落档。kernel 重构仅剩 K6（能力注册表，可选尾巴）。
- **M6 完成 = Phase 0 完成（2026-06-11）**：agent（DeepSeek）自主端到端通过——5 轮 7 工具调用写出 BlueprintFunctionLibrary 并编译成功（7.51s 零错误），写文件前自动 checkpoint。过程中排除三个真实挂死根因（均非模型问题）：① MCP 子进程缺 env；② UBT 等锁/管道死锁→临时文件重定向+taskkill /T+墙钟预算；③ UBT git 工作集探测与 checkpoint 抢锁→全局 BuildConfiguration.xml 禁用。首批真实 trace 已归档 runs/，作为 K4 设计输入。

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
