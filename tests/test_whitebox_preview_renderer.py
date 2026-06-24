"""白盒 compiler 级本地预览渲染器。"""

from __future__ import annotations

import json

from PIL import Image

from ue5agent.whitebox import preview_renderer
from ue5agent.whitebox.asset_preview_cache import AssetPreview, AssetPreviewCache
from ue5agent.whitebox.manifest import AssetDef, Manifest
from ue5agent.whitebox.preview_renderer import render_layout_preview


def test_render_layout_preview_writes_multi_view_contact_sheet(tmp_path):
    layout = {
        "name": "preview_room",
        "structure_mode": "slab",
        "scale_profile": "realistic",
        "rooms": [{"name": "Room", "rect": [0, 0, 4, 3], "doors": []}],
        "walls": [{"name": "loose_wall", "from": [5, 0], "to": [5, 3]}],
    }

    result = render_layout_preview(layout, tmp_path / "preview")

    assert result.ok is True
    assert result.contact_sheet.exists()
    assert {view.name for view in result.views} == {"top", "iso_ne", "iso_sw"}
    assert all(view.path.exists() for view in result.views)
    assert result.placement_count >= 6
    with Image.open(result.contact_sheet) as image:
        assert image.width > image.height
        assert image.getbbox() is not None


def test_render_layout_preview_default_resolution_preserves_wall_detail(tmp_path):
    layout = {
        "name": "detail_preview",
        "structure_mode": "slab",
        "scale_profile": "realistic",
        "walls": [
            {"name": "long_wall", "from": [0, 0], "to": [24, 0]},
            {"name": "short_return", "from": [12, 0], "to": [12, 3]},
        ],
    }

    result = render_layout_preview(layout, tmp_path / "preview")

    for view in result.views:
        with Image.open(view.path) as image:
            assert image.width >= 1024
            assert image.height >= 768
    with Image.open(result.contact_sheet) as image:
        assert image.width >= 3072
        assert image.height >= 768


def test_render_layout_preview_emits_render_preview_facts(tmp_path):
    layout = {
        "name": "wall_only_preview",
        "structure_mode": "slab",
        "scale_profile": "realistic",
        "walls": [{"name": "wall_a", "from": [0, 0], "to": [8, 0]}],
    }

    result = render_layout_preview(layout, tmp_path / "preview")
    facts = result.facts()

    assert facts["kind"] == "render_preview"
    assert facts["ok"] is True
    assert facts["source"] == "compiler"
    assert facts["geometry_fidelity"] == "aabb"
    assert facts["mesh_fidelity"] == "none"
    assert facts["asset_shape_exact"] is False
    assert facts["view_count"] == 3
    assert len(facts["paths"]) == 3
    assert all(path.endswith(".png") for path in facts["paths"])
    assert json.loads(json.dumps(facts, ensure_ascii=False))["path"].endswith("contact_sheet.png")


