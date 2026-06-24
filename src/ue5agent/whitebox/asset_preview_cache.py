"""白盒资产本地预览 cache。

扫描阶段可把 UE 侧导出的简化几何写到 manifest 同目录的 JSON 旁路文件中。
本模块只负责纯数据标准化，不依赖 UE：renderer 目前优先消费 top_silhouette，
后续桥端能导出更完整 mesh 时可直接复用 simplified_mesh 字段。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ue5agent.whitebox.scanner import asset_key

MeshVertices = tuple[tuple[float, float, float], ...]
MeshFaces = tuple[tuple[int, ...], ...]


@dataclass(frozen=True)
class AssetPreview:
    key: str
    source: str
    top_silhouette: tuple[tuple[float, float], ...] = ()
    mesh_vertices: MeshVertices = ()
    mesh_faces: MeshFaces = ()
    thumbnail_path: str = ""

    @property
    def has_renderable_proxy(self) -> bool:
        return bool(self.mesh_vertices and self.mesh_faces) or bool(self.top_silhouette)


@dataclass(frozen=True)
class AssetPreviewCache:
    items: dict[str, AssetPreview] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.items)

    def renderable_items(self) -> dict[str, AssetPreview]:
        return {key: item for key, item in self.items.items() if item.has_renderable_proxy}


def preview_cache_path_for_manifest(manifest_path: str | Path) -> Path:
    """约定 cache 与 kit.yaml 同目录，避免改 manifest schema 也能被工具自动发现。"""
    return Path(manifest_path).with_name("asset_preview_cache.json")


def preview_cache_from_scan_items(items: list[dict]) -> AssetPreviewCache:
    previews: dict[str, AssetPreview] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or item.get("asset_path") or "").strip()
        if not path:
            continue
        key = asset_key(path)
        preview = _preview_from_scan_item(key, path, item)
        if preview is not None:
            previews[key] = preview
    return AssetPreviewCache(items=previews)


def load_asset_preview_cache(path: str | Path) -> AssetPreviewCache:
    cache_path = Path(path)
    if not cache_path.exists():
        return AssetPreviewCache(items={})
    data = json.loads(cache_path.read_text(encoding="utf-8"))
    assets = data.get("assets") if isinstance(data, dict) else None
    if not isinstance(assets, dict):
        return AssetPreviewCache(items={})
    previews: dict[str, AssetPreview] = {}
    for raw_key, raw_item in assets.items():
        if not isinstance(raw_key, str) or not isinstance(raw_item, dict):
            continue
        preview = _preview_from_cache_item(raw_key, raw_item)
        if preview is not None:
            previews[preview.key] = preview
    return AssetPreviewCache(items=previews)


def write_asset_preview_cache(cache: AssetPreviewCache, path: str | Path) -> None:
    cache_path = Path(path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "assets": {key: _preview_to_dict(preview) for key, preview in sorted(cache.items.items())},
    }
    cache_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _preview_from_scan_item(key: str, source: str, item: dict[str, Any]) -> AssetPreview | None:
    preview = item.get("preview") if isinstance(item.get("preview"), dict) else {}
    assert isinstance(preview, dict)
    raw_silhouette = _first_value(preview, item, "top_silhouette", "silhouette")
    raw_mesh = _first_value(preview, item, "simplified_mesh", "mesh")
    thumbnail = str(_first_value(preview, item, "thumbnail_path", "thumbnail") or "").strip()
    silhouette = _parse_polygon(raw_silhouette)
    vertices, faces = _parse_mesh(raw_mesh)
    if not silhouette and not (vertices and faces) and not thumbnail:
        return None
    return AssetPreview(
        key=key,
        source=source,
        top_silhouette=silhouette,
        mesh_vertices=vertices,
        mesh_faces=faces,
        thumbnail_path=thumbnail,
    )


def _preview_from_cache_item(key: str, item: dict[str, Any]) -> AssetPreview | None:
    source = str(item.get("source") or "").strip()
    silhouette = _parse_polygon(item.get("top_silhouette") or item.get("silhouette"))
    vertices, faces = _parse_mesh(item.get("simplified_mesh") or item.get("mesh"))
    thumbnail = str(item.get("thumbnail_path") or item.get("thumbnail") or "").strip()
    if not source:
        source = key
    if not silhouette and not (vertices and faces) and not thumbnail:
        return None
    return AssetPreview(
        key=key,
        source=source,
        top_silhouette=silhouette,
        mesh_vertices=vertices,
        mesh_faces=faces,
        thumbnail_path=thumbnail,
    )


def _preview_to_dict(preview: AssetPreview) -> dict[str, Any]:
    out: dict[str, Any] = {
        "source": preview.source,
    }
    if preview.top_silhouette:
        out["top_silhouette"] = [list(point) for point in preview.top_silhouette]
    if preview.mesh_vertices and preview.mesh_faces:
        out["simplified_mesh"] = {
            "vertices": [list(vertex) for vertex in preview.mesh_vertices],
            "faces": [list(face) for face in preview.mesh_faces],
        }
    if preview.thumbnail_path:
        out["thumbnail_path"] = preview.thumbnail_path
    return out


def _first_value(primary: dict[str, Any], fallback: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in primary:
            return primary[key]
        if key in fallback:
            return fallback[key]
    return None


def _parse_polygon(raw: object) -> tuple[tuple[float, float], ...]:
    if not isinstance(raw, (list, tuple)) or len(raw) < 3:
        return ()
    points: list[tuple[float, float]] = []
    for point in raw:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            return ()
        x, y = point
        if not _finite_unit(x) or not _finite_unit(y):
            return ()
        points.append((float(x), float(y)))
    if len(set(points)) < 3:
        return ()
    return tuple(points)


def _parse_mesh(
    raw: object,
) -> tuple[MeshVertices, MeshFaces]:
    if not isinstance(raw, dict):
        return (), ()
    raw_vertices = raw.get("vertices")
    raw_faces = raw.get("faces")
    if not isinstance(raw_vertices, (list, tuple)) or not isinstance(raw_faces, (list, tuple)):
        return (), ()
    vertices: list[tuple[float, float, float]] = []
    for vertex in raw_vertices:
        if not isinstance(vertex, (list, tuple)) or len(vertex) != 3:
            return (), ()
        x, y, z = vertex
        if not _finite_unit(x) or not _finite_unit(y) or not _finite_unit(z):
            return (), ()
        vertices.append((float(x), float(y), float(z)))
    faces: list[tuple[int, ...]] = []
    for face in raw_faces:
        if not isinstance(face, (list, tuple)) or len(face) < 3:
            continue
        parsed_face: list[int] = []
        valid = True
        for index in face:
            if isinstance(index, bool) or not isinstance(index, int):
                valid = False
                break
            if index < 0 or index >= len(vertices):
                valid = False
                break
            parsed_face.append(index)
        if valid and len(set(parsed_face)) >= 3:
            faces.append(tuple(parsed_face))
    if not vertices or not faces:
        return (), ()
    return tuple(vertices), tuple(faces)


def _finite_unit(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0.0 <= float(value) <= 1.0
    )


__all__ = [
    "AssetPreview",
    "AssetPreviewCache",
    "load_asset_preview_cache",
    "preview_cache_from_scan_items",
    "preview_cache_path_for_manifest",
    "write_asset_preview_cache",
]
