# Agent Kernel 重构开发方案（Phase 0.5）

> 状态：历史方案，已完成并归档 | 日期：2026-06-10
> 决策：采用完整 Agent Kernel 重构（状态机化），施工顺序为「真模型基线 → kernel 细化」。
> 关系文档：现状以 [architecture/design.md](architecture/design.md) 与 [architecture/decisions/0006-kernel-state-machine.md](architecture/decisions/0006-kernel-state-machine.md) 为准；本文件只保留施工背景。

## 1. 背景与决策

当前 `core/loop.py` 是最小可运行 agent loop（单 while 循环 + 工具调度），M1–M5 已为它配齐容错、trace、重试降级、历史压缩与评测。它验证了链路，但不是长期形态：没有任务状态概念、没有结构化验收、跑崩后只剩一团对话记录、无法中断恢复。

决策（用户拍板，2026-06-10）：

1. **按完整 Agent Kernel 形态重构**：loop 升级为带阶段状态机的任务运行器，配套 Planner、Verifier、Recovery、结构化 trace 与产物目录。
2. **先拿真模型基线，再细化 kernel**：在重构前用 API key 对现有 eval 套件跑分并归档 trace，kernel 的细节设计（恢复策略、验收规则、阶段划分粒度）由真实失败形态驱动，不凭想象。
3. kernel 不懂 UE：UE 能力全部工具化，提示词中的 UE 语境从 kernel 移到装配层。

## 2. 目标架构

```
src/ue5agent/
├─ agent/                    # Agent Kernel（本次重构的主体，不懂 UE）
│  ├─ runner.py              # 任务运行器：阶段状态机、预算、中断恢复
│  ├─ state.py               # TaskSession / PlanStep / Artifact 数据结构与持久化
│  ├─ events.py              # 类型化 TraceEvent 定义与 RunWriter（runs/ 目录）
│  ├─ planner.py             # 计划生成与修订（活文档，不是一次性产物）
│  ├─ context.py             # 上下文装配与压缩（吸收 core/context.py）
│  ├─ tool_pipeline.py       # 工具调用管线（吸收 validation + dispatch 链）
│  ├─ recovery.py            # 错误分类、重复失败熔断、恢复策略
│  ├─ verifier.py            # 验收：证据规则 + judge 模型判定
│  └─ report.py              # 最终变更报告生成
├─ llm/                      # 模型层（已有，K6 扩能力注册表）
├─ tools/                    # 注册表与 MCP 客户端（registry 保留注册职责）
├─ mcp_servers/              # ue_build（已有）、repo_tools（K3 新增）...
└─ evals/                    # 评测（已有，K0 扩指标）
```

迁移对应关系：

| 现有模块 | 去向 |
|---|---|
| `core/loop.py` | 降级为 execute_step 的步内执行引擎，K4 末并入 runner 后删除 |
| `core/context.py` | 迁入 `agent/context.py` |
| `tools/validation.py` + `registry.dispatch` 链 | 迁入 `agent/tool_pipeline.py`；registry 只留注册/schema 职责 |
| `core/permissions.py` | 升级 4 级后归入 kernel 的 Policy Guard 职责（文件位置不变） |
| `session_log.py` | 被 `agent/events.py` 的 RunWriter 取代后删除 |

## 3. TaskSession 状态机设计

### 3.1 阶段与转移

```
intake ──(trivial)──────────────────────┐ fast path
   │ (standard/complex)                 ▼
 plan ──→ prepare_context ──→ execute_step ──→ verify_step
   ▲                              ▲   │            │
   │          (修订计划)           │   │(步内失败)   │通过
   └────────── recover ◄──────────┴───┴──(失败)    │
                  │                                ▼
                  │(不可恢复/预算尽)      还有步骤？──(是)→ execute_step
                  ▼                                │(否)
            abort_with_report          summarize → checkpoint → final_report
```

### 3.2 各阶段契约