def test_render_layout_preview_uses_asset_silhouette_proxy(tmp_path):
    layout = {
        "name": "silhouette_preview",
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
    manifest = Manifest(
        grid=100,
        assets={
            "crate_l": AssetDef(
                key="crate_l",
                path="/Game/Kit/Props/Crate_L",
                size=(100, 100, 100),
                category="prop",
                pivot=(0.5, 0.5, 0),
                calibrated=True,
            )
        },
    )
    cache = AssetPreviewCache(
        items={
            "crate_l": AssetPreview(
                key="crate_l",
                source="/Game/Kit/Props/Crate_L",
                top_silhouette=((0, 0), (1, 0), (1, 0.45), (0.45, 0.45), (0.45, 1), (0, 1)),
            )
        }
    )

    result = render_layout_preview(
        layout, tmp_path / "preview", manifest=manifest, preview_cache=cache
    )
    facts = result.facts()

    assert facts["geometry_fidelity"] == "silhouette"
    assert facts["mesh_fidelity"] == "proxy"
    assert facts["preview_cache_assets"] == 1
    assert facts["silhouette_proxy_count"] == 1


def test_render_layout_preview_uses_simplified_mesh_proxy(tmp_path):
    layout = {
        "name": "mesh_preview",
        "structure_mode": "slab",
        "scale_profile": "realistic",
        "rooms": [
            {
                "name": "Room",
                "rect": [0, 0, 5, 5],
                "doors": [],
                "props": [{"key": "crate_mesh", "at": [2, 2]}],
            }
        ],
    }
    manifest = Manifest(
        grid=100,
        assets={
            "crate_mesh": AssetDef(
                key="crate_mesh",
                path="/Game/Kit/Props/Crate_Mesh",
                size=(100, 100, 100),
                category="prop",
                pivot=(0.5, 0.5, 0),
                calibrated=True,
            )
        },
    )
    cache = AssetPreviewCache(
        items={
            "crate_mesh": AssetPreview(
                key="crate_mesh",
                source="/Game/Kit/Props/Crate_Mesh",
                mesh_vertices=((0, 0, 0), (1, 0, 0), (0.5, 1, 0), (0.5, 0.5, 1)),
                mesh_faces=((0, 1, 2), (0, 1, 3), (1, 2, 3), (2, 0, 3)),
            )
        }
    )

    result = render_layout_preview(
        layout, tmp_path / "preview", manifest=manifest, preview_cache=cache
    )
    facts = result.facts()

    assert facts["geometry_fidelity"] == "mesh_proxy"
    assert facts["mesh_fidelity"] == "proxy"
    assert facts["mesh_proxy_count"] == 1


def test_top_view_silhouette_does_not_fill_entire_aabb():
    box = preview_renderer._Box(
        "crate_l",
        "prop",
        0,
        0,
        0,
        100,
        100,
        100,
        top_silhouette=((0, 0), (1, 0), (1, 0.45), (0.45, 0.45), (0.45, 1), (0, 1)),
    )

    image = preview_renderer._render_top_view([box], preview_renderer._bounds([box]), (240, 240))
    # 右上象限在 L 形 silhouette 外；若仍画 AABB，这里会是 prop 填充色。
    assert image.getpixel((144, 96)) == preview_renderer._BG


def test_iso_faces_are_camera_visible_and_depth_sorted():
    far = preview_renderer._Box(
        name="far",
        kind="wall",
        min_x=0,
        min_y=0,
        min_z=0,
        max_x=100,
        max_y=100,
        max_z=100,
    )
    near = preview_renderer._Box(
        name="near",
        kind="wall",
        min_x=300,
        min_y=300,
        min_z=0,
        max_x=400,
        max_y=400,
        max_z=100,
    )

    faces = preview_renderer._iso_visible_faces([near, far], flip=False)

    assert [face.depth for face in faces] == sorted(face.depth for face in faces)
    assert {face.name for face in faces if face.box.name == "near"} == {
        "x_min",
        "y_min",
        "z_max",
    }

    flipped = preview_renderer._iso_visible_faces([far], flip=True)

    assert {face.name for face in flipped} == {"x_max", "y_max", "z_max"}


def test_iso_preview_angle_keeps_four_internal_rectangles_visible():
    boxes = [
        preview_renderer._Box("floor", "floor", 0, 0, -20, 1200, 800, 0),
        preview_renderer._Box("south_a", "wall", -10, -10, 0, 500, 10, 400),
        preview_renderer._Box("south_b", "wall", 700, -10, 0, 1210, 10, 400),
        preview_renderer._Box("north_a", "wall", -10, 790, 0, 500, 810, 400),
        preview_renderer._Box("north_b", "wall", 700, 790, 0, 1210, 810, 400),
        preview_renderer._Box("west", "wall", -10, 10, 0, 10, 790, 400),
        preview_renderer._Box("east", "wall", 1190, 10, 0, 1210, 790, 400),
        preview_renderer._Box("prop_a", "prop", 200, 200, 0, 454, 672, 80),
        preview_renderer._Box("prop_b", "prop", 700, 200, 0, 954, 672, 80),
        preview_renderer._Box("prop_c", "prop", 700, 100, 0, 900, 170, 80),
        preview_renderer._Box("prop_d", "prop", 200, 100, 0, 400, 170, 80),
    ]
    bounds = preview_renderer._bounds(boxes)

    iso_ne = preview_renderer._render_iso_view(boxes, bounds, (1024, 768), flip=False)
    iso_sw = preview_renderer._render_iso_view(boxes, bounds, (1024, 768), flip=True)

    assert _structure_pixel_count(iso_ne) >= 22000
    assert _structure_pixel_count(iso_sw) >= 22000


def _structure_pixel_count(image: Image.Image) -> int:
    colors = {
        (140, 146, 154),
        (126, 131, 139),
        (104, 108, 114),
        (92, 97, 105),
    }
    pixels = image.load()
    return sum(1 for y in range(image.height) for x in range(image.width) if pixels[x, y] in colors)
