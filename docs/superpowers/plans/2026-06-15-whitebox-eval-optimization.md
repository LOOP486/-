# Whitebox Eval Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 2026-06-14 两轮 SPC/DST 白盒评测暴露的问题转成可执行的修复计划，稳定默认 slab 空间生成、视觉检测和在线 eval 报告。

**Architecture:** 保持现有 DSL 与 TaskRunner 架构不变，优先补几何编译器/validator 的硬约束，再收紧 agent 构建提示、截图取证和 eval 分类。所有修复都要从 trace、截图和现有 facts 出发，用小范围单测锁住。

**Tech Stack:** Python 3、pytest、UE MCP tools、白盒 DSL、TaskRunner、LiteLLM、PowerShell/uv。

---

## 复盘范围

- 第一轮结构/导航测试：`evals/baselines/ue/space-agent-test-20260614-221045.json`
- SPC2 单独重跑：`evals/baselines/ue/space-agent-test-spc2-20260614-223449.json`
- 第二轮视觉检测测试：`evals/baselines/ue/space-agent-test-visual-round2-facts-20260614-234248.json`
- 重点截图拼图：`C:/Users/chengpeixin/AppData/Local/Temp/ue_round2_contact_sheet.jpg`
- 楼梯问题重点 trace：`runs/20260614-235117_slab_single_level_stairw/trace.jsonl`

## 问题列表

### P0 真实几何问题

1. 墙体端点交接没有按墙厚补偿。
   - 现象：墙端点都延伸到轴线交点，外角处出现亮边/错位。
   - 正确行为：交接处一面墙前伸 `1/2 wall_thickness`，另一面墙后退 `1/2 wall_thickness`，外轮廓刚好补齐。
   - 风险：视觉上形成缝、重叠边或多余墙角，也会干扰 ring/crossfire 这类短墙较多的布局。

2. 楼梯间开口与护墙生成不稳定。
   - 现象：SPC3V 过程中出现“楼梯穿墙”“楼梯井越界”“楼梯井护墙 west/east 重叠”；最终通过 validate 的版本仍在楼梯旁切出很窄的小空间。
   - 证据：`runs/20260614-235117_slab_single_level_stairw/trace.jsonl` 中多次 `wb_build` error 和 `parallel_wall_duplicate_count`。
   - 风险：validator 只检查数量/重复墙，没检查楼梯开口方向、护墙侧边净空和小夹缝。

### P0 Agent 稳定性问题

3. coder LLM 经常在首轮布局生成阶段超时。
   - 现象：SPC2、SPC2V 都是 execute/s1 的 coder 请求卡满 120s，0 次工具调用。
   - 证据：`LLMUnavailable: 角色 coder 的全部模型不可用：deepseek/deepseek-v4-pro（TimeoutError）`。
   - 根因链：UE eval 使用 `max_retries=1` 和 `request_timeout_seconds=120.0`；执行阶段异常在 `TaskRunner` 中直接 abort，不走 step retry；复杂白盒首轮经常输出 7000-9000 completion tokens。

4. 视觉失败后的重试携带过长 history。
   - 现象：SPC1V 首次 s1 已累计约 59k prompt tokens 和 12.8k completion tokens；视觉失败后重试更容易触发 120s 超时。
   - 风险：视觉闭环越工作，重试 prompt 越重，失败概率反而升高。

### P1 视觉检测与截图取证问题

5. 截图环境会污染视觉判断。
   - 现象：坐标轴/gizmo 出现在截图里；相邻旧测试结构进入画面。
   - 风险：视觉模型把 overlay 或邻近结构当成布局缺陷，导致 high/medium 误报。

6. 视觉 gate 过严。
   - 现象：第二轮 4 个用例结构和导航已成功，但因为 `issue_count > 0` 仍全部判失败。
   - 建议：high severity 才阻断自动收口；medium/low 写入报告和待复核项。

7. 视觉提示词会要求非白盒阶段细节。
   - 现象：楼梯踏步/扶手、门窗细节、房间具体标签等被列为 medium。
   - 风险：把 blockout 阶段不要求的表现细节混入生成质量判断。

