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
from itertools import pairwise
from typing import Any

from ue5agent.whitebox.compiler import LayoutSpec, Placement, compile_layout
from ue5agent.whitebox.level_metrics import LevelMetrics, audit_layout_scale
from ue5agent.whitebox.manifest import Manifest

_LOCATION_TOL = 1.0
"""位置容差（uu）：UE 回读浮点会有精度噪声，超过 1uu 视为漂移。"""

_SCALE_TOL = 0.01
_ROTATION_TOL = 0.1
_VISUAL_AABB_TOL = 1.0

_CUBE_HALF = 50.0
"""manifest cube 基准尺寸 100uu 的半边长：AABB 半尺寸 = scale * 50。"""

_LAP_TOLERANCE = 25.0
"""穿插判定阈值（uu）：同房间相邻墙在角部有约一个墙厚（20uu）的正常搭接，
重叠区三轴最小边长超过此值才算真穿插。"""

_ROUTE_CORRIDOR_HALF_WIDTH = 45.0
"""主路线保留走廊半宽（uu）：自动掩体/柱子不应占用这条通路。"""


@dataclass
class ActorView:
    """编辑器回读的一个构件（find_actors_by_name 的实测视图）。"""

    name: str
    location: tuple[float, float, float]
    scale: tuple[float, float, float]
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    actor_type: str = "StaticMeshActor"


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
    level_metrics: LevelMetrics | None = None,
) -> ValidationReport:
    """对照期望放置与实测构件，返回 violations + metrics。"""
    expected = compile_layout(spec, manifest)
    report = ValidationReport()

    matched = _diff_expected_actual(expected, actors, prefix, report)
    _check_visual_alignment(expected, report)
    _check_structural_coverage(expected, actors, prefix, manifest.grid, report)
    _check_overlaps(expected, actors, prefix, report)
    _check_route_blockers(expected, actors, prefix, report)
    _check_foreign_residue(expected, actors, prefix, report)
    _fill_metrics(spec, expected, actors, prefix, manifest.grid, report, level_metrics)
    report.metrics["matched_count"] = matched
    return report


_FOREIGN_PATTERN = re.compile(r"^([A-Za-z0-9]{1,8})_([0-9a-f]{4,})_(.+)$")
"""泛化的白盒批次命名形态：异前缀残留（上次任务未清理）也长这样。"""


def _check_foreign_residue(
    expected: list[Placement],
    actors: list[ActorView],
    prefix: str,
    report: ValidationReport,
) -> None:
    """异前缀白盒残留检测：与布局区域重叠的旧批次构件会堵门、断 navmesh，
    但它们不属于本前缀，缺件/多件对照天然看不见——必须单独检。
    （真机 e2e 实测：S1_ 残留墙横在新布局门洞上，path_test 全部 partial，
    模型误诊为 agent radius。）"""
    if not expected:
        return
    # 布局包围盒（外扩半格，贴边残留也算）
    boxes = [_placement_aabb(p) for p in expected]
    xs_lo = min(box[0][0] for box in boxes) - 50
    xs_hi = max(box[0][1] for box in boxes) + 50
    ys_lo = min(box[1][0] for box in boxes) - 50
    ys_hi = max(box[1][1] for box in boxes) + 50
    foreign: dict[str, int] = {}
    for actor in actors:
        if parse_batch_name(actor.name, prefix) is not None:
            continue  # 本前缀构件走缺件/多件对照
        match = _FOREIGN_PATTERN.match(actor.name)
        if match is None:
            continue  # 非白盒命名形态的场景 actor（PlayerStart 等）不管
        box = _aabb(actor)
        if box[0][1] < xs_lo or box[0][0] > xs_hi or box[1][1] < ys_lo or box[1][0] > ys_hi:
            continue  # 不在布局区域内的旧批次不拦验收（但场景整洁是另一回事）
        foreign[match.group(1)] = foreign.get(match.group(1), 0) + 1
    for other_prefix, count in sorted(foreign.items()):
        report.violations.append(
            f"异前缀白盒残留：{other_prefix}_ ×{count} 与布局区域重叠"
            f'（会堵门/断 navmesh；用 wb_clear(prefix="{other_prefix}") 清除）'
        )


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
        rotation_drift = max(
            _angle_delta(a, b) for a, b in zip(view.rotation, placement.rotation, strict=False)
        )
        if loc_drift > _LOCATION_TOL or scale_drift > _SCALE_TOL or rotation_drift > _ROTATION_TOL:
            report.violations.append(
                f"构件偏差：{placement.name} 位置偏 {loc_drift:.1f}uu / "
                f"缩放偏 {scale_drift:.3f} / 旋转偏 {rotation_drift:.1f}deg"
                f"（期望 loc={placement.location} 实测 loc={view.location}）"
            )
            continue
        matched += 1
    for stripped, views in actual_by_name.items():
        report.violations.append(f"多余构件：{stripped}×{len(views)}（不在布局期望中）")
    return matched


