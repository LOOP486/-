# Stage E 行为闭环与编排——细化任务清单（Phase 3）

> 依据：[roadmap.md](roadmap.md) Phase 3、[development-plan.md](development-plan.md) Stage E。
> 制定：2026-06-12（Stage A/B/C1/D 离线项收口后）。同 P1 模式：开工前先出此细案。
> 状态：2026-06-13 已全部真机收口；C2 收尾、C3、D1.1 服务端与 E1/E2/E3 均已落地。

## 目标

从"agent 能做且能证明做对"升级到"行为闭环 + 多角色编排"：
1. 关卡/逻辑改动后能自动跑 PIE/Functional Test 验证运行期行为（不止编译/几何/导航）。
2. 子代理体系：上下文隔离 + 按角色配模型，解除单一 DeepSeek 依赖（vision/judge 换专长模型）。
3. 完整评测基准工程与跑分（一次通过率/迭代次数/人工干预次数）。

## 排序原则

1. 复用既有接缝：PIE/Automation 经 ue_editor 桥新增命令暴露（同 A1/C2 插件模式）；子代理复用 runner/loop，不另起架构。
2. 真机优先级：E1（PIE/Automation）是 Phase 3 的产品价值核心，先做；E2（子代理）解依赖、E3（基准）量化收尾。
3. 右尺寸：仍是单机单用户工具，子代理是"进程内角色隔离"，不做分布式 worker。

---

## E1 运行期验证闭环（PIE / Output Log / Functional Test）✅（2026-06-13）

- **目标**：agent 改完蓝图/关卡后，能启动 PIE、读 Output Log 作为运行期证据。
- **任务**：
  1. ✅ 插件（agent_test，C++）新增命令：
     - `pie_start` / `pie_stop`：启动/结束 PIE，pie_stop 返回本窗口新增 Error/Warning 计数与错误行。
       拆成两命令是因为 ExecuteCommand 同步占用 GameThread——单命令里跑 N 秒会让 PIE 无法 tick；
       由 Python 侧 `pie_smoke` 工具编排 start→等待→stop。当前播放当前关卡（map 参数留后续）。
     - `output_log_tail(lines, severity)`：读 Output Log 尾部（按级别过滤）。
     - 新增 `MCPLogCapture`（GLog 环形捕获，线程安全 + 单调序号窗口精确 + 滤控制消息/Verbose）。
     - ✅ `run_functional_test`：UE Automation/Functional Test——**真机验证通过（2026-06-13）**。
       插件 `functest_start`（StartTestByName 触发）+ `functest_poll`（跨帧 ExecuteLatentCommands
       推进 + StopTest + ExecInfo 解析）两命令 + `functest_list`（GetValidTestNames 发现）；
       Python `run_functional_test(test_name,timeout,poll_interval)` 跨帧轮询编排，落
       `functional_test` 事实（ok=passed），超时不伪造。真机：functest_list 列出 4854 个测试，
       `run_functional_test("FFColorSmokeTest")` 真跑通 passed=true，负向名正确报 not found。
  2. ✅ `ue_editor` 注册：pie_smoke 标 write_project（进 PIE/改状态），output_log_tail 标 read。
  3. ✅ A3 证据信封：pie 落 `pie` 事实（ok=error_count==0）、output_log_tail 落 `output_log` 事实。
  4. `pie_crash` 类别未单列：PIE 期间编辑器若崩，桥失联自然归 bridge_down（已有恢复），
     暂不需专类；真遇到崩溃高发再立。
- **验收**：✅ 真机：pie_start→等待→pie_stop 进出 PIE、error_count 窗口精确归零；output_log_tail
  按 severity 过滤、无控制消息噪声；run_functional_test smoke 已进入 E3 基线。更复杂的"会报错的
  关卡→修复→复跑零 Error"用例作为后续故障注入补充，不再阻塞 Stage E 收口。
- **依赖**：编辑器在线 + 插件 C++（已编译验证，2026-06-13）。

## E2 子代理体系（上下文隔离 + 按角色配模型）✅（2026-06-13）

- **目标**：把"探索/审查/规划"等子任务交给隔离上下文的子代理，各自可配专长模型，主循环只收摘要。
- **任务**：
  1. ✅ `agent/subagent.py`：spawn_subagent = 独立 history + 独立只读 system + 受限工具面
     （复用 ScopedRegistry）+ 角色级模型（复用 LiteLLMClient role 路由，未配角色回退 planner）；
     跑完只回结构化摘要、全文落 `Artifact(kind="subagent_summary")`（呼应 B4：主上下文不被
     子任务细节淹没）。
  2. ✅ 以 `spawn_subagent(task, role, allowed_tools)` 工具形态（READ 级）暴露为步内能力——
     步内 loop 可直接调；cli 在构造 runner 前注册（闭包指向当次 writer，chat 复用 registry
     故 replace=True）。未做成独立 PlanStep 类型（YAGNI：工具形态已够，不动状态机）。
  3. ✅ 角色解耦：models.yaml roles 可独立配 explorer/judge 等角色（vision 已是 Kimi）；
     models.example.yaml 加了 explorer/judge 注释示例与 provider params 示例。
