# Changelog

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [未发布]

### 修复（SPC/DST 白盒评测稳定性）

- slab 墙体编译新增外角半墙厚端点补偿，避免水平/垂直墙都冲到轴线交点造成亮边、错位或视觉重叠。
- 楼梯间护墙生成会跳过不足一格的侧边窄缝，`wb_validate` 新增
  `stairwell_overlap_count`、`stairwell_out_of_bounds_count` 与 `stairwell_sliver_count`，
  并按旋转后 AABB 识别重复墙，减少楼梯井误报和小夹缝漏检。
- UE eval 的 coder 超时从直接 abort 调整为分类记录 `llm_timeout` 并同 step 重试一次；LLM 请求开始前新增
  `llm_request_start` 事件，trace 可见 role、turn、消息数、估算字符量与工具数。
- UE eval 每个任务独立挂载 MCP server，避免长任务后 stdio session 关闭导致后续任务连续
  `ClosedResourceError`。
- MCP 客户端在 stdio session 已关闭、损坏或 end-of-stream 后会自动重启对应 server session，
  避免单个断链工具把后续调用全部污染成同类错误。
- 声明式验收支持 `path_test.total` / `path_test.count` 以及 `path_test_result` 作为最新
  `path_test` 事实存在别名：
  只要最终导航事实没有显式 `ok=false`，即可计为 1，避免 `path_test` 已成功但报告误判失败。
- 声明式验收支持 `wb_validate.is_valid` 作为 `wb_validate.ok` 的别名，避免 planner 生成的
  `is_valid=true` 契约在校验事实已 PASS 时被误判。
- UE eval 报告新增 `failure_type`，并在控制台表格/JSON baseline 中区分 `llm_timeout`、
  `env_unready`、`vision_high`、`vision_medium_low`、`layout_error`、`geometry_check` 等类型，
  避免所有失败都混成“验收未通过”。
- 白盒 `wb_build` 执行提示改为少解释、优先直接调工具且不重复粘贴完整 JSON；同时新增通用
  构型守则：先按整数格推导 room.rect 邻接表，共享墙门洞必须双侧成对且 at/width 对齐，
  不确定外墙时宁可不写 windows，结构/导航任务可完全省略窗。
- 白盒布局失败重试会把常见 `wb_build` LayoutError 翻译成可执行恢复提示：外墙窗错误时删除
  非必要 windows，共享墙门洞错误时退回更简单的矩形邻接并重新成对校准，楼梯错误时优先保证
  footprint 在大房间内部且不切断门到门路线；视觉失败重试只携带目标、最新 folder/screenshot 与
  high 问题摘要，避免长 history 放大超时概率。
- runner 在派发 `wb_build` 前新增轻量布局 guardrail：删除确定落在共享墙上的 windows，为单侧
  共享墙门洞补齐对侧同轴同宽门洞，并把明显越界的常见楼梯 footprint 收进所在房间内部；这属于
  agent 侧入参预检，不改变评测题面，也不替模型重设计布局。
- `viewport_screenshot` 新增 `clean_view`、`focus_prefix` 与 `margin`；runner 会在模型未显式传参时
  用最新 `wb_build.folder_root` 自动聚焦本批白盒，UE 侧按 Actor/Outliner 前缀计算 bbox、隐藏
  grid/选中描边/轴标并俯拍，减少旧批次和邻近结构污染视觉判断。
- 视觉审查 gate 改为 high-only：`VisionReviewResult.passed` 与视觉评测任务只让
  `high_count > 0` 或解析失败阻断自动收口，medium/low 继续作为报告字段保留。
- 默认视觉审查清单明确按 blockout 阶段评价，不因缺少门框/窗框、楼梯踏步/扶手或房间文字标签扣分。

### 新增（关卡尺度 metrics）

- 新增 `config/whitebox/level_metrics.yaml` 与 `scale_profile="realistic"`：第一版按真实室内空间
  控制尺度，视觉/LLM 只负责理解空间结构，米制尺寸由 metrics 表收敛。
- `wb_validate` metrics 新增 `scale_profile`、`scale_grid_m`、`min_room_area_m2`、
  `min_room_dimension_m`、`min_door_width_m`、`wall_height_m`、`scale_warning_count` 与
  `scale_warnings`。第一版尺度问题只作为 warning，不改变几何 PASS/FAIL。

### 新增（空间黑盒评测）

- 新增 `evals/tasks/ue_space.yaml`：用真实 UE 工具评测 agent 自主设计默认 slab 空间的能力，
  固定 `SPC1/SPC2/SPC3` 与 `DST1/DST2/DST3` 前缀，按 2x3 测试区并排 origin 落地，
  明确禁止 hand-written 布局补救、gameplay/props/
  cover/spawn/routes 与 `viewport_screenshot`，只观察 `wb_build -> wb_validate ->
  navmesh_rebuild -> path_test` trace。
- SPC/DST 空间评测冻结为标准回归集：任务声明 `prompt_id=spc-dst-space-v1`、
  `prompt_locked=true`，并固定 `planner/coder/judge/explorer=deepseek/deepseek-v4-pro` 与
  `vision=moonshot/moonshot-v1-8k-vision-preview`（Kimi 轻量视觉）。UE eval 会读取任务模型 pin
  临时覆写角色路由；冲突的 `--model` 会直接报错，避免标准测试漂移。
- `AgentLoop` 会把 `wb_build` / `wb_validate` 工具参数里的完整 `layout_json` 自动保存为
  `artifacts/layouts/*.json`，并在 trace 的 `tool_call.layout_artifact` 标出路径，避免复杂空间 DSL
  只留在 500 字符截断的 arguments 预览里。
