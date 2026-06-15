# 路线图

阶段定义与验收标准的完整版见 [architecture/design.md §11](architecture/design.md)。本页是工作清单，随进展勾选。

## Phase 0：核心骨架 + C++ 编译闭环（已完成）

- [x] 仓库工程化：uv + ruff + pytest + mypy，setup/check 脚本
- [x] 配置体系：models.yaml（角色路由）/ agent.yaml / .env，校验与 CLI
- [x] agent 主循环：tool-calling、工具错误回传、迭代预算、会话 JSONL 日志
- [x] 权限网关：read/write/dangerous 三级 + 白名单 + CLI 确认
- [x] MCP 客户端：stdio 挂载、工具前缀、按 server 配授权
- [x] ue_build MCP server：UBT 编译 + 结构化诊断解析（含单测）
- [x] 对真实 UE 工程实测 ubt_compile（UE5.7 + VS2026：成功/工具链缺失/注入错误三种形态均验证，真机样本固化为测试）
- [x] chat 会话内多轮记忆
- [x] 历史压缩 compact_history 实现（确定性摘要版）
- [x] 验收：任一模型完成「加一个 gameplay 功能并编译通过」（2026-06-11，DeepSeek 自主完成 BlueprintFunctionLibrary：5 轮 7 工具调用，编译 7.51s 零错误，全程自动 checkpoint——**Phase 0 完成**）

## Phase 1：编辑器桥（让 agent 看见蓝图）

- [x] fork flopperam/unreal-engine-mcp 进 unreal/，跑通最小链路（见 ADR-0005）（P1.1，2026-06-11）
- [x] 砍蓝图编辑工具，保留场景/资产/截图/日志（P1.2 = Stage C1，2026-06-12：瘦桥只转发审定的只读命令 + 分级表 + 回归守卫）
- [x] 自研蓝图只读导出：bp_overview / bp_pseudocode / bp_graph / bp_find_usages（C2，2026-06-12 + 06-13 收尾：bp_overview 忠实概览、bp_pseudocode 控制流伪代码（exec connections 重建，无连接退回摘要）、bp_graph=bp_analyze（graph_name 选图）、bp_find_usages（插件 find_blueprint_references 已落地，2026-06-13 真机验证）；agent e2e 解释蓝图通过）
- [x] 验收：agent 能回答「这个蓝图做了什么」✅、「谁在用它」✅（2026-06-13：bp_find_usages 真机验证 BP_ThirdPersonCharacter→BP_ThirdPersonGameMode）

## Phase 2：白盒搭建子系统

- [x] 资产 manifest v1（LevelPrototyping 套件，config/whitebox/；自动扫描草稿待资产包到位后做）
- [x] 布局 DSL 与编译器 v1（矩形房间/四向墙/门洞，几何与校验有单测，见 ADR-0004）
- [x] 批量 spawn + 前缀整批回滚（已对真实编辑器落地 12 件双房间布局验证；崩溃根因根治后三房间 agent 端到端核验通过）
- [x] 校验器：重叠/封闭/连通 + 关卡 metrics 表 + NavMesh 可达性（A1+A2 完成 2026-06-12：
  wb_validate 期望/实测对照 + navmesh_rebuild/path_test 真机验证）
- [x] 视觉迭代：俯视/漫游截图 → vision 审查 → 局部重生成（development-plan A4 完成 2026-06-12：
  vision 接 Kimi，审查模块 + runner 集成回灌已单测覆盖；截图降采样压缩 + 视觉审查硬超时降级；
  「三房间死斗」全链路真机 e2e 通过——搭建→截图→视觉审查→wb_validate(PASS)→navmesh→
  path_test 三对房间均可达，全程无人值守）
- [x] 验收：文字需求 + 模块资产库 → 可走通的白盒关卡 + 截图证据（2026-06-12 三房间死斗 e2e 达成）

### 白盒能力优化（ArchKit / 玩法 / 平面图）

- [x] A. 结构质感：接入 `/Game/LevelPrototyping/Meshes/ArchKit`，编译器可按真实地板、
  墙、门、窗模块拼装；支持显式 `windows`、400uu 默认墙高、rotation 落地与校验。
  2026-06-13 真机 e2e：两房间 ArchKit 布局 spawn 25 件（含每房间隐藏 navproxy），`wb_validate`
  PASS，`navmesh_rebuild + path_test` 可达；当前 ArchKit 角件因体积过大默认禁用，墙体改用
  `Wall1_4` 单件拉伸，东西墙按墙厚端部缩进形成 butt joint，门/窗框通过 manifest `snap_box`
  用 20uu 结构核心贴齐墙厚。2026-06-14 起该路径降级为显式 `structure_mode="modular"`。
