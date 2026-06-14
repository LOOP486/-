"""轻量 FBX 二进制解析器：体检一批 FBX 的单位 / 包围盒 / pivot。

用途：评估外部 modular kit 能否被 ue5agent 白盒系统直接消费。
只读，不依赖任何第三方库（自带 FBX binary 解析 + zlib 解压数组）。

用法：
    uv run python scripts/fbx_probe.py "<目录>"            # 扫整个目录
    uv run python scripts/fbx_probe.py "<目录>" --csv out.csv
"""

from __future__ import annotations

import os
import struct
import sys
import zlib
from dataclasses import dataclass, field

# 命名前缀归类规则的单一事实源（见 src/ue5agent/whitebox/scanner.py）；
# 经 `uv run` 时 ue5agent 已 editable 安装，可直接 import。
from ue5agent.whitebox.scanner import classify_by_name as classify

# ---------------------------------------------------------------------------
# FBX binary 解析（够用版：拿 Vertices / UnitScaleFactor / Lcl Translation）
# ---------------------------------------------------------------------------


@dataclass
class Node:
    name: str
    props: list = field(default_factory=list)
    children: list = field(default_factory=list)

    def find_all(self, name):
        for c in self.children:
            if c.name == name:
                yield c

    def find(self, name):
        for c in self.children:
            if c.name == name:
                return c
        return None


def _read_array(data, off, kind):
    length, encoding, comp_len = struct.unpack_from("<III", data, off)
    off += 12
    raw = data[off : off + comp_len]
    off += comp_len
    if encoding == 1:
        raw = zlib.decompress(raw)
    fmt = {"f": "f", "d": "d", "i": "i", "l": "q", "b": "b"}[kind]
    vals = list(struct.unpack_from(f"<{length}{fmt}", raw, 0))
    return vals, off


def _read_prop(data, off):
    type_code = chr(data[off])
    off += 1
    if type_code == "Y":
        (v,) = struct.unpack_from("<h", data, off)
        return v, off + 2
    if type_code == "C":
        (v,) = struct.unpack_from("<?", data, off)
        return v, off + 1
    if type_code == "I":
        (v,) = struct.unpack_from("<i", data, off)
        return v, off + 4
    if type_code == "F":
        (v,) = struct.unpack_from("<f", data, off)
        return v, off + 4
    if type_code == "D":
        (v,) = struct.unpack_from("<d", data, off)
        return v, off + 8
    if type_code == "L":
        (v,) = struct.unpack_from("<q", data, off)
        return v, off + 8
    if type_code in ("f", "d", "i", "l", "b"):
        return _read_array(data, off, type_code)
    if type_code in ("S", "R"):
        (ln,) = struct.unpack_from("<I", data, off)
        off += 4
        b = data[off : off + ln]
        off += ln
        return (b.decode("utf-8", "replace") if type_code == "S" else b), off
    raise ValueError(f"未知属性类型 {type_code!r} @ {off}")


def _read_node(data, off, ver):
    if ver >= 7500:
        end_off, num_props, _plen = struct.unpack_from("<QQQ", data, off)
        off += 24
        nlen = data[off]
        off += 1
        sentinel = 25
    else:
        end_off, num_props, _plen = struct.unpack_from("<III", data, off)
        off += 12
        nlen = data[off]
        off += 1
        sentinel = 13
    if end_off == 0:
        return None, off
    name = data[off : off + nlen].decode("utf-8", "replace")
    off += nlen
    node = Node(name)
    for _ in range(num_props):
        v, off = _read_prop(data, off)
        node.props.append(v)
    while off < end_off - sentinel:
        child, off = _read_node(data, off, ver)
        if child is None:
            break
        node.children.append(child)
    off = end_off
    return node, off


def parse_fbx(path):
    with open(path, "rb") as f:
        data = f.read()
    if not data.startswith(b"Kaydara FBX Binary"):
        raise ValueError("不是二进制 FBX")
    (ver,) = struct.unpack_from("<I", data, 23)
    off = 27
    root = Node("__root__")
    while off < len(data) - (sentinel_size(ver)):
        node, off = _read_node(data, off, ver)
        if node is None:
            break
        root.children.append(node)
    return root, ver


def sentinel_size(ver):
    return 25 if ver >= 7500 else 13


# ---------------------------------------------------------------------------
# 提取关心的信息
# ---------------------------------------------------------------------------


def unit_scale(root):
    gs = root.find("GlobalSettings")
    if not gs:
        return None
    props = gs.find("Properties70")
    if not props:
        return None
    for p in props.find_all("P"):
        if p.props and p.props[0] == "UnitScaleFactor":
            # P: name, type, label, flags, value...
            return p.props[-1]
    return None


def collect_geometry_bboxes(root):
    """返回 [(geom_id, (minx,miny,minz),(maxx,maxy,maxz))]"""
    out = []
    objects = root.find("Objects")
    if not objects:
        return out
    for geom in objects.find_all("Geometry"):
        verts_node = geom.find("Vertices")
        if not verts_node or not verts_node.props:
            continue
        vals = verts_node.props[0]
        if not isinstance(vals, list) or len(vals) < 3:
            continue
        xs = vals[0::3]
        ys = vals[1::3]
        zs = vals[2::3]
        out.append(
            (
                geom.props[0] if geom.props else None,
                (min(xs), min(ys), min(zs)),
                (max(xs), max(ys), max(zs)),
            )
        )
    return out