- UE eval 支持 `no_unrecovered_tool_errors` 检查：允许 agent 自行修复中途工具错误，但最终失败时仍
  把未恢复错误纳入报告；runner 对执行期模型不可用/超时会写入确定性失败并快速终止。
- 工具说明补充空间 eval 约束：允许显式 `prefix`/`origin` 做并排对比，纯空间任务不要生成玩法件；
  stair 示例与 footprint 提示改为更贴近默认 slab 测试。
- 新增 `docs/superpowers/plans/2026-06-15-whitebox-eval-optimization.md`：归档两轮 SPC/DST
  白盒测试问题清单与优化策略，覆盖墙体端点厚度补偿、楼梯间开口/护墙、LLM 超时、截图取证、
  视觉 high-only gate 与 eval 报告分类。
- 归档 SPC/DST 标准结构 baseline：
  `evals/baselines/ue/space-agent-test-20260615-205313.json`，6/6 通过，pass_rate=1.0，
  first_try_pass_rate=0.8333，平均迭代 3.0，人工干预 0。

### 新增（升级版资产扫描：UE 真值重建 manifest）

- `ue_whitebox` 新增 `wb_asset_scan(content_path, apply, out_path)`（write_project）：以 UE 导入后
  StaticMesh bounds 为真值反推 `size`/`pivot`/`footprint`/`local_bounds`（直接 `calibrated`），
  重建 manifest v2，消除“重导资产后手工回填 path、尺寸漂移”。默认 `apply=False` 仅预览 diff，
  确认后 `apply=True` 才写盘；重扫会保留用户手调的 `roles` 与人工 `desc`。
- 新建纯逻辑模块 `whitebox/scanner.py`：名称前缀 + **几何先验**混合归类——命名兜不住时按包围盒
  长宽高比把件从 `unknown` 收敛到 wall/floor/pillar/cover 等大类，并一律标 `needs_review`
  （几何分不清墙/窗/门“同形不同义”）；命名命中但几何明显矛盾的件也提示复核。
- `wb_asset_scan` 优先调桥命令 `scan_assets` 枚举整个内容目录（含新导入但清单未登记的件）；
  旧插件未实现该命令时，回退用 `get_mesh_bounds` 逐件刷新存量清单（发现不了新件，会在结果中提示）。
- UnrealMCP 插件新增只读 `scan_assets` 命令（AssetRegistry 枚举 + `ScanPathsSynchronous` 防漏 +
  逐件 bounds）；**需重新编译插件后该路径才可用**。
- `scripts/fbx_probe.py` 改为复用 `scanner.classify_by_name`，命名归类规则收敛为单一事实源。
- ③视觉识别 `scripts/asset_vision_scan.py`：对命名不规范/几何同形难辨的件（柜子/箱子/掩体等
  填充物）渲 3/4 白模缩略图 → 喂 vision 角色（多模态）→ 输出 `semantic`/`usage`/`category`，
  合并进 manifest 的 `desc`/`tags`/`category`（高置信可清 `needs_review`）。零新增 C++：渲染复用
  `spawn_actor`(临时点)+`viewport_screenshot`(定相机)+`delete_actor`，识别复用 `LiteLLMClient`。
  分工：①②几何给"对齐抓手"（精确尺寸/pivot），③视觉给"这是什么、怎么用"（结构件 vs 填充物）。
- UnrealMCP 插件 bridge 路由白名单补登 `scan_assets`（之前漏登记导致命令被判 Unknown）。
- `asset_vision_scan` 支持三种取数：默认调 VLM、`--render-only` 只渲图、`--labels` 用外部标签
  合并（VLM 慢时可两段式：先 `--render-only` 渲图，人工/离线识别后 `--labels … --apply` 合并）；
  spawn 用运行唯一名（复用同名会触发 UE `Cannot generate unique name` Fatal，见 `wb_build`）。
- `scanner.build_manifest_dict` 的 roles 合并改为"保留仍有效的手调映射 + 丢弃指向已删除资产的项 +
  缺失标准角色用 best-guess 补齐"，避免重扫后 roles 指向不存在的旧资产。
- 编译器/loader 单测改用冻结的 `tests/data/kit_archkit_sample.yaml`，与随用户重扫而变的
  `config/whitebox/kit.yaml` 解耦（`config/whitebox/kit.yaml` 现按真实导入资产由扫描生成）。

### 变更（白盒 slab-first 默认策略）

- 布局 JSON 新增顶层 `structure_mode`：缺省为 `"slab"`，显式 `"modular"` 才走旧 ArchKit
  模块化结构路径。
- 默认 slab 模式改为 Engine Cube 连续地板与连续片墙；`doors`/`windows` 只切墙洞，不再生成
  `wall_door`、`window`、`glass_wall` actor，也不生成 ArchKit 导航 `navproxy`。
- 默认 slab 模式只支持 `room.level=0`；需要旧多层 room 时必须显式设置
  `structure_mode="modular"`。
- slab 模式允许只有 level 0 room 的楼梯（如 `from_level=0,to_level=1`）：生成楼梯 mesh 与
  Engine Cube 楼梯间护墙，不生成上层 floor/wall。
- `wb_validate` metrics 新增 `structure_mode` 与只读空间指标 `wall_fragmentation_score`；视觉审查
  清单改为关注主空间、开合、遮挡、转角、比例与无意义孤立墙，并明确不因缺少门框/窗框扣分。
- 白盒落地时会按 `Placement.metadata["room"]` 写入 World Outliner 文件夹：房间构件进入
  `<prefix>/<batch>/Rooms/<room>`，`wb_build` facts 回传 `batch_id` 与 `folder_root`；
  空间 eval 会硬检查 `folder_root` 非空，避免 trace 通过但大纲里没有新测试文件夹。
- 共享墙去重会把保留墙段合并到共享边中心轴线，避免相邻段落一段在左/下侧、一段在右/上侧；
  `windows` 现在只允许开在外墙，内部共享墙开窗会报可读 `LayoutError`。
