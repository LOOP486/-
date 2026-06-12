# 开发计划

> 本页是施工蓝图：里程碑顺序、任务分解、验收标准。高层阶段定义见 [roadmap.md](roadmap.md)，两者关系：roadmap 管"做什么/做没做"，本页管"按什么顺序怎么做"。
> 制定：2026-06-10 | 修订：2026-06-12（吸收外部架构评审，规划 Stage A–E；此前 Phase 0、Kernel K0–K5、P1.1、Phase 2 核心均已完成）
>
> **执行者须知（人或 agent）**：动手前先读 [worklog.md](worklog.md)（最新状态与踩坑史）和 [CLAUDE.md](../CLAUDE.md)（硬性约定）。动架构先读 design.md 与 ADR。

## 排序原则

1. **产品价值优先**：本项目的最终交付是"关卡策划 AI 工具"——白盒关卡的可信闭环（搭建→校验→视觉验证→修正）排在 kernel 完善之前。
2. **验证能力先于生成能力**：agent 已能"做"，瓶颈在"证明做对了"。确定性校验、证据化验收优先于新工具面扩张。
3. **每个里程碑独立收口**：结束时 `.\scripts\check.ps1` 全绿、`uv run ue5agent eval` 两档不低于基线（evals/baselines/）、roadmap 勾选、CHANGELOG [未发布] 段更新、独立提交。
4. **右尺寸**：这是单机单用户的本地工具，多租户并发、容器化部署、远程编排明确不做（见 Stage D 末尾）。

## 已完成总账

| 里程碑 | 交付物 | 完成日 |
|---|---|---|
| M1–M5 agent 工程化 | 弱模型容错（JSON 修复/schema 校验/工具名纠正）、trace 与回放、API 重试与角色降级、多轮记忆与历史压缩、迷你评测集与 `ue5agent eval` | 06-10 |
| K0–K5 Agent Kernel | TaskSession/PlanStep/runs/ 产物目录、工具管线（规范化/ToolOutcome 信封/失败签名熔断）、权限 4 级 + WRITE_PROJECT 自动 checkpoint、TaskRunner 阶段状态机（计划/执行/judge 验收/恢复/报告，ADR-0006）、真模型门禁通过 | 06-11 |
| M6 = Phase 0 完成 | agent 自主"写 BlueprintFunctionLibrary→编译通过"端到端（UE5.7+VS2026）；排除三个挂死根因（MCP 子进程 env、UBT 管道死锁、git 索引锁） | 06-11 |
| P1.1 编辑器桥最小链路 | UnrealMCP 插件（flopperam fork）UE5.7 编译通过；自研瘦桥 ue_editor（4 只读工具）；实测读场景 71 actor、读蓝图节点图 | 06-11 |
| P2 白盒核心三件套 | 资产 manifest、布局 DSL 编译器（几何/门图连通校验，ADR-0004）、wb_build/wb_clear 批量落地与前缀回滚；spawn 运行唯一名根治编辑器崩溃（踩坑史第 7 条）；三房间 agent 端到端核验通过（run 20260611-222536） | 06-11 |
| 环境自愈三件套 | editor_status 探测、ue_lifecycle/editor_launch 幂等启动、环境未就绪 fail-fast（`[env:unready]` 错误类别先例） | 06-11 |

当前：128+ 单测全绿；5 个自带 MCP server（ue_build / repo_tools / ue_editor / ue_whitebox / ue_lifecycle）；模型 DeepSeek 单一供给，vision 角色未配。

## 外部评审吸收（2026-06-12）

收到一份对照"生产级 agent 16 要素"的架构评审。结论与本项目自评一致：**骨架要素已覆盖，短板不是缺要素，而是部分要素停在第一版、未体系化**。评审最有价值的 6 个建议及本计划的消化方式：

