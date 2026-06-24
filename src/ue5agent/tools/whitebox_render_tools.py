"""白盒本地预览渲染工具：给 agent 调用的 compiler 级视觉证据。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ue5agent.core.permissions import PermissionLevel
from ue5agent.tools.effects import ToolEffects
from ue5agent.tools.registry import ToolSpec
from ue5agent.whitebox.asset_preview_cache import (
    AssetPreviewCache,
    load_asset_preview_cache,
    preview_cache_path_for_manifest,
)
from ue5agent.whitebox.compiler import LayoutError
from ue5agent.whitebox.manifest import Manifest, load_manifest
from ue5agent.whitebox.preview_renderer import facts_json, render_layout_preview

_RENDER_EFFECTS = ToolEffects(
    idempotent=True,
    requires_checkpoint=False,
    supports_dry_run=False,
    resources=("build_artifacts",),
)
_MAX_LAYOUT_BYTES = 2_000_000


def build_whitebox_render_tools(project_root: Path) -> list[ToolSpec]:
    root = project_root.resolve()

    def _safe_output_dir(raw: str | None, layout_name: str) -> Path:
        if raw:
            candidate = Path(raw)
            path = candidate if candidate.is_absolute() else root / candidate
        else:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in layout_name)
            path = root / "runs" / "whitebox_render_preview" / f"{stamp}_{safe_name or 'layout'}"
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"路径越界（必须在工程根 {root} 内）：{raw}")
        return resolved

    async def whitebox_render_preview(
        layout_json: str | None = None,
        layout_path: str | None = None,
        layout_artifact: str | None = None,
        output_dir: str | None = None,
    ) -> str:
        try:
            layout = _load_layout(
                root,
                layout_json=layout_json,
                layout_path=layout_path or layout_artifact,
            )
            layout_name = str(layout.get("name", "layout")).strip() or "layout"
            result = render_layout_preview(
                layout,
                _safe_output_dir(output_dir, layout_name),
                manifest=_load_preview_manifest(root),
                preview_cache=_load_preview_cache(root),
            )
        except (LayoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            return f"[error] 本地白盒预览失败：{exc}"
        topology_line = ""
        if result.wall_topology is not None:
            status = "PASS" if result.wall_topology.ok else "FAIL"
            topology_line = (
                f"- 墙图拓扑：{status}，components={result.wall_topology.component_count}，"
                f"dangling={result.wall_topology.dangling_endpoint_count}，"
                f"near_miss={result.wall_topology.near_miss_count}，"
                f"isolated={result.wall_topology.isolated_segment_count}\n"
            )
        fidelity = "AABB 低保真预览，不显示真实 StaticMesh 外形"
        if result.mesh_proxy_count:
            fidelity = "mesh proxy"
        elif result.silhouette_proxy_count:
            fidelity = "silhouette proxy"
        return (
            f"本地白盒预览完成：{result.placement_count} 个 compiler placement，"
            f"{len(result.views)} 个视角（{fidelity}）。\n"
            f"- contact sheet：{result.contact_sheet}\n"
            f"{topology_line}"
            f"[facts] {facts_json(result)}"
        )

    return [
        ToolSpec(
            "whitebox_render_preview",
            (
                "把白盒 layout_json 编译为 placements，并在本地生成 top/iso 多角度 "
                "contact sheet；默认视觉审查优先使用该工具，不依赖 UE 视口截图。"
            ),
            _schema(
                layout_json={
                    "type": "string",
                    "description": "白盒 DSL JSON 字符串；也可改传 layout_path/layout_artifact",
                },
                layout_path={
                    "type": "string",
                    "description": "工程根内的 layout JSON 文件路径",
                },
                layout_artifact={
                    "type": "string",
                    "description": "当前 run artifact 路径，runner 会优先受控展开为 layout_json",
                },
                output_dir={
                    "type": "string",
                    "description": (
                        "输出目录，缺省写入 runs/whitebox_render_preview/；必须在工程根内"
                    ),
                },
            ),
            PermissionLevel.WRITE_PROJECT,
            whitebox_render_preview,
            effects=_RENDER_EFFECTS,
        )
    ]


def _load_layout(
    root: Path,
    *,
    layout_json: str | None = None,
    layout_path: str | None = None,
) -> dict[str, Any]:
    if isinstance(layout_json, str) and layout_json.strip():
        return _normalize_layout_payload(json.loads(layout_json))
    if isinstance(layout_path, str) and layout_path.strip():
        path = _resolve_layout_path(root, layout_path)
        if path.stat().st_size > _MAX_LAYOUT_BYTES:
            raise ValueError(f"layout 文件过大：{path}")
        return _normalize_layout_payload(json.loads(path.read_text(encoding="utf-8")))
    raise ValueError("必须提供 layout_json 或 layout_path/layout_artifact")


def _resolve_layout_path(root: Path, raw: str) -> Path:
    candidate = Path(raw)
    path = candidate if candidate.is_absolute() else root / candidate
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"路径越界（必须在工程根 {root} 内）：{raw}")
    if resolved.suffix.lower() != ".json":
        raise ValueError(f"只允许读取 JSON layout：{resolved}")
    if not resolved.exists() or not resolved.is_file():
        raise ValueError(f"layout 文件不存在：{resolved}")
    return resolved


def _normalize_layout_payload(payload: Any) -> dict[str, Any]:
    if (
        isinstance(payload, dict)
        and "layout" in payload
        and not any(key in payload for key in ("rooms", "walls"))
    ):
        payload = payload["layout"]
    if not isinstance(payload, dict):
        raise ValueError("layout 必须是 JSON object")
    if "rooms" not in payload and "walls" not in payload:
        raise ValueError("layout 缺少 rooms 或 walls")
    return payload


def _load_preview_manifest(root: Path) -> Manifest:
    manifest_path = root / "config" / "whitebox" / "kit.yaml"
    if manifest_path.exists():
        return load_manifest(manifest_path)
    return Manifest(grid=100.0, assets={})


def _load_preview_cache(root: Path) -> AssetPreviewCache:
    manifest_path = root / "config" / "whitebox" / "kit.yaml"
    return load_asset_preview_cache(preview_cache_path_for_manifest(manifest_path))


def _schema(**props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props}


__all__ = ["build_whitebox_render_tools"]