- 结构层 DSL 坐标收紧为整数格：`room.rect`、`doors/windows.at/width`、`props/stairs.at`
  遇到 `1.5` 这类半格值会直接报错，避免静默 `int()` 截断造成 SPC/DST 类空间墙体错位。
- `wb_validate` 新增近距离同向并列墙检测，能把共享墙重复/错轴导致的视觉双墙作为 violation 暴露。
- SPC/DST 空间 eval 新增 `metrics.parallel_wall_duplicate_count <= 0` 硬检查，防止
  真机评测遗漏共享墙双层/并列墙回归。
- planner 会把弱模型产出的 `path_test.success` 契约别名归一化为 `path_test.reachable`，避免
  导航事实已通过却被 runner 误判失败；用户未请求截图/视觉时，也会移除白盒步骤幻觉出的
  `screenshot` / `vision_review` 硬证据门禁，即使 planner 没有同时开放 `viewport_screenshot`。

### 新增（白盒可靠性底座）

- manifest v2 支持 UE 校准后的 `local_bounds_min`、`local_bounds_max` 与 `calibrated` 字段；
  `Placement` 记录 `asset_key`、`visual_min`、`visual_size`、校准状态与 snap box 状态。
- `ue_whitebox` 新增只读 `wb_asset_audit`，用 UE `get_mesh_bounds` 对照 manifest 尺寸并输出
  `wb_asset_audit` facts，避免 FBX 草稿数据与 UE imported mesh bounds 偏离后继续自洽搭建。
- `wb_validate` 新增校准资产 visual AABB 对齐检查与 `calibrated_asset_count`、
  `visual_mismatch_count`、`stairwell_count` metrics；transform 完全匹配但真实视觉 AABB 偏移会报
  `视觉对齐偏差`。
- 默认 ArchKit 清单为 `floor_n`、`floor2_2`、`floor4_4`、`wall1_4`、`stair_2` 写入 UE
  imported bounds 校准数据；`wb_validate` 新增 `floor_hole_count` / `wall_gap_count`，把缺地板、
  缺墙从单个 actor 缺失提升为可聚合的结构洞/缝指标。
- `PlanStep` 新增 `required_evidence`；runner 在 `success_checks` 前执行硬证据门禁，白盒步骤缺
  `screenshot` / `vision_review` 等 facts 时不会只凭 `wb_validate` PASS 收口。
- `viewport_screenshot` 的 `screenshot` facts 新增本地取景快检：成功回包后会检查截图文件存在、
  非背景主体占比与主体是否居中；缺文件、空画面或主体贴边会令 `screenshot.ok=false`，避免“截到天空/
  边角”也被当作白盒视觉硬证据。
- 楼梯编译会生成 `stairwell` guard pieces，validator 可计数，补上“有楼梯但没有楼梯间结构”的基础契约。

### 新增（白盒 B+ 垂直结构与玩法层）

- 布局 DSL 扩展 `room.level`、`level_height`、顶层 `stairs`、`room.props` 与顶层
  `gameplay`；未提供 `gameplay` 时保持旧结构层输出，提供 `gameplay`（含 `{}`）时自动生成
  出生点、主路线、掩体与柱子。
- `gameplay.spawn_points` 与 `gameplay.routes` 只有缺省时才走默认生成；显式 `[]` 会覆盖默认
  生成并关闭对应 PlayerStart 或 route marker 输出。
- 多层房间按 `origin.z + level * level_height` 抬升；楼梯只允许连接相邻楼层，资产高度必须匹配
  层高差，上层楼梯井不铺地板/NavProxy。
- 保持结构墙 `Wall1_4` 拉伸 + butt joint 对齐策略；新增 stair/prop/cover/pillar 原生尺寸放置路径，
  自动件过滤 `needs_review` 资产，非结构件 `scale=(1,1,1)`。
- 编译期按外墙厚度检查原生尺寸件目标 AABB：显式 props 与 stairs 不能侵入外墙边界带，避免
  `wb_build` 把肉眼可见的穿墙布局先落地再等 `wb_validate` 失败。
- 显式 props 与自动 cover/pillar 会避开同房间门到门 corridor；required prop 堵住该 corridor 时
  编译期报错，optional prop 则跳过。
- stairs 会避开同房间对穿门的直通 corridor，防止楼梯井切断穿堂动线；相邻转角门仍允许通过房间内
  其它空间绕行。
- 自动 cover/pillar 的占用表会同时保留楼梯 footprint 的上下层安全区，避免上层楼梯井洞口被自动玩法件填住。
- 默认 gameplay route 经过楼梯边时会在下层楼梯脚与上层楼梯口各插入 route marker，让自动玩法件避让
  真正的上下楼动线，而不是只连接上下层房间中心。
- `Placement`/spawner 支持非 StaticMeshActor；白盒 gameplay 出生点以真实 `PlayerStart` 落地，
  `spawn_actor` 调用不再传 `static_mesh`。
- `wb_build` 在 spawn 阶段遇到桥断开、丢响应或单件落地失败时，会自动按同前缀执行 best-effort
  回滚，避免半批次 actor 残留到下次校验并造成叠批穿插。
- validator 增加 `level_count`、`stair_count`、`prop_count`、`spawn_count`、`route_count` metrics，
  并检查 cover/prop/pillar 是否堵塞主路线 corridor；PlayerStart/route/navproxy 不污染 wall/floor metrics。
- UnrealMCP 插件侧 `spawn_actor` 白名单新增 `type="PlayerStart"`，并对空参数请求返回错误。

### 新增（ArchKit 结构质感）

