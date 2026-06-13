# Phase 1 编辑器桥——细化任务清单

> 依据：[ADR-0005](architecture/decisions/0005-fork-flopperam-bridge.md)（fork flopperam）、[ADR-0003](architecture/decisions/0003-blueprint-readonly.md)（蓝图只读）。
> 上游：flopperam/unreal-engine-mcp（MIT），C++ 插件 25 对 cpp/h + Python MCP server（TCP 55557 桥接）。
> 制定：2026-06-11。

## 目标

agent 能回答「这个蓝图做了什么、谁在用它」，并具备资产查询/场景读取/截图/日志能力。蓝图**只读**。

## 里程碑

### P1.1 插件兼容性与最小链路（✅ 2026-06-11）

- [x] UnrealMCP 插件随 agent_testEditor 在 UE5.7+VS2026 编译通过（0 错误）
- [x] 自研瘦桥 server（mcp_servers/ue_editor：TCP 客户端 + 4 个只读工具），
  弃用上游 2000 行 Python server——只暴露挑选过的只读命令，顺带完成 P1.2 大半
- [x] 编辑器在线实测：editor_actors 读出 71 个 Actor；bp_read 读出
  BP_ThirdPersonCharacter（父类/函数/事件图节点，中文节点标题正常）
- 验收达成：agent 经同一 MCP 链路即可回答场景与蓝图问题

### P1.2 裁剪与分级（✅ 2026-06-12，= Stage C1）

- [x] 移除/禁用蓝图编辑类与批量构建类工具：瘦桥（mcp_servers/ue_editor）只转发挑选过的
  只读命令，编辑/批量构建类命令（spawn_actor/delete_actor/set_*/compile_blueprint/
  create_town 等）一律不暴露——由"只 forward 选定命令"的构造方式强制保证（非靠禁用开关）。
- [x] 按 K3 权限 4 级给保留工具标级（config/agent.yaml 的 ue_editor.tool_permissions）。
- [x] server 已是 ue5agent 子包（src/ue5agent/mcp_servers/ue_editor），纳入 ruff/pytest。
- [x] 回归守卫：tests/test_ue_editor_tools.py 断言瘦桥工具集恰为审定集、源码不含任何
  编辑类桥命令、蓝图工具只发只读查询命令。
- 验收达成：工具清单与分级表入文档（见下）。

#### ue_editor 工具清单与分级表

| 工具 | 转发的桥命令 | 权限级 | 用途 |
|---|---|---|---|
| `editor_status` | （probe_editor 握手） | read | 探测编辑器桥在线 |
| `editor_actors` | get_actors_in_level | read | 列出关卡 Actor |
| `actor_find` | find_actors_by_name | read | 按名查 Actor |
| `bp_read` | read_blueprint_content | read | 蓝图概览（组件/变量/函数/图） |
| `bp_analyze` | analyze_blueprint_graph | read | 蓝图节点图分析（graph_name 选图） |
| `bp_overview` | read_blueprint_content | read | 紧凑概览（C2 默认视图） |
| `bp_pseudocode` | analyze_blueprint_graph | read | 控制流伪代码（无连接退回摘要） |
| `bp_find_usages` | find_blueprint_references | read | 引用查找（插件命令待真机） |
| `viewport_screenshot` | viewport_screenshot | read | 视口截图存 PNG |
| `navmesh_rebuild` | navmesh_rebuild | **write_project** | 重建 NavMesh（改关卡） |
| `path_test` | path_test | read | 两点导航可达性 |
| `output_log_tail` | output_log_tail | read | 读 Output Log 尾部（按级别） |
| `pie_smoke` | pie_start + pie_stop | **write_project** | PIE 跑 N 秒读运行期 Error/Warning |

写级工具：`navmesh_rebuild` 与 `pie_smoke`（write_project，触发自动 checkpoint 语义）；其余全部 read。
蓝图相关一律只读（ADR-0003），不提供任何蓝图编辑/编译/连线命令。

### P1.3 蓝图只读导出四件套（自研重点）= Stage C2

