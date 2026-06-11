# ADR-0006：Agent Kernel 采用「阶段状态机包宏步骤、步内自由微循环」结构

- 状态：已接受
- 日期：2026-06-11

## 背景

最小 agent loop（单 while 循环）验证了链路但缺任务状态、结构化验收与中断恢复；纯聊天记录无法回答"它死在哪一步"。用户决策采用完整 Agent Kernel 重构（kernel-refactor-plan.md），同时上轮讨论识别出硬性阶段机的形式主义风险。

## 决策

TaskRunner 实现阶段状态机：plan → [execute → verify → recover]* → report，配三条防形式主义硬约束：

1. **fast path**：intake 判 trivial 的任务单步直通，不付流程税；
2. **步内微循环**：execute 内部是自由 tool-calling 循环（AgentLoop 降级为步内引擎保留），状态机只管宏步骤边界与证据；
3. **judge 三态验收**（pass/fail/insufficient）只看工具客观证据，不信执行方自述；judge 输出不可解析时保守按 insufficient 处理。

恢复策略：验收理由回灌上下文重试，超出步级上限放弃并跳过余步，全程产出报告与 trace 阶段事件。

## 备选与取舍

- 九阶段全显式状态机（GPT 原案）：阶段间回跳边复杂度高、简单任务被流程拖累，收敛为上述三约束版本；
- 维持纯 loop：无任务状态与结构化验收，自嗨与不可恢复问题无解，否。

## 后果

- 计划修订（replan）暂未实现，recover 仅 retry/abort 两动作——等真实使用暴露需求再加；
- chat 的跨输入连续记忆与"每输入一个 TaskSession"存在张力，K5 切换时按任务粒度取舍；
- 真模型门禁通过后（eval 两档满分持平基线 + e2e 走 TaskRunner），本 ADR 生效。
