# ADR-0009：白盒默认结构改为 slab-first

- 状态：已接受
- 日期：2026-06-14

## 背景

ADR-0007 之后，白盒结构层默认接入 ArchKit 模块资产，能更接近真实模块化场景，也暴露出默认规划的偏差：模型容易把“读空间”的早期白盒误解为“读资产拼装”，把门框、窗框、navproxy、多层 room 当作默认表达方式。对关卡策划的第一轮 blockout 来说，更重要的是连续地面、片墙、开合、遮挡、转角和比例，而不是模块资产细节。

同时，多层 room 让默认 agent 容易通过上层空间逃避单层平面组织问题。楼梯作为空间构件仍然有价值，但默认生成二层/三层房间会扩大验证面，并把早期白盒拉向灰盒/模块搭建阶段。

## 决策

我们决定将白盒布局默认结构模式改为 `structure_mode="slab"`：使用 Engine Cube 生成连续地板与连续片墙，`doors`/`windows` 只参与墙体切分，不默认生成门框/窗框模块或 navproxy。默认 slab 模式只允许 `room.level=0`；楼梯可以连接 `from_level=0,to_level=1`，但只生成楼梯 mesh 与楼梯间护墙，不生成上层 room 的 floor/wall。

旧 ArchKit 模块化结构保留为显式 `structure_mode="modular"`，继续承担旧 floor/wall/door/window/navproxy 和多层 room 行为。ArchKit 仍用于楼梯、props、cover、pillar，以及后续灰盒替换或显式模块化路径。

## 备选与取舍

- 继续 ArchKit kit-first：资产感更强，但默认输出容易碎片化，并让视觉审查误扣“缺装饰/缺门窗框”。
- 立即引入 `floors/walls/zones/slices` 新 DSL：表达力更清晰，但会扩大本轮接口和迁移成本；当前先沿用 `rooms/doors/windows/stairs/gameplay`。
- 完全移除 modular 多层能力：默认行为会更干净，但会破坏已有 ArchKit 多层样例和回归测试；因此改为显式 opt-in。

## 后果

默认白盒更适合作为空间组织草模，构件数量更少，`wb_validate` 的结构指标更贴近 floor/wall 连续性。视觉审查需要按 blockout 空间质量评估，不因缺少门框/窗框扣分。

代价是旧用法如果依赖 ArchKit 门窗模块、navproxy 或多层 room，必须在布局 JSON 顶层显式写 `structure_mode="modular"`。后续如果要表达任意楼板、片墙、区域切片，应另行设计 space-slicing DSL，并以新的 ADR 记录。
