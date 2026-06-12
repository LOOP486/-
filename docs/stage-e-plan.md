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

## E1 运行期验证闭环（PIE / Functional Test）

- **目标**：agent 改完蓝图/关卡后，能启动 PIE 或运行 Functional Test，读 Output Log 与测试结果作为运行期证据。
- **任务**：
  1. 插件（agent_test，C++）新增只读/受控命令（全部 GameThread 化 + 超时熔断）：
     - `pie_smoke(map, seconds)`：启动 PIE 跑 N 秒，捕获 Output Log 的 Error/Warning 计数与致命错误，结束 PIE，返回结构化结果。
     - `run_functional_test(test_name|map)`：触发 UE Automation/Functional Test，返回 pass/fail + 失败用例 + 日志摘要。
     - `output_log_tail(lines, severity)`：读 Output Log 尾部（按级别过滤），供编译/PIE 后查错。
  2. `ue_editor` 注册三工具：pie_smoke/run_functional_test 标 write_project（会进 PIE/改状态），output_log_tail 标 read。
  3. A3 证据信封：三者产出 facts（kind=pie/functional_test/output_log，ok 由 error_count==0 / 测试全过驱动），接 verifier 确定性通道。
  4. B3 错误分类：PIE 崩溃/超时 → 新类别 `pie_crash`（差异化恢复：读 Crash 日志而非空转重试，呼应踩坑史第 8 条）。
- **验收**：对一个会触发蓝图运行期错误的关卡，agent 跑 pie_smoke → 读到 Error → 修复 → 复跑零 Error；报告含 PIE 证据。
- **依赖**：编辑器在线 + 插件 C++（同 A1/C2，需重编译）。

## E2 子代理体系（上下文隔离 + 按角色配模型）

- **目标**：把"探索/审查/规划"等子任务交给隔离上下文的子代理，各自可配专长模型，主循环只收摘要。
- **任务**：
  1. `agent/subagent.py`：SubAgent = 独立 history + 独立 system + 受限工具面（复用 ScopedRegistry）+ 角色级模型（复用 LiteLLMClient role 路由）；跑完只回结构化摘要给主 runner（呼应 B4 上下文工程：主上下文不被子任务细节淹没）。
  2. 主 runner 暴露 `spawn_subagent(role, task, allowed_tools)` 作为一种步内能力（或 PlanStep 类型）；子代理产物登记 Artifact。
  3. 角色解耦收益：vision/judge 可换专长模型——vision 已是 Kimi，judge 可独立配；planner/coder 仍 DeepSeek。配置在 config/models.yaml roles 扩展。
- **验收**：单测：子代理上下文与主上下文隔离、工具面受限、模型按角色路由、只回摘要；e2e：用 explorer 子代理读多个蓝图后回主代理汇总（token 较单上下文显著下降）。
- **依赖**：无硬真机依赖（可先用沙盒 + 替身单测）；与 E1/E3 并行。

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

## 与其它真机待办的合并建议

下次 UE 在线会话建议一次性推进（都需编辑器 + 插件重编译）：
- **C2 收尾**：新增 AssetRegistry 引用查找命令 find_blueprint_references（→bp_find_usages，Python 侧已就绪）。pin 连接端点与函数图选择已确认插件本就支持（graph_name 参数修正后无需插件改动，见 phase1-bridge-plan.md 二次修订）。
- **D1.1 服务端**：插件启动生成随机 token 写 `Saved/ue5agent_bridge_token.txt`、握手校验 token 与 protocol 版本（客户端侧已就绪，见 bridge.py PROTOCOL_VERSION / UE_MCP_TOKEN[_FILE]）。
- **E1**：PIE/Functional/Output Log 三命令。
三者同属一次插件 C++ 改动 + 重编译，建议合并以省一次编译/重启循环。
