"""白盒资产清单：模块件的路径/尺寸/类别/pivot/角色（manifest 质量决定搭建质量）。

v2（ADR-0004 资产抽象层）在 v1（path/size/category）基础上新增：
- pivot：归一化原点位置（[0.5,0.5,0.5]=正中，[0.5,0.5,0.0]=底面中心）。
  由 wb_asset_scan/fbx_probe 从包围盒**反推、不 clamp**，编译器据此补偿落地，
  使原点不统一（甚至落在几何外）的真实资产也能精确摆放。
- footprint：占格数（X×Y），供 props 放置层与网格对齐校验用。
- roles：结构角色（floor/wall/wall_door…）→ 资产 key 的映射，编译器按角色取件；
  未配置角色时回退到 `cube`，保证 v1 清单（levelprototyping.yaml）行为不变。
- tags/desc/source_fbx/needs_review：检索、参考图图鉴、溯源与人工复核标记。

向后兼容：v1 清单无 version/roles/pivot/footprint，loader 全部给默认值
（version=1、pivot=[0.5,0.5,0.5]=cube 等价、footprint=[1,1]、roles={}），
cube 老路逐字节不变。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

# cube 等价默认：正中 pivot，使 v1 资产经新编译路径产出与旧逻辑完全一致的坐标。
_DEFAULT_PIVOT: tuple[float, float, float] = (0.5, 0.5, 0.5)


@dataclass
class AssetDef:
    key: str
    path: str
    size: tuple[float, float, float]
    category: str
    pivot: tuple[float, float, float] = _DEFAULT_PIVOT
    footprint: tuple[int, int] = (1, 1)
    tags: tuple[str, ...] = ()
    desc: str = ""
    source_fbx: str = ""
    needs_review: bool = False


@dataclass
class Manifest:
    grid: float
    assets: dict[str, AssetDef]
    version: int = 1
    roles: dict[str, str] = field(default_factory=dict)

    def require(self, key: str) -> AssetDef:
        if key not in self.assets:
            raise KeyError(f"manifest 中没有资产：{key}（可用：{', '.join(self.assets)}）")
        return self.assets[key]

    def asset_for_role(self, role: str, *, fallback_key: str = "cube") -> AssetDef:
        """按结构角色取件：先查 roles 映射，未配置则回退到 fallback_key（默认 cube）。

        这是"清单驱动编译器"的入口——换成真实 kit 只需改 roles，编译器无需改动；
        v1 清单（无 roles）则全部回退 cube，行为与升级前一致。
        """
        mapped = self.roles.get(role)
        if mapped is not None:
            return self.require(mapped)
        if fallback_key in self.assets:
            return self.assets[fallback_key]
        raise KeyError(
            f"manifest 未配置角色 {role!r}（roles 缺该项），且无回退资产 {fallback_key!r}；"
            f"请在 roles 下指定 {role} 对应的资产 key"
        )


def _as_xyz(raw: object, default: tuple[float, float, float]) -> tuple[float, float, float]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        return default
    return (float(raw[0]), float(raw[1]), float(raw[2]))


def load_manifest(path: Path) -> Manifest:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assets = {
        key: AssetDef(
            key=key,
            path=item["path"],
            size=(float(item["size"][0]), float(item["size"][1]), float(item["size"][2])),
            category=item.get("category", "block"),
            pivot=_as_xyz(item.get("pivot"), _DEFAULT_PIVOT),
            footprint=_footprint(item.get("footprint")),
            tags=tuple(str(t) for t in (item.get("tags") or [])),
            desc=str(item.get("desc", "")),
            source_fbx=str(item.get("source_fbx", "")),
            needs_review=bool(item.get("needs_review", False)),
        )
        for key, item in data["assets"].items()
    }
    roles = {str(k): str(v) for k, v in (data.get("roles") or {}).items()}
    return Manifest(
        grid=float(data.get("grid", 100)),
        assets=assets,
        version=int(data.get("version", 1)),
        roles=roles,
    )


def _footprint(raw: object) -> tuple[int, int]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return (1, 1)
    return (max(1, int(raw[0])), max(1, int(raw[1])))
