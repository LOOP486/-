# ADR-0007：限定白盒结构拉伸与非结构件原生尺寸边界

- 状态：已接受
- 日期：2026-06-13

## 背景

ArchKit 接入后，白盒结构层需要干净连续的墙体轮廓。实测发现当前 corner 件体积过大，直接拼角会穿模；改用 `Wall1_4` 沿墙段拉伸并在东西墙端部按墙厚缩进，可以形成稳定的 butt joint 转角。与此同时，楼梯、掩体、柱子、道具若被拉伸，会破坏玩法尺度、碰撞预期和资产语义。

## 决策

结构墙允许并继续使用 `_fit_placement` 拉伸：当前 ArchKit 主路径固定优先 `Wall1_4`，用目标 AABB 适配墙长/墙厚/层高，并通过 butt joint 对齐转角。非结构白盒件（stair、prop、cover、pillar）必须走原生尺寸放置路径，编译输出 `scale=(1,1,1)`；它们只允许平移与 90 度朝向旋转，不用缩放补格。真实 gameplay 出生点使用 `PlayerStart` actor，不伪装成 StaticMeshActor。

## 备选与取舍

- 全部模块都不拉伸：墙段会退回多件拼接，接缝和外沿错位更明显，也会重新暴露 corner 件问题。
- 全部模块统一拉伸：实现简单，但楼梯高度、掩体宽度、柱子直径和道具语义都会被破坏，玩法校验失去可信尺度。
- 重新启用 corner 件：当前资产不是墙厚级角件，和直墙叠放穿模；等有合适 corner 资产后可用新 ADR 替代本决策。

## 后果

- compiler 需要维护两条明确路径：结构墙用 `_fit_placement` 适配目标 AABB，非结构件用 native placement 保持原生尺度。
- validator 的 wall/floor metrics 必须排除 PlayerStart、route marker、navproxy 与玩法 props，避免玩法层污染结构指标。
- 资产 manifest 的 `needs_review` 继续影响自动选择；显式指定资产仍由调用者承担语义风险。
