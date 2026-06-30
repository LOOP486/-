# 文档地图

## 当前状态入口

当前项目状态只看两处：

- [roadmap.md](roadmap.md)：任务完成度、剩余工作和短期方向。
- [../CHANGELOG.md](../CHANGELOG.md)：已经落地的用户可见变化。

[worklog.md](worklog.md) 是历史交接和踩坑史，不再作为最新状态权威。`runs/**/*.md` 是自动生成的运行报告/进度文件，不纳入手工维护索引。

| 目录 | 放什么 | 更新时机 |
|---|---|---|
| [architecture/design.md](architecture/design.md) | 整体架构设计（唯一权威版本） | 架构演进时 |
| [architecture/decisions/](architecture/decisions/) | ADR：每个重大决策一篇，编号递增 | 做出/推翻重大决策时 |
| [guides/](guides/) | 操作性文档：上手、开发工作流 | 步骤变化时 |
| [guides/floorplan-whitebox.md](guides/floorplan-whitebox.md) | 平面图图片生成白盒：CLI 入口、v1 范围、验收方式 | 平面图输入流程变化时 |
| [guides/whitebox-stability.md](guides/whitebox-stability.md) | 白盒构型稳定性：失败样本台账、分桶流程、回归命令 | 新增失败样本或稳定性防线时 |
| [portfolio.html](portfolio.html) | 作品集展示页：项目定位、架构、能力、证据与路线摘要 | 展示口径或证据资产变化时 |
| [roadmap.md](roadmap.md) | 阶段计划与任务清单 | 任务完成或计划调整时 |
| [worklog.md](worklog.md) | 历史接手状态、踩坑史、真机环境备注 | 只追加关键踩坑，不写最新状态 |
| [reference/](reference/) | 术语表等查阅型资料 | 随需 |
| [../CHANGELOG.md](../CHANGELOG.md) | 对外可见行为变化与发布记录 | 用户可见行为变化时 |

## 历史施工档案

这些文件保留背景和验收过程，不作为当前计划来源：

- [development-plan.md](development-plan.md)：Stage A-E 施工蓝图，主线已完成。
- [kernel-refactor-plan.md](kernel-refactor-plan.md)：Agent Kernel 重构旧方案，现状以 [architecture/decisions/0006-kernel-state-machine.md](architecture/decisions/0006-kernel-state-machine.md) 和 [architecture/design.md](architecture/design.md) 为准。
- [phase1-bridge-plan.md](phase1-bridge-plan.md)：Phase 1 编辑器桥施工清单，已收口。
- [stage-e-plan.md](stage-e-plan.md)：Phase 3 行为闭环施工清单，已收口。
- [superpowers/plans/2026-06-15-whitebox-eval-optimization.md](superpowers/plans/2026-06-15-whitebox-eval-optimization.md)：B7 白盒评测复盘执行记录。

## 维护规则

1. **一个事实只写在一处**，其它地方用链接。当前状态归 roadmap.md，行为变化归 CHANGELOG.md，架构事实归 design.md，决策理由归 ADR，操作步骤归 guides。
2. **ADR 不可改写**：已接受的 ADR 状态只能变为「被 XXXX 取代」，新决策写新 ADR——决策历史本身是资产。
3. 文档跟代码同一个 PR/提交更新，不积压。