# ---------------------------------------------------------------------------
# manifest v2 生成：按命名前缀分类 + m→uu 单位换算 + pivot 反推（不 clamp）
# ---------------------------------------------------------------------------

# 这批散件里疑似遗留/未命名的通用图元，本轮按用户要求跳过（"待清理的先不管"）。
JUNK_STEMS = {
    "cube",
    "cube_001",
    "cube_002",
    "cube_003",
    "cube_004",
    "cube_005",
    "cube_239",
    "cylinder_002",
    "vert_037",
    "pasted__pasted__pcube7",
}


def _round_uu(v):
    return round(v * 100.0)  # 米 → uu（FBX 顶点为米，UE 导入 ×100）


def _pivot_axis(min_m, size_m):
    """归一化 pivot：原点(0)在 AABB 内的比例位置。不 clamp，保留真实值。"""
    if abs(size_m) < 1e-4:
        return 0.5
    return round((0.0 - min_m) / size_m, 3)


def build_manifest(rows):
    """rows（含米制 min/size）→ manifest v2 dict + 每件诊断。"""
    assets = {}
    diagnostics = []
    for r in rows:
        if "error" in r or r["size"] is None:
            continue
        stem = os.path.splitext(os.path.basename(r["file"]))[0]
        if stem.lower() in JUNK_STEMS:
            continue
        key = stem.lower()
        sx, sy, sz = r["size"]
        mn = r["min"]
        size_uu = [_round_uu(sx), _round_uu(sy), _round_uu(sz)]
        pivot = [_pivot_axis(mn[i], r["size"][i]) for i in range(3)]
        footprint = [max(1, round(sx)), max(1, round(sy))]
        category, tags, ambiguous, note = classify(stem)

        # 原点跑到几何外（任一轴 pivot 明显越界）→ 标记复核
        pivot_outside = any(p < -0.05 or p > 1.05 for p in pivot)
        review_reasons = []
        if ambiguous:
            review_reasons.append(note or "类别不确定")
        if pivot_outside:
            review_reasons.append("原点在几何外(pivot 越界)，落地前建议归零或人工核对")

        assets[key] = {
            "path": f"/Game/ArchKit/{category}/{stem}",  # 占位 UE 路径，导入后须核对
            "source_fbx": r["file"].replace("\\", "/"),  # 来源，供离线扫描/重导用
            "size": size_uu,
            "category": category,
            "pivot": pivot,
            "footprint": footprint,
            "tags": tags,
        }
        if note and not ambiguous:
            assets[key]["desc"] = note
        if review_reasons:
            assets[key]["needs_review"] = True
            assets[key]["review"] = "；".join(review_reasons)
        diagnostics.append((key, category, bool(review_reasons)))
    return assets, diagnostics


