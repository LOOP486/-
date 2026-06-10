# 开发计划

> 本页是施工蓝图：里程碑顺序、任务分解、验收标准。高层阶段定义见 [roadmap.md](roadmap.md)，两者关系：roadmap 管"做什么/做没做"，本页管"按什么顺序怎么做"。
> 制定日期：2026-06-10。

## 排序原则

1. **评测与容错先行**：agent 开发是盲调重灾区，先有分数和容错层，后面每一步都可度量（来自"agent 开发本身很复杂"的讨论，见 roadmap 横切任务）。
2. **不被外部依赖阻塞**：API key、UE 工程路径、模块化资产包到位前，先完成全部纯代码件（M1–M5 均可离线开发，单测验证）。
3. **每个里程碑独立收口**：结束时 lint+测试全绿、文档同步、独立提交，可随时中断不留半成品。

## 里程碑

### M1 弱模型容错层（无外部依赖）

多模型支持是否成立取决于这一层。全部挂在 `registry.dispatch` 链上，不动 loop。

- [ ] 坏 JSON 修复：markdown 代码块包裹、尾逗号等常见损坏形态的机械修复
- [ ] 参数 schema 校验（jsonschema）：必填/类型/枚举违例转结构化错误回传模型
- [ ] 幻觉工具名：提示最近似的真实工具名
- [ ] CI workflow（GitHub Actions：ruff + format + pytest，待配远端后生效）
- 验收：每条容错路径有单测；现有测试不回归

### M2 可观测性——trace 与回放（无外部依赖）

- [ ] AssistantTurn 携带 token 用量；LoopResult 汇总 token
- [ ] loop 事件点埋设：逐轮 llm_turn / tool_call 事件，含耗时与预览
- [ ] CLI `ue5agent trace [path]`：回放查看最近会话（默认 sessions/ 最新）
- 验收：FakeModel 跑一轮后 trace 文件完整、回放命令可读；单测

### M3 API 故障面（无外部依赖）

- [ ] LiteLLMClient 重试退避（限流/网络/5xx），注入式 sleep 便于单测
- [ ] 角色级 fallback 链（models.yaml 新增 fallbacks 段，配置校验同步）
- 验收：假客户端单测重试与降级路径；example 配置更新

### M4 会话与上下文（无外部依赖）

- [ ] chat 多轮记忆：loop 支持外部持有的 history，跨输入延续
- [ ] compact_history 实装：超预算时压缩早期轮次（保 system + 近期窗口，窗口边界不得切断 assistant/tool 配对）；先做确定性摘要，模型摘要后续升级
- 验收：多轮连续性与压缩边界规则有单测

### M5 迷你评测集（harness 无外部依赖；真模型跑分需 API key）

- [ ] 评测沙盒工具组（echo/计算/临时文件笔记，不碰真实工程）
- [ ] 任务定义 schema（YAML）+ 检查器（工具是否被正确调用/最终答复断言/轮数上限）
- [ ] runner + 报告（通过率/平均轮数/token），CLI `ue5agent eval`
- [ ] 首批 10 个任务（覆盖：单工具、多步、诱导性错误工具名、枚举参数、直答不调工具）
- 验收：mock 模型下 eval 全链路跑通；报告字段齐全

### M6 真实闭环验证（阻塞：需用户提供）

- [ ] 需要：任一模型的 API key；UE 引擎根目录与一个 .uproject
- [ ] models.yaml 真配置 + eval 对真模型跑分（DeepSeek/GPT/Claude 至少各一）
- [ ] ubt_compile 对真实工程实测，按实际输出补解析样本与测试
- [ ] 端到端验收：「加一个 gameplay 功能并编译通过」= Phase 0 完成
- 验收：roadmap Phase 0 全勾

### M7 编辑器桥（Phase 1，M6 后单独细化）

fork flopperam → 裁剪 → 蓝图只读导出四件套。开工前出细化任务清单，此处不展开（见 ADR-0005）。

## 当前等待用户提供

| 事项 | 阻塞的里程碑 |
|---|---|
| 至少一个模型的 API key（推荐先 DeepSeek，便宜）| M5 跑分、M6 |
| UE 引擎根目录 + 测试用 .uproject | M6、M7 |
| GitHub/Gitee 远端仓库（CI 生效与备份）| 仅 CI |
| 模块化资产包 + 关卡 metrics 表 | Phase 2（白盒搭建）|
