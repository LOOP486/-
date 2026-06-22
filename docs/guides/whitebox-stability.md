# 白盒构型稳定性补强指南

本指南用于把 UE 在线评测或真实关卡搭建中的失败样本，沉淀成可复现、可回归的稳定性补强。
原则是：先归因，再补防线；优先补确定性校验、planner 后处理和 runner 证据链，避免靠收窄题面刷通过率。

## 当前状态

- B7 已归档两份正式 baseline：
  - `evals/baselines/ue/space-agent-test-20260615-205313.json`：SPC/DST 结构档 6/6 通过。
  - `evals/baselines/ue/space-agent-test-visual-20260616-001231.json`：SPC/DST 视觉档 6/6 通过。
- 现有防线覆盖：共享墙门洞 guardrail、外墙窗预检、楼梯 footprint 收拢、视觉 high-only gate、
  `viewport_screenshot` 自动聚焦/裁剪、MCP 断链重连、跨步骤成功 facts 复用、同轮 tool_calls 完整回包。
- 后续重点：真实失败样本的分类、最小复现和回归测试，而不是继续修改冻结评测题面。

## 失败样本台账

| 样本 | 现象 | 已有防线 | 继续补强方向 |
|---|---|---|---|
| `space-agent-test-20260614-221045.json` / `slab_branching_training_space` | 初版房间不连通，共享墙门洞未成对或未对齐 | planner 通用构型守则；`wb_build` 派发前补齐单侧共享门洞；validator 报具体相邻房间 | 新样本若仍出现，应优先补 `tests/test_runner.py` 的 planner/guardrail 单测，确认不是题面歧义 |
| `space-agent-test-visual-round2-hooked-20260614-230436.json` | 内部共享墙被写窗，导致 `wb_build` LayoutError | 派发前删除共享墙 windows；提示词要求不确定就不写窗 | 若命名复杂导致误判，补房间邻接推导测试，不改 eval prompt |
| `runs/20260614-235117_slab_single_level_stairw/trace.jsonl` | 楼梯穿墙、护墙重叠、楼梯旁小夹缝 | 编译器楼梯护墙修正；`stairwell_*` metrics；楼梯 footprint guardrail | 新楼梯失败先补 `tests/test_whitebox_vertical_gameplay.py`，再改 compiler/validator |
| 视觉 round2 多例 | medium/low 视觉问题压低 pass rate | 视觉 gate 改为 high-only；blockout 清单不因门窗框/标签扣分 | 新视觉误判先判断是否应交给 facts，能确定的约束不要交给 vision |
| `No active editor viewport` 截图失败 | 模型反复换截图参数，history 膨胀 | 映射为 `env_unready` 快速终止；截图无 facts 不触发 vision | 环境类失败只补分类和提示，不让模型用布局重建解决 |
| 同轮 `wb_validate + viewport_screenshot` | early-stop 跳过截图 tool 回包，污染下一步 history | loop 等同轮所有 tool_calls 回包后再 early-stop | 若再见 provider 的 tool message 顺序错误，先查 trace 的最后一轮 tool_calls |
| 导航步骤复审旧截图 | 非视觉步骤被历史截图触发随机 vision high | vision 只审本 attempt 新截图；纯重复验证步可用历史 success checks 本地收口 | 新增跨步骤证据逻辑时，必须区分“验证 facts 复用”和“视觉证据复用” |

## 处理流程

1. 保存失败证据：
   - baseline JSON 放在 `evals/baselines/ue/`，诊断产物可保留但不要冒充正式 baseline。
   - trace 路径写入复盘记录；截图或 contact sheet 写绝对路径或 run artifact 路径。
2. 先分桶：
   - `llm_timeout`：看 `llm_request_start` 的 role、消息数、估算字符量。
   - `layout_error`：看 `wb_build` 错误文本，优先判断是否能在 planner/guardrail 层预防。
   - `geometry_check`：看 `wb_validate.metrics` 与 violations，优先补 compiler/validator 单测。
   - `vision_high`：判断是否是真截图可见问题；若是数值/连通性/导航，应降回 facts 验收。
   - `env_unready` / `bridge_down`：只补环境分类、重连或快速终止，不让模型重构布局。
3. 写最小回归：
   - planner/runner 漂移：优先改 `tests/test_runner.py`。
   - eval 分类或检查器：改 `tests/test_evals.py`。
   - 视觉误判：改 `tests/test_vision_review.py`。
   - 截图取证：改 `tests/test_ue_editor_tools.py`。
   - 几何/楼梯/墙体：改 `tests/test_whitebox.py`、`tests/test_whitebox_validator.py` 或
     `tests/test_whitebox_vertical_gameplay.py`。
4. 再做最小实现：
   - 首选确定性规则和 facts。
   - 其次是 planner 后处理或 runner 证据作用域。
   - 最后才改模型提示；提示词变化必须配单测，防止后续回退。
5. 归档结论：
   - 更新 `docs/roadmap.md` 的 B7/后续条目。
   - 对外行为变化写入 `CHANGELOG.md` 的 `[未发布]`。
   - 长任务踩坑写入 `docs/worklog.md`。

## 推荐回归命令

```powershell
uv run python scripts/run_tests.py
uv run pytest tests/test_runner.py tests/test_evals.py tests/test_vision_review.py -q
uv run pytest tests/test_whitebox.py tests/test_whitebox_validator.py tests/test_whitebox_vertical_gameplay.py -q
```

UE 在线回归只在编辑器就绪时跑：

```powershell
uv run ue5agent eval --suite ue --tasks evals/tasks/ue_space.yaml --out evals/baselines/ue/space-agent-test-YYYYMMDD-HHMMSS.json
uv run ue5agent eval --suite ue --tasks evals/tasks/ue_space_visual.yaml --out evals/baselines/ue/space-agent-test-visual-YYYYMMDD-HHMMSS.json
```

正式 baseline 只归档稳定进程完整跑完的结果；中途因环境、进程终止或诊断目的产生的 JSON，只作为复盘证据。