def emit_manifest_yaml(assets):
    """手写 YAML，按类别分组 + 注释，便于人工审 _needs_review。"""
    # 角色默认映射（best-guess，待用户确认 → 全标注释）
    role_guess = {
        "floor": "floor_n",
        "wall": "wall1_4",
        "wall_door": "doorframe1_2_1",
    }
    cat_order = [
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
    by_cat = {}
    for key, a in assets.items():
        by_cat.setdefault(a["category"], []).append((key, a))

    lines = []
    lines.append("# 白盒资产清单 v2（由 scripts/fbx_probe.py 从 FBX 离线生成，请人工复核）")
    lines.append("# 来源：E:/BaiduSyncdisk/LEVEL DESIGN/assets/Architecture Model/UE_Exports/fbx")
    lines.append("# 单位：size 为缩放=1 的包围盒(uu)，由 FBX 米制 ×100 得到（UE 导入 1m→100uu）。")
    lines.append("# pivot：归一化原点位置(未 clamp)，[0.5,0.5,0]=底面中心；编译器据此补偿落地。")
    lines.append("# 待办：① 核对 path（导入 UE 后的真实 /Game 路径）；② 审带 needs_review 的件；")
    lines.append(
        "#       ③ 确认下方 roles 角色映射。当前 v1 loader 会忽略 v2 扩展字段，不影响加载。"
    )
    lines.append("version: 2")
    lines.append("grid: 100")
    lines.append("")
    lines.append("# 结构角色 → 资产 key 的默认映射（best-guess，请确认/替换）")
    lines.append("roles:")
    for role, key in role_guess.items():
        mark = "" if key in assets else "   # _needs_review: 候选 key 不存在，请指定"
        lines.append(f"  {role}: {key}{mark}")
    lines.append("")
    lines.append("assets:")

    def fmt_list(v):
        return "[" + ", ".join(str(x) for x in v) + "]"

    for cat in cat_order:
        items = by_cat.get(cat)
        if not items:
            continue
        lines.append(f"  # ---- {cat} ({len(items)}) ----")
        for key, a in sorted(items):
            review = a.get("needs_review")
            head = f"  {key}:"
            if review:
                head += f"   # _needs_review: {a.get('review', '')}"
            lines.append(head)
            lines.append(f"    path: {a['path']}")
            lines.append(f"    source_fbx: {a['source_fbx']}")
            lines.append(f"    size: {fmt_list(a['size'])}")
            lines.append(f"    category: {a['category']}")
            lines.append(f"    pivot: {fmt_list(a['pivot'])}")
            lines.append(f"    footprint: {fmt_list(a['footprint'])}")
            lines.append(f"    tags: {fmt_list(a['tags'])}")
            if a.get("desc"):
                lines.append(f'    desc: "{a["desc"]}"')
            if review:
                # 同时写成机器可读字段（loader 消费）+ 上方注释（人读）
                lines.append("    needs_review: true")
                lines.append(f'    review: "{a.get("review", "")}"')
        lines.append("")
    return "\n".join(lines) + "\n"


def main():
    if len(sys.argv) < 2:
        print("用法: python fbx_probe.py <目录> [--csv out.csv] [--manifest kit.yaml]")
        sys.exit(1)
    root_dir = sys.argv[1]
    csv_path = None
    if "--csv" in sys.argv:
        csv_path = sys.argv[sys.argv.index("--csv") + 1]
    manifest_path = None
    if "--manifest" in sys.argv:
        manifest_path = sys.argv[sys.argv.index("--manifest") + 1]

    files = []
    for dirpath, _dirs, names in os.walk(root_dir):
        for n in sorted(names):
            if n.lower().endswith(".fbx"):
                files.append(os.path.join(dirpath, n))

    rows = []
    for path in files:
        rel = os.path.relpath(path, root_dir)
        try:
            tree, ver = parse_fbx(path)
            us = unit_scale(tree)
            bboxes = collect_geometry_bboxes(tree)
            if bboxes:
                # 合并所有 geometry 的总包围盒（组合件会有多个）
                mins = [min(b[1][i] for b in bboxes) for i in range(3)]
                maxs = [max(b[2][i] for b in bboxes) for i in range(3)]
                size = [maxs[i] - mins[i] for i in range(3)]
                rows.append(
                    {
                        "file": rel,
                        "ver": ver,
                        "unit": us,
                        "ngeom": len(bboxes),
                        "min": mins,
                        "max": maxs,
                        "size": size,
                    }
                )
            else:
                rows.append(
                    {
                        "file": rel,
                        "ver": ver,
                        "unit": us,
                        "ngeom": 0,
                        "min": None,
                        "max": None,
                        "size": None,
                    }
                )
        except Exception as e:
            rows.append({"file": rel, "error": str(e)})

    # 控制台输出
    print(f"{'file':42} {'unit':>6} {'g':>2}  {'sizeX':>8} {'sizeY':>8} {'sizeZ':>8}   {'minZ':>8}")
    print("-" * 100)
    for r in rows:
        if "error" in r:
            print(f"{r['file']:42} ERROR: {r['error']}")
            continue
        if r["size"] is None:
            print(f"{r['file']:42} {r['unit']!s:>6} {r['ngeom']:>2}  (no geometry)")
            continue
        sx, sy, sz = r["size"]
        minz = r["min"][2]
        print(
            f"{r['file']:42} {r['unit']!s:>6} {r['ngeom']:>2}  "
            f"{sx:8.1f} {sy:8.1f} {sz:8.1f}   {minz:8.1f}"
        )

    if csv_path:
        import csv

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(
                [
                    "file",
                    "ver",
                    "unit",
                    "ngeom",
                    "minX",
                    "minY",
                    "minZ",
                    "maxX",
                    "maxY",
                    "maxZ",
                    "sizeX",
                    "sizeY",
                    "sizeZ",
                ]
            )
            for r in rows:
                if "error" in r or r["size"] is None:
                    w.writerow(
                        [r["file"], r.get("ver"), r.get("unit"), r.get("ngeom"), *([""] * 9)]
                    )
                    continue
                w.writerow(
                    [r["file"], r["ver"], r["unit"], r["ngeom"], *r["min"], *r["max"], *r["size"]]
                )
        print(f"\nCSV 已写出: {csv_path}")

    if manifest_path:
        assets, diagnostics = build_manifest(rows)
        text = emit_manifest_yaml(assets)
        os.makedirs(os.path.dirname(os.path.abspath(manifest_path)), exist_ok=True)
        with open(manifest_path, "w", encoding="utf-8") as f:
            f.write(text)
        n_review = sum(1 for _k, _c, rv in diagnostics if rv)
        print(f"\nmanifest v2 已写出: {manifest_path}")
        print(f"  收录资产 {len(assets)} 件（已跳过 {len(JUNK_STEMS)} 件待清理图元）")
        print(f"  其中 {n_review} 件标了 _needs_review")
        from collections import Counter

        cc = Counter(c for _k, c, _rv in diagnostics)
        print("  类别分布: " + ", ".join(f"{k}={v}" for k, v in sorted(cc.items())))


if __name__ == "__main__":
    main()