- [x] `bp_overview(path)`：父类/组件树/变量表/函数事件列表/接口 —— ✅ 2026-06-12，Python 转换层
  （`src/ue5agent/blueprint.py` format_overview）压 read_blueprint_content 的 JSON 为紧凑文本
  （token ≈ 原始 1/7）；真机 + agent e2e 验证（agent 用它准确解释 BP_ThirdPersonCharacter）。
- [x] `bp_pseudocode(path, func)`：EdGraph → 控制流伪代码 —— ✅（二次修订）：读插件源码确认
  graph_data 本就带 connections（from/to node+pin 端点），且按 `graph_name` 可选事件图/函数图；
  早期"pin 无连接端点、恒返回 EventGraph"的结论系瘦桥误传 `function_name` 参数（被插件忽略）
  所致。format_pseudocode 据 exec connections 重建执行流（事件/输入入口缩进列出执行顺序），
  无连接信息时退回结构化摘要。
- [x] `bp_graph(path, func)`：节点级 JSON —— = 现有 `bp_analyze`（转发 analyze_blueprint_graph，
  参数已修正为 graph_name）。
- [x] `bp_find_usages(symbol)`：跨蓝图引用查找（AssetRegistry）—— ✅ 2026-06-13：插件新增
  `find_blueprint_references`（GetReferencers + 过滤引擎/自身），真机验证
  BP_ThirdPersonCharacter → BP_ThirdPersonGameMode。
- 验收：对 BP_ThirdPersonCharacter 输出可读概览/伪代码 ✅、谁在用它 ✅；标准答案进 eval case → 移交 C3。

#### 待真机的插件侧 C++ 增强（C2 收尾 + D1.1）—— ✅ 已完成（2026-06-13，commit agent_test 4d280a5）
1. ~~analyze_blueprint_graph 补 pin 连接端点 / 按函数图返回~~——已确认插件本就支持，
   经 graph_name 参数修正后无需插件改动（2026-06-12 二次修订）。
2. ✅ 新增 AssetRegistry 引用查找命令（find_blueprint_references），支撑 bp_find_usages。
3. ✅ D1.1 服务端：插件生成 token 写 Saved/ + 握手校验 protocol/token（详见 stage-e-plan.md E1 批次）。
3. （D1.1）插件启动生成 token 写 Saved/，bridge 握手出示 + 协议版本握手。
   **客户端侧已就绪（2026-06-12）**：bridge.py 每条命令握手带 `protocol`（PROTOCOL_VERSION=1），
   并在配了 `UE_MCP_TOKEN` 或 `UE_MCP_TOKEN_FILE`（指向插件写的 token 文件）时附 `token` 字段；
   未配则不带，与无 token 插件兼容（已单测）。**服务端侧待真机 C++**：插件启动生成随机 token 写
   `Saved/ue5agent_bridge_token.txt`，握手时校验 payload 的 token 与 protocol（不匹配返回明确
   error 而非静默错乱）；agent.yaml/.env 配 `UE_MCP_TOKEN_FILE` 指向该文件。

> Stage E（PIE/Automation/子代理/基准）细案见 [stage-e-plan.md](stage-e-plan.md)；其中 E1 的
> PIE/Functional/Output Log 三命令与上述 C2 收尾、D1.1 服务端同属一次插件 C++ 改动，建议合并编译。

### P1.4 验证与评测

- [x] navmesh_rebuild + path_test + viewport_screenshot（✅ 2026-06-12 以 Stage A1 完成：
  插件 C++ 三命令 + ue_editor 注册 + 真机三房间验证，见 development-plan.md A1）
- [ ] eval 新增 UE 依赖档（read_blueprint_and_explain 等，需编辑器在线，单独 suite）→ 移交 Stage C3
- 验收：UE 档 eval 在编辑器开启时可跑分

## 风险

| 风险 | 对策 |
|---|---|
| 上游插件不兼容 5.7/VS2026 | P1.1 首先验证；报错逐个适配（我们 fork 自持） |
| 蓝图导出 API 需编辑器模块 | 插件已是 Editor 模块；commandlet 兜底后置 |
| 编辑器没开时工具全部不可用 | MCP server 返回明确 [error] 提示先启动编辑器；不做静默降级 |
