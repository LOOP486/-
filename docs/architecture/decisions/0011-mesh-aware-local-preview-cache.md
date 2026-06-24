# ADR-0011：本地白盒预览使用资产 preview cache

- 状态：已接受
- 日期：2026-06-23

## 背景

ADR-0010 把白盒视觉门禁默认切到 compiler 级本地预览，解决了 UE 活动视口、取景和环境在线状态阻塞 agent loop 的问题。但初版 renderer 只画 placement AABB：结构墙体足够直观，props/cover 若只画盒子，视觉模型无法判断外形、朝向和占用空间是否合理。

直接在 agent loop 中启动 UE 视口渲染真实 mesh 成本高、状态不稳定；而资产扫描阶段已经需要接触 UE 导入后的 StaticMesh 真值，可以顺手导出轻量 preview 数据，供本地 renderer 快速复现外形。

## 决策

我们决定为白盒资产扫描增加旁路 preview cache：`wb_asset_scan(apply=true)` 在写出 `kit.yaml` 的同时，若桥端 `scan_assets` 返回 `preview.top_silhouette`、`preview.simplified_mesh` 或 `thumbnail_path`，则写出同目录 `asset_preview_cache.json`。

`whitebox_render_preview` 会自动加载该 cache：

- 有 `simplified_mesh` 时，iso 视图按简化 mesh face 投影绘制，top 视图按 mesh footprint hull 绘制；
- 没有 mesh 但有 `top_silhouette` 时，按 silhouette footprint 绘制 top view，并在 iso 视图挤出成 proxy 体；
- cache 缺失或单件未命中时，继续回退到 AABB。

`render_preview` facts 必须暴露 fidelity：`geometry_fidelity`、`mesh_fidelity`、`preview_cache_assets`、`silhouette_proxy_count`、`mesh_proxy_count` 与 `asset_shape_exact=false`。这让 agent 和视觉模型知道当前证据是 AABB、silhouette proxy 还是 mesh proxy，避免把本地预览误当 UE 最终画面。

白盒视觉任务的默认证据链路使用 `whitebox_render_preview + vision_review`。只有用户明确要求 UE 视口截图、真机截图或编辑器截图时，planner 才使用 `viewport_screenshot`。

## 备选与取舍

- 继续要求所有白盒视觉检查走 `viewport_screenshot`：真实度最高，但重新引入 UE 视口在线、相机和旧场景污染等默认阻塞面。
- 在 manifest v2 里直接内嵌 mesh/silhouette：减少文件数量，但会让人工维护的 kit 清单变重，也把视觉 cache 生命周期和资产几何校准耦合在一起。
- 只保存 thumbnail：适合人看图鉴，但不能参与本地几何投影、遮挡和多角度检查。

## 后果

本地 renderer 可以在不依赖 UE 视口的情况下给视觉模型提供更接近真实资产外形的证据，尤其适合白盒空间自查和显式 props/cover 预览。

代价是 preview cache 仍是简化 proxy，不等同于 UE 材质、LOD、碰撞或最终渲染。桥端若暂时只返回 bounds 而不返回 preview 字段，本地预览会明确回退 AABB；需要最终真实画面验收时仍应显式要求 `viewport_screenshot`。
