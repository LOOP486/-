"""compiler 级白盒预览工具包装。"""

from __future__ import annotations

import json

from ue5agent.agent.tool_pipeline import extract_facts
from ue5agent.core.permissions import PermissionLevel
from ue5agent.tools.whitebox_render_tools import build_whitebox_render_tools


async def test_whitebox_render_preview_tool_writes_contact_sheet_and_facts(tmp_path):
    layout = {
        "name": "tool_preview",
        "structure_mode": "slab",
        "scale_profile": "realistic",
        "walls": [{"name": "wall_a", "from": [0, 0], "to": [4, 0]}],
    }
    spec = {spec.name: spec for spec in build_whitebox_render_tools(tmp_path)}[
        "whitebox_render_preview"
    ]

    text = await spec.handler(
        layout_json=json.dumps(layout, ensure_ascii=False),
        output_dir="runs/render-preview-test",
    )
    visible, facts = extract_facts(text)

    assert spec.level is PermissionLevel.WRITE_PROJECT
    assert spec.effects is not None
    assert spec.effects.requires_checkpoint is False
    assert "本地白盒预览" in visible
    assert facts is not None
    assert facts["kind"] == "render_preview"
    assert facts["ok"] is True
    assert facts["geometry_fidelity"] == "aabb"
    assert facts["mesh_fidelity"] == "none"
    assert facts["asset_shape_exact"] is False
    assert facts["view_count"] == 3
    assert facts["wall_topology"]["ok"] is True
    assert (tmp_path / "runs" / "render-preview-test" / "contact_sheet.png").exists()


async def test_whitebox_render_preview_tool_loads_asset_preview_cache(tmp_path):
    config = tmp_path / "config" / "whitebox"
    config.mkdir(parents=True)
    (config / "kit.yaml").write_text(
        """
version: 2
grid: 100
assets:
  crate_l:
    path: /Game/Kit/Props/Crate_L
    size: [100, 100, 100]
    category: prop
    pivot: [0.5, 0.5, 0]
    calibrated: true
""",
        encoding="utf-8",
    )
    (config / "asset_preview_cache.json").write_text(
        json.dumps(
            {
                "version": 1,
                "assets": {
                    "crate_l": {
                        "source": "/Game/Kit/Props/Crate_L",
                        "top_silhouette": [
                            [0, 0],
                            [1, 0],
                            [1, 0.45],
                            [0.45, 0.45],
                            [0.45, 1],
                            [0, 1],
                        ],
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    layout = {
        "name": "tool_silhouette_preview",
        "structure_mode": "slab",
        "scale_profile": "realistic",
        "rooms": [
            {
                "name": "Room",
                "rect": [0, 0, 5, 5],
                "doors": [],
                "props": [{"key": "crate_l", "at": [2, 2]}],
            }
        ],
    }
    spec = {spec.name: spec for spec in build_whitebox_render_tools(tmp_path)}[
        "whitebox_render_preview"
    ]

    text = await spec.handler(
        layout_json=json.dumps(layout, ensure_ascii=False),
        output_dir="runs/render-preview-cache-test",
    )
    visible, facts = extract_facts(text)

    assert "silhouette proxy" in visible
    assert facts is not None
    assert facts["geometry_fidelity"] == "silhouette"
    assert facts["mesh_fidelity"] == "proxy"
    assert facts["silhouette_proxy_count"] == 1


async def test_whitebox_render_preview_tool_marks_bad_wall_topology(tmp_path):
    layout = {
        "name": "bad_topology",
        "structure_mode": "slab",
        "scale_profile": "realistic",
        "walls": [
            {"name": "horizontal", "from": [0, 0], "to": [6, 0]},
            {"name": "vertical", "from": [6, 1], "to": [6, 5]},
        ],
    }
    spec = {spec.name: spec for spec in build_whitebox_render_tools(tmp_path)}[
        "whitebox_render_preview"
    ]

    text = await spec.handler(
        layout_json=json.dumps(layout, ensure_ascii=False),
        output_dir="runs/render-bad-topology-test",
    )
    visible, facts = extract_facts(text)

    assert "墙图拓扑" in visible
    assert facts is not None
    assert facts["kind"] == "render_preview"
    assert facts["ok"] is False
    assert facts["wall_topology"]["ok"] is False
    assert facts["wall_topology"]["near_miss_count"] == 1
    assert (tmp_path / "runs" / "render-bad-topology-test" / "contact_sheet.png").exists()


async def test_whitebox_render_preview_tool_reads_layout_path_under_project(tmp_path):
    layout = {
        "name": "path_preview",
        "structure_mode": "slab",
        "scale_profile": "realistic",
        "walls": [{"name": "wall_a", "from": [0, 0], "to": [0, 4]}],
    }
    layout_path = tmp_path / "runs" / "layouts" / "layout_walls.json"
    layout_path.parent.mkdir(parents=True)
    layout_path.write_text(json.dumps(layout, ensure_ascii=False), encoding="utf-8")
    spec = {spec.name: spec for spec in build_whitebox_render_tools(tmp_path)}[
        "whitebox_render_preview"
    ]

    text = await spec.handler(layout_path=str(layout_path), output_dir="runs/render-path-test")
    _visible, facts = extract_facts(text)

    assert facts is not None
    assert facts["ok"] is True
    assert facts["layout_name"] == "path_preview"


async def test_whitebox_render_preview_tool_rejects_layout_path_escape(tmp_path):
    outside = tmp_path.parent / "outside_layout.json"
    outside.write_text('{"walls": []}', encoding="utf-8")
    spec = {spec.name: spec for spec in build_whitebox_render_tools(tmp_path)}[
        "whitebox_render_preview"
    ]

    text = await spec.handler(layout_path=str(outside), output_dir="runs/render-escape-test")

    assert text.startswith("[error]")
    assert "路径越界" in text
