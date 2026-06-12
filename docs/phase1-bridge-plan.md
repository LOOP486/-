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

### P1.2 裁剪与分级

- [ ] 移除/禁用蓝图编辑类与批量构建类工具（create_town 等保留代码、不注册）
- [ ] 按 K3 权限 4 级给保留工具标级（场景写=write_project，截图/日志=read）
- [ ] server 改造为 ue5agent 子包或独立目录，纳入 lint/测试体系
- 验收：注册进 registry 的工具清单与分级表入文档

### P1.3 蓝图只读导出四件套（自研重点）

- [ ] `bp_overview(path)`：父类/组件树/变量表/函数事件列表/接口（插件侧遍历 UBlueprint）
- [ ] `bp_pseudocode(path, func)`：EdGraph → 伪代码文本（token 约 JSON 的 1/5，默认视图）
- [ ] `bp_graph(path, func)`：节点级 JSON（仅歧义时下钻）
- [ ] `bp_find_usages(symbol)`：跨蓝图引用查找（AssetRegistry 依赖图）
- [ ] 全部 GameThread 化 + 超时熔断
- 验收：对第三人称模板的 BP_ThirdPersonCharacter 输出可读伪代码；标准答案进 eval case

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
