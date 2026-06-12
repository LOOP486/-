# Changelog

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [未发布]

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