- [x] B. 玩法可用性：自动放掩体、柱子、路线、出生点，让关卡更像可玩的 blockout。
  2026-06-13 完成 B+ 垂直结构：DSL 支持 `room.level`、`level_height`、`stairs`、
  `props` 与显式 `gameplay`；无 gameplay 时旧布局保持结构层行为，有 `gameplay` 时自动生成
  真实 `PlayerStart`、route markers 与 cover/pillar。墙体继续 `Wall1_4` 拉伸 + butt joint；
  stair/prop/cover/pillar 均以资产原生尺寸落地（scale=1），validator 增加 level/stair/prop/
  spawn/route metrics 与主路线堵塞检查；跨楼层默认 route 会插入楼梯脚/楼梯口 marker，保护真实
  上下楼动线。
- [x] B2. 可靠性底座：UE imported StaticMesh bounds 作为资产真值；manifest 支持
  `local_bounds_min/local_bounds_max/calibrated`，新增 `wb_asset_audit` 检查 manifest 与 UE
  bounds 偏差；`Placement` 带 visual AABB，`wb_validate` 能抓 transform 自洽但视觉 AABB 偏移；
  白盒步骤可声明 `required_evidence`，缺截图/视觉审查时不能仅凭 `wb_validate` PASS；楼梯会生成
  可计数的 `stairwell` guard pieces；默认关键 ArchKit 地板/墙/楼梯资产已写入校准 bounds，
  validator 增加 `floor_hole_count` / `wall_gap_count` 量化缺地板与墙体缺口；截图 facts 增加本地
  取景快检，主体贴边/空图不能作为视觉硬证据。
- [x] B3. Slab-first 默认策略：布局 DSL 顶层新增 `structure_mode`，默认 `slab` 生成 Engine Cube
  连续地板与连续片墙，门窗只切墙洞且不生成 door/window actor 或 navproxy；`room.level > 0`
  在默认模式下直接报错，旧 ArchKit 模块化与多层 room 行为仅在显式 `structure_mode="modular"`
  保留。`wb_validate` 增加 `structure_mode` 与 `wall_fragmentation_score` 指标，视觉审查改为评价
  blockout 空间组织，不因缺少门框/窗框扣分。
- [x] B4. 升级版资产扫描（①Registry 扫描 + ②几何先验）：`wb_asset_scan` 以 UE 导入后 bounds 为
  真值重建 manifest v2（size/pivot/footprint/local_bounds 直接 calibrated），消除重导后手工回填
  path/尺寸漂移；归类为名称前缀 + 几何先验混合，把 `unknown` 收敛到大类并标 needs_review；
  默认 apply=False 预览 diff、保留手调 roles/desc；纯逻辑在 `whitebox/scanner.py` 单测，UE 侧
  新增只读 `scan_assets`（AssetRegistry 枚举 + bounds，需重编插件）。
  剩余 ③VLM 缩略图识别（专攻命名不规范/歧义件语义）待接现有 vision 链路。
- [x] B5. 关卡尺度 metrics v0：新增 `scale_profile="realistic"` 与
  `config/whitebox/level_metrics.yaml`，先按真实室内空间控制尺度；视觉/LLM 负责理解平面图的空间
  结构，米制尺寸由 metrics 表收敛。`wb_validate` 输出 `scale_warnings`、最小房间面积/尺寸、
  最小门洞宽、墙高等诊断；第一版只 warning，不作为硬失败。
- [x] B6. 空间黑盒 agent eval：新增 `evals/tasks/ue_space.yaml`，用默认 slab、真实尺度、固定
  `SPC1/SPC2/SPC3` 与 `DST1/DST2/DST3` 前缀，以 2x3 测试区并排 origin 评测 agent 自主空间布局能力；
  评测只旁观 trace，不手写
  layout、不手动补救 UE 场景，要求 `wb_build -> wb_validate -> navmesh_rebuild -> path_test`。
  本轮暴露并修复共享墙重叠/轴线不齐、内部共享墙门窗误用、模型超时快速失败与可恢复工具错误
  判定问题。2026-06-14 追加：结构层 DSL 拒绝半格门窗/构件坐标，避免静默截断；`wb_validate`
  能检出近距离同向并列墙，SPC/DST eval 也会硬检查并列墙计数为 0；`wb_build` 会输出
  `<prefix>/<batch>` Outliner 根文件夹，eval 硬检查 `folder_root` 证据，防止 UE 崩溃/回档后误报。
  SPC/DST 题面冻结为 `prompt_id=spc-dst-space-v1`，并固定
  `deepseek/deepseek-v4-pro` + Kimi 轻量视觉模型；planner 会把 `path_test.success` 别名归一化为
  `reachable`，并清理未请求视觉时幻觉出的截图/视觉硬门禁。后续需把楼梯间阻断主通路提升为硬约束。
