"""白盒资产扫描：把 UE 导入后的 StaticMesh 真值（bounds）转成 manifest v2。

与 scripts/fbx_probe.py 的分工：
- fbx_probe 是**导入前**离线 FBX 草稿体检，单位/包围盒只能近似，路径只能占位；
- 本模块以**导入后 StaticMesh bounds 为真值**（ADR-0008）扫描，结果直接 calibrated，
  path 即真实 /Game 路径——消除"重导后手工回填 path / 尺寸漂移"的痛点。

归类策略（比 fbx_probe 的"纯命名前缀"更鲁棒）：
1. 名称前缀规则命中 → 高精度，category 直接采用；
2. 命中不到（命名不规范）→ 用包围盒长宽高比做**几何先验**兜底，把件从 unknown 里捞出来，
   但一律标 needs_review（几何只能猜大类，分不清墙/窗/门这种"同形不同义"）；
3. 命名命中但几何明显矛盾（如名为 floor 却又高又窄）→ 标 needs_review 提示复核。

纯逻辑：输入资产记录（path + bounds），输出 manifest dict / YAML / diff；不依赖 UE 或
litellm，可用假数据单测。在线取数（枚举 + bounds）由 ue_whitebox 的 wb_asset_scan 负责。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ue5agent.whitebox.manifest import Manifest

# ---------------------------------------------------------------------------
# 名称前缀归类（单一事实源；scripts/fbx_probe.py 复用 classify_by_name）
# ---------------------------------------------------------------------------

# (前缀, category, tags, ambiguous, note)；按顺序先到先得。ambiguous=True 会标 needs_review。
NAME_RULES: list[tuple[str, str, list[str], bool, str]] = [
    ("cornerwall", "corner", ["structure", "vertical"], False, ""),
    ("doorframe", "wall_door", ["structure", "vertical", "opening"], False, "门洞框"),
    ("wall", "wall", ["structure", "vertical"], False, ""),
    ("w_gls_frm", "glass_wall", ["structure", "vertical", "window"], False, "玻璃幕墙"),
    ("window", "window", ["structure", "vertical", "window"], False, ""),
    ("flooroutdoor", "floor", ["structure", "ground"], False, "室外地板"),
    ("floor", "floor", ["structure", "ground"], False, ""),
    ("pillar", "pillar", ["structure", "vertical"], False, ""),
    ("stair", "stair", ["structure", "vertical", "traversal"], False, ""),
    ("ramp", "ramp", ["structure", "traversal"], False, ""),
    ("gridbeamroof", "roof", ["structure", "ceiling"], False, ""),
    ("tri_beamroof", "roof", ["structure", "ceiling"], False, ""),
    ("roofoverhang", "roof", ["structure", "ceiling"], False, ""),
    ("fence", "fence", ["cover", "barrier"], False, ""),
    ("shippingcontainer", "cover", ["cover", "prop", "large"], False, "集装箱(大掩体)"),
    ("smallwoodencrate", "cover", ["cover", "prop"], False, "木箱"),
    ("woodplank", "prop", ["prop"], False, "木板"),
    ("pipeline", "prop", ["prop"], False, "管道"),
    ("table", "prop", ["prop"], False, "桌子"),
    ("tallcabinet", "prop", ["prop"], False, "高柜"),
    ("littlecabinet", "prop", ["prop"], False, "小柜"),
    ("truck", "prop", ["prop", "large"], False, "卡车"),
    ("doorlock", "prop", ["prop", "attachment"], True, "门锁挂件，建议作 Doorframe 附件而非独立件"),
    ("irongrilledoor", "wall_door", ["structure", "opening"], True, "铁栅门，确认是门扇还是门洞"),
    ("collection", "prop", ["prop", "combined"], True, "组合件，内容未知需确认语义/footprint"),
]


def classify_by_name(stem: str) -> tuple[str, list[str], bool, str]:
    """按文件名前缀归类，返回 (category, tags, ambiguous, note)；无命中返回 unknown。"""
    s = stem.lower()
    for prefix, cat, tags, amb, note in NAME_RULES:
        if s.startswith(prefix):
            return cat, list(tags), amb, note
    return "unknown", ["unknown"], True, ""


# ---------------------------------------------------------------------------
# 几何先验归类（命名兜不住时按包围盒长宽高比猜大类）
# ---------------------------------------------------------------------------

# 阈值按 uu（1 格=100uu，默认墙高 400）。只为把 unknown 收敛到大类，不追求精确。
_THIN = 60.0  # 单轴"薄"的上限
_TALL = 200.0  # "高"的下限
_FLAT = 60.0  # "扁"的上限
_SMALL_FOOT = 90.0  # 柱类水平占地上限


def classify_by_geometry(size: tuple[float, float, float]) -> tuple[str, list[str], str]:
    """按包围盒猜大类，返回 (category, tags, note)；猜不出返回 unknown。

    几何只能区分形态（薄/高/扁/敦实），区分不了语义（墙 vs 窗 vs 门同为薄高片），
    故凡走到这里的件都应被标 needs_review。
    """
    sx, sy, sz = size
    h_min = min(sx, sy)
    h_max = max(sx, sy)
    if sz <= _FLAT and sx >= 150 and sy >= 150:
        return "floor", ["structure", "ground"], "几何判为扁平大件(地板/屋顶),请确认朝向与用途"
    # 柱（两个水平轴都小且高）须在墙（单轴薄且高）之前判，否则细柱会被误当薄墙
    if sx <= _SMALL_FOOT and sy <= _SMALL_FOOT and sz >= _TALL:
        return "pillar", ["structure", "vertical"], "几何判为细高件(柱类)"
    if h_min <= _THIN and h_max >= 100 and sz >= _TALL:
        note = "几何判为薄高片(墙类),若含门窗开口请改 wall_door/window"
        return "wall", ["structure", "vertical"], note
    if h_min <= 20 and sz <= _FLAT and h_max >= 100:
        return "prop", ["prop"], "几何判为细长扁件(板/梁类)"
    if sz < _TALL and h_max <= 350:
        return "cover", ["cover", "prop"], "几何判为敦实方块(掩体/道具),请确认"
    return "unknown", ["unknown"], ""


@dataclass
class ClassifyResult:
    category: str
    tags: list[str]
    note: str
    method: str  # "name" | "geometry" | "none"
    ambiguous: bool


def classify(stem: str, size: tuple[float, float, float]) -> ClassifyResult:
    """混合归类：先名称前缀，兜不住再几何先验。"""
    cat, tags, amb, note = classify_by_name(stem)
    if cat != "unknown":
        return ClassifyResult(cat, tags, note, "name", amb)
    g_cat, g_tags, g_note = classify_by_geometry(size)
    if g_cat != "unknown":
        return ClassifyResult(g_cat, g_tags, g_note, "geometry", True)
    return ClassifyResult("unknown", ["unknown"], "命名与几何都无法归类，请人工归类", "none", True)


# 命名命中但几何明显矛盾时提示复核（保守：只查少数一眼可判的冲突）。
def _name_geometry_conflict(category: str, size: tuple[float, float, float]) -> str:
    sx, sy, sz = size
    if category in {"floor", "roof"} and sz >= _TALL and min(sx, sy) <= _THIN:
        return "命名为水平件但几何又高又窄，疑似归类错误"
    if category in {"wall", "window", "wall_door", "glass_wall"} and sz <= _FLAT:
        return "命名为竖直墙类但几何很扁，疑似归类错误"
    if category == "pillar" and (sx > 150 or sy > 150):
        return "命名为柱但水平占地过大，疑似归类错误"
    return ""


# ---------------------------------------------------------------------------
# 资产记录 → manifest 资产项
# ---------------------------------------------------------------------------


@dataclass
class AssetRecord:
    """UE 侧扫描到的单件真值：资产路径 + scale=1 的本地包围盒（uu）。"""

    path: str
    bounds_min: tuple[float, float, float]
    bounds_max: tuple[float, float, float]

    @property
    def size(self) -> tuple[float, float, float]:
        return tuple(self.bounds_max[i] - self.bounds_min[i] for i in range(3))  # type: ignore[return-value]


def asset_key(path: str) -> str:
    """从 /Game/.../AssetName 取末段并小写作为 manifest key。"""
    return path.rstrip("/").rsplit("/", 1)[-1].lower()


def _pivot_axis(min_v: float, size_v: float) -> float:
    """归一化 pivot：本地原点(0)在 AABB 内的比例位置；不 clamp，保留真实值。"""
    if abs(size_v) < 1e-4:
        return 0.5
    return round((0.0 - min_v) / size_v, 3)


def _footprint(size_v: float, grid: float) -> int:
    return max(1, round(size_v / grid))


def build_asset(record: AssetRecord, *, grid: float = 100.0) -> dict:
    """单件记录 → manifest 资产 dict（calibrated，几何全部来自 UE 真值）。"""
    stem = record.path.rstrip("/").rsplit("/", 1)[-1]
    mn = record.bounds_min
    mx = record.bounds_max
    size = record.size
    size_i = [round(size[i]) for i in range(3)]
    pivot = [_pivot_axis(mn[i], size[i]) for i in range(3)]
    result = classify(stem, size)

    asset: dict = {
        "path": record.path,
        "size": size_i,
        "category": result.category,
        "pivot": pivot,
        "local_bounds_min": [round(v) for v in mn],
        "local_bounds_max": [round(v) for v in mx],
        "calibrated": True,
        "footprint": [_footprint(size[0], grid), _footprint(size[1], grid)],
        "tags": list(result.tags),
    }

    reasons: list[str] = []
    if result.method == "name" and result.ambiguous:
        reasons.append(result.note or "命名归类不确定")
    elif result.method == "geometry":
        reasons.append(result.note or "由几何先验归类，请确认语义")
    elif result.method == "none":
        reasons.append(result.note)
    else:
        # 命名稳妥归类：note 作为人读 desc 保留（如"门洞框""卡车"）
        if result.note:
            asset["desc"] = result.note
        conflict = _name_geometry_conflict(result.category, size)
        if conflict:
            reasons.append(conflict)

    if any(p < -0.05 or p > 1.05 for p in pivot):
        reasons.append("原点在几何外(pivot 越界)，落地前建议归零或人工核对")

    if reasons:
        asset["needs_review"] = True
        asset["review"] = "；".join(r for r in reasons if r)
    return asset


# ---------------------------------------------------------------------------
# manifest 组装 + 与现有清单合并（保留人工 curation）
# ---------------------------------------------------------------------------

_ROLE_GUESS_ORDER: list[tuple[str, str]] = [
    ("floor", "floor"),
    ("wall", "wall"),
    ("wall_door", "wall_door"),
]


def _guess_roles(assets: dict[str, dict]) -> dict[str, str]:
    """为常用结构角色挑一个代表件（best-guess，供 merge 在无现成 roles 时兜底）。

    优先挑命名稳妥（无 needs_review）的件作角色默认，避免把几何先验猜出来的待复核件
    设成结构角色。
    """
    roles: dict[str, str] = {}
    for role, cat in _ROLE_GUESS_ORDER:
        candidates = sorted(k for k, a in assets.items() if a["category"] == cat)
        if not candidates:
            continue
        clean = [k for k in candidates if not assets[k].get("needs_review")]
        roles[role] = (clean or candidates)[0]
    return roles


def build_manifest_dict(
    records: list[AssetRecord],
    *,
    grid: float = 100.0,
    existing: Manifest | None = None,
) -> dict:
    """记录列表 → manifest v2 dict。

    合并策略（existing 非空时，保护人工 curation 不被重扫覆盖）：
    - roles：完全沿用 existing.roles（用户手调的角色映射优先），为空才用 best-guess；
    - desc：existing 有手写 desc、本次几何无 desc 时沿用旧 desc；
    - 其余几何字段（size/pivot/footprint/bounds/category/tags/needs_review）一律以本次扫描为准。
    """
    assets: dict[str, dict] = {}
    for record in records:
        key = asset_key(record.path)
        assets[key] = build_asset(record, grid=grid)

    if existing is not None:
        for key, asset in assets.items():
            old = existing.assets.get(key)
            if old is not None and old.desc and "desc" not in asset:
                asset["desc"] = old.desc

    # roles：保留 existing 里仍然有效的映射（用户手调优先），丢弃指向已删除资产的项，
    # 缺的标准角色用 best-guess 补齐——避免重扫后 roles 指向不存在的旧资产（如 floor_n）。
    roles: dict[str, str] = {}
    if existing is not None:
        roles = {role: key for role, key in existing.roles.items() if key in assets}
    for role, key in _guess_roles(assets).items():
        roles.setdefault(role, key)

    return {"version": 2, "grid": round(grid), "roles": roles, "assets": assets}


# ---------------------------------------------------------------------------
# YAML 生成（手写，按类别分组 + 注释，便于人工审 needs_review）
# ---------------------------------------------------------------------------

_CAT_ORDER = [
    "floor",
    "wall",
    "corner",
    "wall_door",
    "window",
    "glass_wall",
    "pillar",
    "stair",
    "ramp",
    "roof",
    "fence",
    "cover",
    "prop",
    "unknown",
]


def _fmt_list(values) -> str:
    return "[" + ", ".join(str(v) for v in values) + "]"


def emit_yaml(manifest: dict) -> str:
    """manifest dict → YAML 文本（可被 load_manifest 解析回来）。"""
    assets: dict[str, dict] = manifest["assets"]
    roles: dict[str, str] = manifest.get("roles", {})
    grid = manifest.get("grid", 100)

    by_cat: dict[str, list[tuple[str, dict]]] = {}
    for key, asset in assets.items():
        by_cat.setdefault(asset["category"], []).append((key, asset))

    lines: list[str] = [
        "# 白盒资产清单 v2（由 wb_asset_scan 从 UE 导入后 StaticMesh bounds 扫描生成）",
        "# 几何为真值(calibrated)：size/pivot/local_bounds 来自 UE，path 即真实 /Game 路径。",
        "# 复核要点：审带 needs_review 的件（几何先验归类/命名歧义/pivot 越界），并确认 roles。",
        f"version: {manifest.get('version', 2)}",
        f"grid: {grid}",
        "",
        "# 结构角色 → 资产 key 的映射（重扫会保留你手调过的映射）",
        "roles:",
    ]
    if roles:
        for role, key in roles.items():
            mark = "" if key in assets else "   # _needs_review: 候选 key 不存在，请指定"
            lines.append(f"  {role}: {key}{mark}")
    else:
        lines.append("  {}")
    lines.append("")
    lines.append("assets:")

    ordered_cats = _CAT_ORDER + [c for c in sorted(by_cat) if c not in _CAT_ORDER]
    for cat in ordered_cats:
        items = by_cat.get(cat)
        if not items:
            continue
        lines.append(f"  # ---- {cat} ({len(items)}) ----")
        for key, asset in sorted(items):
            review = asset.get("needs_review")
            head = f"  {key}:"
            if review:
                head += f"   # _needs_review: {asset.get('review', '')}"
            lines.append(head)
            lines.append(f"    path: {asset['path']}")
            lines.append(f"    size: {_fmt_list(asset['size'])}")
            lines.append(f"    category: {asset['category']}")
            lines.append(f"    pivot: {_fmt_list(asset['pivot'])}")
            if "local_bounds_min" in asset:
                lines.append(f"    local_bounds_min: {_fmt_list(asset['local_bounds_min'])}")
                lines.append(f"    local_bounds_max: {_fmt_list(asset['local_bounds_max'])}")
            if asset.get("calibrated"):
                lines.append("    calibrated: true")
            lines.append(f"    footprint: {_fmt_list(asset['footprint'])}")
            lines.append(f"    tags: {_fmt_list(asset['tags'])}")
            if asset.get("desc"):
                lines.append(f'    desc: "{asset["desc"]}"')
            if review:
                lines.append("    needs_review: true")
                lines.append(f'    review: "{asset.get("review", "")}"')
        lines.append("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 与现有清单对比（dry-run 预览用）
# ---------------------------------------------------------------------------


@dataclass
class DiffReport:
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    resized: list[str] = field(default_factory=list)  # "key: 旧→新"
    needs_review: list[str] = field(default_factory=list)
    total: int = 0

    def summary(self) -> str:
        lines = [
            f"扫描 {self.total} 件：新增 {len(self.added)}、消失 {len(self.removed)}、"
            f"尺寸变化 {len(self.resized)}、需复核 {len(self.needs_review)}",
        ]
        if self.added:
            lines.append("  + 新增: " + ", ".join(sorted(self.added)))
        if self.removed:
            lines.append("  - 消失(清单有但 UE 未扫到): " + ", ".join(sorted(self.removed)))
        if self.resized:
            lines.append("  ~ 尺寸变化:")
            lines += [f"      {r}" for r in self.resized]
        if self.needs_review:
            lines.append("  ? 需复核: " + ", ".join(sorted(self.needs_review)))
        return "\n".join(lines)


def diff_manifest(new_manifest: dict, existing: Manifest | None) -> DiffReport:
    """对比新扫描结果与现有 manifest，产出人读 diff。"""
    new_assets: dict[str, dict] = new_manifest["assets"]
    report = DiffReport(total=len(new_assets))
    report.needs_review = [k for k, a in new_assets.items() if a.get("needs_review")]
    if existing is None:
        report.added = list(new_assets)
        return report

    old_keys = set(existing.assets)
    new_keys = set(new_assets)
    report.added = sorted(new_keys - old_keys)
    report.removed = sorted(old_keys - new_keys)
    for key in sorted(new_keys & old_keys):
        old_size = tuple(round(v) for v in existing.assets[key].size)
        new_size = tuple(new_assets[key]["size"])
        if any(abs(a - b) > 1 for a, b in zip(old_size, new_size, strict=False)):
            report.resized.append(f"{key}: {_fmt_list(old_size)} → {_fmt_list(new_size)}")
    return report


def records_from_bounds_payload(items: list[dict]) -> list[AssetRecord]:
    """把桥命令返回的资产列表（每项含 path/asset_path + min/max 或 size）解析为记录。

    宽容解析：缺 min/max 但有 size 时按 [0,0,0]→size 估（原点不可知，pivot 退化为 0）。
    无法取尺寸的项跳过。
    """
    records: list[AssetRecord] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or item.get("asset_path") or "").strip()
        if not path:
            continue
        mn = _as_xyz(item.get("min") or item.get("local_min"))
        mx = _as_xyz(item.get("max") or item.get("local_max"))
        if mn is not None and mx is not None:
            records.append(AssetRecord(path=path, bounds_min=mn, bounds_max=mx))
            continue
        size = _as_xyz(item.get("size"))
        if size is not None:
            records.append(AssetRecord(path=path, bounds_min=(0.0, 0.0, 0.0), bounds_max=size))
    return records


def _as_xyz(v: object) -> tuple[float, float, float] | None:
    """把 [x,y,z] 数组解析为 float 三元组；非法（长度/类型/NaN/Inf）返回 None。"""
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        return None
    out = []
    for x in v:
        if not isinstance(x, (int, float)) or isinstance(x, bool) or not math.isfinite(x):
            return None
        out.append(float(x))
    return (out[0], out[1], out[2])