| 评审建议 | 消化为 | 调整说明 |
|---|---|---|
| EvidenceGraph + deterministic checker | **A3 证据信封**（先行）+ A2 白盒校验器（首个确定性 checker） | 不建独立"证据图"子系统，在 ToolOutcome 信封上加结构化 facts 字段，渐进式 |
| UE5 E2E 验证闭环（截图/NavMesh/PIE/Automation） | **A1 视觉与导航工具**（即原 P1.4），PIE/Automation 留 Stage E | 截图+NavMesh 是白盒闭环刚需，先做；PIE/Automation 是 Phase 3 范畴 |
| PlanStep Contract 强结构化 | **B1** | 已有 id/intent/acceptance/evidence 骨架，是扩展不是重写 |
| Tool Effect System | **B2** | 已有权限 4 级 + WRITE_PROJECT 自动 checkpoint，补副作用声明与重试治理 |
| 安全隔离（桥鉴权/secret redaction） | **D1** | 单机工具按本机威胁模型右尺寸，不做企业级 |
| 更真实的 eval（UE 集成/故障注入） | **C3 + 各里程碑验收自带** | 沙盒 eval 已饱和（K0 结论），新 eval 必须挂真实 UE |

评审中与项目事实不符、已无需处理的点（避免后续执行者重复劳动）：MCP server 是 5 个非 4 个；会话/步/墙钟三层预算闸已有（踩坑史第 2 条）；失败签名熔断已有（tool_pipeline.FailureTracker）；编辑器掉线已有 fail-fast + editor_launch 自愈；judge 已是"只看工具证据"且区分修改类/查询类任务；白盒 DSL 编译期校验（LayoutError 拦截非法布局）已是 dry-run 的雏形。

---

## Stage A：白盒可信闭环（Phase 2 收口，最高优先）

产品主线。完成后达成 roadmap Phase 2 终验：**文字需求 + 模块资产库 → 可走通的白盒关卡 + 截图证据**。

> 进度：A1 ✅ A2 ✅ A3 ✅（2026-06-12，真机验证全过：三房间 navmesh 可达 + 俯视截图 +
> wb_validate 正负样本 + 确定性验收单测；eval 两档满分持平基线）。剩 A4（等 vision key）。

### A1 视觉与导航验证工具（原 P1.4）

- **目标**：agent 能"看见"自己搭的关卡并验证可达性。
- **任务**：
  1. UnrealMCP 插件（agent_test 工程内，C++）新增三命令：`viewport_screenshot`（俯视/自由视角截图存文件）、`navmesh_rebuild`、`path_test(start, end)`（返回可达性与路径长度）。仿照现有命令注册方式；全部 GameThread 化 + 超时熔断。
  2. `src/ue5agent/mcp_servers/ue_editor/` 注册三工具：截图标 READ，navmesh_rebuild 标 WRITE_PROJECT，path_test 标 READ。截图文件落 `runs/<session>/artifacts/` 并登记 Artifact(kind="screenshot")。
  3. 插件改动需重新编译并重启编辑器（用 ue_lifecycle/editor_launch 验证自愈链路顺便回归）。
- **验收**：对三房间白盒布局，agent 调用链 wb_build → navmesh_rebuild → path_test 返回"房间 A 到房间 C 可达"；viewport_screenshot 产出俯视图进 artifacts。
- **依赖**：无外部依赖。注意插件 TCP 单连接（踩坑史第 4 条）。

### A2 白盒确定性校验器

- **目标**：白盒质量判定不依赖 LLM——纯几何/图算法给出 pass/fail 与指标。这是"deterministic checker"理念的首个落地。
- **任务**：
  1. 新建 `src/ue5agent/whitebox/validator.py`（纯逻辑，可单测，不依赖编辑器）：AABB 重叠检测、房间封闭性（墙体围合无缺口，门洞除外）、门图连通性复核（compiler 已有编译期版本，validator 针对落地后回读的 actor 实测坐标再验一遍）、关卡 metrics 表（房间数/总面积/门数/最长走廊/死端数）。
  2. `ue_whitebox` server 新增 `wb_validate` 工具（READ）：经 ue_editor 桥回读 `WB_` 前缀 actor 实际 transform → validator 判定 → 返回结构化结果（violations 列表 + metrics）。
  3. NavMesh 可达性不在 validator 内实现，复用 A1 的 path_test，由 runner/judge 组合两者证据。
- **验收**：单测覆盖重叠/缺口/不连通三类注入缺陷；真实编辑器中对故意写坏的布局，wb_validate 能指出具体 violation（哪两个构件重叠、哪面墙缺口）。
- **依赖**：A1（actor 回读已有，path_test 需 A1）。

