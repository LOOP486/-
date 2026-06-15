# 工作日志（对话交接用）

> 最后更新：2026-06-15（B7 白盒 agent 侧布局 guardrail + MCP 断链重连/视觉步骤早停 + focus 截图前景裁剪；当前 483 个单测全绿）。
> 新对话接手前先读本页 + [development-plan.md](development-plan.md)。
> 架构权威版：[architecture/design.md](architecture/design.md) + ADR 0001–0006。
> ✅ 最新结论：**Stage A–D 全收口；Stage E（E1/E2/E3）全部真机收口**。
> 本轮（真机，编辑器在线）：
> - **E1 收口**：插件 C++ 新增 functest_start/functest_poll/functest_list（一次编译落地，
>   StartTestByName + 跨帧 ExecuteLatentCommands + StopTest + GetValidTestNames）；ue_editor 新增
>   `run_functional_test`(write_project，跨帧轮询编排+functional_test 事实) 与 `functest_list`(read)。
>   真机验证：functest_list 列 4854 测试、FFColorSmokeTest 真跑通 passed=true、负向名报 not found。
> - **E3 真机出基线**：`eval --suite ue --out evals/baselines/ue/deepseek-2026-06-13.json` →
>   4/4 通过、一次通过率 100%、平均迭代 1.5、人工干预 0（含 run_functional_test 用例）。
>   故障注入复核：杀编辑器后单跑 → env_unready → 1 次尝试快速终止（13s，不空转）。
> 483 单测全绿（`uv run pytest -q`），ruff format/check、mypy 与 check-config 全绿。
> **插件改动尚未提交 agent_test git**（functest 三命令在 EpicUnrealMCPEditorCommands.*/Bridge.cpp）；
> 本轮追加 viewport_screenshot clean/focus 真机修复并已编译验证；下次可 commit。
> 编辑器本轮被重启过，结束时已重新拉起在线。
> B7 UE 在线标准结构档已归档：
> `evals/baselines/ue/space-agent-test-20260615-205313.json`，SPC/DST 6/6 通过，
> pass_rate=1.0，first_try_pass_rate=0.8333，平均迭代 3.0，人工干预 0。前序复跑暴露白盒
> agent 对外墙窗/共享墙门洞仍会靠猜；本轮已停止继续收窄测试题面，改为补通用构型守则、
> LayoutError 恢复提示，以及 `wb_build` 派发前轻量布局 guardrail（删除共享墙窗、补齐单侧共享
> 门洞、收拢越界楼梯 footprint）。视觉 baseline 后续作为验证项保留；UE eval 表格/JSON 现带
> `failure_type`，可直接区分 LLM timeout、视觉、布局/几何、环境等失败类型。
> 视觉档复跑首个 SPC1V 已搭建并 `wb_validate` PASS，但 UE 当前处于“恢复包”窗口、没有活动
> Level Editor 视口，`viewport_screenshot` 返回 `No active editor viewport`；已改为
> `env_unready` 快速分类，避免 agent 连续重试截图参数导致 70k tokens 级 history 膨胀。
> UE 恢复正常视口后，小步复跑又暴露 `McpError: Connection closed` 未透明重连、视觉步骤完成后
> coder 在同一步漂移到下一阶段的问题；已修 MCP client 断链重连与 runner 视觉证据齐备早停。
> 后续聚焦截图探针又发现：即使 `focus_prefix` 已居中当前白盒，宽屏视口仍可能带入相邻旧测试结构；
> 已在 `viewport_screenshot` wrapper 侧追加前景连通域裁剪，仅在 `focus_prefix` 场景保留居中的当前主体，
> 避免 vision 把旧批次误判成本次布局问题。视觉 baseline 仍作为后续验证项，不再靠反复长跑测试收敛。
> 后续受控重跑首个视觉任务确认 crop 已生效，但 vision 把 `path_length` 这类非视觉指标提前判 high，
> 同时自动 focus 未覆盖模型手写相机导致截图上下贴边；已修为视觉审查只阻断截图可见的 blockout
> 空间问题，导航/path_length 继续由 facts 验收，runner 自动 focus 时会丢弃手写 `location`/`rotation`，
> 贴边图也会被本地快检拦下。
> 再次受控重跑已确认首个视觉任务通过截图/vision gate；随后导航步骤暴露 `navmesh_rebuild`
> 因 git checkpoint 前置失败被连续拒绝。已把 `navmesh_rebuild` 标成无需 git checkpoint 的
> `write_project` 工具（权限仍在，git 快照不再阻断编辑器运行态 NavMesh 构建）。

## 项目一句话

ue5agent：UE5 游戏开发 agent——C++ 实现功能、蓝图只读理解、白盒场景搭建、自主验证。
模型 DeepSeek（planner/coder）+ Kimi/Moonshot kimi-k2.6（vision，.env 均有 key），
宿主自研 kernel（非 Claude Code 运行时），工具层 MCP。