def _check_overlaps(
    expected: list[Placement], actors: list[ActorView], prefix: str, report: ValidationReport
) -> None:
    """实测 AABB 两两穿插检查；角部搭接（重叠最小边 <= 阈值）豁免。"""
    expected_by_name = {p.name: p for p in expected}
    boxes = []
    for actor in actors:
        stripped = strip_batch_name(actor.name, prefix)
        if not stripped:
            continue
        placement = expected_by_name.get(stripped)
        box = (
            _placement_aabb(placement)
            if placement and _actor_matches_placement(actor, placement)
            else _aabb(actor)
        )
        boxes.append((actor.name, box))
    for i, (name_a, box_a) in enumerate(boxes):
        for name_b, box_b in boxes[i + 1 :]:
            placement_a = _placement_for_actor(name_a, expected_by_name, prefix)
            placement_b = _placement_for_actor(name_b, expected_by_name, prefix)
            if _overlap_exempt(name_a, placement_a) or _overlap_exempt(name_b, placement_b):
                continue
            laps = _overlap_axes(box_a, box_b)
            if laps and _overlap_pair_exempt(placement_a, placement_b, box_a, box_b, laps):
                continue
            # 搭接（墙角 20×20×高、墙坐落在地板上沿）至多一个轴的重叠超容差；
            # 两个及以上轴超容差说明是实体面重叠（如两块地板叠放、墙穿房间）
            if laps and sum(1 for lap in laps if lap > _LAP_TOLERANCE) >= 2:
                dims = "×".join(f"{lap:.0f}" for lap in laps)
                report.violations.append(
                    f"构件穿插：{name_a} 与 {name_b} 重叠区域 {dims}uu（超过搭接容差）"
                )


def _check_visual_alignment(expected: list[Placement], report: ValidationReport) -> None:
    """校准资产的真实视觉 AABB 必须贴合编译目标，避免 manifest/UE pivot 自洽假阳性。"""
    mismatch_count = 0
    calibrated_count = 0
    for placement in expected:
        if not (
            placement.asset_calibrated
            and placement.snap_box_default
            and placement.target_min is not None
            and placement.target_size is not None
            and placement.visual_min is not None
            and placement.visual_size is not None
        ):
            continue
        calibrated_count += 1
        min_delta = max(
            abs(a - b) for a, b in zip(placement.visual_min, placement.target_min, strict=False)
        )
        size_delta = max(
            abs(a - b) for a, b in zip(placement.visual_size, placement.target_size, strict=False)
        )
        if min_delta <= _VISUAL_AABB_TOL and size_delta <= _VISUAL_AABB_TOL:
            continue
        mismatch_count += 1
        report.violations.append(
            f"视觉对齐偏差：{placement.name} visual_aabb 与 target_aabb 偏差 "
            f"min={min_delta:.1f}uu size={size_delta:.1f}uu"
        )
    report.metrics["calibrated_asset_count"] = calibrated_count
    report.metrics["visual_mismatch_count"] = mismatch_count


def _check_structural_coverage(
    expected: list[Placement],
    actors: list[ActorView],
    prefix: str,
    grid: float,
    report: ValidationReport,
) -> None:
    """把缺地板/缺墙从单件缺失提升为可聚合的结构洞/缝指标。"""
    actual_by_name: dict[str, ActorView] = {}
    for actor in actors:
        stripped = strip_batch_name(actor.name, prefix)
        if stripped is not None and stripped not in actual_by_name:
            actual_by_name[stripped] = actor

    floor_holes = 0
    wall_gaps = 0
    for placement in expected:
        if not (_is_visual_floor(placement) or placement.kind == "wall"):
            continue
        actual_actor = actual_by_name.get(placement.name)
        if actual_actor is not None and _actor_matches_placement(actual_actor, placement):
            continue
        units = _coverage_units(placement, grid)
        if _is_visual_floor(placement):
            floor_holes += units
            report.violations.append(f"地板缺口：{placement.name} 未覆盖 {units} 格")
        elif placement.kind == "wall":
            wall_gaps += units
            report.violations.append(f"墙体缺口：{placement.name} 缺失 {units} 格墙段")

    report.metrics["floor_hole_count"] = floor_holes
    report.metrics["wall_gap_count"] = wall_gaps


