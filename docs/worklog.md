# 工作日志（对话交接用）

> 最后更新：2026-06-12。新对话接手前先读本页 + [development-plan.md](development-plan.md)。
> 架构权威版：[architecture/design.md](architecture/design.md) + ADR 0001–0006。
> ✅ 最新结论：**Stage A1–A3 完成（2026-06-12）**——插件新增 viewport_screenshot/
> navmesh_rebuild/path_test 三命令（UE5.7 编译实测通过，navmesh 可自动补 NavMeshBoundsVolume）；
> wb_validate 确定性校验器（期望/实测对照，真机正负样本通过）；证据信封 v1（[facts] 标记 →
> ToolOutcome.facts → 验收两段式：确定性规则先行不调 LLM，judge 兜底）。
> eval 两档满分持平基线，165 单测全绿，mypy 零错误。Stage A 仅剩 A4 视觉迭代（等 vision key）。

## 项目一句话

ue5agent：UE5 游戏开发 agent——C++ 实现功能、蓝图只读理解、白盒场景搭建、自主验证。
模型 DeepSeek（.env 有 key），宿主自研 kernel（非 Claude Code 运行时），工具层 MCP。

## 当前状态（全部已推送 github.com/LOOP486/-，HEAD=760ac8f 后另有 P2/修复提交）

| 阶段 | 状态 |
|---|---|
| Phase 0（C++ 编译闭环） | ✅ 完成。agent 自主「写 BlueprintFunctionLibrary→编译 7.51s 零错误」验收通过 |
| Kernel 重构 K0–K5 | ✅ 完成（ADR-0006）。K6 能力注册表可选未做 |
| Phase 1 编辑器桥 P1.1 | ✅ 完成。UnrealMCP 插件（flopperam fork）编译于 UE5.7+VS2026；自研瘦桥 ue_editor（4 只读工具 + A1 三验证工具） |
| P1.3 蓝图伪代码视图 | ⬜ 未做（bp_read/bp_analyze 是底料，= Stage C2） |
| P1.4 NavMesh/截图 | ✅ 完成（2026-06-12 以 Stage A1 完成：插件三命令 + 真机三房间验证） |
| Stage A2 白盒校验器 | ✅ 完成。wb_validate（缺件/多件/漂移/穿插 + metrics），真机正负样本通过 |
| Stage A3 证据信封 | ✅ 完成。[facts] → ToolOutcome.facts → trace；验收两段式（deterministic 优先） |
| Stage B1 PlanStep 契约 | ✅ 完成。allowed_tools/ceiling/preconditions/success_checks/rollback/step_budget；验收优先级 contract→deterministic→judge |
| Phase 2 白盒 | ✅ 完成：manifest + 布局 DSL 编译器（含门图连通性校验）+ wb_build/wb_clear MCP 工具。崩溃根因已根治（spawn 改运行唯一名，踩坑史第 7 条），**三房间完整 agent 端到端核验已通过**（run 20260611-222536：3 次 build 含"清旧建新"场景全程不崩、verify=pass、崩溃目录零新增） |

## 环境清单

- 引擎 `C:/Program Files/Epic Games/UE_5.7`；VS2026 Community；测试工程 `C:/Users/chengpeixin/Documents/Unreal Projects/agent_test`（git 管理，含 UnrealMCP 插件）
- 仓库内：config/models.yaml + agent.yaml + .env（均不入库，已配好）；MCP servers：ue_build / repo_tools / ue_editor / ue_whitebox
- 用户入口：双击 ue5agent-chat.bat 或 `uv run ue5agent run "任务"`；trace 回放 `uv run ue5agent trace`
- 128 个单测全绿；评测 `uv run ue5agent eval`（basic+hard 双档基线满分，evals/baselines/）

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

## 下一步（按序）

**2026-06-12 起以 [development-plan.md](development-plan.md) 的 Stage A–E 为准**（吸收外部架构评审后全面修订，含逐里程碑任务分解与验收标准）。接下来的施工顺序：

1. **A1**：UnrealMCP 插件加 C++ 命令（viewport_screenshot / navmesh_rebuild / path_test），编译后需重启编辑器（原 P1.4）。
2. **A2**：白盒确定性校验器 `whitebox/validator.py` + `wb_validate` 工具（重叠/封闭/连通/metrics）。
3. **A3**：证据信封 v1（ToolOutcome.facts + verifier 确定性规则先行）。可与 A2 并行。
4. **A4**：视觉迭代闭环 = Phase 2 终验（vision key 到位前先做截图存档子项）。
5. 之后按 Stage B（kernel 体系化）/ C（蓝图四件套）/ D（安全与工程化）推进。

历史备忘：白盒三房间端到端核验已通过（run `20260611-222536`，3 次 wb_build 含"清旧建新"必崩场景全程无 Fatal/无 10061、verify=pass）。回归保护：tests/test_whitebox_spawn.py + 该 run 基线。若日后又崩，立刻读 `agent_test/Saved/Crashes/` 最新日志末尾的 Fatal 栈（别只看 trace 的 10061 拒连，那是次生现象）。体验项备忘：输出风格规范（列表类先汇总后明细）、evals 输出完整性断言档。

## 文档地图

development-plan.md（里程碑总账）/ kernel-refactor-plan.md（K0–K6 细案）/ phase1-bridge-plan.md（P1）/ roadmap.md（勾选清单）/ evals/baselines/（基线）/ CLAUDE.md（仓库开发约定与硬性规则）