- `ue_whitebox` 默认白盒清单切到 `config/whitebox/kit.yaml`（可用 `WB_MANIFEST` 覆写回旧
  LevelPrototyping 清单），`wb_build` 现在优先使用 `/Game/LevelPrototyping/Meshes/ArchKit`
  的真实模块件搭结构。
- 白盒布局 DSL 新增显式 `windows` 字段（同 `doors` 的 `wall/at/width` 结构）；`layout_json`
  默认墙高调整为 400uu，以匹配 ArchKit 墙/门/窗的原生高度。
- 编译器新增混合模块铺排：地板按 tile 覆盖，墙体主路径统一使用 `Wall1_4` 单件沿 X 拉伸到目标
  长度，减少多墙段接缝与外沿错位；门洞/窗洞放 `wall_door`/`window` 模块。
- 当前 ArchKit `corner` 资产是占 1x1m 的实体角墙，和已铺到角点的直墙叠放会严重穿模；
  因此默认禁用自动角件，转角由南北墙满长 + 东西墙按墙厚端部缩进形成 butt joint，后续换成墙厚级角件后再接入。
- manifest v2 新增 `snap_box`：资产可声明用于拼接/贴齐的结构核心盒，而不是拿完整视觉包围盒对齐；
  ArchKit 门框与窗框已标中间 20uu 核心，外框可自然突出但墙体核心仍贴齐。
- 修复共享墙去重误判：去重时纳入 yaw，避免南北墙和东西墙在角点附近被当成同一面墙而误删。
- 相邻房间的共享门只在墙段上切开通道，不再额外放 `wall_door` 门框，避免室内连通口被实体门框
  碰撞误堵；外墙门仍使用 ArchKit `wall_door` 模块。
- ArchKit 地板下方自动生成薄 `navproxy` 导航承载面（Engine cube，藏在地板下，不计入地板面积/
  墙体 metrics），解决当前 ArchKit floor 资产不产出可走 NavMesh 的问题。
- 白盒落地与校验支持旋转：`Placement` 记录 rotation/目标 AABB，`spawn_layout` 透传 rotation，
  `wb_validate` 对照 rotation 并用目标 AABB 避免旋转模块误报穿插。

### 新增（WB-1：资产库地基）

- FBX 批量导入（**真机导入 80 件通过 2026-06-13**）：`ue_editor` 新增 `import_fbx(tasks,
  import_materials, replace_existing, save, import_uniform_scale, transform_vertex_to_absolute,
  bake_pivot_in_vertex, timeout)`（write_project）——把 FBX 批量导入为 StaticMesh 资产，逐件回报
  ok/asset_path，落 `import_fbx` 事实（ok=失败数为 0，带 imported/failed 计数）。
  - 缩放与原点参数：`import_uniform_scale`（米制源 ×100→uu）、`transform_vertex_to_absolute`
    （传 False 让网格回局部原点、不烘 DCC 世界位置）——模块化套件按此对齐离线扫描的 size/pivot。
  - 插件 C++（agent_test，commit 待提交）：`HandleImportFbx`——校验后**一次性**把全部任务交
    `IAssetTools::ImportAssetTasks`，并在导入期间**临时关 `Interchange.FeatureFlags.Import.FBX`
    强制走 legacy `UFbxFactory`**（Interchange 异步导入会在桥的 GameThread 任务内 pump TaskGraph，
    触发 RecursionGuard 断言崩溃；legacy 同步内联且 honor UFbxImportUI 的缩放/原点/材质选项）；
    `UnrealMCP.Build.cs` 加 `AssetTools` 依赖。
  - 真机落地：按 `config/whitebox/kit.yaml` 把 ArchKit 80 件 FBX 导入
    `/Game/LevelPrototyping/Meshes/ArchKit/<类别>/<名字>`（import_materials=False 用默认材质，
    transform_vertex_to_absolute=False 修正原点偏移），各类别数量与清单完全吻合；kit.yaml 的
    80 条 `path` 已回填为真实导入路径（WB-1 待办①完成）。
- 网格尺寸校验与修正工具（**实测验证 2026-06-13**）：`ue_editor` 新增
  `get_mesh_bounds(asset_path)`（read，返回 StaticMesh 本地包围盒真实 uu 尺寸）+
  `set_mesh_build_scale(asset_path, scale)`（write_project，设 BuildScale3D 并重建保存）。
  - 关键发现：FBX 导入的缩放选项（ImportUniformScale / ConvertSceneUnit）只在
    `transform_vertex_to_absolute=True` 时生效，而该项又会把 DCC 世界位置烘进顶点致原点偏移——
    二者互斥。故改用 **BuildScale3D 在导入后对几何缩放**（围绕本地原点，原点不变），解耦尺寸与原点。
  - 插件 C++ `HandleSetMeshBuildScale`：设 `BuildSettings.BuildScale3D` → `Build()` →
    **`FStaticMeshCompilingManager::FinishCompilation` 阻塞等异步构建完成**（否则保存的是未缩放旧数据、
    不持久化，且累积异步任务会致崩）→ 保存。米制 ArchKit 80 件经 build_scale=100 校正到正确 uu 尺寸
    （Wall8_4 实测 800×20×400），原点保持局部。
- StaticMesh 默认材质工具：`ue_editor` 新增 `set_static_mesh_material(asset_path, material_path,
  material_slot=0)`（write_project），插件 C++ 新增同名命令；`ue_whitebox` 新增
  `wb_apply_manifest_material(material_path=MI_PrototypeGrid_Gray)` 批量读取当前 manifest 并逐个设置
  ArchKit 资产默认材质。当前源码已接线，需重编译/重启编辑器后生效。

### 新增（Stage E：行为闭环与编排）