def _coverage_units(placement: Placement, grid: float) -> int:
    if placement.target_size is None or grid <= 0:
        return 1
    x_units = max(1, round(placement.target_size[0] / grid))
    y_units = max(1, round(placement.target_size[1] / grid))
    if _is_visual_floor(placement):
        return x_units * y_units
    return max(x_units, y_units)


def _aabb(actor: ActorView) -> tuple[tuple[float, float], ...]:
    """构件的世界 AABB：((x_lo, x_hi), (y_lo, y_hi), (z_lo, z_hi))。"""
    return tuple(
        (c - s * _CUBE_HALF, c + s * _CUBE_HALF)
        for c, s in zip(actor.location, actor.scale, strict=False)
    )


def _placement_for_actor(
    actor_name: str, expected_by_name: dict[str, Placement], prefix: str
) -> Placement | None:
    stripped = strip_batch_name(actor_name, prefix)
    return expected_by_name.get(stripped or "")


def _overlap_exempt(actor_name: str, placement: Placement | None) -> bool:
    if placement is not None and placement.kind in {"nav_proxy", "route", "spawn"}:
        return True
    return "_corner_" in actor_name or "_navproxy" in actor_name


def _overlap_pair_exempt(
    placement_a: Placement | None,
    placement_b: Placement | None,
    box_a: tuple[tuple[float, float], ...],
    box_b: tuple[tuple[float, float], ...],
    laps: tuple[float, float, float],
) -> bool:
    if placement_a is None or placement_b is None:
        return False
    return _floor_vertical_slab_lap(placement_a, placement_b, box_a, box_b, laps) or (
        _floor_vertical_slab_lap(placement_b, placement_a, box_b, box_a, laps)
    )


def _floor_vertical_slab_lap(
    floor: Placement,
    vertical: Placement,
    floor_box: tuple[tuple[float, float], ...],
    vertical_box: tuple[tuple[float, float], ...],
    laps: tuple[float, float, float],
) -> bool:
    """多层结构里，楼板厚度向下吃进下层墙/柱/楼梯井是合法承托关系。"""
    if not _is_visual_floor(floor) or vertical.kind not in {"wall", "stairwell", "pillar"}:
        return False
    floor_z = floor_box[2]
    vertical_z = vertical_box[2]
    floor_thickness = floor_z[1] - floor_z[0]
    return (
        vertical_z[0] <= floor_z[0] + _LOCATION_TOL
        and vertical_z[1] >= floor_z[1] - _LOCATION_TOL
        and laps[2] <= floor_thickness + _LOCATION_TOL
    )


def _placement_aabb(placement: Placement) -> tuple[tuple[float, float], ...]:
    """编译器目标 AABB；无目标信息时退回旧 cube scale 近似。"""
    if placement.target_min is not None and placement.target_size is not None:
        return tuple(
            (placement.target_min[i], placement.target_min[i] + placement.target_size[i])
            for i in range(3)
        )
    actor = ActorView(
        name=placement.name,
        location=placement.location,
        scale=placement.scale,
        rotation=placement.rotation,
    )
    return _aabb(actor)


def _actor_matches_placement(actor: ActorView, placement: Placement) -> bool:
    loc_drift = max(abs(a - b) for a, b in zip(actor.location, placement.location, strict=False))
    scale_drift = max(abs(a - b) for a, b in zip(actor.scale, placement.scale, strict=False))
    rotation_drift = max(
        _angle_delta(a, b) for a, b in zip(actor.rotation, placement.rotation, strict=False)
    )
    return (
        loc_drift <= _LOCATION_TOL and scale_drift <= _SCALE_TOL and rotation_drift <= _ROTATION_TOL
    )


def _angle_delta(a: float, b: float) -> float:
    """角度差的最短环绕距离；UE 可能把 270 回读成 -90。"""
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _check_route_blockers(
    expected: list[Placement],
    actors: list[ActorView],
    prefix: str,
    report: ValidationReport,
) -> None:
    corridors = _route_corridors(expected)
    if not corridors:
        return
    expected_by_name = {p.name: p for p in expected}
    for actor in actors:
        stripped = strip_batch_name(actor.name, prefix)
        if not stripped:
            continue
        placement = expected_by_name.get(stripped)
        if placement is None or placement.kind not in {"cover", "pillar", "prop"}:
            continue
        box = (
            _placement_aabb(placement)
            if _actor_matches_placement(actor, placement)
            else _aabb(actor)
        )
        for corridor in corridors:
            if _overlap_axes(box, corridor) is None:
                continue
            report.violations.append(f"主路线被阻挡：{actor.name} 占用 route corridor")
            break