### A3 证据信封 v1（EvidenceGraph 最小版）

- **目标**：验收从"LLM 读工具 transcript（800 字符窗口）"升级为"先跑确定性规则、LLM 只解释与兜底"，降低误判成功。
- **任务**：
  1. `tool_pipeline.ToolOutcome` 增加 `facts: dict` 字段：工具可附带结构化事实，trace 全量记录，回传模型文本不变。
  2. 首批产出 facts 的工具：`ubt_compile`（exit_code/error_count/errors[file,line,code]）、`wb_build`（batch_id/spawned_count/failed_count）、`wb_validate`（violations/metrics）、`repo_checkpoint`（checkpoint_id）、`repo_status`（changed_files）。MCP 工具经结果文本约定（如末尾 JSON 块）传递，mcp_client 解析进 facts——具体通道实现时定，原则是不破坏现有 `[error]` 文本协议。
  3. `agent/verifier.py` 改两段式：若 PlanStep 携带 success_checks（B1 之前先支持内置规则：编译步看 exit_code==0、白盒步看 violations 为空），先跑确定性判定；规则通过/失败后 LLM judge 只负责解释原因、判断证据是否充分、写人类可读结论。规则与 LLM 矛盾时以规则为准并在报告中标注。
- **验收**：单测：构造 facts 注入，验证规则先行、LLM 仅解释；e2e 回归 M6 编译任务与三房间白盒任务，报告中出现确定性判定段。eval 两档不退步。
- **依赖**：A2（wb_validate 是主要事实源）。可与 A2 并行开发、联调收口。

### A4 视觉迭代闭环（Phase 2 终验）

- **目标**：截图 → vision 审查 → 局部重生成，补上"工具层成功但画面不对"的盲区。
- **任务**：
  1. `config/models.yaml` vision 角色接入真实多模态模型（**需用户提供 vision 模型 key**，见文末等待清单）。
  2. 审查链路：A1 截图 → vision 角色按 checklist 审查（布局与需求一致性、明显穿插/悬空、比例失调）→ 输出结构化问题列表（问题区域用房间/构件名定位，不用像素坐标）。
  3. 局部重生成：runner 把 vision 问题列表回灌 planner，仅对问题房间 wb_clear（按构件名子集）+ 重 build；整批回滚仍是兜底。
  4. e2e 案例：「搭一个三房间死斗关卡，房间两两连通」→ 全链路产出关卡 + 截图 + wb_validate 通过 + path_test 可达 → **勾掉 roadmap Phase 2 全部项**。
- **验收**：上述 e2e 全程无人工干预跑通，报告含截图证据与确定性校验结论。
- **依赖**：A1、A2、A3；用户提供 vision key（key 未到位时 1–3 项先行，vision 审查降级为"截图存档供人看"）。

## Stage B：kernel 体系化（契约/效果/恢复/上下文）

外部评审主体建议。不动 ADR-0006 状态机架构，全部是往既有接缝加结构。

> 进度：B1 ✅（2026-06-12，含 ScopedRegistry 工具面收紧与契约验收优先级
> contract → deterministic → judge；弱模型无契约时行为与 v1 一致）。

### B1 PlanStep 契约 v2

- **目标**：步骤从"自然语言 intent + acceptance"升级为带约束的结构化契约。
- **任务**：
  1. `agent/state.py` PlanStep 扩展字段（全部带默认值，旧 session 可加载）：`allowed_tools: list[str]`（空=不限）、`permission_ceiling: str`（步内允许的最高权限级）、`preconditions: list[str]`（如 "checkpoint_created"、"editor_online"）、`success_checks: list[dict]`（声明式谓词，如 `{"fact": "compile_result.exit_code", "op": "==", "value": 0}`）、`required_evidence: list[str]`、`rollback_policy: str`（none/restore_checkpoint）、`step_budget: dict`（max_seconds/max_turns）。
  2. `agent/planner.py` 提示词升级让模型产出契约字段；解析容错保持现状（缺字段用默认值，解析失败回退单步——弱模型兜底不能丢）。
  3. `agent/runner.py`：步开始前查 preconditions（editor_online 用 editor_status，checkpoint 查 repo facts）；步内 loop 的工具面按 allowed_tools 过滤、权限按 ceiling 收紧；验收读 success_checks 走 A3 的确定性通道；失败按 rollback_policy 行动。
