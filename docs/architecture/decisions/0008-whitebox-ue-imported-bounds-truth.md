# ADR-0008：白盒资产以 UE 导入后 bounds 作为几何真值

- 状态：已接受
- 日期：2026-06-13

## 背景

ArchKit 白盒接入后，曾用 `scripts/fbx_probe.py` 从 FBX 原始坐标反推 `size/pivot`。该数据能快速生成 manifest 草稿，但 UE 导入会受坐标轴、构建缩放、pivot 烘焙等设置影响，导致 manifest 与编辑器里的真实 StaticMesh bounds 不一致。结果是编译器和 validator 围绕错误 manifest 自洽，`wb_validate` 可以 PASS，而视口中仍出现墙缝、地板偏移、楼梯落点不对等问题。

## 决策

白盒资产几何真值以 UE 导入后的 StaticMesh bounds 为准。manifest 支持记录 `local_bounds_min/local_bounds_max/calibrated`，编译产物 `Placement` 同时携带目标 AABB 与真实 visual AABB；`wb_validate` 对校准资产检查 visual AABB 是否贴合目标盒。新增 `wb_asset_audit` 只读工具，比较 manifest 与 UE `get_mesh_bounds` 的尺寸差异，并输出 `wb_asset_audit` facts。

`fbx_probe.py` 继续作为离线草稿扫描器使用，但不作为最终验收依据。结构墙仍遵循 ADR-0007：墙可按 snap box 拉伸，非结构件保持原生尺寸。

validator 还需要把视口里最常见的结构失败转成可追踪指标：缺地板计入 `floor_hole_count`，缺墙段计入 `wall_gap_count`。这两个指标不替代逐 actor diff，而是让 runner、评测和人工复盘能直接看出“白盒是否连续成面/成围合”。

截图证据也必须有最低可审查性：`viewport_screenshot` 成功后，本地检查文件存在、非背景主体占比与主体
位置；主体过小、贴边或空图时，`screenshot` fact 必须 `ok=false`。LLM 视觉审查负责语义判断，本地快检只负责
防止“截到天空/边角”这类无效证据进入硬门禁。

## 后果

- 资产导入或 BuildScale 调整后，应先跑 `wb_asset_audit`，再让 agent 进行白盒搭建。
- validator 不再只信 actor transform；校准资产即使 transform 与 placement 完全一致，也会因 visual AABB 偏移而失败。
- 默认关键 ArchKit 地板、`Wall1_4` 与 `Stair_2` 必须优先写入校准 bounds，作为后续批量校准其他资产的基线。
- 缺地板、缺墙不只报“缺失构件”，还要进入 `floor_hole_count` / `wall_gap_count`，避免大面积结构错误被单件列表淹没。
- 白盒步骤可声明 `required_evidence`，要求 `screenshot`、`vision_review` 等硬证据；缺证据时不能仅凭 `wb_validate` PASS 收口。
- `screenshot` fact 的 `ok` 不再只代表桥回包成功；截图文件缺失或取景明显不可审查时同样失败，runner 会要求重取证据。
- 楼梯不再只是挖洞加 stair mesh，编译器会生成可计数的 `stairwell` guard pieces，供 validator metrics 和视觉审查识别。
