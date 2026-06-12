"""白盒落地结果的确定性校验（Stage A2）：纯几何对照，不依赖编辑器、不依赖 LLM。

思路是"期望 vs 实测"：布局 spec 经 compile_layout 算出期望放置（确定性），
与编辑器回读的实测 actor（名称/坐标/缩放）对照，产出 violations 与 metrics。
能抓的缺陷类别：spawn 部分失败（缺件）、清理不净/外部添加（多件）、位置漂移、
构件穿插（AABB 实体重叠）。

与其他校验环节的分工：
- 编译期 _validate（compiler.py）：挡坏布局（重叠房间/非法门/不连通），坏布局不落地；
- 本模块：验证落地后的实际场景与期望一致——实测连通性由"构件齐全且位置无偏差"
  传递保证（门图连通在编译期已验）；
- NavMesh 物理可达性：ue_editor 的 navmesh_rebuild + path_test 负责。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ue5agent.whitebox.compiler import LayoutSpec, Placement, compile_layout
from ue5agent.whitebox.manifest import Manifest

_LOCATION_TOL = 1.0
"""位置容差（uu）：UE 回读浮点会有精度噪声，超过 1uu 视为漂移。"""

_SCALE_TOL = 0.01

_CUBE_HALF = 50.0
"""manifest cube 基准尺寸 100uu 的半边长：AABB 半尺寸 = scale * 50。"""

_LAP_TOLERANCE = 25.0
"""穿插判定阈值（uu）：同房间相邻墙在角部有约一个墙厚（20uu）的正常搭接，
重叠区三轴最小边长超过此值才算真穿插。"""


@dataclass
class ActorView:
    """编辑器回读的一个构件（find_actors_by_name 的实测视图）。"""

    name: str
    location: tuple[float, float, float]
    scale: tuple[float, float, float]


@dataclass
class ValidationReport:
    violations: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.violations


def parse_batch_name(actor_name: str, prefix: str) -> tuple[str, str] | None:
    """`WB_<batch>_<构件名>` → (batch, 构件名)；不匹配该前缀格式则返回 None。"""
    match = re.match(rf"^{re.escape(prefix)}_([0-9a-f]+)_(.+)$", actor_name)
    return (match.group(1), match.group(2)) if match else None


def strip_batch_name(actor_name: str, prefix: str) -> str | None:
    """`WB_<batch>_<构件名>` → `<构件名>`；不匹配该前缀格式则返回 None。"""
    parsed = parse_batch_name(actor_name, prefix)
    return parsed[1] if parsed else None


def validate_layout(
    spec: LayoutSpec,
    manifest: Manifest,
    actors: list[ActorView],
    *,
    prefix: str = "WB",
) -> ValidationReport:
    """对照期望放置与实测构件，返回 violations + metrics。"""
    expected = compile_layout(spec, manifest)
    report = ValidationReport()

    matched = _diff_expected_actual(expected, actors, prefix, report)
    _check_overlaps(actors, prefix, report)
    _fill_metrics(spec, expected, actors, prefix, report)
    report.metrics["matched_count"] = matched
    return report


def _diff_expected_actual(
    expected: list[Placement],
    actors: list[ActorView],
    prefix: str,
    report: ValidationReport,
) -> int:
    """缺失/多余/位移三类对照；返回匹配且无偏差的构件数。"""
    actual_by_name: dict[str, list[ActorView]] = {}
    for actor in actors:
        stripped = strip_batch_name(actor.name, prefix)
        if stripped is not None:
            actual_by_name.setdefault(stripped, []).append(actor)

    batches = {parsed[0] for actor in actors if (parsed := parse_batch_name(actor.name, prefix))}
    if len(batches) > 1:
        report.violations.append(
            f"场景中存在 {len(batches)} 个批次的 {prefix}_ 构件（清理不净或多次落地未清旧）"
        )

    matched = 0
    for placement in expected:
        views = actual_by_name.pop(placement.name, [])
        if not views:
            report.violations.append(f"缺失构件：{placement.name}（spawn 部分失败或被外部删除）")
            continue
        view = views[0]
        loc_drift = max(abs(a - b) for a, b in zip(view.location, placement.location, strict=False))
        scale_drift = max(abs(a - b) for a, b in zip(view.scale, placement.scale, strict=False))
        if loc_drift > _LOCATION_TOL or scale_drift > _SCALE_TOL:
            report.violations.append(
                f"构件偏差：{placement.name} 位置偏 {loc_drift:.1f}uu / 缩放偏 {scale_drift:.3f}"
                f"（期望 loc={placement.location} 实测 loc={view.location}）"
            )
            continue
        matched += 1
    for stripped, views in actual_by_name.items():
        report.violations.append(f"多余构件：{stripped}×{len(views)}（不在布局期望中）")
    return matched


def _check_overlaps(actors: list[ActorView], prefix: str, report: ValidationReport) -> None:
    """实测 AABB 两两穿插检查；角部搭接（重叠最小边 <= 阈值）豁免。"""
    boxes = [(actor.name, _aabb(actor)) for actor in actors if strip_batch_name(actor.name, prefix)]
    for i, (name_a, box_a) in enumerate(boxes):
        for name_b, box_b in boxes[i + 1 :]:
            laps = _overlap_axes(box_a, box_b)
            # 搭接（墙角 20×20×高、墙坐落在地板上沿）至多一个轴的重叠超容差；
            # 两个及以上轴超容差说明是实体面重叠（如两块地板叠放、墙穿房间）
            if laps and sum(1 for lap in laps if lap > _LAP_TOLERANCE) >= 2:
                dims = "×".join(f"{lap:.0f}" for lap in laps)
                report.violations.append(
                    f"构件穿插：{name_a} 与 {name_b} 重叠区域 {dims}uu（超过搭接容差）"
                )


def _aabb(actor: ActorView) -> tuple[tuple[float, float], ...]:
    """构件的世界 AABB：((x_lo, x_hi), (y_lo, y_hi), (z_lo, z_hi))。"""
    return tuple(
        (c - s * _CUBE_HALF, c + s * _CUBE_HALF)
        for c, s in zip(actor.location, actor.scale, strict=False)
    )


def _overlap_axes(
    a: tuple[tuple[float, float], ...], b: tuple[tuple[float, float], ...]
) -> tuple[float, float, float] | None:
    """两 AABB 重叠区域的三轴尺寸；任一轴不相交则返回 None。"""
    laps = []
    for (lo_a, hi_a), (lo_b, hi_b) in zip(a, b, strict=False):
        lap = min(hi_a, hi_b) - max(lo_a, lo_b)
        if lap <= 0:
            return None
        laps.append(lap)
    return (laps[0], laps[1], laps[2])


def _fill_metrics(
    spec: LayoutSpec,
    expected: list[Placement],
    actors: list[ActorView],
    prefix: str,
    report: ValidationReport,
) -> None:
    wb_actors = [a for a in actors if strip_batch_name(a.name, prefix)]
    floors = [p for p in expected if p.name.endswith("_floor")]
    # 地板 scale 是以 100uu 立方为基准的倍率：面积(m²) = sx * sy（1.0 scale = 1m）
    floor_area = sum(p.scale[0] * p.scale[1] for p in floors)
    xs = [p.location[0] for p in expected]
    ys = [p.location[1] for p in expected]
    report.metrics.update(
        {
            "room_count": len(spec.rooms),
            "door_count": sum(len(room.doors) for room in spec.rooms),
            "expected_count": len(expected),
            "actual_count": len(wb_actors),
            "wall_count": len(expected) - len(floors),
            "floor_area_m2": round(floor_area, 1),
            "bbox_center_xy": (round((min(xs) + max(xs)) / 2), round((min(ys) + max(ys)) / 2)),
        }
    )
