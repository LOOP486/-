# ADR-0015：白盒陈设转向 motif/constraint pipeline

- 状态：已接受
- 日期：2026-06-28

## 背景

B9 的 dressing v0 已经建立了 `intent -> solver -> props` 边界，`wb_dressing_dry_run` 能生成可交给 `wb_build -> wb_validate -> whitebox_render_preview` 的 `rooms[].props[]`，validator 也能检查 AABB、门洞、路线、窗户遮挡、密度和覆盖等指标。这个边界仍然有效，应继续保留。

新的问题不在于缺几个单点摆放规则，而在于缺少可审查的陈设中间表示。当前 pipeline 最终能输出 props，也能通过 `prop_count`、`sector_coverage`、`footprint_ratio` 等指标，但中间没有明确的 motif plan、关系约束和求解解释，因此容易出现“数量和密度达标，但看起来仍像随机摆放”的结果。

继续在 dressing v0 上堆 `near_wall`、`corner`、`behind_cover` 等局部规则，或者靠更多随机种子改善观感，会让 solver 更难解释，也无法稳定表达办公室、仓库、战斗房这三类第一阶段目标空间的结构意图。

## 决策

我们决定把 B9 后续方向冻结为 `空间分区 -> motif 选择 -> 关系约束 -> 候选区求解 -> 可验证指标` 的 motif/constraint asset placement pipeline，而不是继续扩写 dressing v0 的贪心补丁。

保留现有能力与边界：`LayoutSpec`、`PropSpec`、`compile_layout`、`wb_dressing_dry_run`、`wb_build -> wb_validate -> whitebox_render_preview` 链路，以及现有 AABB、门洞、路线、窗户遮挡和 dressing metrics。Agent 仍不得直接输出最终 props 坐标、yaw 或 offset；最终 `PropSpec` 仍由纯逻辑 solver 生成。

新增能力按派生层推进，不急着改 `config/whitebox/kit.yaml` 主 schema。第一步在代码里建立 `placement_profile`，从 manifest 现有字段派生 `design_roles`、`gameplay_roles`、`affordances`、`clearance` 与 `group_templates`，让资产选择不再只靠 `category/tags/desc` 猜。

随后新增 motif plan 与约束模型：办公室优先支持 `workstation_cluster`、`meeting_corner`、`storage_wall`，仓库优先支持 `storage_wall`、`rack_aisle`、`loot_alcove`，战斗房优先支持 `cover_strip` 与留白/侧翼路线。约束第一版覆盖 `InRoom`、`InZone`、`AgainstWall`、`Near`、`Face`、`Align`、`AvoidDoor`、`AvoidWindow`、`AvoidPath`、`KeepNavChannel`、`CoverSpacing`、`ReserveNegativeSpace`、`LocalDensityCap`。

placement solver 不再采用“shuffle 后第一个 compile 通过”的策略，而是先从 room 生成 `wall_strip`、`corner`、`center`、`path_spine`、`door_reserve`、`window_reserve` 等候选区 mask，再由 motif 生成 candidate poses，按 hard constraints 过滤、soft constraints 打分，并用 beam search 保留 top K；每放一个 motif 后更新 occupancy，最终输出 `PropSpec`。

`wb_dressing_dry_run` 的外部 API 保持兼容，但 facts 需要新增 `motif_plan`、`constraint_summary`、`relation_satisfaction`、`motif_counts`、`solver_score`。旧 metrics 与现有 eval 暂时保留，用于兼容已冻结的回归；新增或改造的 motif/constraint eval 主要检查 motif 数量、关系满足率、cover spacing、window blockage 和 motif match rate。视觉审查只作为辅助证据，不作为唯一质量裁判。

暂不做 support/on-top、完整 relation graph learning、VLM repair、复杂 MILP，也不提前扩大到通用室内生成。第一阶段只服务办公室、仓库、战斗房三类。

## 备选与取舍

- 继续补 dressing v0 的局部规则：改动最小，但会把空间意图埋在越来越多的 if 和随机候选顺序里，无法解释 motif 是否满足，也难以稳定回归。
- 直接修改 `kit.yaml` 主 schema，给每个资产手写全部 placement 语义：查询简单，但会把人工语义、扫描产物和派生策略耦合；资产重扫时也更容易污染人工维护字段。
- 只提高 `prop_count`、`sector_coverage`、`footprint_ratio` 等指标阈值：能压住空房间问题，但不能保证桌椅关系、货架通道、掩体链和中心留白这些关卡设计结构成立。
- 直接引入 MILP、VLM repair 或完整关系图学习：路线更通用，但本阶段验证面过大，会过早脱离现有 compiler/validator 的可单测优势。

## 后果

roadmap 中 B9 维持为已完成的 dressing v0 边界和基础能力，后续工作以新的 B11 motif/constraint dressing pipeline 承接，不继续把新规则塞进 B9 patch 列表。

实现顺序应为：`placement_profiles.py`、`motifs.py`、`placement_constraints.py`、`placement_solver.py`、接入 `wb_dressing_dry_run`、新增或改造 motif/constraint dressing eval，最后再补 UE 侧 `line_trace_batch` 与 `capsule_sweep_path` 等验证工具。每层纯逻辑先单测，server.py 只做接线。

完成前六步后，办公室应能解释出工位、会议角、收纳墙；仓库应能解释出货架通道、打包/收纳区和角落堆放；战斗房应能解释出掩体链、侧翼路线和中心留白。每个 dry-run 结果都应能说明用了哪些 motif、满足了哪些关系、违反了哪些软约束。

代价是短期内 dressing pipeline 会多一层中间表示和更多测试面。为了控制范围，旧 dry-run API、旧 layout JSON 和旧 metrics 必须保持兼容，直到 motif/constraint eval 稳定后再逐步收紧验收。
