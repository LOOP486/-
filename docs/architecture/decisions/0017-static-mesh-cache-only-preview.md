# ADR-0017：本地预览只用真实 StaticMesh cache 表达资产外形

- 状态：已接受
- 日期：2026-06-29
- 取代：ADR-0016

## 背景

ADR-0016 为缺少 preview cache 的 ArchKit 资产增加了内置 silhouette fallback。该方案能让截图比纯 AABB
更像白盒道具，但它仍然是基于名称、类别和描述推断出的代理体，不是 UE 中的真实 StaticMesh 个体。
这会让用户误以为本地预览已经使用真实资产外形。

用户明确要求本地预览不要使用简化 silhouette proxy，而是直接使用真实网格体。当前仓库的 Python
renderer 可以稳定消费归一化后的顶点/面；真正缺口在 UE bridge 的 `scan_assets` 还没有导出
StaticMesh 顶点/面，因此 cache 缺失时不应猜测外形。

## 决策

我们决定取消 ArchKit manifest silhouette fallback。`whitebox_render_preview` 只在
`asset_preview_cache.json` 中存在 `static_mesh.vertices/faces` 时按真实网格体绘制资产外形，并把 facts
标记为：

- `geometry_fidelity="mesh"`
- `mesh_fidelity="static_mesh"`
- `asset_shape_exact=true`
- `static_mesh_count > 0`

没有 `static_mesh` cache 的资产回退 AABB，并通过 `static_mesh_missing_count` 暴露缺失数量。旧
`top_silhouette` / `simplified_mesh` 字段仍可被 cache loader 兼容解析，但不再作为“真实资产外形”
的本地渲染证据。

## 备选与取舍

- 保留 ADR-0016 的内置 silhouette fallback：视觉上更丰富，但会继续把猜测代理误认为真实资产。
- 默认改用 `viewport_screenshot`：能看到真实 UE 画面，但会重新依赖编辑器在线、活动视口、相机取景和旧场景污染。
- 在 manifest 中手工维护 mesh：会让资产清单过重，并把扫描数据与人工维护字段混在一起。

## 后果

本地预览的语义更严格：没有真实 mesh cache 时宁可显示 AABB，也不假装有真实外形。要获得真实资产个体，
必须先让 UE bridge 的 `scan_assets` 导出 `preview.static_mesh`，再运行 `wb_asset_scan(apply=true)`
写出 `asset_preview_cache.json`。

代价是当前仓库默认没有 `asset_preview_cache.json`，所以在 UE bridge 补齐并重扫之前，本地预览仍会回退
AABB。简要接触阴影仍保留，因为它只表达落地关系，不冒充资产轮廓。
