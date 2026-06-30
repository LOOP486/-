# 术语表

| 术语 | 含义 |
|---|---|
| UnrealBuildTool（UBT，虚幻构建工具） | UE 的构建系统，`Build.bat` 是其入口 |
| UnrealHeaderTool（UHT，虚幻头文件工具） | 处理 UPROPERTY/UFUNCTION 等反射宏的代码生成器 |
| C++（C++，C++ 编程语言） | UE 玩法机制和插件命令常用的原生代码语言，本项目用它承载需要编译与运行验证的机制实现 |
| Play In Editor（PIE，编辑器内运行） | 在编辑器里启动游戏运行态 |
| Artificial Intelligence（AI，人工智能） | 本项目中的智能体能力来源，通常指由大语言模型驱动的规划、代码生成、审查与总结 |
| Agent（智能体） | 能根据目标规划步骤、调用工具并基于证据报告结果的软件执行体 |
| Continuous Integration（CI，持续集成） | 自动执行 lint、格式、类型检查和测试的集成流程 |
| Domain-Specific Language（DSL，领域专用语言） | 面向特定问题域设计的结构化表达，本项目用于描述白盒布局 |
| Functional Test（功能测试） | UE 自动化测试体系中的运行期功能验证，用于检查关卡或玩法行为是否达成预期 |
| Class Default Object（CDO，类默认对象） | UClass 的默认对象，存默认值 |
| .uasset / .umap | UE 的二进制资产/关卡文件，文本工具不可读（本项目经编辑器桥读取） |
| 蓝图（Blueprint） | UE 的可视化脚本，本项目只读分析（ADR-0003） |
| AssetRegistry | UE 的资产注册表，支持按类型/路径查询与依赖分析 |
| NavMesh | 导航网格，AI 寻路数据；本项目用它做白盒关卡可达性校验 |
| path_test（路径测试） | UE 编辑器侧的两点导航可达性检查工具，用于判断白盒路线是否完整或部分可达 |
| Live Coding | UE 的热编译；改头文件/反射宏时不可靠，需完整编译 |
| 白盒（blockout/whitebox） | 用简单几何体/模块件快速搭出关卡结构验证玩法，再做美术替换 |
| Opening Dressing（opening dressing，洞口补件） | 在白盒结构已切好的门窗洞口上，追加匹配宽度与墙向的门框/窗框资产 |
| modular kit | 模块化资产套件（墙/地板/楼梯等），按统一网格尺寸设计 |
| 关卡 metrics | 关卡设计的尺度规范：走廊宽、净空高、跳跃距离、掩体高度等 |
| manifest | 本项目的资产清单：每个模块件的尺寸/pivot/吸附网格/连接语义（ADR-0004） |
| 布局 DSL | 本项目的场景中间表示：格子单位的房间/走廊/连接拓扑，模型的输出物 |
| Model Context Protocol（MCP，模型上下文协议） | LLM 工具接入的开放协议，本项目工具层标准（ADR-0002） |
| LiteLLM | 多 LLM 适配库，把 100+ provider 统一成 OpenAI 格式（ADR-0001） |
| 角色路由 | models.yaml 里按职能（planner/coder/vision）分配不同模型 |
| Architecture Decision Record（ADR，架构决策记录） | 见 docs/architecture/decisions/ |
| Motif（motif，结构模板） | 描述一组资产角色、空间区域和关系约束的可审查摆放模板 |
| Placement Profile（placement profile，摆放画像） | 从资产清单或人工覆写派生的摆放语义，包括设计角色、玩法角色、可放区域和净空要求 |
| Candidate Mask（candidate mask，候选区域掩码） | solver 在房间局部格子上生成的可选/保留区域，如墙边带、中心区、门口保留区 |
| Layout Source（layout source，布局来源） | dry-run facts 中标记本次布局来自 motif solver、motif+legacy fill 或 legacy solver 的字段 |
| Solver（solver，求解器） | 把高层 intent、motif 或 constraint 转成具体布局摆放的确定性逻辑 |
| Beam Search（beam search，束搜索） | 保留 top-K 候选状态的搜索算法，用于避免单步贪心选择破坏后续摆放 |
| Top-K（top-K，前 K 项） | 按评分排序后保留前 K 个候选或状态 |
| Hard Constraint（hard constraint，硬约束） | 不满足时直接拒绝候选摆放的约束 |
| Soft Constraint（soft constraint，软约束） | 不满足时扣 penalty 但不直接拒绝候选摆放的约束 |
| Penalty（penalty，惩罚分） | soft constraint 未满足时扣除的分数，不直接拒绝候选 |
| Facts（facts，事实摘要） | 工具输出中给 runner、eval 或后续步骤消费的结构化事实 |
| Trace（trace，运行轨迹） | 执行过程记录的步骤、工具调用、耗时、结果和证据摘要，用于回放与排障 |
| Rollback（rollback，回滚） | 工具失败或副作用不完整时撤销、清理或恢复本批改动的动作 |
| Axis-Aligned Bounding Box（AABB，轴对齐包围盒） | 与坐标轴平行的最小包围盒，本项目用于快速验证资产占位、碰撞与本地预览 |
| Static Mesh（StaticMesh，静态网格体） | UE 中不可变形的网格资产类型，白盒资产清单以导入后的 StaticMesh bounds 为几何真值 |
| Level Prototyping（LevelPrototyping，关卡原型资源） | UE 第三人称模板中的原型关卡资源目录，本项目默认从 `/Game/LevelPrototyping` 读取白盒资产 |
| ArchKit（ArchKit，建筑原型套件） | `/Game/LevelPrototyping/Meshes/ArchKit` 下的真实白盒建筑/道具资产集合 |
| Render Preview（render_preview，本地渲染预览事实） | `whitebox_render_preview` 生成的 compiler 级视觉证据 fact，记录 contact sheet、几何保真度和拓扑检查结果 |
| Preview Cache（preview cache，预览缓存） | `asset_preview_cache.json` 中保存的资产预览旁路数据；真实外形必须来自 `static_mesh` 顶点/面 |
| Static Mesh Cache（static mesh cache，静态网格缓存） | preview cache 中的 `static_mesh.vertices/faces`，用于本地预览绘制真实 StaticMesh 外形 |
| Silhouette Proxy（silhouette proxy，轮廓代理体） | 用资产俯视轮廓挤出的轻量白盒几何；它不是真实资产外形，当前本地 renderer 不把它当真实证据 |
| Mesh Proxy（mesh proxy，网格代理体） | 用简化顶点和面绘制的轻量白盒几何；它不等同于 StaticMesh cache |
| Fidelity（fidelity，保真度） | 本地预览事实中描述几何证据接近真实资产外形的程度，如 AABB、static mesh 或 partial mesh |
| Fallback（fallback，回退策略） | 主数据或高保真数据缺失时使用的次级确定性路径 |
| Level of Detail（LOD，细节层级） | UE 渲染中按距离或性能需求切换的网格细节版本 |
| Line Trace（line trace，线段射线检测） | UE 中沿起点到终点查询碰撞遮挡的几何验证 |
| Capsule Sweep（capsule sweep，胶囊体扫掠） | UE 中用玩家胶囊体沿路径查询通行碰撞的几何验证 |
