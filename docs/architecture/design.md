# UE5 游戏开发 Agent 框架设计

> 目标：一个能在 UE5 工程里端到端解决问题的开发代理——理解需求 → 读懂工程（C++ + 蓝图 + 资产）→ 用 C++ 实现功能、用模块化资产搭建白盒场景 → 编译运行验证 → 拿证据汇报。
> 面向用户：关卡策划、Gameplay 程序、TA。

**v2（2026-06-10）**：① 功能实现以 C++ 为主，蓝图只读理解、不做节点编辑；② 新增白盒场景搭建子系统（核心场景）；③ agent 核心改为模型无关的自研 loop，支持 GPT / Claude / DeepSeek 及自定义 API；④ 补充现有开源生态调研与复用策略。

---

## 1. 核心设计判断

普通 coding agent 在 UE5 上表现差，不是因为模型不懂 UE，而是几个工程性障碍。本框架的全部设计都围绕拆掉它们：

| 障碍 | 后果 | 本框架的解法 |
|---|---|---|
| 蓝图/资产是二进制 `.uasset` | agent 对半个工程是瞎的 | 编辑器桥把蓝图导出为概览/伪代码，**只读**（§5） |
| 验证链路长（编译≠行为正确） | agent 写完代码无法自证 | 编译→测试→PIE→截图→日志的多级验证闭环（§7） |
| 引擎 API 巨大且版本差异大 | 模型凭记忆写 API 经常错 | 本地引擎源码 checkout + 源码级检索，不靠记忆（§9） |
| 场景搭建要产出空间坐标 | LLM 直接吐 transform 错误率极高 | 布局 DSL 中间表示 + 网格吸附 + 程序化校验（§6） |

两个定位性决策：

- **C++ 优先**：新功能一律用 C++ 实现（暴露 `UPROPERTY`/`UFUNCTION(BlueprintCallable)` 给蓝图层用）。蓝图工具只做"读懂"——这砍掉了编辑器桥最难做的蓝图节点编辑，大幅降低自研成本。
- **模型无关**：agent 核心自研轻量 loop，经 LiteLLM 适配任意 OpenAI 兼容 API（GPT / Claude / DeepSeek / 自定义 base_url）。工具层全部走 MCP 标准协议，与模型和 loop 都解耦。

---

## 2. 总体架构

```
┌─────────────────────────────────────────────────────┐
│  Agent 核心层（自研 loop，模型无关）                      │
│  LiteLLM 适配（GPT/Claude/DeepSeek/自定义 API）          │
│  主控 + 子代理（C++ / 蓝图分析 / 白盒搭建 / 调试）          │
└────────────────────────┬────────────────────────────┘
                         │ MCP 协议                  ▲
┌────────────────────────▼─────────────────────────┐ │
│  MCP 工具层                                        │ │ 结果回传
│  代码与检索 │ 编辑器桥（只读蓝图·资产·场景写）│ 构建验证  │ │ （错误·日志·
└────────────────────────┬─────────────────────────┘ │   截图·报告）
                         │                           │
┌────────────────────────▼─────────────────────────┐ │
│  UE5 工程与运行时                                   │─┘
│  C++ 源码 │ 编辑器进程（蓝图·关卡·PIE）│ 验证产物      │
└──────────────────────────────────────────────────┘

横向支撑：知识与记忆层（引擎源码索引 / CLAUDE.md / 资产 manifest / 关卡 metrics）
横向支撑：安全层（git/P4 checkpoint / 工具分级授权）
```

---

## 3. Agent 核心层（自研，模型无关）

### Loop 设计
标准 tool-calling 循环：系统提示 + 对话历史 → 模型 → 工具调用 → 结果回填 → 直到产出最终答复。需要自己实现的配套件：

- **上下文管理**：工具结果超长截断与外置（写临时文件、给路径）、历史压缩（超过阈值时摘要早期轮次）；
- **MCP 客户端**：用官方 `mcp` Python SDK 连接各 MCP server（stdio/HTTP），工具 schema 自动转成各家 function calling 格式（LiteLLM 已处理大部分差异）；
- **权限网关**：写操作工具调用先过确认/白名单逻辑（§10）。

### 多模型配置
`models.yaml` 用户自管：

```yaml
providers:
  deepseek:   { base_url: https://api.deepseek.com, api_key_env: DEEPSEEK_KEY }
  anthropic:  { api_key_env: ANTHROPIC_KEY }
  openai:     { base_url: 可自定义中转, api_key_env: OPENAI_KEY }
roles:
  planner:    anthropic/claude-...        # 主控：最强模型
  coder:      deepseek/deepseek-chat      # 大批量代码活：性价比模型
  vision:     openai/gpt-...              # 截图审查：必须是多模态模型
```