## 当前状态（已推送 origin/main；A4/B3/B4/C1-C2/D 按里程碑拆分提交，每个提交点单测/ruff 单独验证过）

| 阶段 | 状态 |
|---|---|
| Phase 0（C++ 编译闭环） | ✅ 完成。agent 自主「写 BlueprintFunctionLibrary→编译 7.51s 零错误」验收通过 |
| Kernel 重构 K0–K5 | ✅ 完成（ADR-0006）。K6 能力注册表可选未做 |
| Phase 1 编辑器桥 | ✅ **全收口**：P1.1 链路 + C1 裁剪分级 + C2 蓝图四件套（bp_overview/bp_pseudocode/bp_graph/bp_find_usages，2026-06-13 真机验证「谁在用它」） |
| Phase 2 白盒（Stage A 全部） | ✅ **完成并真机终验**：manifest + DSL 编译器 + wb_build/wb_clear + wb_validate 校验器 + 证据信封 + A4 视觉迭代闭环（截图→Kimi 审查→问题区域回灌重生成）。「三房间死斗」全链路 e2e 通过 |
| Stage B（kernel 体系化） | ✅ B1 契约 v2 / B2 工具效果声明 / B3 错误分类与恢复策略表 / B4 上下文工程（工程摘要注入 + progress.md + 工具结果摘要器）全部完成 |
| Stage D（安全与工程化） | ✅ **全收口**：secret 掩码、注入围栏、桥鉴权（D1.1 客户端+服务端 2026-06-13 真机）、运行锁、runs prune、CI |
| Stage E（Phase 3） | ✅ **全部真机收口**：E1（pie_smoke/output_log_tail/run_functional_test）、E2 子代理、E3 UE 在线 eval（首份基线 4/4 通过）。仅"更多故障注入用例"为持续补充项 |

## 环境清单

- 引擎 `C:/Program Files/Epic Games/UE_5.7`；VS2026 Community；测试工程 `C:/Users/chengpeixin/Documents/Unreal Projects/agent_test`（git 管理，含 UnrealMCP 插件）
- 仓库内：config/models.yaml + agent.yaml + .env（均不入库，已配好；vision=moonshot/kimi-k2.6，provider params 注入 temperature=1）；5 个 MCP server：ue_build / repo_tools / ue_editor / ue_whitebox / ue_lifecycle
- **D1.1 桥鉴权已启用**：插件启动写 `agent_test/Saved/ue5agent_bridge_token.txt`，.env 配
  `UE_MCP_TOKEN_FILE` 指向它，客户端握手自动出示；裸 `send_command`（不经 cli load_dotenv）会被拒，
  调试时需手动带 `UE_MCP_TOKEN_FILE` 环境变量。插件源码在 agent_test 仓库（独立 git）。
- 用户入口：双击 ue5agent-chat.bat 或 `uv run ue5agent run "任务" --yes`；trace 回放 `uv run ue5agent trace`；清理旧运行 `uv run ue5agent runs prune`
- 483 个单测全绿；评测 `uv run ue5agent eval`（basic+hard 双档基线满分，evals/baselines/）；
  UE 在线档 `uv run ue5agent eval --suite ue`（需编辑器在线，离线探活失败即退出不跑分；
  首份基线 evals/baselines/ue/deepseek-2026-06-13.json；B7 SPC/DST 标准结构 baseline
  evals/baselines/ue/space-agent-test-20260615-205313.json 已归档 6/6，通过率 100%；视觉 baseline
  后续验证）
- 插件重编译流程：关编辑器（DLL 锁）→ `run_build(engine, uproject, 'agent_testEditor')` → 重启编辑器
  → 轮询 probe_editor。本会话首编 19.8s、增量 9.5s。

## 踩坑史（同类问题先查这里）

1. **子进程必须有超时 + 输出落临时文件**：Windows 孙进程（git fsmonitor/UBA/dotnet）继承管道句柄会让 capture_output 永久阻塞。已修 ubt.py 与 gitops.py（60s/600s + taskkill /T + 禁 core.fsmonitor）。UBT 的 git 工作集探测已全局禁用（%APPDATA%/Unreal Engine/UnrealBuildTool/BuildConfiguration.xml）。
   - **1b（2026-06-11 晚补）git 子进程还要切断 stdin**：gitops._git 的 Popen 原先没重定向 stdin，git 继承被 MCP stdio 协议占用的 stdin、等待交互输入（凭据/提示）而挂死 60s，超时被 is_git_repo 误判成"不是 git 仓库"（三房间白盒诊断时发现：agent_test 本就是 git 仓库，repo_tools 却全报非仓库）。已修：Popen 加 stdin=subprocess.DEVNULL + 注入 GIT_TERMINAL_PROMPT=0/GIT_ASKPASS=echo/GCM_INTERACTIVE=Never/GIT_OPTIONAL_LOCKS=0。验证 is_git_repo 0.2s 秒回，test_gitops.py 全过。是第 1 条的遗漏分支（当时只管了 stdout）。
