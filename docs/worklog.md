# 工作日志（对话交接用）

> 最后更新：2026-06-12（晚，按里程碑拆分提交收口）。新对话接手前先读本页 + [development-plan.md](development-plan.md)。
> 架构权威版：[architecture/design.md](architecture/design.md) + ADR 0001–0006。
> ✅ 最新结论：**Stage A（含 A4 视觉迭代）、Stage B（B1–B4）、C1、C2（差一个插件命令）、
> Stage D 离线项全部完成（2026-06-12）**——Phase 2 终验「三房间死斗」全链路真机 e2e 通过
> （搭建→截图→Kimi 视觉审查→wb_validate→navmesh→path_test 三对房间可达，全程无人值守）。
> 265 单测全绿，mypy 零错误，eval 两档满分持平基线。Stage E（Phase 3）细案已就绪
> （[stage-e-plan.md](stage-e-plan.md)），剩余工作集中在"UE 在线 + 插件 C++"一个批次。

## 项目一句话

ue5agent：UE5 游戏开发 agent——C++ 实现功能、蓝图只读理解、白盒场景搭建、自主验证。
模型 DeepSeek（planner/coder）+ Kimi/Moonshot kimi-k2.6（vision，.env 均有 key），
宿主自研 kernel（非 Claude Code 运行时），工具层 MCP。

## 当前状态（已推送 origin/main；A4/B3/B4/C1-C2/D 按里程碑拆分提交，每个提交点单测/ruff 单独验证过）

| 阶段 | 状态 |
|---|---|
| Phase 0（C++ 编译闭环） | ✅ 完成。agent 自主「写 BlueprintFunctionLibrary→编译 7.51s 零错误」验收通过 |
| Kernel 重构 K0–K5 | ✅ 完成（ADR-0006）。K6 能力注册表可选未做 |
| Phase 1 编辑器桥 | ✅ P1.1 链路 + C1 裁剪分级 + C2 只读导出（bp_overview/bp_pseudocode 控制流伪代码/bp_graph；bp_find_usages Python 侧就绪，差插件 find_blueprint_references 命令） |
| Phase 2 白盒（Stage A 全部） | ✅ **完成并真机终验**：manifest + DSL 编译器 + wb_build/wb_clear + wb_validate 校验器 + 证据信封 + A4 视觉迭代闭环（截图→Kimi 审查→问题区域回灌重生成）。「三房间死斗」全链路 e2e 通过 |
| Stage B（kernel 体系化） | ✅ B1 契约 v2 / B2 工具效果声明 / B3 错误分类与恢复策略表 / B4 上下文工程（工程摘要注入 + progress.md + 工具结果摘要器）全部完成 |
| Stage D（安全与工程化） | ✅ 离线项全部完成：secret 掩码、注入围栏、桥鉴权客户端侧（protocol+token）、运行锁、runs prune、CI（ruff+format+mypy+pytest）。仅 D1.1 服务端待插件 C++ |
| Stage E（Phase 3） | ⬜ 细案就绪（stage-e-plan.md）：E1 PIE/Functional Test（真机）、E2 子代理（可离线先行）、E3 完整基准+UE 在线 eval |

## 环境清单

- 引擎 `C:/Program Files/Epic Games/UE_5.7`；VS2026 Community；测试工程 `C:/Users/chengpeixin/Documents/Unreal Projects/agent_test`（git 管理，含 UnrealMCP 插件）
- 仓库内：config/models.yaml + agent.yaml + .env（均不入库，已配好；vision=moonshot/kimi-k2.6，provider params 注入 temperature=1）；5 个 MCP server：ue_build / repo_tools / ue_editor / ue_whitebox / ue_lifecycle
- 用户入口：双击 ue5agent-chat.bat 或 `uv run ue5agent run "任务" --yes`；trace 回放 `uv run ue5agent trace`；清理旧运行 `uv run ue5agent runs prune`
- 265 个单测全绿；评测 `uv run ue5agent eval`（basic+hard 双档基线满分，evals/baselines/）

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

## 下一步（按序）

Stage A–D 已收口（仅剩真机项），**以 [stage-e-plan.md](stage-e-plan.md) 为准**：

1. **下次 UE 在线会话一次性推进（同属一次插件 C++ 改动 + 重编译，合并省循环）**：
   - C2 收尾：插件新增 find_blueprint_references（AssetRegistry 引用查找）→ bp_find_usages 生效；
   - D1.1 服务端：插件启动生成 token 写 `Saved/ue5agent_bridge_token.txt` + 握手校验 token/protocol
     （客户端侧已就绪：bridge.py PROTOCOL_VERSION / UE_MCP_TOKEN[_FILE]）；
   - E1：pie_smoke / run_functional_test / output_log_tail 三命令 + 证据/恢复接入。
2. **E2 子代理体系**：可离线先行（独立 history + ScopedRegistry + 角色级模型，只回摘要）。
3. **E3 = C3 + 完整基准**：eval 框架支持 MCP+编辑器在线执行路径，`ue5agent eval --suite ue` 出基线。

历史备忘：白盒三房间端到端核验已通过（run `20260611-222536`，3 次 wb_build 含"清旧建新"必崩场景全程无 Fatal/无 10061、verify=pass）。回归保护：tests/test_whitebox_spawn.py + 该 run 基线。若日后又崩，立刻读 `agent_test/Saved/Crashes/` 最新日志末尾的 Fatal 栈（别只看 trace 的 10061 拒连，那是次生现象）。体验项备忘：输出风格规范（列表类先汇总后明细）、evals 输出完整性断言档。

## 文档地图

development-plan.md（里程碑总账）/ kernel-refactor-plan.md（K0–K6 细案）/ phase1-bridge-plan.md（P1）/ roadmap.md（勾选清单）/ evals/baselines/（基线）/ CLAUDE.md（仓库开发约定与硬性规则）
