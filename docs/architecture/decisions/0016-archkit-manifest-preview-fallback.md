# ADR-0016：为 ArchKit 清单资产生成本地白盒预览 fallback

- 状态：已接受
- 日期：2026-06-29

## 背景

ADR-0011 规定 `whitebox_render_preview` 优先消费 `asset_preview_cache.json` 中的
`simplified_mesh` 或 `top_silhouette`，cache 缺失或单件未命中时回退 AABB。实际使用中，
主清单 `config/whitebox/kit.yaml` 已经包含 `/Game/LevelPrototyping/Meshes/ArchKit`
真实资产路径、校准尺寸、pivot 与本地 bounds，但仓库默认没有提交 preview cache。结果是
本地视觉证据仍呈现纯盒图，不能体现木箱、车辆、管道、楼梯、门窗框等真实资产的基础轮廓。

我们仍不希望默认视觉链路重新依赖 UE 视口截图。默认链路需要稳定、可重复、可单测；同时也必须
避免把简化白盒预览误称为最终 UE 材质或真实渲染。

## 决策

我们决定在 `whitebox_render_preview` 中增加 ArchKit manifest fallback：当 placement 的资产
来自 `/Game/LevelPrototyping/Meshes/ArchKit`、manifest 标记 `calibrated=true`，且 preview
cache 没有该资产时，renderer 按资产 key、category、tags、desc 与 front direction 生成一份
确定性的 silhouette proxy。

优先级保持为：`simplified_mesh` > `top_silhouette` > ArchKit manifest fallback >
AABB fallback。`render_preview` facts 仍保持 `asset_shape_exact=false`，并通过
`geometry_fidelity`、`mesh_fidelity`、`silhouette_proxy_count`、`mesh_proxy_count` 说明证据等级。

iso 视图同时增加简要接触阴影，用于表达资产与地面的空间关系；它不参与几何校验，也不替代
`wb_validate` 的 actor transform、visual AABB、碰撞与路线检查。

## 备选与取舍

- 要求仓库提交完整 `asset_preview_cache.json`：外形更接近扫描数据，但 cache 生命周期与 UE 插件导出能力
  绑定；缺失时仍会退回纯盒图。
- 默认改回 `viewport_screenshot`：真实度最高，但重新引入活动视口、相机、旧场景污染和 UE 在线状态等默认
  阻塞面。
- 在 manifest 中手工维护完整轮廓：精度更可控，但会让人工资产清单变重，且与 ADR-0011 的旁路 cache
  分工冲突。

## 后果

默认本地视觉证据在没有 preview cache 时也能体现真实 ArchKit 资产的基础轮廓和落地关系，减少纯 AABB
图对视觉审查和人工判断的误导。

代价是该 fallback 仍是白盒 proxy，不等同于 UE StaticMesh 的完整三角网格、LOD、材质、碰撞或光照。
需要最终画面验收时，仍应显式要求 `viewport_screenshot` 或其他 UE 侧渲染证据。
