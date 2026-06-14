"""关卡尺度 metrics：把真实空间尺度约束从视觉理解中分离出来。

视觉可以判断"这是几个房间、怎样相连"，但米制尺寸必须由 metrics 表收敛；
本模块只做确定性尺度审计，不参与几何生成。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ue5agent.whitebox.compiler import LayoutSpec


@dataclass(frozen=True)
class RoomScaleMetrics:
    min_area_m2: float = 6.0
    min_dimension_m: float = 2.0
    max_area_m2: float = 240.0


@dataclass(frozen=True)
class OpeningScaleMetrics:
    min_door_width_m: float = 0.9
    min_window_width_m: float = 0.6


@dataclass(frozen=True)
class VerticalScaleMetrics:
    min_clear_height_m: float = 2.4
    max_room_height_m: float = 4.5


@dataclass(frozen=True)
class LevelMetricsProfile:
    name: str = "realistic"
    room: RoomScaleMetrics = field(default_factory=RoomScaleMetrics)
    opening: OpeningScaleMetrics = field(default_factory=OpeningScaleMetrics)
    vertical: VerticalScaleMetrics = field(default_factory=VerticalScaleMetrics)


@dataclass(frozen=True)
class LevelMetrics:
    profiles: dict[str, LevelMetricsProfile] = field(
        default_factory=lambda: {"realistic": LevelMetricsProfile()}
    )

    def require_profile(self, name: str) -> LevelMetricsProfile:
        key = name.strip().lower()
        if key not in self.profiles:
            available = "、".join(sorted(self.profiles))
            raise ValueError(f"level metrics 未配置 scale_profile={name!r}（可用：{available}）")
        return self.profiles[key]


@dataclass(frozen=True)
class ScaleAudit:
    profile: str
    warnings: list[str]
    metrics: dict[str, Any]


DEFAULT_LEVEL_METRICS = LevelMetrics()


def load_level_metrics(path: Path) -> LevelMetrics:
    """从 YAML 载入尺度 metrics；未写字段使用 realistic 默认值补齐。"""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw_profiles = data.get("profiles") or {}
    profiles: dict[str, LevelMetricsProfile] = {}
    for name, raw_profile in raw_profiles.items():
        if not isinstance(raw_profile, dict):
            continue
        profile_name = str(name).strip().lower()
        profiles[profile_name] = LevelMetricsProfile(
            name=profile_name,
            room=_room_metrics(raw_profile.get("room")),
            opening=_opening_metrics(raw_profile.get("opening")),
            vertical=_vertical_metrics(raw_profile.get("vertical")),
        )
    if "realistic" not in profiles:
        profiles["realistic"] = LevelMetricsProfile()
    return LevelMetrics(profiles=profiles)


def audit_layout_scale(
    spec: LayoutSpec,
    *,
    grid: float,
    metrics: LevelMetrics | None = None,
) -> ScaleAudit:
    """按 metrics 审计布局尺度，返回 warning 与可观测指标。

    第一版只诊断、不判 FAIL：几何正确性仍由 validator violations 决定。
    """
    level_metrics = metrics or DEFAULT_LEVEL_METRICS
    profile = level_metrics.require_profile(spec.scale_profile)
    grid_m = grid / 100.0
    warnings: list[str] = []

    room_areas: list[float] = []
    room_min_dims: list[float] = []
    door_widths: list[float] = []
    window_widths: list[float] = []

    for room in spec.rooms:
        width_m = room.rect[2] * grid_m
        depth_m = room.rect[3] * grid_m
        area_m2 = width_m * depth_m
        min_dim_m = min(width_m, depth_m)
        room_areas.append(area_m2)
        room_min_dims.append(min_dim_m)

        if area_m2 < profile.room.min_area_m2:
            warnings.append(
                f"尺度警告：房间 {room.name} 面积 {area_m2:.1f}m² "
                f"小于 {profile.name} 最小 {profile.room.min_area_m2:.1f}m²"
            )
        if min_dim_m < profile.room.min_dimension_m:
            warnings.append(
                f"尺度警告：房间 {room.name} 最窄边 {min_dim_m:.1f}m "
                f"小于 {profile.name} 最小 {profile.room.min_dimension_m:.1f}m"
            )
        if area_m2 > profile.room.max_area_m2:
            warnings.append(
                f"尺度警告：房间 {room.name} 面积 {area_m2:.1f}m² "
                f"大于 {profile.name} 常规上限 {profile.room.max_area_m2:.1f}m²"
            )

        for door in room.doors:
            width = door.width * grid_m
            door_widths.append(width)
            if width < profile.opening.min_door_width_m:
                warnings.append(
                    f"尺度警告：房间 {room.name} 门洞宽 {width:.1f}m "
                    f"小于最小通行宽 {profile.opening.min_door_width_m:.1f}m"
                )
        for window in room.windows:
            width = window.width * grid_m
            window_widths.append(width)
            if width < profile.opening.min_window_width_m:
                warnings.append(
                    f"尺度警告：房间 {room.name} 窗洞宽 {width:.1f}m "
                    f"小于常规最小 {profile.opening.min_window_width_m:.1f}m"
                )

    wall_height_m = spec.wall_height / 100.0
    if wall_height_m < profile.vertical.min_clear_height_m:
        warnings.append(
            f"尺度警告：墙高/净高 {wall_height_m:.1f}m "
            f"小于真实室内最小 {profile.vertical.min_clear_height_m:.1f}m"
        )
    if wall_height_m > profile.vertical.max_room_height_m:
        warnings.append(
            f"尺度警告：墙高/净高 {wall_height_m:.1f}m "
            f"大于真实室内常规上限 {profile.vertical.max_room_height_m:.1f}m"
        )

    values: dict[str, Any] = {
        "scale_profile": profile.name,
        "scale_grid_m": round(grid_m, 3),
        "min_room_area_m2": _round_or_zero(min(room_areas) if room_areas else None),
        "max_room_area_m2": _round_or_zero(max(room_areas) if room_areas else None),
        "min_room_dimension_m": _round_or_zero(min(room_min_dims) if room_min_dims else None),
        "min_door_width_m": _round_or_zero(min(door_widths) if door_widths else None),
        "min_window_width_m": _round_or_zero(min(window_widths) if window_widths else None),
        "wall_height_m": round(wall_height_m, 2),
        "scale_warning_count": len(warnings),
        "scale_warnings": warnings,
    }
    return ScaleAudit(profile=profile.name, warnings=warnings, metrics=values)


def _room_metrics(raw: object) -> RoomScaleMetrics:
    data = raw if isinstance(raw, dict) else {}
    defaults = RoomScaleMetrics()
    return RoomScaleMetrics(
        min_area_m2=float(data.get("min_area_m2", defaults.min_area_m2)),
        min_dimension_m=float(data.get("min_dimension_m", defaults.min_dimension_m)),
        max_area_m2=float(data.get("max_area_m2", defaults.max_area_m2)),
    )


def _opening_metrics(raw: object) -> OpeningScaleMetrics:
    data = raw if isinstance(raw, dict) else {}
    defaults = OpeningScaleMetrics()
    return OpeningScaleMetrics(
        min_door_width_m=float(data.get("min_door_width_m", defaults.min_door_width_m)),
        min_window_width_m=float(data.get("min_window_width_m", defaults.min_window_width_m)),
    )


def _vertical_metrics(raw: object) -> VerticalScaleMetrics:
    data = raw if isinstance(raw, dict) else {}
    defaults = VerticalScaleMetrics()
    return VerticalScaleMetrics(
        min_clear_height_m=float(data.get("min_clear_height_m", defaults.min_clear_height_m)),
        max_room_height_m=float(data.get("max_room_height_m", defaults.max_room_height_m)),
    )


def _round_or_zero(value: float | None) -> float:
    return 0.0 if value is None else round(value, 2)