def _route_corridors(expected: list[Placement]) -> list[tuple[tuple[float, float], ...]]:
    groups: dict[int, list[Placement]] = {}
    for placement in expected:
        if placement.kind != "route":
            continue
        route_id = int(placement.metadata.get("route_id", 0))
        groups.setdefault(route_id, []).append(placement)

    corridors: list[tuple[tuple[float, float], ...]] = []
    for points in groups.values():
        ordered = sorted(points, key=lambda p: p.name)
        for left, right in pairwise(ordered):
            x0, y0, z0 = left.location
            x1, y1, z1 = right.location
            if (
                abs(x0 - x1) > _ROUTE_CORRIDOR_HALF_WIDTH
                and abs(y0 - y1) > _ROUTE_CORRIDOR_HALF_WIDTH
            ):
                corridors.append(_route_point_corridor(left))
                corridors.append(_route_point_corridor(right))
                continue
            corridors.append(
                (
                    (
                        min(x0, x1) - _ROUTE_CORRIDOR_HALF_WIDTH,
                        max(x0, x1) + _ROUTE_CORRIDOR_HALF_WIDTH,
                    ),
                    (
                        min(y0, y1) - _ROUTE_CORRIDOR_HALF_WIDTH,
                        max(y0, y1) + _ROUTE_CORRIDOR_HALF_WIDTH,
                    ),
                    (min(z0, z1) - 20.0, max(z0, z1) + 200.0),
                )
            )
    return corridors


def _route_point_corridor(placement: Placement) -> tuple[tuple[float, float], ...]:
    x, y, z = placement.location
    return (
        (x - _ROUTE_CORRIDOR_HALF_WIDTH, x + _ROUTE_CORRIDOR_HALF_WIDTH),
        (y - _ROUTE_CORRIDOR_HALF_WIDTH, y + _ROUTE_CORRIDOR_HALF_WIDTH),
        (z - 20.0, z + 200.0),
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
    grid: float,
    report: ValidationReport,
    level_metrics: LevelMetrics | None,
) -> None:
    wb_actors = [a for a in actors if strip_batch_name(a.name, prefix)]
    floors = [p for p in expected if _is_visual_floor(p)]
    walls = [p for p in expected if p.kind == "wall"]
    route_ids = {int(p.metadata.get("route_id", 0)) for p in expected if p.kind == "route"}
    floor_area = sum(
        (p.target_size[0] * p.target_size[1] / 10_000.0)
        if p.target_size is not None
        else p.scale[0] * p.scale[1]
        for p in floors
    )
    total_wall_length_m = sum(
        max(p.target_size[0], p.target_size[1]) / 100.0
        if p.target_size is not None
        else max(p.scale[0], p.scale[1])
        for p in walls
    )
    xs = [p.location[0] for p in expected]
    ys = [p.location[1] for p in expected]
    report.metrics.update(
        {
            "structure_mode": spec.structure_mode,
            "room_count": len(spec.rooms),
            "door_count": sum(len(room.doors) for room in spec.rooms),
            "level_count": len({room.level for room in spec.rooms}),
            "expected_count": len(expected),
            "actual_count": len(wb_actors),
            "wall_count": len(walls),
            "stair_count": sum(1 for p in expected if p.kind == "stair"),
            "stairwell_count": sum(1 for p in expected if p.kind == "stairwell"),
            "prop_count": sum(1 for p in expected if p.kind in {"prop", "cover", "pillar"}),
            "spawn_count": sum(1 for p in expected if p.kind == "spawn"),
            "route_count": len(route_ids),
            "floor_area_m2": round(floor_area, 1),
            "wall_fragmentation_score": round(
                len(walls) / max(total_wall_length_m, 1.0),
                3,
            ),
            "bbox_center_xy": (round((min(xs) + max(xs)) / 2), round((min(ys) + max(ys)) / 2)),
        }
    )
    report.metrics.update(audit_layout_scale(spec, grid=grid, metrics=level_metrics).metrics)


def _is_visual_floor(placement: Placement) -> bool:
    return placement.kind == "floor" or (
        "_floor" in placement.name and not _is_nav_proxy(placement)
    )


def _is_nav_proxy(placement: Placement) -> bool:
    return placement.kind == "nav_proxy" or "_navproxy" in placement.name