| 阶段 | 输入 | 输出 | 失败路径 |
|---|---|---|---|
| intake | 用户输入、会话历史 | 任务理解 + `task_class`（trivial/standard/complex）+ 初始验收标准 | 理解不了→向用户澄清 |
| plan | intake 产物、项目知识 | `PlanStep[]`，每步带意图与验收标准 | 计划不合格→judge 打回重拟（≤2 次） |
| prepare_context | 当前步骤 | 为该步装配的上下文（检索结果、相关文件） | 检索失败→降级为空上下文继续 |
| execute_step | 步骤意图 + 上下文 | 工具调用与产物（diff/文件/输出） | 工具连续失败→recover |
| verify_step | 步骤验收标准 + 产物 | pass/fail + 证据引用 | fail→recover |
| recover | 失败上下文、失败签名计数 | 重试（带修正）/ 修订计划 / 求助用户 / 放弃 | 熔断触发→abort_with_report |
| summarize | 全部步骤 | 压缩的会话摘要（喂给后续任务） | 无 |
| checkpoint | 会话产物 | git checkpoint / 产物归档 | checkpoint 失败→警告但不阻塞报告 |
| final_report | TaskSession 全量 | 变更报告（做了什么、证据、未尽事项） | 无 |

### 3.3 防形式主义的三条设计保障

这是上轮讨论中识别的状态机风险，作为硬性设计约束写入：

1. **fast path**：intake 判为 trivial 的任务（单文件小改、查询类）跳过 plan/prepare，直接 execute→verify→report。简单任务不付流程税。
2. **步内微循环**：execute_step 内部就是现在的 AgentLoop——模型在步内仍是自由的 tool-calling 循环（带步级预算），状态机只包住"宏步骤"。**状态机管进出与证据，不管模型怎么思考。**
3. **计划是活文档**：recover 可以修订 PlanStep 列表（增删改余下步骤），修订记 trace；不存在"计划锁死后只能跑完"的形式主义。

### 3.4 数据结构草案

```python
@dataclass
class TaskSession:
    id: str                      # 时间戳+slug，对应 runs/ 子目录
    goal: str
    task_class: str              # trivial | standard | complex
    plan: list[PlanStep]
    current_step: int
    status: str                  # running | done | aborted | awaiting_user
    budgets: Budgets             # 总轮数/每步轮数/token 上限
    artifacts: list[Artifact]
    # 持久化为 runs/<id>/session.json，进程重启后可恢复（awaiting_user 场景）

@dataclass
class PlanStep:
    id: str
    intent: str                  # 这一步要达成什么
    acceptance: str              # 怎样算完成（verify 的依据）
    status: str                  # pending | running | done | failed | skipped
    attempts: int
    evidence: list[ArtifactRef]  # 验收证据

@dataclass
class Artifact:
    kind: str                    # diff | build_log | screenshot | report | file
    path: str                    # runs/<id>/artifacts/ 下的相对路径
    meta: dict

# TraceEvent（events.py，类型化）：
# phase_enter / phase_exit / llm_turn / tool_call / verify_result
# / recover_action / checkpoint / budget_warning
# 公共字段：ts, session_id, phase, step_id
```

### 3.5 runs/ 产物目录

```
runs/2026-06-12_143055_add-dash-skill/
├─ session.json        # TaskSession 终态（可恢复）
├─ trace.jsonl         # 全部 TraceEvent
├─ artifacts/          # diff、编译日志、截图、生成文件
└─ report.md           # final_report 产物
```

`ue5agent trace` 命令适配新格式，按阶段分组渲染。

## 4. 工具调用管线（tool_pipeline.py）

现有 dispatch 链已实现大半，迁移时补三个增量（粗体）：

```
模型输出 → 工具名检查(近似纠正) → JSON 解析(机械修复) → Schema 校验
→ **参数规范化**(路径分隔符/相对路径归一) → 权限检查(4 级)
→ 执行 → **结果信封** {ok, summary, error{kind, retryable}}
→ **失败签名计数**(喂 recovery 熔断) → 回传模型
```

结果信封对模型序列化为紧凑文本，在 trace 中保留结构化形态。

## 5. 权限升级：3 级 → 4 级

| 级别 | 例子 | 策略 |
|---|---|---|
| READ | 读代码/蓝图/资产/日志、检索 | 自动放行 |
| WRITE_SAFE | 写临时文件、生成报告、评测沙盒、白盒临时关卡、git checkpoint | 自动放行 |
| WRITE_PROJECT | 改源码、改 uasset、改关卡、git commit | **前置条件：checkpoint 存在**，否则管线自动先打 checkpoint；交互模式下确认 |
| DANGEROUS | 删资产、批量重命名、迁移目录、改 DefaultEngine.ini/插件配置 | 默认拒绝，白名单 + 人工确认双条件 |

配套：`repo_tools` MCP server（git checkpoint / diff / status / restore），checkpoint 信息记入 TaskSession，整任务可一键回滚。

## 6. 模型层扩展

