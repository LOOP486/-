"""白盒资产清单：模块件的路径/尺寸/类别（manifest 质量决定搭建质量）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class AssetDef:
    key: str
    path: str
    size: tuple[float, float, float]
    category: str


@dataclass
class Manifest:
    grid: float
    assets: dict[str, AssetDef]

    def require(self, key: str) -> AssetDef:
        if key not in self.assets:
            raise KeyError(f"manifest 中没有资产：{key}（可用：{', '.join(self.assets)}）")
        return self.assets[key]


def load_manifest(path: Path) -> Manifest:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assets = {
        key: AssetDef(
            key=key,
            path=item["path"],
            size=(float(item["size"][0]), float(item["size"][1]), float(item["size"][2])),
            category=item.get("category", "block"),
        )
        for key, item in data["assets"].items()
    }
    return Manifest(grid=float(data.get("grid", 100)), assets=assets)