- [ ] B7. SPC/DST 第二轮复盘优化：问题列表与执行计划见
  [2026-06-15-whitebox-eval-optimization.md](superpowers/plans/2026-06-15-whitebox-eval-optimization.md)。
  2026-06-15 已完成代码侧修复：墙体端点半墙厚补偿、楼梯间护墙小夹缝与 validator metrics、
  coder LLM 超时分类/请求开始事件/同 step 重试、白盒执行提示与视觉失败重试 history 压缩、
  白盒 agent 通用构型守则与 LayoutError 恢复提示、MCP stdio session 关闭后的自动重启、
  `path_test.total/count/path_test_result` 与 `wb_validate.is_valid` 验收别名、UE eval `failure_type` 报告分类、
  `wb_build` 派发前轻量 guardrail（删除共享墙窗、补齐单侧共享门洞、收拢越界楼梯 footprint）、
  `viewport_screenshot` clean view/focus_prefix/margin 与按最新 `folder_root` 自动聚焦截图、
  视觉 high-only gate，以及 blockout 视觉清单（不因门窗框、楼梯踏步/扶手、房间标签扣分）。
  离线回归已更新到 486 个单测全绿；标准结构档
  `evals/baselines/ue/space-agent-test-20260615-205313.json` 已归档，SPC/DST 6/6 通过，
  pass_rate=1.0，first_try_pass_rate=0.8333，平均迭代 3.0，人工干预 0。后续重点继续放在
  白盒搭建 agent 的自主构型稳定性。视觉档复跑时暴露编辑器无活动视口导致截图不可用，已归类为
  `env_unready` 并快速终止，避免模型反复换参数刷大 history；视觉 baseline 待编辑器恢复正常关卡视口后
  作为后续验证项保留，不再通过反复改测试题面收敛。2026-06-15 追加修复 MCP SDK
  `McpError: Connection closed` 透明重连，以及白盒视觉步骤在 `wb_build`/`wb_validate`/截图证据齐备后
  立即交还 runner 做 `vision_review` 的早停逻辑，避免断链重试外包给模型和步骤内漂移。随后小步
  复核又发现 focus 取景在宽屏下仍可能带入相邻旧结构；`viewport_screenshot` 已在 `focus_prefix`
  场景追加本地前景连通域裁剪，只保留当前居中的白盒主体，继续把修复点放在 agent 取证链路而非测试题面。
  后续受控视觉重跑确认 crop 生效，但暴露 vision 把 `path_length`/NavMesh/path_test 等非视觉指标提前
  判为 high，以及自动 focus 未覆盖模型手写相机导致截图贴边；已改为视觉 high 只看截图可见的 blockout
  空间问题，确定性导航指标仍交给 facts 验收，自动 focus 也会丢弃手写 `location`/`rotation`。
  继续受控重跑时，首个视觉任务已通过截图/vision gate，但导航步骤暴露 `navmesh_rebuild` 被 git
  checkpoint 前置条件误拒；现已把该工具声明为不要求 git checkpoint 的 `write_project` 工具，
  保留权限分级，同时避免编辑器运行态 NavMesh 副作用被 git 快照机制挡住。
  后续又发现 vision 会把“中心距小于 16 格”这类精确数值约束当成 high；已统一降级精确格数/中心距/
  距离阈值类视觉误判，让这类约束回到 DSL/path facts 验收。再次受控重跑发现 planner 会把
  `screenshot`/`vision_review` 硬证据提前挂到 build/validate 步，导致该步通过后又反复补截图/校验；
  现已改为优先把视觉门禁绑定到实际截图/视觉步骤，单步白盒视觉计划才回退绑定 build 步。
