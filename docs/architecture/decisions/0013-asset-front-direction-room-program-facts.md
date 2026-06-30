# ADR-0013：资产正面方向与 room-program facts

- 状态：已接受
- 日期：2026-06-25

## 背景

B9 的 dressing solver 已经能把办公室/通用房间从 intent 转成可验证的 `rooms[].props[]`，但真实 StaticMesh 里有一类资产不是各向同性盒子：例如 `Cube_003`、`Cube_004`、`Cube_005` 这类机柜、柜台或带斜面/缺口的块体，贴墙时必须区分正面和背面。只靠 footprint、visual AABB 和 yaw 数字，validator 只能确认“位置对了”，不能确认“正面没有朝墙”。

同时，大空间陈设不能只输出一串坐标或总密度指标。下一轮 eval 需要先知道墙体/房间边界，再说明墙边、中心、远侧空白和通行动线分别承担什么用途，避免“有物件但空间意图不清”。

## 决策

manifest 增加可选 `front_direction` 字段，取值限定为 `north/east/south/west`。它描述资产在 yaw=0 时本地正面朝向；缺省为空时保留旧行为。资产扫描重建 manifest 时会保留已有人工 `front_direction` 标注，避免重扫丢失语义。

靠墙/角落 dressing 放置时，如果资产带 `front_direction`，solver 按目标墙面的室内方向计算 yaw，使资产正面朝向房间内部；如果资产没有该字段，则继续使用原有 fallback yaw，保证旧资产和旧测试不被整体翻转。

`wb_dressing_dry_run` 透出 `room_program` facts。通用房间 solver 会为每个房间列出 `wall_storage`、`primary_work_area`、`space_divider`、`secondary_activity_area`、`circulation` 等功能区及用途说明。后续 eval 可以直接检查 room-program 是否存在，再检查密度、sector 覆盖和视觉结果。

## 后果

`Cube_003`、`Cube_004`、`Cube_005` 已在 ArchKit manifest 中标注 `front_direction: north`。这类有正反面的资产贴墙时不再只按普通盒体处理。

`room_program` 不改变最终布局 DSL 的边界：Agent 仍只提交 dressing intent，不提交最终坐标、yaw 或 offset；room-program 是 solver/dry-run 的可解释事实，用来辅助验收和失败诊断。