- **验收**：✅ 单测（tests/test_subagent.py，10 例）：上下文隔离（独立 system、看不到主任务措辞）、
  工具面受限（只读、剔除写工具与 spawn_subagent 自身）、模型按角色路由、只回摘要、与主循环
  集成（主循环只见摘要不见子代理工具原始输出）、错误降级（无只读工具/异常/预算耗尽/空摘要/
  空任务均转 [error] 不上抛）。真机 token 下降量化对照可在后续真实任务中继续观察，不作为收口门禁。
- **依赖**：无硬真机依赖（沙盒 + 替身单测已覆盖）；与 E1/E3 并行。
- **范围取舍**：子代理工具面硬限只读（写操作必须留主循环——checkpoint/回滚/验收机器都在那里）；
  嵌套深度 1（恒排除 spawn_subagent 自身防递归失控）；子代理工具的 facts 不进主步骤证据通道
  （验证类工具应在主步骤跑）；预算 8 轮/180s 小于主步骤（子任务跑不完该拆小，不吞主任务墙钟）。

## E3 完整评测基准与 UE 在线 eval（含 C3）✅（2026-06-13：框架 + 用例 + 指标 + 真机基线全部完成）

- **目标**：统一 C3（UE 在线 eval 档）+ 完整基准跑分，量化"一次通过率/迭代次数/人工干预次数/token/成本"。
- **任务**：
  1. ✅ eval 框架扩展：`evals/ue_suite.py`（编排/指标/检查器，注入式 `run_one` 可离线单测）+
     `ue5agent eval --suite ue` 真机路径（probe_editor 探活 → 挂载 MCP → 逐任务 TaskRunner）。
  2. ✅ UE suite 用例 `evals/tasks/ue.yaml`（干净基线：read_blueprint_and_explain /
     blueprint_find_usages / wb_build_and_validate / run_functional_test_smoke）+
     `evals/tasks/ue_faults.yaml`（故障注入：编辑器断连 / UBT 多错误 / 白盒部分失败 / PIE 报错，
     需手动制造故障后单跑——不与基线混跑）。
  3. ✅ 指标：iteration_count（=Σ attempts）/ max_step_attempts（一次通过判据）/
     human_intervention（无人值守恒 0）；report 出 pass_rate / first_try_pass_rate /
     avg_iterations / total_human_intervention；`--out` 先落盘再打印（防控制台异常丢报告）。
  4. ✅ 沙盒两档（basic/hard）仍离线 CI 门禁；UE 档真机回归。
- **验收 ✅（真机 2026-06-13）**：`eval --suite ue --out evals/baselines/ue/deepseek-2026-06-13.json`
  → **4/4 通过、一次通过率 100%、平均迭代 1.5、人工干预 0**（含 run_functional_test 用例 agent
  端到端跑通）。故障注入复核：杀编辑器后单跑 → env_unready → 1 次尝试快速终止（13s 不空转）。
- **依赖**：E1 ✅、C2 ✅、编辑器在线（跑分阶段）。

### 插件命令契约（已落地真机 2026-06-13）

- `functest_start{test_name}` → `{started:bool, test_name}`：StartTestByName 触发，立即返回。
- `functest_poll` → `{finished:bool, passed:bool, error_count, warning_count, errors[]}`：
  ExecuteLatentCommands 跨帧推进；完成时 StopTest + ExecInfo。
- `functest_list{filter?, max?}` → `{total, returned, tests[]}`：GetValidTestNames（临时放宽
  RequestedTestFilter 到 ApplicationContextMask|FilterMask）。

## 历史施工顺序

```
E1（插件 PIE/Automation 命令 + ue_editor 注册 + 证据/恢复）   ← 真机
E2（子代理，离线可先行，单测 + 沙盒 e2e）                      ∥ 与 E1 并行
E3 = C3 + 完整基准（依赖 E1 用例 + C2 标准答案，编辑器在线跑分）← 真机收尾
```

## 与其它真机待办的合并建议（✅ 已于 2026-06-13 一次插件编译完成）

下面三项已在一次插件 C++ 改动 + 重编译中全部落地并真机验证（commit agent_test 4d280a5）：
- **C2 收尾** ✅：find_blueprint_references（→bp_find_usages）。
- **D1.1 服务端** ✅：token 生成/写盘 + 握手校验 protocol/token。
- **E1** ✅：pie_smoke + output_log_tail + run_functional_test/functest_list。

持续增强项：
- **pie_smoke 增强**：可选 map 参数（先 OpenLevel 再 PIE）。
- **更多故障注入用例**：会报错的关卡→pie_smoke 读 Error→修复→复跑零 Error、BuildCookRun smoke 等。
