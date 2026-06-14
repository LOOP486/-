"""关卡尺度 metrics：真实空间默认表与尺度审计。"""

from pathlib import Path

from ue5agent.whitebox.compiler import layout_from_dict
from ue5agent.whitebox.level_metrics import audit_layout_scale, load_level_metrics


def test_load_level_metrics_realistic_profile_from_yaml(tmp_path: Path):
    path = tmp_path / "level_metrics.yaml"
    path.write_text(
        """
version: 1
profiles:
  realistic:
    room:
      min_area_m2: 10
      min_dimension_m: 2
    opening:
      min_door_width_m: 0.9
    vertical:
      min_clear_height_m: 2.4
      max_room_height_m: 4.2
""",
        encoding="utf-8",
    )

    metrics = load_level_metrics(path)
    spec = layout_from_dict(
        {
            "name": "custom-metrics",
            "rooms": [{"name": "Small", "rect": [0, 0, 3, 3]}],
        }
    )

    audit = audit_layout_scale(spec, grid=100, metrics=metrics)

    assert audit.profile == "realistic"
    assert any("Small" in warning and "面积" in warning for warning in audit.warnings)