### P1 Agent 构建策略问题

8. 门洞/共享墙对齐仍依赖模型自觉。
   - 现象：第一轮 SPC1 初版门没成对或相邻房间没有真正共享墙，导致房间不连通。
   - 风险：模型会先犯结构错误，再依赖 `wb_build` 报错修复；对复杂布局耗时较高。

9. 楼梯 footprint 与房间边界缺少 agent 侧预检查。
   - 现象：SPC3V 多次把楼梯放到穿墙或越界位置。
   - 风险：楼梯类任务需要多次重建，增加 LLM 超时和场景残留风险。

10. DST2V 的十字/侧袋语义一开始不够明确。
    - 现象：前两版视觉判定侧袋不存在或不清楚；后续重建才改善。
    - 风险：DSL 本身能表达，但 agent 对“十字形/侧袋”的构型模板不够稳定。

### P2 Eval 报告与可观测性问题

11. 报告没有区分失败类型。
    - 现象：LLM 超时、视觉 high、视觉 medium、真实几何 bug 都落成“验收未通过”。
    - 风险：复盘时需要手工读 trace，无法快速判断回归来源。

12. LLM 请求超时前没有 start 事件。
    - 现象：超时样本只能看到 phase_exit/verify_result，没有该请求的 prompt 规模和模型信息。
    - 风险：无法量化是模型慢、prompt 大、输出长还是 provider 抖动。

## 优化策略

### Task 1: 墙体端点厚度补偿

**Files:**
- Modify: `src/ue5agent/whitebox/compiler.py`
- Test: `tests/test_whitebox.py`

- [x] **Step 1: 写失败测试**

在 `tests/test_whitebox.py` 中新增一个 slab L/T 字墙交接测试，断言相交墙段的目标 AABB 只互补半墙厚，不都冲到轴线交点。

Run: `uv run pytest tests/test_whitebox.py -q`
Expected: 新测试失败，暴露当前端点补偿错误。

- [x] **Step 2: 实现端点补偿**

在 slab wall run 编译阶段区分同一交点上的水平/垂直墙：一个方向延伸 `wall_thickness / 2`，另一个方向缩回 `wall_thickness / 2`，保持外轮廓闭合。

- [x] **Step 3: 验证**

Run: `uv run pytest tests/test_whitebox.py tests/test_whitebox_validator.py -q`
Expected: 新测试和既有墙体/并列墙测试全部通过。

### Task 2: 楼梯间开口与小夹缝约束

**Files:**
- Modify: `src/ue5agent/whitebox/compiler.py`
- Modify: `src/ue5agent/whitebox/validator.py`
- Test: `tests/test_whitebox_vertical_gameplay.py`
- Test: `tests/test_whitebox_validator.py`

- [x] **Step 1: 写失败测试**

新增一个 SPC3V 等价布局测试：楼梯 facing east/west 时，楼梯井护墙不得互相重叠、不得越界、不得在楼梯侧边留下小于 `wall_thickness` 或小于 1 格的夹缝空间。

Run: `uv run pytest tests/test_whitebox_vertical_gameplay.py -q`
Expected: 当前楼梯间生成策略无法满足新增断言。

- [x] **Step 2: 修正护墙生成**

在 `_compile_stairwell_guards_with_grid` 中按楼梯朝向明确：
- 楼梯上下通行方向两端保持开口。
- 两侧护墙只沿楼梯 footprint 两侧生成。
- 护墙端点按墙厚补偿，不能把楼梯外侧切成无法通行的小空间。

- [x] **Step 3: 增加 validator 诊断**

为楼梯间增加 metrics：
- `stairwell_overlap_count`
- `stairwell_out_of_bounds_count`
- `stairwell_sliver_count`

Run: `uv run pytest tests/test_whitebox_vertical_gameplay.py tests/test_whitebox_validator.py -q`
Expected: 楼梯类测试全部通过，SPC3V 类问题能被结构化 facts 暴露。

