# ADR-0012：白盒陈设采用 intent → solver 边界

- 状态：已接受
- 日期：2026-06-24

## 背景

B8 之后，白盒链路已经支持显式 `rooms[].props[]`、原生尺寸落地、门洞/路线/楼梯避让、`wb_validate` 与本地预览。问题不在于缺少“能放道具”的接口，而在于缺少可校验的自动陈设中间层：如果继续让 Agent 直接输出 props 坐标，模型会把资产选择、空间意图和最终坐标混在一起，容易变成随机撒家具，也难以解释失败原因。

现有仓库的优势是确定性 compiler 和 validator，所以自动资产布置也应沿这条线走：高层语义由 Agent 表达，最终坐标由纯逻辑 solver 生成，再交给 compiler/validator 检查。

## 决策

我们决定新增白盒 dressing/asset layout 边界：Agent 只能输出 `scene_type`、`prop_density`、`cover_count` 范围、`preferred_roles`、`banned_roles`、`allowed_assets`、`zone_preferences`、`threat_direction` 等意图字段；不得输出最终 `x/y/at/yaw/rotation/placements`。

资产 `layout_role` 不写入 manifest，也不替代 manifest 的结构 `roles` 映射；它由 `category`、`tags`、`desc`、`footprint`、`size`、`needs_review` 等字段派生，作为 solver 选材的白盒语义层。结构类资产默认推断为 `ignored_structural`，`needs_review` 资产默认不进入自动池，只能通过 `allowed_assets` 显式指定。

solver 是 compiler 前的纯逻辑层，输入 `LayoutSpec + Manifest + DressingIntent`，输出可并入现有 DSL 的 `PropSpec` 候选；第一版支持 `warehouse_combat/indoor_combat` 的 cover solver，生成 3-6 个紧凑掩体，避开门洞、门到门路线、gameplay 主路线和楼梯占用，并按 threat direction 设置朝向。cover 稳定后，prop dressing 只从 edge/corner/near_wall/behind_cover/storage_cluster 等受控 zone 中补充 storage/filler/compact crate/fence 等语义资产，不做全空间随机散点；`generic_room/office_room/common_room` 走通用房间陈设路径，先做 room-program 计划，再落位墙边柜、中心桌椅组和必要的柜子隔断。室内 seating 默认排除长椅/bench，中心桌椅按多人使用在桌子两侧放单人椅；`rooms[].props[]` 仍保留为人工/既有流程的显式摆放入口，但 agent 自动陈设不应直接写它的坐标。

通用房间的 `prop_density` 不直接等价于随机散点数量；大房间 high density 会先选择一面主收纳墙形成连续 storage run，再用第二面墙、中部桌椅组和柜体隔断提高空间填充率，仍由 solver 生成坐标并接受 compiler/validator 检查。

`prop_density` 的验收同时参考物件数量、真实 footprint 面积占比和连续可活动区比例。validator 负责输出面积占比与最大连续空地 metrics；solver 可用这些 metrics 校准“太稀/太满”的边界，但仍必须保留门洞、主路线和基础流线。

大房间 high density 还需要检查空间分布，而不是只检查总面积。validator 使用 4x4 sector coverage 发现整侧空白；solver 可以在已有主墙柜和中心桌椅后补 secondary activity island 或柜体隔断，但仍不得改由 Agent 直接输出最终坐标。

为处理真实 StaticMesh 的 pivot/snap box 与视觉包围盒差异，solver 可在输出的 `PropSpec` 上写入厘米级 `offset`，用于把 storage/table 等资产的 visual AABB 贴齐室内墙面。`offset` 不属于 `DressingIntent`，Agent 仍不得输出 `offset`、最终坐标或 yaw；它只作为 dry-run/solver → compiler 的白盒校正字段，并继续接受 `wb_validate` 的穿墙、门洞和路线检查。

`wb_validate` 增加 dressing metrics：`cover_count`、`cover_spacing`、`cover_route_clearance`、`prop_density`、`role_coverage`、`ignored_asset_count`、`structural_random_placed_count`。第一版指标以“可解释、可回归”为目标，不把审美质量作为硬约束。

## 备选与取舍

- 继续让 Agent 直接写 `rooms[].props[]` 坐标：改动最小，但失败原因混在 prompt、资产语义和几何冲突里，无法稳定回归。
- 在 manifest 中手写每个资产的 `layout_role`：查询简单，但会把派生语义和几何真值耦合；资产扫描重写清单时也更容易丢人工语义。
- 直接输出 `Placement` 并绕过 compiler：能精确控制最终 actor，但会绕开现有 props 校验、命名和预览链路，不适合作为第一版接口。

## 后果

B9 的实现顺序变为接口先行：先冻结 intent/schema 与 layout role 推断，再做 dry-run/solver/validator metrics，最后再接 Agent prompt 与 UE eval。这样可以在不依赖 UE 在线状态的情况下单测 cover solver，并继续复用 `wb_build -> wb_validate -> whitebox_render_preview -> vision_review` 的验收链路。

代价是自动陈设不会一次覆盖全部资产。第一版只使用小范围、低风险的 cover/prop pool；车辆、大型边界围栏和复杂组合件仍要等独立 eval 与 Agent prompt 接入后再扩大范围。