- [ ] C. 平面图输入：从手绘/平面图/草图识别房间、门窗与连通关系，生成布局 DSL；透视图仅作风格/
  语义参考。（剩余的"图→布局 DSL 结构化识别"尚未做。）

## Phase 3：行为闭环与编排

> 细案见 [stage-e-plan.md](stage-e-plan.md)（E1 PIE/Automation、E2 子代理、E3 基准+UE eval）。下列项多需 UE 在线 + 插件 C++。

- [x] 运行期验证闭环（E1，2026-06-13 真机：pie_smoke 启 PIE 跑 N 秒读运行期 Error/Warning + output_log_tail 读 Output Log；**run_functional_test 真机收口**——插件 functest_start/poll/list（StartTestByName + 跨帧 ExecuteLatentCommands + StopTest），Python 跨帧轮询 + functional_test 事实，FFColorSmokeTest 真跑通 passed=true）
- [x] 子代理体系（上下文隔离 + 按角色配模型）（E2，2026-06-13：agent/subagent.py spawn_subagent 工具——独立 history/system + 只读 ScopedRegistry 工具面 + 角色级模型路由 + 只回摘要、全文落 artifact；离线单测覆盖隔离/工具面/角色/错误降级，与主循环集成）
- [x] 完整评测基准工程与跑分（一次通过率/迭代次数/人工干预次数）（E3=含 C3，**2026-06-13 真机出基线**：evals/ue_suite.py + evals/tasks/ue.yaml(+ue_faults.yaml) + `eval --suite ue` 真机路径；首份 UE 基线 deepseek-chat 4/4 通过、一次通过率 100%、平均迭代 1.5、人工干预 0，evals/baselines/ue/；故障注入复核 env_unready 1 次尝试快速终止）
- [x] CI（GitHub Actions：ruff + pytest）（D2.3，2026-06-12：.github/workflows/ci.yml = uv sync + ruff check/format + mypy + pytest 离线）

## 横切：agent 工程化（贯穿各阶段，按优先级排序）

agent 开发自身的复杂度清单。共同特征：不动架构，往既有接缝加模块。

- [x] 弱模型容错：参数 schema 校验、坏 JSON 修复、幻觉工具名纠正（registry.dispatch 链）
- [x] 迷你评测集：10 任务 smoke + `ue5agent eval`（通过率/工具错误率/token/成本）；DeepSeek 基线已归档（evals/baselines/）
- [x] 可观测性：trace 事件（耗时/token/预览）+ `ue5agent trace` 回放
- [x] API 故障面：指数退避重试、超时、角色级 fallback 链
- [ ] 流式输出与任务中断（Phase 2）
- [ ] 会话持久化与恢复（Phase 2）

## 横切：生产级强化（2026-06-12 外部评审吸收，细案见 development-plan.md Stage B/D）

- [x] 证据信封 v1：ToolOutcome.facts + verifier 两段式（确定性规则先行，LLM judge 兜底）（A3，2026-06-12）
- [x] PlanStep 契约 v2：allowed_tools / preconditions / success_checks / rollback_policy / 步级预算（B1，2026-06-12）
- [x] 工具效果声明：幂等性 / requires_checkpoint / rollback_tool / 非幂等工具重试治理（B2，2026-06-12）
- [x] 错误分类与恢复策略表：bridge_down / partial_side_effect / evidence_missing 等差异化恢复（B3，
  2026-06-12：core/errors.py ErrorCategory taxonomy + classify()；runner 恢复策略表按类别路由——
  env_unready 快速终止、bridge_down 探活后定夺、partial_side_effect 回滚后重试，其余默认重试）
- [x] 上下文工程 v1：工程状态摘要注入、progress 文件、按工具类型的结果摘要器（B4，2026-06-12：
  开场探测 editor_status/repo_status/engine_info 拼 ≤500 字摘要注入 system；每步刷新
  progress.md + 提示注入进度行；summarize_tool_result 按 actor 列表/编译日志类型摘要，
  truncate 保留为兜底）
- [x] 桥与凭据安全：trace secret 掩码 + 外部内容围栏 ✅（D1.2/D1.3，2026-06-12）；TCP token 鉴权 ✅（D1.1 完成 2026-06-13：客户端 protocol+token 握手 + 插件服务端生成 token/握手校验，真机验证无 token 被拒、带 token 放行）
- [x] 运行锁与清理：同工程单 runner 文件锁、`runs prune`、CI 离线门禁 ✅（D2，2026-06-12）