- **验收**：单测覆盖：越权工具被拒、precondition 不满足时先补救再执行、success_checks 驱动验收、rollback 触发。eval 两档不退步（重点回归：弱模型产不出完整契约时系统照常工作）。
- **依赖**：A3。

### B2 工具效果声明（Tool Effect System）

- **目标**：工具元数据从"权限级"扩展为"副作用语义"，重试治理有据可依。
- **任务**：
  1. `tools/registry.py` 工具定义增加 effects 元数据：`idempotent: bool`、`requires_checkpoint: bool`（现有 WRITE_PROJECT 自动 checkpoint 改由此驱动）、`rollback_tool: str | None`、`supports_dry_run: bool`、`resources: list[str]`（level_actors/source_files/git_index…）。
  2. 首批标注：wb_build（非幂等，rollback=wb_clear）、wb_clear（幂等）、ubt_compile（幂等）、repo_restore（非幂等，dangerous 不变）、文件写工具（非幂等，requires_checkpoint）。
  3. `tool_pipeline`：非幂等写工具触发失败签名熔断时，回传文本明确禁止原样重试，提示先查状态（如 find_actors）或换路径；FailureTracker 阈值对非幂等工具降为 2。
- **验收**：单测：非幂等工具连续失败 2 次后回传文本含禁止重试指引；requires_checkpoint 驱动与现有自动 checkpoint 行为等价。
- **依赖**：无硬依赖，可与 B1 并行。

### B3 错误分类与恢复策略表

- **目标**：recovery 从"统一重试 ≤3 次"升级为按错误类别查表的差异化策略。`[env:unready]` 是已有先例，推广成体系。
- **任务**：
  1. `core/errors.py` 定义错误 taxonomy（沿用跨进程文本标记传递的既有机制）：`env_unready`（已有）、`bridge_down`、`ubt_compile_error`、`permission_denied`、`budget_exhausted`、`evidence_missing`、`partial_side_effect`（如 spawn 部分失败）、`tool_arg_error`、`transient`。
  2. `agent/runner.py` recovery 策略表：env_unready→editor_launch 自愈一次再 fail-fast（已有，纳入表）；bridge_down→探活+重连一次；ubt_compile_error→带结构化错误进修复循环（不算 recovery 次数，算正常迭代）；permission_denied→不自动绕过，终止并报告；evidence_missing→补采证据（调 wb_validate/repo_status）而非直接 fail；partial_side_effect→先 rollback_tool 清理再重试；budget_exhausted→保存 session、产出 partial report（已有，纳入表）。
  3. MCP 工具侧按上表给错误文本打类别标记。
- **验收**：单测逐类注入错误验证策略路由；故障注入 e2e：编辑器中途杀进程 → bridge_down → 自愈重连或 fail-fast 报告（不再空转重试，踩坑史第 8 条的体系化保障）。
- **依赖**：B2（partial_side_effect 策略用 rollback_tool）。

### B4 上下文工程 v1

- **目标**：管住"模型看什么"。UE 工程上下文会爆炸，需要显式的注入/压缩/摘要策略。
- **任务**：
  1. 任务开场注入"工程状态摘要"：runner 起步时一次性探测（editor_status + repo_status + engine_info）拼成 ≤500 字摘要进 system context，省去模型自己逐个探测的轮次。
  2. 长任务 progress 文件：runner 每步收口把"已完成/当前/剩余"写 `runs/<session>/progress.md`；compact_history 触发时 progress 摘要保证保留（解决压缩后模型忘记任务进度的问题）。
  3. 工具结果摘要器：超长工具结果按工具类型摘要（编译日志→错误列表保留+正常输出折叠；actor 列表→计数+前 N 个），替代现有统一字符截断。`max_tool_result_chars` 机制保留为兜底。
- **验收**：单测：摘要器按类型路由；compact 后 progress 仍在上下文。长任务 e2e（三房间+视觉迭代）token 用量相比基线下降且不丢任务目标。
- **依赖**：无硬依赖；建议在 A4 之后做（视觉迭代是最好的长任务测试床）。