2. **预算三层闸**：loop 墙钟（30min/步内 5min）+ TaskRunner 总预算 20min + 步级尝试 3 次。任何任务最坏 20min 出报告。
3. **MCP 子进程要显式传 env**（mcp_client 已传 os.environ），否则 UE_ENGINE_ROOT 等丢失。
4. 插件 find_actors_by_name 是**子串匹配**非通配符；插件 TCP **单连接**，任务占用时旁路连接被拒。
5. judge 证据窗口 800 字符；查询类任务以最终答复为交付物（verifier 提示词已区分修改类/查询类）。
6. 用户机器禁用了 Intel NPU（编辑器崩溃修复），与构建无关勿改回。MSVC 输出是中文本地化，解析器已兼容。
7. **白盒 spawn 必须用运行唯一名，绝不复用旧名——这是反复崩编辑器的真根因（2026-06-11 引擎日志确诊）**：
   - 崩溃铁证（`agent_test/Saved/Crashes/UECC-...416E.../agent_test.log` 末尾）：`delete_actor WB_Hall_east_0→success` → `find WB_→[]` → `find WB_Hall_floor→[]` → `spawn_actor WB_Hall_floor` → `Fatal error: LevelActor.cpp:585 Cannot generate unique name for 'WB_Hall_floor'`。
   - 机制：UE 的 `DestroyActor` 是"标记销毁 + 延迟 GC"。delete 后 actor 当帧即从 level 列表移除，故 `find_actors_by_name` **立刻查不到**（返回空，看似删净）；但该对象 + FName 在 GC 真正回收前仍占命名空间。delete 与 spawn 仅隔约 233ms，GC 远未发生，spawn 复用同名命中引擎硬 check → Fatal。
   - **致命陷阱**：任何依赖 `find` 的"删后复查 / spawn 前重名预检"对这种"僵尸名"天然失效（Python 侧视图 ≠ 引擎命名空间）。前一版按此思路修，崩溃照样复现——别再走回头路。
   - 根治（已实施，spawner.py v3）：spawn 名 = `WB_<批次时间戳base36>_<构件名>`（`_batch_token()`），新名在引擎命名空间必然空闲。clear 仍按 `WB_` 前缀整批回滚（唯一名仍带前缀可清，但 clear 只为防场景堆积，不再承担防崩职责）。
8. **"卡很久没反应"的来源**：编辑器崩溃后桥端口关闭，agent 不会立即退出，而是对死掉的编辑器反复重试（每次 `WinError 10061 拒绝连接`，掺杂跑偏去编译 C++），把时间耗在无效重试上。根因（第 7 条）解决后不再触发崩溃，自然不再死循环；若仍见大量 10061，先确认编辑器进程是否还活着（`Get-Process *UnrealEditor*` + 探 55557 端口）。
9. **任何异常都不准从"assistant 发出 tool_calls"到"tool 回包入列"之间逃逸**（2026-06-12 e2e 确诊）：一旦逃逸，history 里的 tool_calls 永远缺回包，该会话**之后每次** LLM 请求都被 API 拒（`insufficient tool messages following tool_calls`），步骤重试三次全空耗且报错样子像 API 故障。首个实例：非交互运行（bash 后台）下 WRITE_PROJECT 触发 `typer.confirm`，无 TTY 抛 Abort 从 gate 逃逸。已修三层：CLI 无 TTY 不挂确认器、pipeline 兜住 gate 一切异常转 [denied]、loop 调度异常转 [error] 仍回包（tests/test_loop.py::test_dispatch_exception_still_answers_tool_call）。若再见 BadRequestError 连发，先查 trace 里最后一个 llm_turn 是否带 tool_names 而其后无 tool_call 事件。
   - **9b：TTY 启发式在 Git Bash/pty 包装下会误判**（同日二次实测）：单看 `sys.stdin.isatty()` 在 bash 后台仍为 True，确认器被挂上、Abort 被兜住返回 False → write_project 全部被拒（报"未获用户确认"）。已改 stdin+stdout 双检，且 `ue5agent run` 加 `--yes/-y`——**脚本化/agent 调用 run 一律带 --yes**，别赌启发式。
