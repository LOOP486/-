"""fs 工具：越界拦截、读写改、检索。"""

import pytest

from ue5agent.tools.fs_tools import build_fs_tools


@pytest.fixture
def tools(tmp_path):
    (tmp_path / "Source").mkdir()
    (tmp_path / "Source" / "a.cpp").write_text("int x = 1;\nint y = 2;\n", encoding="utf-8")
    return {spec.name: spec for spec in build_fs_tools(tmp_path)}


async def test_escape_blocked(tools):
    with pytest.raises(ValueError, match="越界"):
        await tools["read_file"].handler(path="../outside.txt")


async def test_read_with_line_numbers(tools):
    text = await tools["read_file"].handler(path="Source/a.cpp")
    assert "    1 int x = 1;" in text


async def test_write_and_replace(tools):
    await tools["write_file"].handler(path="Source/new.h", content="#pragma once\n")
    result = await tools["replace_in_file"].handler(
        path="Source/new.h", old="#pragma once", new="#pragma once\n// ok"
    )
    assert "已替换" in result
    missing = await tools["replace_in_file"].handler(path="Source/new.h", old="不存在", new="x")
    assert "[error]" in missing


async def test_replace_requires_unique(tools):
    result = await tools["replace_in_file"].handler(path="Source/a.cpp", old="int", new="long")
    assert "出现 2 次" in result


async def test_search(tools):
    hits = await tools["search_text"].handler(pattern=r"y = \d")
    assert "a.cpp:2" in hits


async def test_permission_levels(tools):
    assert tools["read_file"].level.value == "read"
    assert tools["write_file"].level.value == "write_project"
