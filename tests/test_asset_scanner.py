"""资产扫描：几何反推、名称+几何混合归类、合并保留 curation、YAML 往返、wb_asset_scan 工具。"""

import ue5agent.mcp_servers.ue_whitebox.server as wb_server
from ue5agent.whitebox.asset_preview_cache import load_asset_preview_cache
from ue5agent.whitebox.manifest import load_manifest
from ue5agent.whitebox.scanner import (
    AssetRecord,
    build_manifest_dict,
    classify,
    classify_by_name,
    diff_manifest,
    emit_yaml,
    records_from_bounds_payload,
)


class TestClassify:
    def test_name_prefix_hits(self):
        assert classify_by_name("Wall1_4")[0] == "wall"
        assert classify_by_name("Doorframe1_2_1")[0] == "wall_door"
        # 玻璃幕墙前缀须在 window 之前命中
        assert classify_by_name("W_GLS_FRM_2_3")[0] == "glass_wall"
        # collection 命名命中但标 ambiguous
        cat, _tags, ambiguous, _note = classify_by_name("Collection9")
        assert cat == "prop" and ambiguous is True

    def test_name_unknown(self):
        assert classify_by_name("Blob_017")[0] == "unknown"

    def test_geometry_fallback_when_name_unknown(self):
        # 薄高片 → 墙类（几何先验）
        r = classify("Blob_017", (100, 20, 400))
        assert r.category == "wall" and r.method == "geometry" and r.ambiguous is True
        # 扁平大件 → 地板
        assert classify("Mesh_x", (400, 400, 40)).category == "floor"
        # 细高 → 柱
        assert classify("Mesh_y", (40, 40, 400)).category == "pillar"

    def test_name_takes_priority_over_geometry(self):
        # 命名命中即用命名，不被几何覆盖
        r = classify("Floor2_2", (200, 200, 100))
        assert r.category == "floor" and r.method == "name"

    def test_totally_unclassifiable(self):
        r = classify("zzz", (500, 500, 500))
        assert r.category == "unknown" and r.method == "none" and r.ambiguous is True


class TestBuildAsset:
    def test_pivot_reversed_from_bounds(self):
        # 原点居中：min=-50 max=50 → pivot.x=0.5；底面 z：min=0 → pivot.z=0
        rec = AssetRecord("/Game/Kit/wall/Wall", (-50, 0, 0), (50, 20, 400))
        m = build_manifest_dict([rec])
        a = m["assets"]["wall"]
        assert a["pivot"] == [0.5, 0.0, 0.0]
        assert a["size"] == [100, 20, 400]
        assert a["local_bounds_min"] == [-50, 0, 0]
        assert a["calibrated"] is True
        assert a["footprint"] == [1, 1]

    def test_geometry_guess_marks_needs_review(self):
        rec = AssetRecord("/Game/Kit/misc/Blob_017", (0, 0, 0), (100, 20, 400))
        a = build_manifest_dict([rec])["assets"]["blob_017"]
        assert a["needs_review"] is True
        assert "墙类" in a["review"]

    def test_pivot_outside_flagged(self):
        # 原点远在几何外：min=(0,0,0) max=(100,20,400)，但若 min 让 pivot 越界
        rec = AssetRecord("/Game/Kit/wall/Wall1_1", (0, 20, 200), (100, 40, 300))
        a = build_manifest_dict([rec])["assets"]["wall1_1"]
        # pivot.z = (0-200)/100 = -2.0 → 越界
        assert a["needs_review"] is True
        assert "pivot 越界" in a["review"]

    def test_name_classified_keeps_desc_no_review(self):
        rec = AssetRecord("/Game/Kit/wall/Wall1_4", (0, 0, 0), (100, 20, 400))
        a = build_manifest_dict([rec])["assets"]["wall1_4"]
        assert a.get("needs_review") is None


