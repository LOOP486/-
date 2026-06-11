"""UnrealBuildTool 调用与输出解析（纯函数部分独立成模块，便于单测）。"""

from __future__ import annotations

import contextlib
import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

# MSVC 诊断行，如：
#   D:\Game\Source\Foo.cpp(42): error C2065: 'Bar': undeclared identifier
#   D:\Game\Source\Foo.h(7,15): warning C4100: unreferenced formal parameter
_MSVC_DIAGNOSTIC = re.compile(
    r"^(?P<file>.+?)\((?P<line>\d+)(?:,\d+)?\)\s*:\s*"
    r"(?P<kind>error|warning)\s+(?P<code>[A-Z]+\d+)\s*:\s*(?P<message>.*)$"
)
# 链接诊断，如：Foo.cpp.obj : error LNK2019: unresolved external symbol ...
_LINK_DIAGNOSTIC = re.compile(
    r"^(?P<file>\S.*?)\s*:\s*(?P<kind>error|warning)\s+(?P<code>LNK\d+)\s*:\s*(?P<message>.*)$"
)
# UBT 自身错误，如：ERROR: Missing precompiled manifest ...
_UBT_ERROR = re.compile(r"^\s*ERROR:\s*(?P<message>.+)$")
# UBT 失败汇总行，如：Result: Failed (OtherCompilationError)
# 真实失败（如工具链缺失）常常只有一行无前缀说明 + 这行汇总（2026-06 真机样本）
_UBT_RESULT_FAILED = re.compile(r"^\s*Result:\s*Failed\s*\((?P<reason>[^)]+)\)")
_BOILERPLATE_PREFIXES = (
    "Using bundled",
    "Running UnrealBuildTool",
    "Log file:",
    "Total execution",
    "Total time in",
    "Trace written",
    "Creating makefile",
    "Building ",
)


@dataclass
class Diagnostic:
    kind: str
    message: str
    file: str | None = None
    line: int | None = None
    code: str | None = None


@dataclass
class BuildResult:
    success: bool
    exit_code: int
    error_count: int
    warning_count: int
    diagnostics: list[Diagnostic]
    raw_tail: str
    """原始输出末尾，便于排查解析遗漏的报错形态。"""

    def to_dict(self) -> dict:
        return asdict(self)


def parse_output(output: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    seen: set[tuple] = set()  # UBT 并行构建会重复打印同一条诊断
    last_context: str | None = None  # 最近一行无前缀的说明文字，给 Result: Failed 当消息用
    for raw_line in output.splitlines():
        line = raw_line.strip()
        diagnostic = _parse_line(line)
        if diagnostic is None:
            failed = _UBT_RESULT_FAILED.match(line)
            if failed:
                if any(d.kind == "error" for d in diagnostics):
                    continue  # 已解析出具体错误，汇总行不再重复
                diagnostic = Diagnostic(
                    kind="error",
                    message=last_context or "UBT 构建失败（未解析出具体诊断，见 raw_tail）",
                    code=failed.group("reason"),
                )
            elif line and not line.startswith(_BOILERPLATE_PREFIXES):
                last_context = line
                continue
            else:
                continue
        key = (diagnostic.file, diagnostic.line, diagnostic.code, diagnostic.message)
        if key not in seen:
            seen.add(key)
            diagnostics.append(diagnostic)
    return diagnostics


def _parse_line(line: str) -> Diagnostic | None:
    for pattern in (_MSVC_DIAGNOSTIC, _LINK_DIAGNOSTIC):
        match = pattern.match(line)
        if match:
            groups = match.groupdict()
            return Diagnostic(
                kind=groups["kind"],
                message=groups["message"].strip(),
                file=groups.get("file"),
                line=int(groups["line"]) if groups.get("line") else None,
                code=groups.get("code"),
            )
    match = _UBT_ERROR.match(line)
    if match:
        return Diagnostic(kind="error", message=match.group("message").strip())
    return None


def build_command(
    engine_root: Path,
    uproject: Path,
    target: str,
    configuration: str = "Development",
    platform_name: str = "Win64",
) -> list[str]:
    build_bat = engine_root / "Engine" / "Build" / "BatchFiles" / "Build.bat"
    return [
        str(build_bat),
        target,
        platform_name,
        configuration,
        f"-Project={uproject}",
        "-WaitMutex",
    ]


def run_build(
    engine_root: Path,
    uproject: Path,
    target: str,
    configuration: str = "Development",
    timeout_seconds: int = 600,
) -> BuildResult:
    """编译目标。超时（默认 10 分钟）转为结构化失败，绝不让调用方无限阻塞。

    实现要点（2026-06-11 真机僵死教训）：
    - 输出落临时文件而非管道：UBT 的子进程（git/UBA）会继承管道句柄，
      它们僵死时管道读取端永久阻塞；
    - 超时用 taskkill /T 杀整个进程树，避免孤儿 dotnet 继续持有资源。
    """
    command = build_command(engine_root, uproject, target, configuration)
    log_fd, log_name = tempfile.mkstemp(suffix=".ubt.log")
    log_path = Path(log_name)
    try:
        with open(log_fd, "w", encoding="utf-8", errors="replace") as out:
            process = subprocess.Popen(command, stdout=out, stderr=subprocess.STDOUT)
            try:
                exit_code = process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                _kill_tree(process.pid)
                partial = log_path.read_text(encoding="utf-8", errors="replace")
                return BuildResult(
                    success=False,
                    exit_code=-1,
                    error_count=1,
                    warning_count=0,
                    diagnostics=[
                        Diagnostic(
                            kind="error",
                            message=(
                                f"编译超时（{timeout_seconds}s 未结束，已终止进程树）。"
                                "常见原因：另一构建/编辑器持锁，或首次全量编译过慢。"
                            ),
                            code="BuildTimeout",
                        )
                    ],
                    raw_tail=partial[-4000:],
                )
        output = log_path.read_text(encoding="utf-8", errors="replace")
    finally:
        with contextlib.suppress(OSError):
            log_path.unlink()
    diagnostics = parse_output(output)
    return BuildResult(
        success=exit_code == 0,
        exit_code=exit_code,
        error_count=sum(1 for d in diagnostics if d.kind == "error"),
        warning_count=sum(1 for d in diagnostics if d.kind == "warning"),
        diagnostics=diagnostics,
        raw_tail=output[-4000:],
    )


def _kill_tree(pid: int) -> None:
    """Windows 下 kill() 只杀直接子进程，必须 /T 连根拔掉孙进程（dotnet/git/UBA）。"""
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        timeout=30,
    )