## Stage C：蓝图理解（Phase 1 收口）

细案见 [phase1-bridge-plan.md](phase1-bridge-plan.md)，此处只列顺序与验收。

- **C1 = P1.2 裁剪与分级**：上游插件蓝图编辑类/批量构建类命令不注册；保留工具按 4 级权限标级；工具清单与分级表入 phase1-bridge-plan.md。
- **C2 = P1.3 蓝图只读导出四件套**：bp_overview / bp_pseudocode（默认视图，token 约 JSON 1/5）/ bp_graph（歧义时下钻）/ bp_find_usages（AssetRegistry 依赖图）。验收：对 BP_ThirdPersonCharacter 输出可读伪代码，标准答案进 eval case。
- **C3 UE 在线 eval 档**：新建需编辑器在线的独立 suite（read_blueprint_and_explain、wb_build_and_validate、故障注入类：编辑器断连/UBT 多错误/白盒部分失败）。验收：编辑器开启时 `ue5agent eval --suite ue` 可跑分并出基线。沙盒两档继续作为离线门禁。

## Stage D：安全加固与工程化（右尺寸）

- **D1 桥与凭据安全**：
  1. TCP 桥（55557）加 localhost token 鉴权：插件启动生成随机 token 写工程 Saved/ 下文件，bridge.py 读取并在握手时出示；同时做协议版本握手（版本不匹配明确报错而非静默错乱）。
  2. secret redaction：trace/report/progress 落盘前对 .env 中的 key 值做掩码（实现在 events.py RunWriter 单点）。
  3. 工具输出注入防护（轻量）：MCP 工具结果中出现指令样文本（"ignore previous instructions" 类）时在回传文本加 `[external-content]` 围栏标记，verifier 提示词声明围栏内内容不可作为指令。
- **D2 运行锁与 CI**：
  1. 同一 UE 工程同时只允许一个 runner：`runs/` 下文件锁，冲突时明确报错（插件 TCP 本就单连接，锁是给出可读错误而非莫名拒连）。
  2. runs/ 保留策略：`ue5agent runs prune`（按数量/天数清理，artifacts 中 screenshot 可选保留）。
  3. CI：GitHub Actions 跑 ruff + 离线单测 + 沙盒 eval（roadmap Phase 3 既有项提前，纯离线无 UE 依赖）。
- **明确不做**（单机单用户工具，写入本节即决策记录）：多租户并发、容器化部署、远程任务编排、企业级 secret manager。若日后需要再立 ADR。

## Stage E：Phase 3 行为闭环（待 Stage A–C 后细化）

保持 roadmap 既有定义，开工前单独出细案（同 P1 模式）：

- PIE smoke test 与 UE Automation/Functional Test 闭环（评审指出的 UE5 E2E 剩余面：Output Log parser、Blueprint compile check、BuildCookRun smoke 归此）。
- 子代理体系（上下文隔离 + 按角色配模型；届时 vision/judge 可换专长模型，解除单一 DeepSeek 依赖）。
- 完整评测基准工程与跑分（一次通过率/迭代次数/人工干预次数）。

## 建议施工顺序

```
A1 → A2 ─┬→ A4（vision key 到位后）→ B4
A3 ──────┘
B1 → B3        （B2 与 B1 并行；B 整体可与 A4 等待 key 期间穿插）
C1 → C2 → C3   （C 可在 A 收口后、B 进行中并行，改动面不重叠）
D1、D2         （任意空档插入，单项均 ≤1 天量级）
E              （A–C 收口后细化）
```

## 当前等待用户提供

| 事项 | 阻塞 |
|---|---|
| vision 多模态模型 API key | A4 视觉审查（A4 其余子项不阻塞） |
| 正式模块化资产包 + 关卡 metrics 期望表 | manifest 自动扫描（roadmap Phase 2 备注项）；A 阶段先用 LevelPrototyping |
| 对照模型 API key（可选） | 仅多模型评测 |

已到位：DeepSeek key；UE5.7 + VS2026 + agent_test 测试工程（含 UnrealMCP 插件，git 管理）；GitHub 远端。
