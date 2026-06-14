"""③ 视觉资产识别：对 UE 里的 StaticMesh 渲缩略图 → 多模态模型识别语义/用途 → 合并进 manifest。

定位：①②（wb_asset_scan）以几何真值搞定结构件的尺寸/对齐抓手；命名不规范、几何同形难辨
（柜子/箱子/掩体/填充物）的件交给本脚本——渲一张 3/4 缩略图喂给 vision 角色（config 里的
多模态模型），让它给出 semantic（中文物体名）/ usage（structural/filler/cover/...）/ category。

零新增 C++：渲染复用现成桥命令 spawn_actor（放到 _VisionScan 临时点）+ viewport_screenshot
（定相机）+ delete_actor；识别复用 LiteLLMClient（vision 角色）。

用法（需 UE 编辑器开着 + .env 配好 vision 角色的 API key）：
    uv run python scripts/asset_vision_scan.py                 # 只看 unknown/needs_review，预览
    uv run python scripts/asset_vision_scan.py --all --apply   # 全部 + 合并写回 kit.yaml
    uv run python scripts/asset_vision_scan.py --limit 5       # 先试 5 件
    uv run python scripts/asset_vision_scan.py --skip-existing # 续跑（跳过已渲缩略图的件）

离线/换识别器的两段式（VLM 慢或想人工把关时用）：
    ... --all --render-only                 # 只渲缩略图，不调 VLM
    ... --all --labels labels.json --apply  # 用外部标签(report json 格式)合并，不调 VLM
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import math
import os
import re
import time
from pathlib import Path

from ue5agent.agent.vision_review import image_to_data_url
from ue5agent.config import load_models_config
from ue5agent.llm.client import LiteLLMClient
from ue5agent.mcp_servers.ue_editor.bridge import probe_editor, send_command
from ue5agent.whitebox.manifest import load_manifest
from ue5agent.whitebox.scanner import build_manifest_dict, emit_yaml, records_from_bounds_payload

_ROOT = Path(__file__).resolve().parent.parent
_SCRATCH = (50000.0, 50000.0, 5000.0)  # 远离正常场景的临时摆放点
_THUMB_DIR = _ROOT / "runs" / "asset_thumbs"
_DEFAULT_CONTENT = "/Game/LevelPrototyping/Meshes/ArchKit"
# 资产识别是"看白模归类"的简单任务，用 moonshot 轻量视觉模型即可（实测 ~1s/件）。
# 全局 vision 角色可能配的是 kimi-k2.6 这类推理旗舰——它每件吐 ~1800 token 思维链、~50s/件，
# 用来跑几十件批量极慢。故本脚本对 moonshot 默认改用快模型，不动全局 vision 角色（留给布局审查）。
_FAST_MOONSHOT_VISION = "moonshot/moonshot-v1-8k-vision-preview"

VISION_PROMPT = """\
你是白盒关卡的资产识别员。图中是一件**未上色**的游戏模型（白模），用于关卡搭建。
结合给出的资产名与包围盒尺寸，判断它是什么、在关卡里怎么用。只输出 JSON：
{"semantic": "中文物体名(如 木箱/铁皮柜/卡车/脚手架/带斜面的矮台)",
 "usage": "structural|filler|cover|traversal|decoration|unknown",
 "category": "floor|wall|wall_door|window|pillar|stair|ramp|roof|fence|cover|prop|unknown",
 "confidence": "high|medium|low"}