10. **白盒前缀纪律：异前缀残留是隐形杀手**（2026-06-12 契约 e2e 确诊）：模型自创 spawn 前缀（S1_）后任务 aborted，残留构件没人清（重建语义只清同前缀；当时回滚也按默认 WB 清）。下一个任务在同一 origin 落 WB_ 布局，**S1_ 旧墙正好横在新门洞上** → navmesh 在门处断开、path_test 全 partial；模型把现象误诊为 agent radius（看起来很合理！）。而 wb_validate 当时只查本前缀构件，对异前缀残留全盲——校验 PASS 但场景实际坏了。已修三处：① 契约自洽（success_checks 要求的验证工具自动并入 allowed_tools，planner._reconcile_contract）；② 回滚按实际前缀清（wb_build facts 带 prefix，runner 回滚读取）；③ wb_validate 宽查询 + 异前缀残留检测（与布局区域重叠的旧批次构件 → violation 并给 wb_clear 指引）。**经验：可达性异常先查场景里有没有别的批次的墙，再怀疑导航参数。**
11. **本机终端跑 pytest 会 OSError 22 静默失败（exit 1 零输出）**：pytest 的 terminal writer 在某些包装终端（agent 工具调用、重定向）下写 stdout 报 Invalid argument，且错误本身也打不出来——看起来像"测试坏了但没有任何信息"。规避：`uv run python scripts/run_tests.py`（进程内重定向 stdout/stderr 到 runs/pytest_out.txt 再跑 pytest，读文件看结果）。ruff/mypy/uv 不受影响。直接在真终端（Windows Terminal/PowerShell 窗口）跑则正常。
12. **多模态调用会冻死事件循环**（A4 真机确诊，litellm + moonshot 端点）：litellm 的调用阻塞 asyncio 事件循环且不遵守自身 timeout，整个 run 无限冻结（wall budget 在步边界检查，拦不住步内挂起）。修法成对出现：LLM 调用放工作线程（asyncio.to_thread + 线程内独立事件循环），外层用 asyncio.wait（**不是 wait_for**——await 不可取消的执行器 future 仍会卡死）做硬超时，超时降级。同病防复发：截图送审前降采样到长边 ≤1280 + JPEG 重压，单次最多 3 张。
13. **桥命令不能在单条调用里阻塞 GameThread 跑长任务**（E1 设计教训）：插件 `ExecuteCommand` 把 handler 丢到 GameThread 并 `Future.Get()` 同步等结果——handler 里 sleep N 秒，PIE/世界就停止 tick（PIE 也跑在 GameThread）。所以"跑 N 秒"的命令必须拆成 `pie_start`（立即返回）+ 客户端等待 + `pie_stop`，等待发生在 MCP 进程而非引擎线程。run_functional_test 同理（Automation 跨帧），后续也走 start/poll。
14. **GLog 自定义 OutputDevice 会收到控制消息**（E1 真机）：SetColor 等控制消息掩码 `& VerbosityMask` 后落到 NoLogging=0，若只判 `Level > Display` 会漏过、被当 VeryVerbose 混入结果。正确过滤是 `Level < Fatal || Level > Display`（只留 [Fatal,Display] 真实级别）。"某段窗口内新增的错误"要用单调序号快照（环形缓冲裁剪会让下标失效），不能用下标或全量 tail。

## 下一步（按序）

Stage A–D 全收口、E2 离线 + E1/C2/D1.1 真机完成，**以 [stage-e-plan.md](stage-e-plan.md) 为准**：

1. **run_functional_test**（真机，插件 C++）：UE Automation/Functional Test——同需 PIE 异步会话
   （start/poll，见踩坑史第 13 条），最重最不确定，单独做。
2. **E3 = C3 + 完整基准**（真机）：eval 框架支持 MCP+编辑器在线执行路径，`ue5agent eval --suite ue`
   出基线；E1 完整 e2e（会报错的关卡→pie_smoke 读 Error→修复→复跑零 Error）并入 UE suite 用例。
   E2 真机 token 下降量化对照也在此测。
3. **pie_smoke 增强**（可选）：map 参数（先 OpenLevel 再 PIE，当前只跑当前关卡）。

历史备忘：白盒三房间端到端核验已通过（run `20260611-222536`，3 次 wb_build 含"清旧建新"必崩场景全程无 Fatal/无 10061、verify=pass）。回归保护：tests/test_whitebox_spawn.py + 该 run 基线。若日后又崩，立刻读 `agent_test/Saved/Crashes/` 最新日志末尾的 Fatal 栈（别只看 trace 的 10061 拒连，那是次生现象）。体验项备忘：输出风格规范（列表类先汇总后明细）、evals 输出完整性断言档。

## 文档地图

development-plan.md（里程碑总账）/ kernel-refactor-plan.md（K0–K6 细案）/ phase1-bridge-plan.md（P1）/ roadmap.md（勾选清单）/ evals/baselines/（基线）/ CLAUDE.md（仓库开发约定与硬性规则）
