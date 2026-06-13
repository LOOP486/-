# Stage E 行为闭环与编排——细化任务清单（Phase 3）

> 依据：[roadmap.md](roadmap.md) Phase 3、[development-plan.md](development-plan.md) Stage E。
> 制定：2026-06-12（Stage A/B/C1/D 离线项收口后）。同 P1 模式：开工前先出此细案。
> 前置：Stage A–D 已收口；剩余真机项（C2 收尾 / C3 / D1.1 服务端）与本阶段大量任务都需 UE 在线。

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

## E1 运行期验证闭环（PIE / Output Log）🔶（2026-06-13：pie_smoke + output_log_tail 完成；Functional Test 留后续）

- **目标**：agent 改完蓝图/关卡后，能启动 PIE、读 Output Log 作为运行期证据。
- **任务**：
  1. ✅ 插件（agent_test，C++）新增命令：
     - `pie_start` / `pie_stop`：启动/结束 PIE，pie_stop 返回本窗口新增 Error/Warning 计数与错误行。
       拆成两命令是因为 ExecuteCommand 同步占用 GameThread——单命令里跑 N 秒会让 PIE 无法 tick；
       由 Python 侧 `pie_smoke` 工具编排 start→等待→stop。当前播放当前关卡（map 参数留后续）。
     - `output_log_tail(lines, severity)`：读 Output Log 尾部（按级别过滤）。
     - 新增 `MCPLogCapture`（GLog 环形捕获，线程安全 + 单调序号窗口精确 + 滤控制消息/Verbose）。
     - ⬜ `run_functional_test`：UE Automation/Functional Test——留后续（同样需 PIE 异步会话，
       benefit from pie 会话管线先跑稳；最重最不确定，单独做）。
  2. ✅ `ue_editor` 注册：pie_smoke 标 write_project（进 PIE/改状态），output_log_tail 标 read。
  3. ✅ A3 证据信封：pie 落 `pie` 事实（ok=error_count==0）、output_log_tail 落 `output_log` 事实。
  4. ⬜ B3 `pie_crash` 类别：未单列——PIE 期间编辑器若崩，桥失联自然归 bridge_down（已有恢复），
     暂不需专类；真遇到崩溃高发再立。
- **验收**：✅ 真机：pie_start→等待→pie_stop 进出 PIE、error_count 窗口精确归零；output_log_tail
  按 severity 过滤、无控制消息噪声。"会报错的关卡跑出 Error→修复→复跑零 Error"的完整 agent
  e2e 留待与 E3 用例一起做。
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
  空任务均转 [error] 不上抛）。**真机 token 下降量化对照留待接 UE 在线 eval（E3）时测。**
- **依赖**：无硬真机依赖（沙盒 + 替身单测已覆盖）；与 E1/E3 并行。
- **范围取舍**：子代理工具面硬限只读（写操作必须留主循环——checkpoint/回滚/验收机器都在那里）；
  嵌套深度 1（恒排除 spawn_subagent 自身防递归失控）；子代理工具的 facts 不进主步骤证据通道
  （验证类工具应在主步骤跑）；预算 8 轮/180s 小于主步骤（子任务跑不完该拆小，不吞主任务墙钟）。

## E3 完整评测基准与 UE 在线 eval（含 C3）

- **目标**：统一 C3（UE 在线 eval 档）+ 完整基准跑分，量化"一次通过率/迭代次数/人工干预次数/token/成本"。
- **任务**：
  1. eval 框架扩展：`evals/runner.py` 支持"MCP 挂载 + 编辑器在线"的执行路径（现仅 LLM 沙盒任务）；新增 `ue5agent eval --suite ue`。
  2. UE suite 用例（evals/tasks/ue.yaml）：read_blueprint_and_explain（C2 标准答案）、wb_build_and_validate、故障注入类（编辑器断连 / UBT 多错误 / 白盒部分失败 / PIE 报错）。
  3. 指标扩展：ResultReport 增加 iteration_count / human_intervention（本工具为 0 干预跑通率）；基线归档 evals/baselines/ue/。
  4. 沙盒两档（basic/hard）继续作为离线 CI 门禁；UE 档作为真机回归（编辑器在线时跑）。
- **验收**：编辑器开启时 `ue5agent eval --suite ue` 跑分并出基线；沙盒档仍离线满分。
- **依赖**：E1（PIE 用例）、C2 收尾（蓝图标准答案）、编辑器在线。

## 建议施工顺序

```
E1（插件 PIE/Automation 命令 + ue_editor 注册 + 证据/恢复）   ← 真机
E2（子代理，离线可先行，单测 + 沙盒 e2e）                      ∥ 与 E1 并行
E3 = C3 + 完整基准（依赖 E1 用例 + C2 标准答案，编辑器在线跑分）← 真机收尾
```

## 与其它真机待办的合并建议（✅ 已于 2026-06-13 一次插件编译完成）

下面三项已在一次插件 C++ 改动 + 重编译中全部落地并真机验证（commit agent_test 4d280a5）：
- **C2 收尾** ✅：find_blueprint_references（→bp_find_usages）。
- **D1.1 服务端** ✅：token 生成/写盘 + 握手校验 protocol/token。
- **E1** ✅（pie_smoke + output_log_tail；Functional Test 留后续）。

剩余真机待办（下次 UE 在线会话）：
- **run_functional_test**：UE Automation/Functional Test（同需 PIE 异步会话，最重，单独做）。
- **E3 = C3 + 完整基准**：eval 框架支持 MCP+编辑器在线执行路径，`eval --suite ue` 出基线。
- **pie_smoke 增强**：可选 map 参数（先 OpenLevel 再 PIE）；E1 完整 e2e（会报错的关卡→修复→复跑零 Error）并入 E3 用例。
