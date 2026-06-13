# Changelog

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [未发布]

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
