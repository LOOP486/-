# 上手指南

## 前置条件

- Windows 10/11，git
- [uv](https://docs.astral.sh/uv/)（Python 由 uv 托管，无需单独安装）：`winget install astral-sh.uv`
- 需要工程能力时：本机装有 UE5 引擎（写下引擎根目录备用）

## 安装

```powershell
git clone <repo-url> ue5agent
cd ue5agent
.\scripts\setup.ps1     # 同步依赖 + 生成本机配置模板
```

## 配置

1. `config/models.yaml`：填角色路由。至少配 `planner`；要用截图视觉验证必须配 `vision`（多模态模型）。
2. `.env`：填各 provider 的 API key，以及 `UE_ENGINE_ROOT` / `UE_UPROJECT`。
3. `config/agent.yaml`（可选）：调整 MCP server 挂载与运行限额。

以上三个文件都不入库；模板分别是 `config/models.example.yaml`、`.env.example`、`config/agent.example.yaml`。

## 验证

```powershell
uv run ue5agent check-config   # 配置校验 + 角色路由表
uv run pytest -q               # 单元测试应全绿
uv run ue5agent chat           # 进入交互会话
```

chat 里试一句「编译 MyGameEditor」——agent 会调用 ue_build 的 `ubt_compile` 并回报结构化错误。

## 白盒布局 JSON 速记

`wb_build` 接收布局 DSL，单位为格（默认 1 格 = 100uu）。最小结构层只需要 rooms。
`structure_mode` 缺省为 `slab`：连续地板、连续片墙、门窗只作为墙洞，不默认放门框/窗框模块。
`scale_profile` 缺省为 `realistic`：视觉/LLM 负责理解空间结构，真实米制尺度由
`config/whitebox/level_metrics.yaml` 控制，并通过 `wb_validate` 的 `scale_warnings` 暴露。
提供 `gameplay` 时才会额外生成真实 `PlayerStart`、route markers、cover/pillar。
`gameplay.spawn_points` 与 `gameplay.routes` 只有缺省时才走默认生成；显式写 `[]` 表示关闭
对应默认出生点或路线。

```json
{
  "name": "single_level_slab",
  "scale_profile": "realistic",
  "origin": [0, 0, 0],
  "wall_height": 400,
  "rooms": [
    {
      "name": "main",
      "rect": [0, 0, 8, 8],
      "doors": [{"wall": "east", "at": 2, "width": 2}],
      "windows": [{"wall": "north", "at": 1, "width": 2}]
    },
    {
      "name": "side",
      "rect": [8, 0, 4, 8],
      "doors": [{"wall": "west", "at": 2, "width": 2}]
    }
  ],
  "stairs": [
    {"room": "main", "at": [1, 1], "from_level": 0, "to_level": 1, "facing": "north"}
  ],
  "gameplay": {}
}
```

slab 模式只允许 `room.level=0`；楼梯可以写 `from_level=0,to_level=1` 作为空间构件，
即使没有上层 room，也只会生成楼梯 mesh 与楼梯间护墙，不生成二层空间。
地板、片墙和楼梯间护墙使用 `/Engine/BasicShapes/Cube.Cube`；楼梯、掩体、柱子、props
仍使用资产原生尺寸，生成结果的 `scale` 应为 `[1, 1, 1]`。如果需要旧 ArchKit 地板/墙/
门/窗/navproxy 和多层 room 行为，在顶层显式写 `"structure_mode": "modular"`。
`windows` 只允许开在外墙；相邻房间的共享墙只能用对齐的 `doors` 表达连通或开口，否则会造成
一侧切洞、一侧留墙的双墙问题并被编译器拒绝。
显式 props 与自动 cover/pillar 会避开门洞、同房间门到门 corridor 和 gameplay 主路线；
required prop 冲突会报错，optional prop 冲突会跳过。楼梯会额外避开同房间对穿门的直通
corridor，避免把穿堂动线切断。

白盒搭建前可用 `wb_asset_audit` 对照 manifest 与 UE 导入后的 StaticMesh bounds；搭建后用
`wb_validate` 检查 actor transform、visual AABB、残留批次、route blocker、空间尺度 warnings 与
metrics。若任务要求
截图/视觉自查，计划步骤应声明 `required_evidence=["screenshot", "vision_review"]`，缺硬证据时
runner 不会只凭 `wb_validate` 放行。`viewport_screenshot` 会对落盘 PNG 做轻量取景快检：截图文件
不存在、主体占比过小或主体贴在画面边缘时，`screenshot` fact 会标为 `ok=false`，该步需要重新取景。
`wb_build` 在 spawn 中途失败时会自动按同前缀回滚半批次；若错误文本提示自动回滚失败，先执行
`wb_clear(prefix=...)` 清干净现场，再重新 `wb_build`。

## 常见问题

- `uv: 无法识别` → 重开终端（安装后 PATH 需要重载）。
- LLM 报 401 → 检查 `.env` 的 key 与 `models.yaml` 的 `api_key_env` 名称是否对应。
- `ubt_compile` 报「引擎路径不对」→ `UE_ENGINE_ROOT` 应指向含 `Engine/` 的根目录，如 `C:/Program Files/Epic Games/UE_5.5`。
- 编辑器类工具（场景/蓝图/白盒）报「编辑器桥连接被拒」→ 这些工具需要 UE 编辑器开着（UnrealMCP 插件随工程加载）。用 `editor_status` 工具可先查在线状态；想让 agent 自己启动编辑器，在 `agent.yaml` 挂载 `ue_lifecycle`（dangerous 级）并把 `ue_lifecycle__editor_launch` 加进 `permissions.allowlist`（见 example）。
