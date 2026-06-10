# 文档地图

| 目录 | 放什么 | 更新时机 |
|---|---|---|
| [architecture/design.md](architecture/design.md) | 整体架构设计（唯一权威版本） | 架构演进时 |
| [architecture/decisions/](architecture/decisions/) | ADR：每个重大决策一篇，编号递增 | 做出/推翻重大决策时 |
| [guides/](guides/) | 操作性文档：上手、开发工作流 | 步骤变化时 |
| [roadmap.md](roadmap.md) | 阶段计划与任务清单 | 任务完成或计划调整时 |
| [reference/](reference/) | 术语表等查阅型资料 | 随需 |

## 维护规则

1. **一个事实只写在一处**，其它地方用链接。架构事实归 design.md，决策理由归 ADR，操作步骤归 guides。
2. **ADR 不可改写**：已接受的 ADR 状态只能变为「被 XXXX 取代」，新决策写新 ADR——决策历史本身是资产。
3. 文档跟代码同一个 PR/提交更新，不积压。