usage 含义：structural=参与墙/地/门窗拼接对齐；filler=房间内填充家具(柜子/桌子/货架)；
cover=可当掩体的箱体/集装箱；traversal=楼梯/坡道；decoration=管线/装饰小件；unknown=拿不准。
规则：只输出 JSON，不要多余文字；白模无贴图时按形状+尺寸+名字综合判断；拿不准给 low。
"""

_FENCE = re.compile(r"^```(?:json)?\s*(?P<body>.*?)\s*```\s*$", re.DOTALL)

# 运行唯一标记 + 计数器：spawn 的 actor 名绝不复用。UE 删除是"标记销毁+延迟 GC"，
# 旧名在 GC 前仍占命名空间，复用同名 spawn 会触发引擎 Fatal（Cannot generate unique name）
# 直接崩编辑器——这是本项目已知坑（见 wb_build），渲缩略图同样必须用唯一名。
_RUN_TAG = time.strftime("%H%M%S")
_spawn_counter = itertools.count()


def _load_dotenv() -> None:
    """把仓库 .env 的 KEY=VALUE 注入 os.environ（已存在的不覆盖）。"""
    env = _ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _camera_for(bounds_min, bounds_max):
    """按资产包围盒算一个 3/4 俯视相机（location + [pitch,yaw,roll]）。"""
    center = tuple(_SCRATCH[i] + (bounds_min[i] + bounds_max[i]) / 2 for i in range(3))
    size = [bounds_max[i] - bounds_min[i] for i in range(3)]
    viewdir = (1.0, 1.0, -0.6)
    vlen = math.sqrt(sum(c * c for c in viewdir))
    viewdir = tuple(c / vlen for c in viewdir)
    dist = max(size) * 2.5 + 200
    cam = [center[i] - viewdir[i] * dist for i in range(3)]
    pitch = math.degrees(math.asin(viewdir[2]))
    yaw = math.degrees(math.atan2(viewdir[1], viewdir[0]))
    return cam, [pitch, yaw, 0.0]


def render_thumbnail(asset: dict, out_path: Path) -> str | None:
    """spawn → 定相机截图 → delete，返回截图路径；失败返回 None。"""
    # 唯一名：含运行标记 + 自增序号，绝不复用（复用同名 spawn 会崩编辑器，见 _RUN_TAG 注释）
    name = f"VISIONSCAN_{_RUN_TAG}_{next(_spawn_counter)}"
    cam, rot = _camera_for(asset["min"], asset["max"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sp = send_command(
        "spawn_actor",
        {
            "type": "StaticMeshActor",
            "name": name,
            "static_mesh": asset["path"],
            "location": list(_SCRATCH),
            "folder_path": "_VisionScan",
        },
        timeout=60,
    )
    if sp.get("status") == "error":
        print(f"    spawn 失败：{sp.get('error')}")
        return None
    try:
        ss = send_command(
            "viewport_screenshot",
            {"file_path": str(out_path.resolve()), "location": cam, "rotation": rot},
            timeout=60,
        )
        if ss.get("status") == "error":
            print(f"    截图失败：{ss.get('error')}")
            return None
    finally:
        send_command("delete_actor", {"name": name}, timeout=60)
    return str(out_path)


def parse_vision(text: str) -> dict:
    body = text.strip()
    fence = _FENCE.match(body)
    if fence:
        body = fence.group("body")
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return {
            "semantic": "",
            "usage": "unknown",
            "category": "unknown",
            "confidence": "low",
            "raw": text,
        }
    return data if isinstance(data, dict) else {"raw": text}


async def identify(llm: LiteLLMClient, png: str, name: str, size) -> dict:
    uu = [round(s) for s in size]
    meters = [round(s / 100, 1) for s in size]
    hint = f"资产名：{name}\n包围盒尺寸(uu)：{uu}（约 {meters} 米）"
    messages = [
        {"role": "system", "content": VISION_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": hint},
                {"type": "image_url", "image_url": {"url": image_to_data_url(png)}},
            ],
        },
    ]
    turn = await llm.acomplete("vision", messages)
    return parse_vision(turn.content or "")


# vision usage → 追加到 manifest tags 的标签
_USAGE_TAG = {
    "filler": "filler",
    "cover": "cover",
    "structural": "structure",
    "traversal": "traversal",
    "decoration": "decoration",
}


def _load_labels(path: str) -> dict[str, dict]:
    """读外部标签文件（report json 格式的 list，或 {key: {...}} 的 dict）→ {key: 标签}。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        return {x["key"]: x for x in data if isinstance(x, dict) and x.get("key")}
    return data if isinstance(data, dict) else {}