- 运行期功能测试（E1 收口，**真机验证通过 2026-06-13**）：`ue_editor` 新增 `run_functional_test(
  test_name, timeout, poll_interval)`（write_project）与 `functest_list(filter, max)`（read，发现
  可用测试）。Automation/Functional Test 在编辑器主线程异步执行（自身可进出 PIE），故插件拆成
  `functest_start`（触发）+ `functest_poll`（推进 latent 命令并取结果）两命令，Python 侧跨帧轮询
  编排；落 `functional_test` 事实（ok=passed，带 error/warning 计数），超时不伪造通过。
  - 插件 C++（agent_test，commit 待提交）：`HandleFunctestStart/Poll/List`——
    StartTestByName + 跨帧 ExecuteLatentCommands + StopTest + ExecInfo 解析；functest_list 经
    GetValidTestNames（放宽 RequestedTestFilter）列全部已注册测试名。
  - 真机验证：functest_list 列出 4854 个测试；负向（不存在的名）正确报 not found；正向
    `run_functional_test("FFColorSmokeTest")` 经 start→跨帧 poll→finish 真跑通、passed=true。
- UE 在线评测档（E3 / C3，**真机出基线 2026-06-13**）：`ue5agent eval --suite ue` 用完整
  TaskRunner + 真实 MCP 工具面跑端到端任务，度量**通过率 / 一次通过率 / 平均迭代次数 /
  人工干预次数**（无人值守恒 0）。
  - 新增 `evals/ue_suite.py`（编排/指标/检查器，注入式 run_one 可离线单测）+
    `evals/tasks/ue.yaml`（蓝图理解/白盒校验+可达/运行期 functest 四个干净基线用例）+
    `evals/tasks/ue_faults.yaml`（故障注入：编辑器断连/UBT 多错误/白盒部分失败/PIE 报错，
    需手动制造故障后单跑）。
  - cli `eval --suite ue` 先 probe_editor 探活（offline→退出不伪造分），在线则挂载 MCP +
    逐任务 TaskRunner；`--out` 先落盘基线再打印（避免控制台输出异常丢报告）。
  - **首份 UE 基线**（deepseek/deepseek-chat，2026-06-13，evals/baselines/ue/）：
    4/4 通过、一次通过率 100%、平均迭代 1.5、人工干预 0。
  - 故障注入真机复核：杀编辑器后单跑 → env_unready → 1 次尝试快速终止（13s，不空转重试），
    报告含环境未就绪指引（B3 恢复策略表当前代码生效）。
  - 沙盒两档（basic/hard）仍是离线 CI 门禁；eval 参数 `--tasks` 改 Option，新增 `--agent`。

### 已有（Stage E 此前批次）

- 运行期验证闭环（E1，真机验证通过）：`ue_editor` 新增两个工具（UnrealMCP 插件同步
  新增 C++ 命令）——
  - `pie_smoke(seconds)`：在编辑器里启动 PIE 跑若干秒，结束后返回期间**新增**的
    Error/Warning 计数与错误行（窗口精确，不混入历史错误）；验证"改完能跑起来不报错"。
    实现为插件 `pie_start`/`pie_stop` 两命令 + Python 侧编排等待（PIE 在编辑器主线程
    tick，不能在单条命令里阻塞 GameThread）。落 `pie` 事实（ok=error_count==0）。
  - `output_log_tail(lines, severity)`：按严重度读 Output Log 尾部，供编译/PIE 后查错。
    落 `output_log` 事实（总错误/警告计数）。
  - 插件侧新增 `MCPLogCapture`（注册到 GLog 的线程安全环形日志捕获，单调序号支持
    pie 窗口精确查询，过滤 SetColor 控制消息与 Verbose 噪声）。
- 蓝图引用查找落地（C2 收尾，真机验证通过）：插件新增 `find_blueprint_references`
  命令（AssetRegistry GetReferencers，过滤引擎/自身）→ `bp_find_usages` 工具实际可用
  （此前仅 Python 侧就绪、命令缺失）。真机验证 BP_ThirdPersonCharacter →
  BP_ThirdPersonGameMode。
- 桥鉴权服务端落地（D1.1 收尾，真机验证通过）：插件启动生成随机 token 写
  `Saved/ue5agent_bridge_token.txt`，握手校验 protocol 版本 + token，不符拒绝执行
  （写 token 失败则失败开放不自锁）。配 `UE_MCP_TOKEN_FILE` 后客户端自动出示。
  真机验证：无 token 被拒、带 token 放行、protocol 不符报错。
- 子代理体系（E2 离线先行）：新增 `agent/subagent.py`，以 `spawn_subagent` 工具
  （READ 级）形态把探索性子任务交给上下文隔离的子代理执行——独立 history + 独立
  只读 system + 受限工具面（复用 ScopedRegistry）+ 角色级模型路由（复用 LiteLLM
  role 路由，未配角色回退 planner），跑完只回结构化摘要、全文落
  `Artifact(kind="subagent_summary")`，主上下文不被子任务工具细节淹没（呼应 B4）。
  右尺寸边界：子代理工具面硬限只读（写操作留在主循环——checkpoint/回滚/验收都在
  那里），恒排除 spawn_subagent 自身（嵌套深度 1 防递归），预算小于主步骤
  （8 轮 / 180s）；子代理故障/预算耗尽/空摘要均转 `[error]` 文本回主循环不上抛。
  cli 在构造 runner 前注册（闭包指向当次 writer，chat 复用 registry 故 replace）。
  models.yaml roles 可独立配 explorer/judge 等角色解耦专长模型（vision 已是 Kimi）。
  `ToolRegistry.register` 增 `replace` 形参（同名覆盖，子代理工具按任务重注册用）。

### 新增（Stage A：白盒可信闭环）

