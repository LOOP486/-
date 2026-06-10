# 术语表

| 术语 | 含义 |
|---|---|
| UBT | UnrealBuildTool，UE 的构建系统，`Build.bat` 是其入口 |
| UHT | UnrealHeaderTool，处理 UPROPERTY/UFUNCTION 等反射宏的代码生成器 |
| PIE | Play In Editor，编辑器内运行游戏 |
| CDO | Class Default Object，UClass 的默认对象，存默认值 |
| .uasset / .umap | UE 的二进制资产/关卡文件，文本工具不可读（本项目经编辑器桥读取） |
| 蓝图（Blueprint） | UE 的可视化脚本，本项目只读分析（ADR-0003） |
| AssetRegistry | UE 的资产注册表，支持按类型/路径查询与依赖分析 |
| NavMesh | 导航网格，AI 寻路数据；本项目用它做白盒关卡可达性校验 |
| Live Coding | UE 的热编译；改头文件/反射宏时不可靠，需完整编译 |
| 白盒（blockout/whitebox） | 用简单几何体/模块件快速搭出关卡结构验证玩法，再做美术替换 |
| modular kit | 模块化资产套件（墙/地板/楼梯等），按统一网格尺寸设计 |
| 关卡 metrics | 关卡设计的尺度规范：走廊宽、净空高、跳跃距离、掩体高度等 |
| manifest | 本项目的资产清单：每个模块件的尺寸/pivot/吸附网格/连接语义（ADR-0004） |
| 布局 DSL | 本项目的场景中间表示：格子单位的房间/走廊/连接拓扑，模型的输出物 |
| MCP | Model Context Protocol，LLM 工具接入的开放协议，本项目工具层标准（ADR-0002） |
| LiteLLM | 多 LLM 适配库，把 100+ provider 统一成 OpenAI 格式（ADR-0001） |
| 角色路由 | models.yaml 里按职能（planner/coder/vision）分配不同模型 |
| ADR | Architecture Decision Record，架构决策记录，见 docs/architecture/decisions/ |