```yaml
roles:
  planner:       # intake/plan/recover 修订：最强模型
  coder:         # execute_step 主力
  cheap_worker:  # 日志总结、检索初筛、summarize
  vision:        # 截图审查（Phase 1 起用）
  judge:         # verify_step 与计划评审：强模型，可与 planner 同模型不同提示
```

judge 用独立角色而非复用 planner 会话：验收提示词只看「目标 + 验收标准 + 证据」，不看执行过程的自我陈述，降低自嗨。能力注册表（vision/工具支持/上下文长度，路由与 fallback 时校验能力边界）放 K6。

## 7. 评测门禁（贯穿全程的规则）

- **K0 先建基线**：真模型跑现有 10 任务套件，归档分数与 trace。
- **每个里程碑合并前**：mock 套件全绿 + （有 key 时）真模型套件通过率 ≥ 基线；平均轮数恶化 >20% 必须查明原因。
- K4 起评测同时跑新旧两条路径直至 K5 切换完成。
- eval_cases 向「目录式案例」演进（task.md + expected_checks.yaml + fixture），UE 依赖类案例随 Phase 1 工具落地补充。

## 8. 施工里程碑

| # | 内容 | 规模 | 验收 |
|---|---|---|---|
| **K0 真模型基线**（前置：API key） | 配 models.yaml；eval 报告补两个指标：工具调用错误率、估算成本；对 DeepSeek（+可用的对照模型）跑分归档到 `evals/baselines/`；整理真实失败形态清单 | S | 基线报告入库；失败形态清单作为 K2/K4 设计输入 |
| **K1 数据结构与 trace 升级** | state.py / events.py / runs/ 目录 RunWriter；现有 loop 先经兼容层写新 trace；`trace` 命令适配 | M | chat/eval 在新 trace 下工作；回放按阶段渲染；单测 |
| **K2 工具管线模块化** | tool_pipeline.py 吸收现有链 + 参数规范化 + 结果信封 + 失败签名计数 | M | 现有容错测试全部迁移通过；新增三项各有单测 |
| **K3 权限 4 级 + repo_tools** | permissions.py 升 4 级；repo_tools MCP server；WRITE_PROJECT 的 checkpoint 前置钩子 | M | 权限矩阵单测；临时 git 仓库上的 checkpoint/回滚测试 |
| **K4 Runner 状态机** | runner/planner/recovery/verifier/report；fast path 与步内微循环；judge 验收 gate；UE 提示词移出 kernel | L | 状态机各转移/失败路径/熔断有单测；真模型套件 ≥ K0 基线；trace 含完整阶段事件 |
| **K5 切换与清理** | cli 切到 runner；删除旧 loop/session_log；design.md、ADR-0006、CLAUDE.md、guides 同步 | S | 全绿；基线不退化；文档与代码一致 |
| **K6 能力注册表（可选尾巴）** | models.yaml capabilities 段；路由/fallback 的能力校验；cheap_worker 接入 summarize | S | 配置校验与路由单测 |

依赖关系：K0 → K1 → K2/K3（可并行）→ K4 → K5 → K6。K4 是最大件也是风险集中点，动工前用 K0 的失败形态清单复核阶段划分粒度。

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| 状态机流程税拖慢简单任务 | fast path 是硬性设计约束（§3.3），eval 含 max_turns 检查防退化 |
| 阶段切换打断模型的自然工作流 | 步内微循环：状态机只管宏步骤边界，步内仍是自由 loop |
| 重构期间双轨维护成本 | K4 期间双轨仅限评测对照，K5 强制收口删旧路径，不留长期并存 |
| judge 误杀（验收过严卡死任务） | judge 输出 pass/fail/insufficient-evidence 三态，insufficient 走 recover 补证据而非直接失败；judge 提示词进 eval 回归 |
| 真模型基线本身不稳（单次运行方差） | K0 每模型跑 3 遍取中位数；评测报告记录方差 |
| kernel 越做越大、Phase 1 被无限推迟 | K6 之后冻结 kernel 新需求，除非 eval/真实使用暴露缺陷；Phase 1（编辑器桥）不等 K6 |

## 10. 需要用户提供

| 事项 | 阻塞 | 说明 |
|---|---|---|
| 至少一个模型 API key（建议先 DeepSeek，便宜；最好再给一个对照模型） | K0 | 填 `.env` + `config/models.yaml` 即可，参考 [guides/getting-started.md](guides/getting-started.md) |
| 交互确认方式确认 | K3 | WRITE_PROJECT/DANGEROUS 在 chat 内 y/n 确认是否可接受 |
