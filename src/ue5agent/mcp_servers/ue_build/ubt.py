"""UnrealBuildTool 调用与输出解析（纯函数部分独立成模块，便于单测）。"""

from __future__ import annotations

import re
import subprocess
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
    for raw_line in output.splitlines():
        diagnostic = _parse_line(raw_line.strip())
        if diagnostic is None:
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
    timeout_seconds: int = 1800,
) -> BuildResult:
    command = build_command(engine_root, uproject, target, configuration)
    process = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
    )
    output = (process.stdout or "") + "\n" + (process.stderr or "")
    diagnostics = parse_output(output)
    return BuildResult(
        success=process.returncode == 0,
        exit_code=process.returncode,
        error_count=sum(1 for d in diagnostics if d.kind == "error"),
        warning_count=sum(1 for d in diagnostics if d.kind == "warning"),
        diagnostics=diagnostics,
        raw_tail=output[-4000:],
    )
