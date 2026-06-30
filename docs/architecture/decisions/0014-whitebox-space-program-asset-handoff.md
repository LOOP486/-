# ADR-0014：白盒结构后产出 space_program 作为资产摆放语义交接

- 状态：已接受
- 日期：2026-06-26

## 背景

SPC/DST 与 B9 dressing 链路已经能把 layout DSL、dressing intent、compiler、validator、本地预览和视觉审查串起来。但资产摆放需要的不只是几何坐标，还需要知道已搭建空间中哪些区域是入口、主空间、支线、尽端、侧袋、办公区、收纳墙、通行动线和可摆放区。

如果 Agent 从题面直接跳到 `wb_dressing_dry_run` intent，空间用途、功能分区、动线和工具接手边界只隐含在 prompt 或最终坐标里，难以作为后续陈设 solver 的稳定依据。

## 决策

新增 `space_program` 作为白盒结构后的语义交接事实。它不是几何 DSL，也不是最终摆放坐标；它是 Agent 在白盒结构已搭建或已明确后、进入 dressing/资产摆放前输出的结构化 brief，字段为：

- `concept`：一句话空间目标。
- `zones`：入口、主空间、支线、尽端、侧袋、办公区、收纳区、通行动线等功能区。
- `flow`：主路径、备选路径、回游、交火、避让关系。
- `constraints`：禁止项和硬约束，例如内部共享墙不开窗、门洞成对、不要直接写 props 坐标。
- `handoff`：明确哪些由工具/solver 负责，例如 rect、门洞对齐、props 坐标、yaw、path_length、密度、sector 覆盖。

实现上不新增 `PlanStep` 字段，也不新增 kernel 阶段。planner 只在用户或新版 eval 明确要求 `space_program` / 空间设计说明 / 资产摆放基础时插入一个只读步骤：

- 纯结构任务：先 `wb_build` / `wb_validate`，再输出 `space_program`，再做预览/视觉/报告。
- 陈设任务：先搭建或明确基础白盒结构，再输出 `space_program`，随后 `wb_dressing_dry_run` 基于该语义 brief 生成 dressing layout。

runner 只解析该步骤最终文本中的 JSON，并把合法内容写入 `space_program` facts。缺少必填字段时写入 `ok=false`，复用既有 success-check/retry 机制要求补充。

## 备选与取舍

- 新增 `whitebox_space_program_check` 工具：schema 更硬，但第一版会增加工具面和权限配置；先用 runner 解析文本事实，若稳定性不足再工具化。
- 把 `space_program` 放进 DSL：会把可审查 brief 和几何真值耦合，违背 ADR-0004 的“模型不直接产出最终放置”边界。
- 在白盒结构前强制产出：能早暴露设计意图，但与本需求不符；资产摆放需要的是已搭建/已定义空间的语义解释，而不是替代 layout 设计。

## 后果

新增独立 UE eval `evals/tasks/ue_space_program.yaml`，不覆盖冻结的 SPC/DST 与 B9 baseline。评测检查 `space_program` 必填字段非空，并继续要求后续 build/validate/preview/vision facts 通过。dressing 任务还用参数级检查确认 dry-run intent 未携带最终 props 坐标字段。
