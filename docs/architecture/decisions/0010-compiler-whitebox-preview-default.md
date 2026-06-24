# ADR-0010：白盒视觉门禁默认使用 compiler 本地预览

- 状态：已接受
- 日期：2026-06-22

## 背景

白盒视觉门禁最初依赖 UE 编辑器的 `viewport_screenshot`：agent 需要 UE 在线、有活动 Level Editor 视口、相机取景正确，并且截图里不能混入旧批次或编辑器 UI。这个链路能提供真实落地画面，但在 agent 自主循环中经常把环境状态、取景失败和布局质量混在一起，导致本应由 compiler/DSL 解决的问题被 UE 视口状态阻塞。

当前白盒默认是 slab-first，空间结构由 layout DSL 经 compiler 生成 placement。对 agent 的快速自查来说，最重要的是确认 compiler 视角下的墙体、地板、楼梯、开口和整体拓扑是否合理，不必每轮都依赖 UE 视口。

## 决策

我们决定新增 compiler 级本地白盒预览：`whitebox_render_preview` 读取 `layout_json`、`layout_path` 或 `layout_artifact`，先调用白盒 compiler 得到 placements，再用本地 Pillow 渲染 top/iso 多角度 contact sheet，并产生 `render_preview` fact。

当输入是显式 `walls[]` DSL 时，`whitebox_render_preview` 还会在 compiler 层构建墙图拓扑：拆分交点、统计连通组件、端点、T/cross junction，并检测断角近距未连接、孤立墙段和共线重叠。高危拓扑问题会让 `render_preview.ok=false`，同时在 facts 中写入 `wall_topology`，使 agent 在进入视觉模型前先处理确定性几何错误。

白盒任务只要自然语言要求视觉审查/自查，planner 默认声明 `required_evidence=["render_preview", "vision_review"]`，并加入 `whitebox_render_preview`。runner 在拿到 `wb_build`、必要校验 facts 与 `render_preview` 后，将 contact sheet 交给现有 vision reviewer。只有用户明确要求 UE 视口截图、`viewport_screenshot`、真机截图或编辑器截图时，planner 才改用 `required_evidence=["screenshot", "vision_review"]`，加入 `viewport_screenshot` 并声明 `editor_online`。

## 备选与取舍

- 继续默认 `viewport_screenshot`：保留真实 UE 画面，但会把活动视口、相机、旧场景污染、编辑器 UI 和 bridge 状态纳入默认阻塞面。
- 完全移除 UE 截图：会降低最终落地验收的真实性，也破坏已有显式截图工作流；因此保留为显式 opt-in。
- 只做 JSON/placement 文本检查：速度最快，但 vision 模型无法直观看空间组织，缺少多角度几何证据。

## 后果

默认白盒视觉链路不再要求 UE 有活动视口，agent 可以在 compiler 层快速生成稳定、可重复的视觉证据，降低环境问题造成的空转。`render_preview` contact sheet 与 `wall_topology` metrics 也更适合比对 DSL 拓扑和 compiler placement 是否一致。

代价是默认视觉门禁看到的是 compiler 抽象预览，不是 UE 最终材质、灯光或编辑器截图。需要验证真实 UE 画面、资产导入外观或最终取景时，应在任务中明确要求 UE 视口截图。
