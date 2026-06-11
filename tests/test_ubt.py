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


# 2026-06-10 真机样本：工具链缺失时 UBT 的失败输出没有 ERROR: 前缀
REAL_TOOLCHAIN_MISSING = r"""
Using bundled DotNet SDK version: 8.0.412 win-x64
Running UnrealBuildTool: dotnet "UnrealBuildTool.dll" agent_testEditor Win64 Development
Log file: C:\Users\x\AppData\Local\UnrealBuildTool\Log.txt
Creating makefile for agent_testEditor (no existing makefile)
Platform Win64 is not a valid platform to build. Check that the SDK is installed properly.

Result: Failed (OtherCompilationError)
Total execution time: 0.84 seconds
"""


def test_real_sample_toolchain_missing():
    diagnostics = parse_output(REAL_TOOLCHAIN_MISSING)
    assert len(diagnostics) == 1
    assert diagnostics[0].kind == "error"
    assert diagnostics[0].code == "OtherCompilationError"
    assert "not a valid platform" in diagnostics[0].message


def test_result_failed_without_context_line():
    diagnostics = parse_output("Log file: C:\\x\\Log.txt\nResult: Failed (RulesError)\n")
    assert len(diagnostics) == 1
    assert diagnostics[0].code == "RulesError"
    assert "raw_tail" in diagnostics[0].message


# 2026-06-11 真机样本：有具体编译错误时，Result: Failed 汇总行不应重复成第二条
REAL_COMPILE_ERROR = r"""
[1/7] Compile [x64] agent_test.cpp
C:\Game\Source\agent_test\agent_test.cpp(4): error C2065: "ThisSymbolDoesNotExist": 未声明的标识符
Total time in Unreal Build Accelerator local executor: 0.41 seconds
Result: Failed (OtherCompilationError)
Total execution time: 2.95 seconds
"""


def test_summary_line_suppressed_when_real_error_present():
    diagnostics = parse_output(REAL_COMPILE_ERROR)
    assert len(diagnostics) == 1
    assert diagnostics[0].code == "C2065"
    assert diagnostics[0].line == 4
    assert "未声明的标识符" in diagnostics[0].message


def test_build_timeout_returns_structured_failure(monkeypatch, tmp_path):
    import subprocess

    from ue5agent.mcp_servers.ue_build import ubt

    killed = []

    class FakePopen:
        def __init__(self, *args, **kwargs):
            self.pid = 12345

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired(cmd="Build.bat", timeout=timeout)

    monkeypatch.setattr(ubt.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(ubt, "_kill_tree", lambda pid: killed.append(pid))
    result = ubt.run_build(tmp_path, tmp_path / "x.uproject", "agent_testEditor")
    assert result.success is False
    assert result.error_count == 1
    assert result.diagnostics[0].code == "BuildTimeout"
    assert "超时" in result.diagnostics[0].message
    assert killed == [12345]  # 必须连根杀进程树