class TestMergePreservesCuration:
    def _existing(self, tmp_path):
        p = tmp_path / "old.yaml"
        p.write_text(
            """
version: 2
grid: 100
roles:
  wall: wall1_4
  floor: floor_special
assets:
  wall1_4:
    path: /Game/Kit/wall/Wall1_4
    size: [100, 20, 400]
    category: wall
    desc: "人工写的墙说明"
""",
            encoding="utf-8",
        )
        return load_manifest(p)

    def test_roles_preserved_but_dangling_dropped(self, tmp_path):
        existing = self._existing(tmp_path)
        rec = AssetRecord("/Game/Kit/wall/Wall1_4", (0, 0, 0), (100, 20, 400))
        m = build_manifest_dict([rec], existing=existing)
        # 仍有效的手调 role 保留；指向已删除资产的 floor_special 被丢弃（无 floor 类资产可补）
        assert m["roles"] == {"wall": "wall1_4"}
        # 人工 desc 在几何无 desc 时沿用
        assert m["assets"]["wall1_4"]["desc"] == "人工写的墙说明"

    def test_dangling_role_refilled_by_guess(self, tmp_path):
        existing = self._existing(tmp_path)
        # 新资产里有一个 floor 类件 → floor 角色丢弃旧 floor_special 后用它补齐
        recs = [
            AssetRecord("/Game/Kit/wall/Wall1_4", (0, 0, 0), (100, 20, 400)),
            AssetRecord("/Game/Kit/floor/Flooroutdoor", (0, 0, 0), (200, 200, 45)),
        ]
        m = build_manifest_dict(recs, existing=existing)
        assert m["roles"]["wall"] == "wall1_4"
        assert m["roles"]["floor"] == "flooroutdoor"  # 旧 floor_special 不存在，回退到现有 floor

    def test_geometry_fields_always_refresh(self, tmp_path):
        existing = self._existing(tmp_path)
        # UE 实测尺寸变了（重导缩放）
        rec = AssetRecord("/Game/Kit/wall/Wall1_4", (0, 0, 0), (100, 20, 800))
        m = build_manifest_dict([rec], existing=existing)
        assert m["assets"]["wall1_4"]["size"] == [100, 20, 800]


class TestEmitYamlRoundTrip:
    def test_round_trip_through_loader(self, tmp_path):
        recs = [
            AssetRecord("/Game/Kit/wall/Wall1_4", (0, 0, 0), (100, 20, 400)),
            AssetRecord("/Game/Kit/floor/Floor2_2", (0, 0, 0), (200, 200, 100)),
            AssetRecord("/Game/Kit/misc/Blob_017", (0, 0, 0), (100, 20, 400)),
        ]
        m = build_manifest_dict(recs)
        out = tmp_path / "gen.yaml"
        out.write_text(emit_yaml(m), encoding="utf-8")

        loaded = load_manifest(out)
        assert loaded.version == 2
        wall = loaded.require("wall1_4")
        assert wall.size == (100.0, 20.0, 400.0)
        assert wall.calibrated is True
        assert wall.local_bounds_max == (100.0, 20.0, 400.0)
        assert loaded.require("blob_017").needs_review is True


class TestDiff:
    def test_added_removed_resized(self, tmp_path):
        p = tmp_path / "old.yaml"
        p.write_text(
            """
version: 2
grid: 100
assets:
  wall1_4:
    path: /Game/Kit/wall/Wall1_4
    size: [100, 20, 400]
    category: wall
  gone:
    path: /Game/Kit/gone/Gone
    size: [100, 100, 100]
    category: prop
""",
            encoding="utf-8",
        )
        existing = load_manifest(p)
        recs = [
            AssetRecord("/Game/Kit/wall/Wall1_4", (0, 0, 0), (100, 20, 800)),  # resized
            AssetRecord("/Game/Kit/new/NewThing", (0, 0, 0), (100, 20, 400)),  # added
        ]
        m = build_manifest_dict(recs, existing=existing)
        report = diff_manifest(m, existing)
        assert "newthing" in report.added
        assert "gone" in report.removed
        assert any("wall1_4" in r for r in report.resized)