### Task 3: LLM 超时分类与重试策略

**Files:**
- Modify: `src/ue5agent/llm/client.py`
- Modify: `src/ue5agent/agent/runner.py`
- Modify: `src/ue5agent/cli.py`
- Modify: `src/ue5agent/evals/ue_suite.py`
- Test: `tests/test_llm_client.py`
- Test: `tests/test_runner.py`
- Test: `tests/test_evals.py`

- [x] **Step 1: 写失败测试**

新增测试覆盖：coder 阶段 `LLMUnavailable(...TimeoutError...)` 应记录为 `llm_timeout`，UE eval 报告应显示失败类型，而不是只写“验收未通过”。

Run: `uv run pytest tests/test_runner.py tests/test_evals.py -q`
Expected: 新测试失败。

- [x] **Step 2: 增加请求开始事件**

在 `AgentLoop` 调用 `llm.acomplete` 前写入 `llm_request_start`，包含 role、turn、message_count、estimated_chars、tool_count。超时后 trace 仍能看到请求规模。

- [x] **Step 3: 调整 eval fail-fast 策略**

UE eval 保留总时长可控，但把 coder 文本模型超时从“一次 abort”改为可配置：
- 默认 `request_timeout_seconds=180`
- `max_retries=2`
- 只对 `LLMUnavailable` 且 reason 含 `TimeoutError` 的情况允许同 step 重试一次

- [x] **Step 4: 验证**

Run: `uv run pytest tests/test_llm_client.py tests/test_runner.py tests/test_evals.py -q`
Expected: timeout 分类和 retry 行为测试通过。

### Task 4: 压缩白盒生成输出

**Files:**
- Modify: `src/ue5agent/agent/runner.py`
- Modify: `src/ue5agent/agent/planner.py`
- Test: `tests/test_runner.py`

- [x] **Step 1: 写失败测试**

新增测试：白盒 `wb_build` 步骤提示应明确“少写解释，直接调用工具；最终报告再总结”，并在视觉重试时只注入视觉问题摘要。

Run: `uv run pytest tests/test_runner.py -q`
Expected: 新测试失败。

- [x] **Step 2: 调整白盒执行提示**

对含 `wb_build` 的步骤追加执行约束：
- 不要在工具调用前展开完整设计说明。
- 优先一次性调用 `wb_build`。
- layout_json 已由 trace artifact 保存，回复中不要重复粘贴完整 JSON。
- 先按整数格推导 `room.rect` 邻接表；共享墙门洞必须双侧成对且 `at/width` 对齐。
- `windows` 只写在明确外轮廓墙；不确定时宁可不写，结构/导航任务可完全省略窗。
- `wb_build` 报布局校验错误时，退回更简单的正交连通布局，删除非必要窗并重新成对校准共享墙门洞，
  而不是质疑 validator 或反复微调同一复杂布局。

- [x] **Step 3: 调整视觉重试 history**

视觉失败后重试时，不继续携带完整前一轮 assistant 长文本；保留：
- 原目标
- 最新 `wb_build.folder_root`
- 最新截图路径
- vision high issues
- 需要修正的区域

### Task 5: 截图 clean view 与邻近结构隔离

**Files:**
- Modify: `src/ue5agent/mcp_servers/ue_editor/server.py`
- Modify: `src/ue5agent/agent/runner.py`
- Modify: `agent_test/Plugins/UnrealMCP/Source/UnrealMCP/Private/Commands/EpicUnrealMCPEditorCommands.cpp`
- Test: `tests/test_ue_editor_tools.py`
- Test: `tests/test_runner.py`

- [x] **Step 1: 写失败测试**

新增 `viewport_screenshot` 参数解析测试，覆盖 `clean_view=true`、隐藏坐标轴/选中高亮的参数透传。

Run: `uv run pytest tests/test_ue_editor_tools.py -q`
Expected: 新参数尚未实现，测试失败。

- [x] **Step 2: 实现 clean view 参数**

