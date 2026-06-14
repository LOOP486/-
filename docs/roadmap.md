# 路线图

阶段定义与验收标准的完整版见 [architecture/design.md §11](architecture/design.md)。本页是工作清单，随进展勾选。

## Phase 0：核心骨架 + C++ 编译闭环（进行中）

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
