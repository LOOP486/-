# 平面图生成白盒

本指南用于第一版“本地平面图图片 → 墙线/白盒 DSL → UE 白盒落地”流程。
当前主路径是图像算法优先：先从黑色粗墙体像素提取水平/垂直墙线，生成
SVG、叠加预览和 `walls` DSL；只有算法提不出墙线时，才回退到 vision 角色做
拓扑优先的房间识别。

## 使用方式

```powershell
uv run ue5agent run --floorplan .\test.png "根据这张平面图生成默认 slab 白盒，拓扑优先，做截图和导航验证" --yes
```

`--floorplan` 只接受单张本地图片：`.png`、`.jpg`、`.jpeg`、`.webp`。
如果墙线算法成功，不要求配置 `vision` 角色；若算法失败且需要回退识别，则必须在
`config/models.yaml` 配置 `vision` 角色。

agent 也可以在普通任务中直接调用内置工具：

```text
floorplan_extract_walls(image_path="C:/path/to/plan.png", output_dir="runs/wall-extract")
```

工具会输出完整墙体 SVG、统一线宽中心线 SVG、半透明原图叠加 PNG、`layout_walls.json`
、`snap_report.json` 和 `summary.json`，并回传 `floorplan_wall_extraction` facts。

如果已有经人工或算法确认的 `wall_lines.svg`，可跳过图像检测阶段，直接把 SVG line 坐标映射到
整数格 DSL：

```text
floorplan_svg_to_grid_dsl(line_svg="runs/wall-extract/wall_lines.svg", units_per_grid="auto")
```

这个工具不会重新看图片，也不再做像素检测；它只读取 SVG 中的 `<line x1 y1 x2 y2>` 数据，
按 `units_per_grid` 进行一次 snapping，输出 `layout_walls.json` 与 `snap_report.json`。
`units_per_grid` 是“1 个 DSL 格对应多少 SVG 坐标单位”，传数字可固定比例，传 `"auto"` 会在候选
比例中选择不丢墙、重复少、snap 误差小的一档。

如果 vision 或图像算法已经识别出门洞两侧端点，可用标准门宽反推真实比例。默认约定
`target_door_width_grid=1`，即常规平开门宽约 1m，对应当前 realistic 尺度下 1 个 DSL 格：

```text
floorplan_calibrate_doors_to_grid_dsl(
  line_svg="runs/wall-extract/wall_lines.svg",
  door_candidates=[
    {"id": "door_a", "x1": 240, "y1": 318, "x2": 252, "y2": 318, "confidence": 0.9}
  ],
  apply_openings=true
)
```

该工具只把门候选当作尺度锚点：先取门宽中位数并剔除明显离群值，反推出
`units_per_grid`，再复用 SVG→DSL 转换。`apply_openings=true` 时，会把能投影到连续墙段上的
门写入 `walls[].openings`；若墙线本身已经在门洞处断开，可能只完成尺度标定而不写开口，
这类未匹配门会记录在 `door_calibration_report.json`。

## v1 行为

- 先用确定性图像算法提取墙线：深色像素阈值 → 墙体主厚度估计 → 只保留厚度一致的
  水平/垂直墙段 → 导出统一线宽中心线 SVG。
- `walls` DSL 不从图片像素扫描结果直接生成，而是读取 `wall_lines.svg` 的精确 line 坐标，
  再映射到整数格；这样 SVG 是可审查、可复用的几何源数据。
- 门宽标定工具接受视觉/图像算法输出的门洞端点候选，用常规门宽反推 `units_per_grid`，
  解决不同图片分辨率下默认比例不稳定的问题；最终 DSL 坐标仍由确定性几何 snapping 生成。
- 墙线算法结果会写入 `runs/<session>/artifacts/floorplans/wall_extraction/`：完整墙体
  `wall_body.svg`、统一线宽 `wall_lines.svg`、叠加预览 PNG、`layout_walls.json` 和
  `snap_report.json`、`summary.json`。
- trace 会写入 `floorplan_wall_extraction` fact，包含 `line_count`、`body_rect_count`、
  `wall_thickness_mode_px`、`line_svg`、`layout_json` 与 `snap_report_json` 等证据路径。
- 若墙线算法无法生成可用墙段，才由 vision 角色识别平面图，输出严格 JSON，其中
  `layout` 必须是现有 `wb_build` 可用的 `layout_json`。
- vision 回退结果会写入 `runs/<session>/artifacts/floorplans/`：输入图、原始 vision 回答、
  规范化后的 `layout.json`。
- trace 会写入 `floorplan_recognition` fact，包含 `ok`、`confidence`、`room_count`、
  `warning_count`。
- 若 vision 输出有尾部说明、房间字段使用 `label/id`，会在进入 DSL 校验前做确定性归一化。
  若多房间原始几何重叠或不连通，v1 会过滤明显室外空间并回退为紧凑连通的拓扑优先安全布局，
  同时降低 `confidence` 并在 `warnings` 写明原因。
- 规范化后的 `layout.json` 只保留白盒 DSL 支持的顶层字段；`corridor`、`spawn_points`、
  `cover` 等额外字段不会进入后续 `wb_build` 提示。
- 若识别失败，仍会保存输入图和 `recognition_raw.txt`，trace 中记录 `ok=false` 的
  `floorplan_recognition` fact，便于调 prompt 或手工排查。
- 识别成功后，任务文本会被增强为：优先使用该 `walls` layout_json 调 `wb_build`，再执行
  `wb_validate`、`viewport_screenshot`、`vision_review`、`navmesh_rebuild` 和 `path_test`。
  v1 会提示截图使用 `focus_prefix="WB"`、`margin=6.0`、`clean_view=true`，减少宽屏视口下
  俯视白盒贴边造成的截图重试。

## 范围限制

- 墙线算法只支持水平/垂直墙段；斜墙/曲墙需要后续人工或 vision 辅助归一化。
- 默认 `structure_mode="slab"`、`scale_profile="realistic"`。
- 不生成 gameplay、props、cover、spawn_points、routes。
- 门洞优先成对生成；不确定窗户时省略 `windows`。
- 不支持多图融合、chat 中隐式粘贴图片路径、曲墙/斜墙/非正交空间精确还原。

## 回归建议

离线测试：

```powershell
uv run pytest tests/test_floorplan_wall_extractor.py tests/test_floorplan_tools.py tests/test_floorplan_intake.py tests/test_evals.py::TestUeSuite::test_ue_task_loads_floorplan_image_field -q
```

门宽尺度标定可单独回归：

```powershell
uv run pytest tests/test_floorplan_door_calibration.py tests/test_floorplan_tools.py -q
```

全量离线回归可用：

```powershell
uv run python scripts/run_tests.py
```

结果写入 `runs/pytest_out.txt`。

UE 在线验收使用真实平面图跑 `run --floorplan`。通过标准：

- trace 有 `floorplan_wall_extraction.ok=true`；若走 vision 回退，则有
  `floorplan_recognition.ok=true`。
- `wb_validate.ok=true`。
- 截图 fact 中 `framing_ok=true`。
- `vision_review.high_count=0`。
- 至少一条 `path_test.reachable=true`。