为 `viewport_screenshot` 增加可选参数：
- `clean_view: bool = true`
- `focus_prefix: str | None = None`
- `margin: float = 1.25`

截图前根据 `focus_prefix` 计算目标 bbox，自动调整相机高度，尽量排除邻近测试体。
runner 在模型未显式传 `focus_prefix` 时，会优先使用最新 `wb_build.folder_root`
（如 `SPC1V/<batch>`）补参，避免同前缀历史批次进入截图。

- [x] **Step 3: 验证**

Run: `uv run pytest tests/test_ue_editor_tools.py -q`
Expected: 参数与 facts 均通过。

追加验证：`uv run pytest tests/test_runner.py tests/test_vision_review.py -q`，
并真机验证 `FOCUSVERIFY/<batch>` 精确聚焦截图，`focused_actor_count=8`、
`framing_ok=true`，随后 `wb_clear(prefix="FOCUSVERIFY")` 清理临时构件。

### Task 6: 视觉 gate 分级

**Files:**
- Modify: `evals/tasks/ue_space_visual.yaml`
- Modify: `src/ue5agent/evals/ue_suite.py`
- Test: `tests/test_evals.py`

- [x] **Step 1: 写失败测试**

新增 eval check 测试：`vision_review.high_count <= 0` 作为硬门禁，`issue_count` 只作为报告字段，不让 medium/low 直接失败。

Run: `uv run pytest tests/test_evals.py -q`
Expected: 当前 `issue_count <= 0` 规则导致测试失败。

- [x] **Step 2: 更新视觉任务检查**

从 `evals/tasks/ue_space_visual.yaml` 移除或降级 `fact_lte vision_review.issue_count <= 0`，保留：
- `vision_review.parsed == true`
- `vision_review.high_count <= 0`

- [x] **Step 3: 验证**

Run: `uv run pytest tests/test_evals.py -q`
Expected: high blocking 仍失败，medium/low 不阻断。

### Task 7: 复跑标准测试

**Files:**
- Update: `evals/baselines/ue/*.json`
- Update: `docs/roadmap.md`
- Update: `CHANGELOG.md`

> 2026-06-15 尝试跑结构档：`SPC1` 已完成 `wb_build` 与 `wb_validate`，但进入
> `navmesh_rebuild/path_test` 时本次 eval 拉起的 `ue_editor` MCP 子进程断线并连续返回
> `ClosedResourceError`；直接调用 `ue_editor.navmesh_rebuild` 可成功，说明 UE bridge 本体在线。
> 本次未归档新的 UE baseline，Task 7 保持待复跑。
>
> 2026-06-15 二次复跑结构档：首个任务通过，但第二个任务开始后复用的 `ue_whitebox`
> stdio MCP session 已关闭，后续任务连续 `ClosedResourceError`。已改为 UE eval 每个任务独立
> 挂载 MCP server，避免单条坏 session 污染整套评测；失败报告
> `space-agent-test-20260615-111941.json` 仅作为诊断产物，不作为正式 baseline。
>
> 2026-06-15 后续诊断结构档：`space-agent-test-20260615-185512.json` 跑到 5/6，唯一失败是
> `SPC1` 的 `path_test.total == 1` success check 没有被 verifier 识别；trace 中最终
> `path_test` 已可达，路径长度约 2855uu。已补 `path_test.total/count` 别名与回归测试。
>
> 2026-06-15 继续复跑结构档：`191735`、`193803`、`194825` 三次均已推进到 SPC/DST 中后段，
> 但评测 Python 进程被外部终止并留下 stale `runs/.runner.lock`；没有 Python traceback，
> 也没有生成正式 baseline。结构档仍待稳定进程后复跑；视觉档先暂缓，避免在结构评测进程不稳定时
> 产出不可比报告。
>
> 2026-06-15 再次结构档复跑时，SPC1/SPC2/SPC3 均推进通过并进入 DST1；DST1 暴露出 agent
> 对外墙窗与共享墙门洞仍靠猜，反复把 windows 放到共享墙、把支线房间贴出歧义共享边，最终触发
> `llm_timeout` 重试。按反馈，本轮停止继续改测试题面，改为优化白盒搭建 agent 的通用构型守则与
> LayoutError 恢复提示，并在 `wb_build` 派发前补轻量布局 guardrail：删除共享墙窗、补齐单侧
> 共享门洞、收拢越界楼梯 footprint；正式 baseline 仍保持待复跑。
>
> 2026-06-15 报告侧补强：UE eval 的控制台表格与 JSON baseline 新增 `failure_type`，区分
> `llm_timeout`、`env_unready`、`vision_high`、`vision_medium_low`、`layout_error`、
> `geometry_check` 等，后续复跑无需先手读 trace 才能分桶。
>
> 2026-06-15 标准结构档复跑归档：`space-agent-test-20260615-205313.json` 6/6 通过，
> pass_rate=1.0，first_try_pass_rate=0.8333，avg_iterations=3.0，人工干预 0。复跑期间暴露的
> `wb_validate.is_valid` 契约别名已补到 verifier，并以回归单测锁住。本轮不继续追着视觉档反复复跑，
> 后续重点转向提升白盒搭建 agent 的自主构型稳定性。
>
> 2026-06-15 视觉档复跑探测：`SPC1V` 已完成 `wb_build` 与 `wb_validate`，但截图阶段连续返回
> `No active editor viewport`；当时 UE 主窗口处于“恢复包”，没有可读 Level Editor 视口。本问题不是
> 空间构型失败，已把该截图错误映射为 `env_unready` 并在 runner 侧快速终止，避免模型反复换截图参数
> 把 history 膨胀到 70k tokens。视觉 baseline 暂不归档，待编辑器恢复到正常关卡视口后重跑。