**按角色路由**而不是全局单模型：主控/规划用强模型，批量代码与检索可以用便宜模型，截图审查路由到 vision 模型。注意 DeepSeek 当前主力模型不带视觉，**视觉验证必须配置至少一个多模态模型**，这要在配置校验时强提示。

技术上用 [LiteLLM](https://github.com/BerriAI/litellm)（统一 100+ provider 成 OpenAI 格式，自动处理各家 function calling 消息顺序差异）；若想少写 loop，可用 OpenAI Agents SDK + 其官方 LitellmModel 扩展作为底座，仍然模型无关。

### 开发期捷径
MCP 工具层与 loop 解耦意味着：自研 loop 还没好之前，同一批 MCP server 可以直接挂在 Claude Code / Cursor 下先用起来、先验证工具设计——工具层的打磨不被 loop 进度阻塞。

---

## 4. 现有开源生态与复用策略

2025–2026 年 UE×MCP 生态已经相当热闹，编辑器桥**不需要从零写**：

| 项目 | 许可 | 要点 | 对本项目的价值 |
|---|---|---|---|
| [flopperam/unreal-engine-mcp](https://github.com/flopperam/unreal-engine-mcp) | MIT | UE5.5–5.7，Python MCP server + C++ 插件（TCP），1000+ star，自带 `create_town`/`construct_mansion`/`create_maze` 等批量构建工具 | **首选基底**：fork 后裁剪，场景批量 spawn 思路直接借鉴 |
| [remiphilippe/mcp-unreal](https://github.com/remiphilippe/mcp-unreal) | Apache-2.0 | UE5.7，49 工具，三通道架构：headless `UnrealEditor-Cmd` 编译/测试 + Remote Control API + 自有插件 | **验证闭环参考**：build/test 工具与文档查询设计很完整 |
| [GenOrca/unreal-mcp](https://github.com/GenOrca/unreal-mcp)、[kvick-games/UnrealMCP](https://github.com/kvick-games/UnrealMCP)、[ChiR24/Unreal_mcp](https://github.com/ChiR24/Unreal_mcp)、[lilklon/UEBlueprintMCP](https://github.com/lilklon/UEBlueprintMCP) | 各异 | 各有侧重（行为树/UMG、流式 HTTP 免桥接进程、60+ 蓝图命令） | 工具清单与实现细节参考 |

学术侧可借鉴思路（LLM 出结构化 spec → 程序化管线落地）：UnrealLLM、[AutoUE](https://arxiv.org/pdf/2603.07106)（多 agent 自动生成 UE 游戏）、[SAGE](https://arxiv.org/pdf/2602.10116)、[WorldGen](https://arxiv.org/pdf/2511.16825)。

**复用策略**：fork flopperam 作为编辑器桥基底 → 砍掉蓝图编辑类工具（我们不需要）→ 补三块自研：① 蓝图只读导出（概览/伪代码，现有项目普遍偏弱）；② 白盒搭建工具集（manifest 驱动的批量放置 + 校验，§6）；③ 结构化的编译/测试结果解析（参考 remiphilippe）。

---

## 5. 编辑器桥（MCP 插件层）

形态：C++ 编辑器插件（Editor-only module）内嵌 TCP/WebSocket 服务 + Python MCP server 做协议转换。所有编辑器操作 marshal 到 GameThread；编辑器未启动时用 `UnrealEditor-Cmd -run=pythonscript` 兜底。

### 工具清单

**蓝图理解（只读）**
| 工具 | 说明 |
|---|---|
| `bp_overview(path)` | 父类、组件树、变量表、函数/事件列表、实现的接口——一屏看懂一个蓝图 |
| `bp_pseudocode(path, func)` | 图表转伪代码文本，默认阅读格式（token 约为节点 JSON 的 1/5） |
| `bp_graph(path, func)` | 节点级 JSON，仅在伪代码歧义时下钻用 |
| `bp_find_usages(symbol)` | 谁调用此函数/谁读写此变量（跨蓝图） |

不做蓝图写操作。需要新逻辑时走 C++（`BlueprintCallable`），由人或后续在编辑器里手工接线。

**资产与场景**
`asset_search` / `asset_dependencies` / `asset_referencers`、`datatable_read/write`、`config_read`；`level_actors`、`actor_inspect/spawn/modify/delete`、`actors_spawn_batch`（白盒搭建主力，吃放置指令列表）、`whitebox_render_preview`（compiler 级白盒 contact sheet；命中 preview cache 的 `static_mesh` 时按真实 StaticMesh 网格体绘制，否则回退 AABB；显式 `walls[]` DSL 会附带 `wall_topology` 拓扑检查）、`viewport_screenshot`（显式需要 UE 视口时使用，支持正交俯视与指定相机位）。

**编辑器控制**
`exec_python`（万能逃生舱）、`exec_console`、`pie_start/stop/status`、`get_output_log`（按等级/分类过滤）、`navmesh_rebuild`、`path_test(start, end)`（可达性测试，白盒校验用）。

### Token 经济学
蓝图工具默认返回概览级，按需下钻（overview → 函数列表 → 伪代码 → 节点 JSON）；场景查询默认只回关键属性，详情走 `actor_inspect`。

---

## 6. 白盒场景搭建子系统（核心场景）

输入：自然语言需求（可附参考图/平面草图）+ 用户提供的模块化资产库。输出：搭好的白盒关卡 + 多角度截图证据。

### 6.1 资产清单（Asset Manifest）
模块化资产先登记进 manifest（JSON 或 DataTable），每条记录：

```json
{
  "asset": "/Game/ModKit/Wall_400x300",
  "category": "wall",            // wall | floor | stair | door | platform | prop ...
  "tags": ["concrete", "indoor"],
  "footprint": [400, 20, 300],   // 占格尺寸（uu）
  "pivot": "bottom-corner",      // 资产 pivot 约定
  "grid": 100,                   // 吸附网格
  "rotations": [0, 90, 180, 270],
  "connects": { "sides": "wall", "top": "floor" }  // 连接语义
}
```

首次接入由 agent 自动扫描资产生成草稿（读 bounds、猜类别），人工补连接语义——manifest 质量直接决定搭建质量。

### 6.2 布局 DSL（关键设计：LLM 不直接产出坐标）
让 LLM 直接吐 float transform 错误率极高且不可校验。两级中间表示：

0. **空间语义交接**（显式启用时）：白盒结构搭建/校验后，Agent 输出 `space_program`，说明空间目标、功能分区、动线、硬约束和工具接手边界；它是给资产摆放与 dressing solver 使用的语义 brief，不包含 `layout_json`、最终 props 坐标或 yaw。后续 dressing 工具再负责坐标、密度和验证事实。
1. **空间规划层**（LLM 的主要输出）：房间（格子单位的矩形/多边形 footprint）、走廊、楼层、门/连接关系、功能标注（出生点、战斗区、掩体带、狙击位）。资产密集摆放暂不进入当前默认链路；这一层先保持纯拓扑+2.5D 布局，后续再重新设计可校验的资产布置接口。这一层是纯拓扑+2.5D 布局，可程序化校验、可整体 diff、可局部重生成。
2. **放置指令层**（编译产物）：布局编译器把规划层 + manifest 解析成 `(asset, grid_x, grid_y, floor, rot90)` 列表——选哪面墙、墙段如何分割、楼梯朝向，都是确定性算法而非 LLM 决定。工具层换算 world transform 后 `actors_spawn_batch` 落地。

### 6.3 校验与迭代闭环
- **程序化校验**（spawn 前）：footprint 重叠检测、墙体封闭性、门连通图、楼梯首尾层高匹配；
- **关卡 metrics 硬约束**：策划提供 metrics 表（走廊最小宽、净空高度、跳跃可达高/距、掩体高度档位），校验器按表拦截违规布局——这是策划知识进系统的入口；
- **运行时校验**（spawn 后）：`navmesh_rebuild` + `path_test` 验证出生点到各目标可达；
- **视觉迭代**：默认使用 compiler 级 top/iso contact sheet（看布局、拓扑与 placement）→ vision 模型审查 → 产出空间规划层的局部修改 → 重编译落地。明确要求真实 UE 视口时，再补正交俯视截图/玩家视角截图（看尺度、遮挡与最终画面）。

### 6.4 与 PCG 的边界
主体白盒结构走显式放置（可控、可 diff、可局部改）；重复性填充（栏杆阵列、柱列、散布装饰）可由 agent 调用预制 PCG 规则或生成 PCG 参数。

---

## 7. 验证闭环

agent 的能力上限 = 它能自主验证什么。三级闭环：

1. **编译闭环**：封装 UBT（`Build.bat <Target> Win64 Development -Project=...`），输出解析为结构化错误（文件/行/错误码），自动修复迭代。改头文件/反射宏必须走完整编译而非 Live Coding。
2. **行为闭环**：生成 Functional Test / Automation Spec → `UnrealEditor-Cmd -ExecCmds="Automation RunTests <filter>"` → 解析报告；或 PIE + 截图 + 日志断言。
3. **场景闭环**：§6.3 的程序化校验 + 可达性 + 视觉审查。

主控验收规则写死：**任何修改类任务，结束前必须出示验证证据**（编译输出/测试结果/截图），否则不允许声称完成。

---

## 8. 子代理

| 子代理 | 职责 | 工具集 |
|---|---|---|
| `cpp-engineer` | 写 C++、修编译错、Build.cs 配置 | 文件读写 + 编译工具 + 引擎源码检索 |
| `blueprint-analyst` | 读蓝图、追逻辑链、定位蓝图侧问题 | 编辑器桥只读工具 |
| `level-builder` | 白盒搭建全流程（规划→落地→校验→视觉迭代） | manifest + 布局编译器 + 场景工具 + 本地预览/截图 |
| `debugger` | 复现、日志分析、二分定位 | PIE 控制 + 日志 + console |
| `engine-researcher` | 引擎源码检索、API 版本仲裁 | 只读检索（指向引擎源码） |

子代理的价值是**上下文隔离 + 工具最小化 + 按角色配模型**：蓝图 JSON 和引擎源码让子代理在自己的上下文里消化，只把结论带回主线；`level-builder` 的视觉审查环节强制路由 vision 模型。

---

## 9. 知识与记忆层

- **引擎源码本地 checkout（必须）**：与项目版本一致。API 不确定时直接 grep 源码，比文档和 RAG 都准。
- **项目 CLAUDE.md / 项目说明**：模块结构、编码规范、常用命令、命名约定。
- **资产 manifest + 关卡 metrics 表**：白盒搭建的领域知识载体（§6）。
- **长期记忆**：踩坑记录、项目术语、用户偏好，按条目沉淀。
- **版本意识**：引擎版本写入系统提示，禁用更高版本 API。

---

## 10. 安全与回滚

- 工程必须在 git/P4 管理下；资产写操作前自动 checkpoint。
- 工具三级分权：只读默认放行；写操作（spawn/modify/datatable_write）需确认或白名单；危险操作（删资产、改引擎目录、SCC 提交）默认禁止。
- 永不直接改写 `.uasset` 二进制，资产修改必须经编辑器 API。
- 白盒搭建的批量 spawn 自带"一键撤销"：每次搭建记录 spawn 清单，可整批回滚。
- 会话产出操作日志（动了哪些资产、跑了哪些命令）。

---

## 11. 路线图

| 阶段 | 周期 | 交付物 | 验收标准 |
|---|---|---|---|
| **Phase 0：核心骨架 + C++ 闭环** | 1–2 周 | 自研 loop（LiteLLM + MCP client + models.yaml）+ UBT 编译 MCP 工具 | 用 DeepSeek/Claude/GPT 任一模型，独立完成「加一个 gameplay 功能并编译通过」 |
| **Phase 1：编辑器桥落地** | 2–4 周 | fork flopperam 裁剪 + 自研蓝图只读导出 + 截图/日志/资产查询 | agent 能回答「这个蓝图做了什么、谁在用它」 |
| **Phase 2：白盒搭建子系统** | 4–6 周 | manifest 工具链 + 布局 DSL 与编译器 + 批量 spawn + 校验器 + 视觉迭代 | 给一段文字需求和模块资产库，agent 产出可走通（NavMesh 可达）的白盒关卡 + 截图证据 |
| **Phase 3：行为闭环与编排** | 持续 | 自动化测试闭环、子代理体系、评测基准 | 基准任务集一次通过率达标，策划可日常使用 |

开发期所有 MCP 工具先挂 Claude Code 验证（§3 开发期捷径），自研 loop 并行推进，互不阻塞。

---

## 12. 风险与对策

| 风险 | 对策 |
|---|---|
| 弱模型（function calling 不稳）拖垮体验 | LiteLLM 统一格式 + loop 里做工具调用重试/参数校验；角色路由让关键环节用强模型 |
| 视觉验证在纯文本模型下缺位 | vision 角色独立配置，缺失时降级为"仅程序化校验"并明示用户 |
| 布局 DSL 表达力不够（曲面/异形结构） | DSL 覆盖 80% 盒子式白盒；异形结构留 `exec_python` 逃生舱 + 人工 |
| manifest 语义标注成本高 | agent 自动扫描出草稿，人只补连接语义；模板化常见 kit 约定 |
| 蓝图 JSON/引擎源码撑爆上下文 | 分层 API + 伪代码视图 + 子代理隔离 |
| 编辑器操作崩溃/卡死 | GameThread 化 + 超时熔断 + 桥进程与编辑器解耦 |
| 上游开源项目断更 | MIT/Apache 许可 fork 自持；通信协议简单（TCP+JSON），可控 |