- `ue_editor` 新增三个关卡验证工具（UnrealMCP 插件同步新增 C++ 命令，UE5.7 实测通过）：
  - `viewport_screenshot`：编辑器视口截图存 PNG，可选移动相机（俯视白盒布局）；
  - `navmesh_rebuild`：重建 NavMesh，`bounds_center/extent` 可自动生成 NavMeshBoundsVolume
    （白盒场景默认没有导航体积）；
  - `path_test`：两点导航可达性（自动投影到导航网格，区分完整/部分路径）。
  真机验收：三房间布局 wb_build → navmesh_rebuild → path_test 首尾房间可达（路径 1252uu）
  → 俯视截图，全链路通过。
- MCP server 配置支持 `tool_permissions` 按工具覆写授权级别（同一 server 读写工具混存场景，
  如 ue_editor 的 navmesh_rebuild 标 write_project、其余保持 read）。
- 白盒确定性校验器 `wb_validate`（whitebox/validator.py，纯几何不依赖 LLM）：
  布局期望 vs 编辑器实测对照，检出缺件（spawn 部分失败）/多件（残留）/位置漂移/构件穿插
  （墙角搭接豁免），并产出关卡 metrics（房间数/门数/地板面积等）。真机正负样本验证通过。
- 证据信封 v1：工具可经结果末尾 `[facts] {json}` 标记附带结构化事实（ToolOutcome.facts，
  入 trace 不回传模型）；验收升级为两段式——确定性规则先行（compile/wb_validate/path_test
  事实可判时直接给结论，不调 LLM），LLM judge 兜底。首批产出 facts 的工具：ubt_compile、
  wb_build、wb_validate、path_test、repo_checkpoint。
- PlanStep 契约 v2（B1）：步骤可声明 allowed_tools（步内工具白名单，ScopedRegistry
  收紧工具面）、permission_ceiling（步内权限上限）、preconditions（editor_online 探测，
  未满足时在执行提示注入补救指引）、success_checks（声明式验收绑定 facts 证据，缺证据
  → insufficient 驱动补证据，优先级高于通用确定性规则）、rollback_policy（失败超限自动
  wb_clear；dangerous 级回滚仅提示）、step_budget（步级预算只许收紧）。全部字段可省略，
  弱模型产不出契约时行为与 v1 完全一致；旧 session.json 可直接加载。
  契约 e2e 实测后补三处加固：契约自洽性修正（success_checks 要求的验证工具自动并入
  allowed_tools，否则工具面过滤后证据永远补不上）；回滚按实际落地前缀清（wb_build
  facts 带 prefix）；wb_validate 新增异前缀白盒残留检测（旧批次构件叠在布局区域会
  堵门断 navmesh，且对本前缀对照不可见——实测曾被误诊为 agent radius 问题）。
- 工具效果声明（B2 Tool Effect System）：工具元数据从"权限级"扩展为副作用语义
  （tools/effects.py：idempotent / requires_checkpoint / rollback_tool / supports_dry_run /
  resources）。MCP 工具按裸名查 kernel 侧声明表（不采信远端自报——checkpoint 等安全行为
  的权威必须在本进程），本地工具在定义处声明，未声明按权限级推导保守默认（与旧行为等价）。
  两个行为变化：① WRITE_PROJECT 自动 checkpoint 改由 effects.requires_checkpoint 驱动
  （默认仍打；白盒类工具声明 False 是有意的——git 快照保护不了关卡 actor，回滚靠
  rollback_tool=wb_clear）；② 非幂等工具执行失败（exception/tool_error）的熔断阈值 3→2，
  回传文本禁止原样重试并给出查状态 / rollback 工具指引；执行前失败（schema/bad_json）
  不降阈值——没碰到副作用，修正参数重试是安全的。
- 视觉迭代闭环（A4）：vision 角色接入多模态模型（实测 Kimi/Moonshot `kimi-k2.6`，
  provider 级 `params` 注入固定 `temperature=1`）；`agent/vision_review.py` 把截图按
  审查清单交 vision 角色，产出结构化问题列表（按房间/构件名定位、severity 分级），
  解析失败保守不放行。runner 集成局部重生成：执行步后若本步产出截图
  （`viewport_screenshot` 落 `screenshot` 事实），自动对截图做视觉审查，结果以
  `vision_review` 事实并入证据通道——存在 high 问题或解析失败 → 确定性验收判 fail，
  问题区域回灌 history 引导模型重新落地（wb_build 整批重建为兜底）。未配 vision 角色时
  不注入审查钩子，行为与此前完全一致（截图仅存档供人看）。
- 视觉审查工程化（三房间死斗 e2e 真机暴露的三个生产级问题修复，2026-06-12）：
  ① 截图降采样：视口截图常达 3000+px/数 MB，多模态请求体过大致审查极慢甚至挂起——
  `vision_review.image_to_data_url` 在审查前把长边 > 1280 的图降采样并 JPEG 重压（依赖 Pillow），
  `review_screenshots` 单次最多送 3 张（一步连截多张时取最近几张）。
  ② 视觉审查硬超时降级：litellm 对部分多模态端点（moonshot 实测）的调用会阻塞事件循环、
  不遵守自身 timeout，会无限冻结整个 run——cli 的 vision_reviewer 把 LLM 调用放进工作线程
  （asyncio.to_thread 释放主循环），runner 用 asyncio.wait（非 wait_for，避免 await 不可取消的
  执行器 future）做 120s 硬超时，超时记 vision_review_error 并降级（步骤照常走确定性/judge 验收）。
  ③ planner 引导白盒搭建任务把"搭建 + 俯视截图自查"放在同一步（同时含 wb_build/wb_clear/
  viewport_screenshot），使视觉审查发现问题时能就地重建——截图与重建分到两步会让该步无法修正。
  viewport_screenshot 成功时落 screenshot 事实（path），runner 据此发现本步截图触发审查。