- [x] **Step 1: 跑结构测试**

Run: `uv run ue5agent eval --suite ue --tasks evals/tasks/ue_space.yaml --out evals/baselines/ue/space-agent-test-YYYYMMDD-HHMMSS.json`
Expected: SPC/DST 结构与导航通过，LLM timeout 单独分类。

Actual: `evals/baselines/ue/space-agent-test-20260615-205313.json`，6/6 通过，`failure_type` 均为空。

- [ ] **Step 2: 跑视觉检测测试**

Run: `uv run ue5agent eval --suite ue --tasks evals/tasks/ue_space_visual.yaml --out evals/baselines/ue/space-agent-test-visual-YYYYMMDD-HHMMSS.json`
Expected: high 问题为 0 的任务通过；medium/low 出现在报告但不压低 pass rate。

Attempt: `space-agent-test-visual-20260615-211212.json` 未产出正式 baseline；首个 `SPC1V`
暴露 `viewport_screenshot` 环境错误（无活动编辑器视口），已改为 `env_unready` 快速分类。

- [x] **Step 3: 更新文档**

把复跑结论写入 `docs/roadmap.md` 的 B6/B7 条目，并在 `CHANGELOG.md` 的 `[未发布]` 记录行为变化。

## 优先级

- P0：Task 1、Task 2、Task 3
- P1：Task 4、Task 5、Task 6
- P2：Task 7

## 当前结论

现有 DSL 方向是成立的，不需要因为这两轮问题推翻。代码侧 P0/P1 修复已完成，离线回归已覆盖墙体、
楼梯、LLM timeout、MCP session 重启、截图聚焦、视觉 high-only gate、
`path_test.total/count/path_test_result` 与 `wb_validate.is_valid` 验收别名、白盒搭建 agent 的通用构型守则/
错误恢复提示，以及 `wb_build` 派发前轻量布局 guardrail（删除共享墙窗、补齐单侧共享门洞、收拢越界
楼梯 footprint）和 UE eval `failure_type` 报告分类。结构档正式 baseline 已归档并 6/6 通过；视觉档当前
阻塞在编辑器无活动视口的环境状态，已快速分类为 `env_unready`，不再让模型空转。下一步应优先继续提升
agent 自主布局稳定性，而不是靠反复收窄或反复运行测试题面。视觉档作为后续验证项保留。
