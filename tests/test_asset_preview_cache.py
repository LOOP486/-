"""资产预览 cache：扫描 payload → 本地 renderer 可消费的 silhouette/mesh/thumbnail 索引。"""

from __future__ import annotations

from ue5agent.whitebox.asset_preview_cache import (
    AssetPreviewCache,
    load_asset_preview_cache,
    preview_cache_from_scan_items,
    preview_cache_path_for_manifest,
    write_asset_preview_cache,
)


def test_preview_cache_from_scan_items_parses_silhouette_mesh_and_thumbnail():
    cache = preview_cache_from_scan_items(
        [
            {
                "path": "/Game/Kit/Props/Crate_A",
                "preview": {
                    "top_silhouette": [[0, 0], [1, 0], [0.4, 1]],
                    "simplified_mesh": {
                        "vertices": [[0, 0, 0], [1, 0, 0], [0.4, 1, 0], [0.4, 1, 1]],
                        "faces": [[0, 1, 2], [0, 2, 3]],
                    },
                    "thumbnail_path": "cache/thumbs/crate_a.png",
                },
            },
            {
                "asset_path": "/Game/Kit/Props/BadSilhouette",
                "top_silhouette": [[0, 0], [2, 0], [0, 1]],
            },
            "garbage",
        ]
    )

    assert sorted(cache.items) == ["crate_a"]
    preview = cache.items["crate_a"]
    assert preview.top_silhouette == ((0.0, 0.0), (1.0, 0.0), (0.4, 1.0))
    assert preview.mesh_vertices[3] == (0.4, 1.0, 1.0)
    assert preview.mesh_faces == ((0, 1, 2), (0, 2, 3))
    assert preview.thumbnail_path == "cache/thumbs/crate_a.png"


def test_preview_cache_round_trips_json(tmp_path):
    source = preview_cache_from_scan_items(
        [
            {
                "path": "/Game/Kit/Props/Table_A",
                "top_silhouette": [[0, 0], [1, 0], [1, 0.5], [0, 1]],
            }
        ]
    )
    path = tmp_path / "asset_preview_cache.json"

    write_asset_preview_cache(source, path)
    loaded = load_asset_preview_cache(path)

    assert loaded == source


def test_preview_cache_missing_file_is_empty(tmp_path):
    assert load_asset_preview_cache(tmp_path / "missing.json") == AssetPreviewCache(items={})


def test_preview_cache_path_for_manifest_is_sibling():
    path = preview_cache_path_for_manifest("config/whitebox/kit.yaml")

    assert path.as_posix().endswith("config/whitebox/asset_preview_cache.json")