- 错误分类与恢复策略表（B3 Error Taxonomy）：`core/errors.py` 定义 9 类 ErrorCategory
  （env_unready/bridge_down/ubt_compile_error/permission_denied/budget_exhausted/evidence_missing/
  partial_side_effect/tool_arg_error/transient）+ `[err:<类别>]` 文本标记 + classify()（显式标记优先、
  启发式兜底，分不出归 transient）；env_unready 沿用历史 [env:unready] 标记向后兼容。runner 恢复
  从"统一重试"升级为按主导错误类别查表路由：env_unready→快速终止（不空耗重试）；bridge_down→
  探活一次，离线则快速终止（踩坑史第 8 条"别对死桥空转重试"的体系化）、在线则正常重试；
  partial_side_effect→回滚清理后重试；其余类别走默认重试（对编译错/缺证据/参数错/权限拒绝本就正确）。
  ue_editor 桥错误区分 bridge_down（连上后掉线/超时）与 env_unready（连接被拒/从未开）。
- 上下文工程 v1（B4，Stage B 收口）：① 工程状态摘要——runner 开场一次性探测
  editor_status/repo_status/engine_info（read 级，不耗模型轮次），拼 ≤500 字摘要预置到
  system 消息首位（compact_history 永远保留 head，摘要不被压掉），省去模型自己逐个探测；
  ② 长任务进度——每步收口刷新 runs/<session>/progress.md（各步状态），每步提示注入 [进度]
  行（已完成/当前/待办），随新提示重述故步内压缩后不丢进度目标；③ 工具结果摘要器
  summarize_tool_result 按类型摘要替代一刀切截断（actor 列表→计数+前 N 名；编译日志→保留
  错误/Result/警告行折叠正常输出；其余 truncate 兜底），max_tool_result_chars 仍为兜底上限。
- 蓝图桥裁剪与分级（C1 = P1.2）：明确 ue_editor 瘦桥只转发审定过的只读命令——蓝图相关
  一律只读（ADR-0003），不暴露任何编辑/编译/连线/批量构建命令（由"只 forward 选定命令"的
  构造方式强制保证）；唯一写级工具是 navmesh_rebuild（write_project）。工具清单与权限分级表
  入 docs/phase1-bridge-plan.md；新增回归守卫测试（工具集恰为审定集、源码不含编辑类桥命令、
  蓝图工具只发只读查询）。