class TestRecordsFromPayload:
    def test_min_max_and_size_only_and_invalid(self):
        items = [
            {"path": "/Game/A", "min": [0, 0, 0], "max": [100, 20, 400]},
            {"asset_path": "/Game/B", "size": [50, 50, 50]},
            {"path": "/Game/C"},  # 无尺寸 → 跳过
            "garbage",  # 非 dict → 跳过
        ]
        recs = records_from_bounds_payload(items)
        assert [r.path for r in recs] == ["/Game/A", "/Game/B"]
        assert recs[1].bounds_min == (0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# wb_asset_scan 工具（mock 桥）
# ---------------------------------------------------------------------------

_SCAN_RESULT = {
    "status": "success",
    "result": {
        "assets": [
            {
                "path": "/Game/LevelPrototyping/Meshes/ArchKit/wall/Wall1_4",
                "min": [0, 0, 0],
                "max": [100, 20, 400],
            },
            {
                "path": "/Game/LevelPrototyping/Meshes/ArchKit/misc/Blob_017",
                "min": [0, 0, 0],
                "max": [100, 20, 400],
            },
        ]
    },
}

_SCAN_RESULT_WITH_PREVIEW = {
    "status": "success",
    "result": {
        "assets": [
            {
                "path": "/Game/LevelPrototyping/Meshes/ArchKit/prop/Crate_L",
                "min": [0, 0, 0],
                "max": [100, 100, 100],
                "preview": {
                    "top_silhouette": [[0, 0], [1, 0], [1, 0.45], [0.45, 1], [0, 1]],
                    "thumbnail_path": "Saved/WhiteboxPreviews/Crate_L.png",
                },
            }
        ]
    },
}


def test_wb_asset_scan_preview_does_not_write(monkeypatch, tmp_path):
    manifest_path = tmp_path / "kit.yaml"
    manifest_path.write_text("version: 2\ngrid: 100\nassets: {}\n", encoding="utf-8")
    monkeypatch.setattr(wb_server, "_MANIFEST", manifest_path)

    def fake_send(command, params=None, **_kwargs):
        assert command == "scan_assets"
        return _SCAN_RESULT

    monkeypatch.setattr(wb_server, "send_command", fake_send)
    before = manifest_path.read_text(encoding="utf-8")

    out = wb_server.wb_asset_scan(apply=False)

    assert "预览模式" in out
    assert manifest_path.read_text(encoding="utf-8") == before  # 未写盘
    assert '"kind": "wb_asset_scan"' in out
    assert '"applied": false' in out


def test_wb_asset_scan_apply_writes_loadable_manifest(monkeypatch, tmp_path):
    manifest_path = tmp_path / "kit.yaml"
    manifest_path.write_text("version: 2\ngrid: 100\nassets: {}\n", encoding="utf-8")
    monkeypatch.setattr(wb_server, "_MANIFEST", manifest_path)
    monkeypatch.setattr(wb_server, "send_command", lambda *a, **k: _SCAN_RESULT)

    out = wb_server.wb_asset_scan(apply=True)

    assert "已写出 manifest" in out
    loaded = load_manifest(manifest_path)
    assert loaded.require("wall1_4").size == (100.0, 20.0, 400.0)
    assert loaded.require("blob_017").needs_review is True


def test_wb_asset_scan_apply_writes_preview_cache(monkeypatch, tmp_path):
    manifest_path = tmp_path / "kit.yaml"
    manifest_path.write_text("version: 2\ngrid: 100\nassets: {}\n", encoding="utf-8")
    monkeypatch.setattr(wb_server, "_MANIFEST", manifest_path)
    monkeypatch.setattr(wb_server, "send_command", lambda *a, **k: _SCAN_RESULT_WITH_PREVIEW)

    out = wb_server.wb_asset_scan(apply=True)
    cache_path = tmp_path / "asset_preview_cache.json"
    cache = load_asset_preview_cache(cache_path)

    assert "已写出 preview cache" in out
    assert cache_path.exists()
    assert "crate_l" in cache.items
    assert cache.items["crate_l"].thumbnail_path == "Saved/WhiteboxPreviews/Crate_L.png"
    assert '"preview_asset_count": 1' in out


def test_wb_asset_scan_falls_back_to_get_mesh_bounds(monkeypatch, tmp_path):
    manifest_path = tmp_path / "kit.yaml"
    manifest_path.write_text(
        """
version: 2
grid: 100
assets:
  wall1_4:
    path: /Game/LevelPrototyping/Meshes/ArchKit/wall/Wall1_4
    size: [100, 20, 400]
    category: wall
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(wb_server, "_MANIFEST", manifest_path)

    def fake_send(command, params=None, **_kwargs):
        if command == "scan_assets":
            return {"status": "error", "error": "Unknown command: scan_assets"}
        assert command == "get_mesh_bounds"
        return {"status": "success", "result": {"min": [0, 0, 0], "max": [100, 20, 800]}}

    monkeypatch.setattr(wb_server, "send_command", fake_send)

    out = wb_server.wb_asset_scan(apply=False)

    assert "get_mesh_bounds 回退" in out
    assert '"mode": "get_mesh_bounds 回退"' in out


def test_wb_asset_scan_bridge_refused(monkeypatch, tmp_path):
    manifest_path = tmp_path / "kit.yaml"
    manifest_path.write_text("version: 2\ngrid: 100\nassets: {}\n", encoding="utf-8")
    monkeypatch.setattr(wb_server, "_MANIFEST", manifest_path)

    def fake_send(command, params=None, **_kwargs):
        raise ConnectionRefusedError()

    monkeypatch.setattr(wb_server, "send_command", fake_send)

    out = wb_server.wb_asset_scan(apply=False)
    assert "编辑器" in out  # 环境未就绪标记
