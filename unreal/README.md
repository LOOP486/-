# unreal/ —— UE 编辑器桥（Phase 1）

此目录将放置 UE 编辑器桥：基于 [flopperam/unreal-engine-mcp](https://github.com/flopperam/unreal-engine-mcp)（MIT）fork 裁剪的 C++ 插件 + Python MCP server，决策依据见 [ADR-0005](../docs/architecture/decisions/0005-fork-flopperam-bridge.md)。

计划的改造（详见 [roadmap](../docs/roadmap.md) Phase 1）：

1. 移除蓝图编辑类工具（[ADR-0003](../docs/architecture/decisions/0003-blueprint-readonly.md)：蓝图只读）；
2. 自研蓝图只读导出：`bp_overview` / `bp_pseudocode` / `bp_graph` / `bp_find_usages`；
3. Phase 2 在此基础上加白盒搭建工具集（`actors_spawn_batch`、`navmesh_rebuild`、`path_test`、正交截图）。

实现注意：所有编辑器操作必须 marshal 到 GameThread；工具超时要熔断，避免卡死编辑器。
