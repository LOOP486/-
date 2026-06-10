"""UBT 输出解析：MSVC 编译错误、链接错误、UBT 自身错误、去重。"""

from ue5agent.mcp_servers.ue_build.ubt import parse_output

SAMPLE_OUTPUT = r"""
Using bundled DotNet SDK version: 8.0.300
Building MyGameEditor...
[1/3] Compile [x64] MyActor.cpp
D:\Game\Source\MyGame\MyActor.cpp(42): error C2065: 'Helath': undeclared identifier
D:\Game\Source\MyGame\MyActor.h(7,15): warning C4100: 'DeltaTime': unreferenced formal parameter
[2/3] Link [x64] UnrealEditor-MyGame.dll
MyActor.cpp.obj : error LNK2019: unresolved external symbol TakeDamage referenced in Tick
ERROR: Missing precompiled manifest for 'CoreUObject'
D:\Game\Source\MyGame\MyActor.cpp(42): error C2065: 'Helath': undeclared identifier
"""


def test_parse_msvc_error():
    diagnostics = parse_output(SAMPLE_OUTPUT)
    errors = [d for d in diagnostics if d.code == "C2065"]
    assert len(errors) == 1
    assert errors[0].file == r"D:\Game\Source\MyGame\MyActor.cpp"
    assert errors[0].line == 42
    assert "Helath" in errors[0].message


def test_parse_warning_with_column():
    diagnostics = parse_output(SAMPLE_OUTPUT)
    warnings = [d for d in diagnostics if d.kind == "warning"]
    assert len(warnings) == 1
    assert warnings[0].code == "C4100"
    assert warnings[0].line == 7


def test_parse_linker_error():
    diagnostics = parse_output(SAMPLE_OUTPUT)
    link_errors = [d for d in diagnostics if d.code == "LNK2019"]
    assert len(link_errors) == 1
    assert link_errors[0].file == "MyActor.cpp.obj"
    assert link_errors[0].line is None


def test_parse_ubt_error():
    diagnostics = parse_output(SAMPLE_OUTPUT)
    ubt_errors = [d for d in diagnostics if d.code is None and d.kind == "error"]
    assert len(ubt_errors) == 1
    assert "precompiled manifest" in ubt_errors[0].message


def test_duplicates_removed():
    diagnostics = parse_output(SAMPLE_OUTPUT)
    assert sum(1 for d in diagnostics if d.code == "C2065") == 1


def test_clean_output_yields_nothing():
    assert parse_output("Building MyGameEditor...\nTotal time: 12.3s\n") == []