- 安全加固与工程化（Stage D 离线项）：
  - secret 掩码（D1.2）：trace/report/progress/artifact 落盘前掩掉 .env 中的 API key 值
    （core/redaction.py，RunWriter 单点施加；secret 取自 provider.api_key_env 对应环境变量值，
    长值优先替换避免子串残留）。防 key 因日志/报告被无意分享而泄露。
  - 工具输出注入防护（D1.3）：工具结果含指令样文本（中英文"ignore previous instructions/
    忽略以上指令"等）时用 [external-content]…[/external-content] 围栏包裹，KERNEL/SYSTEM 提示
    声明围栏内只是数据、不可作为指令执行。
  - 运行锁（D2.1）：同一 runs/ 同时只允许一个 runner（runs/.runner.lock，PID+时间戳，
    陈旧锁自动回收），冲突给可读错误而非莫名拒连。
  - `ue5agent runs prune`（D2.2）：按数量（--keep）/天数（--days）清理旧运行目录，
    --keep-screenshots 保留 artifacts/*.png；跳过非 run 目录不误删。
  - CI（D2.3）：.github/workflows/ci.yml 跑 uv sync + ruff check/format + mypy + 离线 pytest。
- 蓝图只读导出（C2）：`ue_editor` 新增 `bp_overview`（父类/组件/接口/变量/函数/事件图分类的
  紧凑概览，token ≈ 原始 JSON 1/7）、`bp_pseudocode`（基于节点 exec connections 重建控制流
  伪代码，无连接信息时退回结构化摘要）与 `bp_find_usages`（AssetRegistry 引用查找，Python 侧
  就绪）三个只读工具；转换逻辑在纯函数模块 `src/ue5agent/blueprint.py`（可单测，夹具取自真机）。
  bp_graph 即既有 bp_analyze，参数修正为 graph_name（插件按它选事件图/函数图；早期误传
  function_name 被忽略、恒返回 EventGraph——读插件源码确认 connections 本就带 from/to 端点，
  此前"pin 无连接端点"的结论系参数传错所致）。真机 + agent e2e 验证：agent 用
  bp_overview/bp_pseudocode 准确解释 BP_ThirdPersonCharacter（继承/组件/输入事件/函数/行为）。
  剩余待插件 C++：引用查找命令（find_blueprint_references），落地前 bp_find_usages 返回错误文本。
- 桥鉴权客户端侧（D1.1 客户端）：bridge.py 每条命令握手附协议版本 `protocol`（PROTOCOL_VERSION=1），
  并在配了 `UE_MCP_TOKEN` 或 `UE_MCP_TOKEN_FILE`（指向插件写的 token 文件）时附 `token` 字段；
  未配则不带，与无 token 插件完全兼容。服务端校验（生成/写 token、握手校验）待插件 C++。
- Stage E（Phase 3）细案 docs/stage-e-plan.md：E1 运行期验证闭环（PIE smoke / Functional Test /
  Output Log，插件 C++ + 证据/恢复）、E2 子代理体系（上下文隔离 + 按角色配模型，可离线先行）、
  E3 完整基准与 UE 在线 eval（含 C3）。并给出"C2 收尾 + D1.1 服务端 + E1 命令合并一次插件编译"的建议。

### 修复

- wb_build 编辑器崩溃根治（运行唯一命名）：spawn 的 actor 名改为 `WB_<批次时间戳>_<构件名>`，绝不复用任何旧名。根因经引擎崩溃日志确诊（`LevelActor.cpp:585 Cannot generate unique name for 'WB_Hall_floor'`）——UE 的 DestroyActor 是"标记销毁 + 延迟 GC"：delete 后 actor 当帧即从 level 列表移除（find_actors_by_name 查不到，看似删净），但其 FName 在 GC 真正回收前仍占命名空间；delete 与 spawn 仅隔数百毫秒，GC 未发生，spawn 复用同名命中引擎硬 check → Fatal 崩编辑器。因此一切依赖 find 的"删后复查 / spawn 前重名预检"对这种僵尸名天然失效——这也是前一版"幂等防重名"修复无效、崩溃复现的原因。唯一命名让新名在引擎命名空间必然空闲，从根上消除重名 Fatal；clear 仍按 `WB_` 前缀整批回滚（唯一名仍带前缀，可被清理）。tests/test_whitebox_spawn.py 同步：删除废弃的 spawn 前预检测试，新增"模拟僵尸名验证唯一名绕开""跨批不撞名"回归。
  - 已废弃前一版结论（仅作历史，勿再采信）：曾误判为"wb_build 不幂等→验收重试二次 spawn 同名→崩"，并加了删后复查 + spawn 前 find 预检；实测崩溃复现，证明 find 看不见僵尸名，该方向无效。
- repo_tools/gitops 子进程切断 stdin（DEVNULL）并注入非交互 env（GIT_TERMINAL_PROMPT=0 等）：修复 git 继承被 MCP 协议占用的 stdin 而挂死、超时后被 is_git_repo 误判为"不是 git 仓库"的问题（三房间白盒诊断时定位，0.2s 秒回）。

### 新增

- `ue_editor` 新增 `editor_status` 工具：探测编辑器桥在线状态，编辑器相关操作前可先确认环境就绪。
- 新增 `ue_lifecycle` MCP server（dangerous 级）：`editor_launch` 启动 UE 编辑器并等待桥端口就绪，已运行则幂等返回；agent.yaml 新增 `permissions.allowlist` 配置 dangerous 工具白名单（CLI 已接线，此前白名单无配置通路、dangerous 工具必被拒）。

### 修复

- 非交互运行的会话 history 污染三层根治（A3 e2e 实测发现）：后台/重定向运行时
  `typer.confirm` 无 TTY 抛 Abort，异常从权限网关逃逸导致 assistant 的 tool_calls
  永远缺回包，该会话后续每次 LLM 请求都被 API 拒绝（步骤重试全部空耗）。修复：
  ① CLI 非交互模式不挂确认器（WRITE_PROJECT 靠自动 checkpoint 放行可回滚，DANGEROUS
  缺人工确认仍拒绝）；② 工具管线把确认器/checkpoint 钩子的任何异常兜成 [denied] 文本；
  ③ loop 保证每个 tool_call 必有回包（调度层异常转 [error] 文本回传模型）。
- `ue5agent run` 新增 `--yes/-y` 无人值守旗标：显式跳过工程写操作的交互确认
  （仍自动 checkpoint）。TTY 启发式判定（stdin+stdout 双检）在 Git Bash 等
  pty 包装下可能误判，脚本化调用一律建议带 `--yes`。
- 环境未就绪快速失败：编辑器桥连接被拒时（如 UE 编辑器未启动），runner 不再消耗 3 次步骤重试逐个跳过，而是立即终止并在报告中给出可操作指引（跨进程靠 `[env:unready]` 文本标记传递错误类别）。
- 任务报告的步骤执行小结截断阈值 300 → 2000 字符，截断时带明确标记，不再无声切碎参数 JSON。

- agent 工程化（M1–M5）：工具调用容错（坏 JSON 修复/schema 校验/近似工具名）、trace 与 `ue5agent trace` 回放、API 重试退避与角色降级链、chat 多轮记忆与历史压缩、迷你评测集与 `ue5agent eval`。
- eval 报告新增工具错误率与成本估算，`--out` 导出 JSON；DeepSeek 真模型基线归档（evals/baselines/，basic+hard 两档全满分零方差）。
- Agent Kernel K1：TaskSession/PlanStep/Artifact 数据结构与持久化、类型化 TraceEvent、runs/ 按次产物目录；chat 会话落 runs/，trace 命令兼容新旧目录。
- ubt 解析器捕获无 ERROR: 前缀的 UBT 失败（Result: Failed + 上文回溯），来自 UE5.7 真机样本。
- Agent Kernel K2：工具管线模块化（参数规范化/结果信封/失败签名熔断），registry 保持兼容。
- Agent Kernel K3：权限 4 级（write_safe/write_project 拆分，工程写前置自动 checkpoint，危险操作双条件）；新增 repo_tools MCP server（git 快照与还原）。
- M6 编译闭环实测通过（UE5.7+VS2026）：成功构建、注入错误结构化定位、修复回绿；解析器在已有具体错误时抑制 Result: Failed 冗余汇总。
- Agent Kernel K4/K5：TaskRunner 阶段状态机（计划/执行/judge 验收/恢复/报告，ADR-0006），chat 与 e2e 切换至 runner；移除 session_log 旧路径；真模型门禁通过（eval 两档满分持平基线，e2e 含 judge 打回重试的真实恢复）。

- 项目骨架：agent 主循环（tool-calling）、按角色的多模型路由（LiteLLM）、工具注册表与三级权限网关、MCP 客户端、会话 JSONL 日志。
- 自带 MCP server `ue_build`：UBT 编译调用与结构化诊断解析（MSVC/链接/UBT 错误）。
- CLI：`ue5agent check-config / chat / version`。
- 文档体系：架构设计、ADR×5、上手与开发指南、路线图、术语表。
- 工程化：uv + ruff + pytest + mypy，scripts/setup.ps1 与 scripts/check.ps1。