async def run(args) -> None:
    _load_dotenv()
    # 三种模式：默认调 VLM；--render-only 只渲图；--labels 用外部标签合并（都不调 VLM）
    use_vlm = not args.render_only and not args.labels
    llm = None
    if use_vlm:
        models = load_models_config(_ROOT / "config" / "models.yaml")
        if not models.has_vision:
            raise SystemExit("[error] 未配置 vision 角色，无法做视觉识别（见 config/models.yaml）")
        # 选识别模型：显式 --vision-model 优先；moonshot 默认换快模型，其余保持全局 vision 角色
        configured = models.roles.get("vision", "")
        chosen = args.vision_model or (
            _FAST_MOONSHOT_VISION if configured.startswith("moonshot/") else configured
        )
        models.roles["vision"] = chosen
        print(f"识别模型：{chosen}")
        llm = LiteLLMClient(models)
    labels = _load_labels(args.labels) if args.labels else {}

    if not probe_editor():
        raise SystemExit("[error] 编辑器桥不可达：请先打开 UE 编辑器并加载 agent_test 工程再跑")

    resp = send_command(
        "scan_assets", {"content_path": args.content_path, "recursive": True}, timeout=180
    )
    if resp.get("status") == "error":
        raise SystemExit(f"[error] scan_assets 失败：{resp.get('error')}（插件重编了吗？）")
    raw_assets = resp.get("result", {}).get("assets", [])
    by_path = {a["path"]: a for a in raw_assets}
    records = records_from_bounds_payload(raw_assets)
    existing = (
        load_manifest(_ROOT / "config" / "whitebox" / "kit.yaml")
        if (_ROOT / "config" / "whitebox" / "kit.yaml").exists()
        else None
    )
    manifest = build_manifest_dict(records, existing=existing)

    # 选目标：默认只看 unknown / needs_review（几何兜不住的），--all 则全量
    targets = []
    for key, a in manifest["assets"].items():
        if args.all or a["category"] == "unknown" or a.get("needs_review"):
            targets.append((key, a))
    targets.sort()
    if args.limit:
        targets = targets[: args.limit]
    # 续跑：跳过已识别过的件（缩略图已存在），重开编辑器后再跑只补未完成的
    if args.skip_existing:
        targets = [(k, a) for k, a in targets if not (_THUMB_DIR / f"{k}.png").exists()]
    print(f"待视觉识别 {len(targets)} 件（content={args.content_path}，all={args.all}）\n")

    _THUMB_DIR.parent.mkdir(parents=True, exist_ok=True)
    report_path = _ROOT / "runs" / f"asset_vision_{time.strftime('%Y%m%d-%H%M%S')}.json"
    report = []
    aborted = False
    for key, a in targets:
        ap = by_path.get(a["path"])
        if ap is None:
            continue
        # render-only / labels 模式下若缩略图已存在则不重渲；labels 模式根本不需要渲
        need_render = not args.labels and not (_THUMB_DIR / f"{key}.png").exists()
        if need_render or args.render_only:
            try:
                png = render_thumbnail(ap, _THUMB_DIR / f"{key}.png")
            except (ConnectionRefusedError, OSError, ConnectionError) as exc:
                # 桥中途掉线（编辑器崩/关）：保存已完成进度，优雅退出而非整段 traceback
                print(f"\n[中断] 编辑器桥掉线于 {key}：{exc}")
                print(f"已完成 {len(report)} 件，重开编辑器后用 --skip-existing 续跑")
                aborted = True
                break
            if not png:
                continue
        if args.render_only:
            print(f"  渲染 {key}")
            continue

        if args.labels:
            res = labels.get(key)
            if not res:
                continue  # 该件没有外部标签，跳过合并
        else:
            res = await identify(llm, str(_THUMB_DIR / f"{key}.png"), key, a["size"])
        sem = str(res.get("semantic", "")).strip()
        usage = str(res.get("usage", "unknown")).strip()
        cat = str(res.get("category", "")).strip()
        conf = str(res.get("confidence", "low")).strip()
        report.append({"key": key, **res})
        print(f"  {key:24} → {sem or '?':12} usage={usage:11} cat={cat:8} conf={conf}")

        # 合并进 manifest（只用现有支持字段：desc / tags / category）
        if sem:
            a["desc"] = sem
        tag = _USAGE_TAG.get(usage)
        if tag and tag not in a["tags"]:
            # 视觉给出明确用途后，几何阶段的 "unknown" 占位标签就没意义了，去掉
            a["tags"] = [t for t in a["tags"] if t != "unknown"] + [tag]
        # 几何判 unknown 时，medium/high 的视觉类别都比 unknown 强，采用；low 仅记 desc
        if cat and cat != "unknown" and conf in {"high", "medium"}:
            a["category"] = cat
        if conf == "high" and cat and cat != "unknown":
            a.pop("needs_review", None)
            a.pop("review", None)

        # 增量落盘：一件一存，桥中途掉线也不丢已完成进度
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        await asyncio.sleep(0.3)  # 给编辑器喘息，降低连发 spawn/截图的压力

    print(f"\n识别报告 → {report_path}")
    print(f"缩略图 → {_THUMB_DIR}")
    if aborted and not args.apply:
        return

    out = Path(args.out) if args.out else (_ROOT / "config" / "whitebox" / "kit.yaml")
    if args.apply:
        out.write_text(emit_yaml(manifest), encoding="utf-8")
        print(f"已合并视觉结果并写出 manifest → {out}")
    else:
        print(f"预览模式：未写盘。确认报告后加 --apply 合并写出到 {out}")


def main() -> None:
    p = argparse.ArgumentParser(description="视觉资产识别 → 合并进白盒 manifest")
    p.add_argument("--content-path", dest="content_path", default=_DEFAULT_CONTENT)
    p.add_argument(
        "--all", action="store_true", help="识别全部资产（默认只看 unknown/needs_review）"
    )
    p.add_argument("--apply", action="store_true", help="把识别结果合并写回 manifest")
    p.add_argument("--out", default="", help="写出路径（默认 config/whitebox/kit.yaml）")
    p.add_argument("--limit", type=int, default=0, help="最多识别几件（先试跑用）")
    p.add_argument(
        "--skip-existing",
        dest="skip_existing",
        action="store_true",
        help="跳过缩略图已存在的件（编辑器中途崩溃后续跑用）",
    )
    p.add_argument(
        "--render-only",
        dest="render_only",
        action="store_true",
        help="只渲缩略图、不调 VLM（之后可人工/离线识别）",
    )
    p.add_argument(
        "--labels",
        default="",
        help="用外部标签文件(report json 或 {key:{...}})合并，不调 VLM",
    )
    p.add_argument(
        "--vision-model",
        dest="vision_model",
        default="",
        help="覆盖识别模型(如 moonshot/moonshot-v1-32k-vision-preview)；默认 moonshot 用轻量快模型",
    )
    args = p.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
