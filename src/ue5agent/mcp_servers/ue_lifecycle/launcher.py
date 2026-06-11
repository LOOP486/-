"""编辑器进程启动（纯逻辑：probe/spawn/clock 可注入替身单测）。"""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from ue5agent.mcp_servers.ue_editor.bridge import probe_editor


class LaunchError(Exception):
    """编辑器启动失败（路径缺失或就绪超时）。"""


def editor_exe(engine_root: Path) -> Path:
    return engine_root / "Engine" / "Binaries" / "Win64" / "UnrealEditor.exe"


def spawn_detached(command: list[str]) -> None:
    """脱离式启动：编辑器存活不依赖 MCP server 进程。"""
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )


def launch_editor(
    engine_root: str | Path,
    uproject: str | Path,
    *,
    timeout: float = 240.0,
    poll_interval: float = 5.0,
    probe: Callable[[], bool] = probe_editor,
    spawn: Callable[[list[str]], None] = spawn_detached,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    """启动编辑器并轮询桥端口直到就绪；已在运行则幂等返回。"""
    if probe():
        return "编辑器已在运行（桥端口可达），无需启动"
    exe = editor_exe(Path(engine_root))
    if not exe.exists():
        raise LaunchError(f"找不到编辑器可执行文件：{exe}")
    project = Path(uproject)
    if not project.exists():
        raise LaunchError(f"找不到工程文件：{project}")
    spawn([str(exe), str(project)])
    started = clock()
    while clock() - started < timeout:
        sleep(poll_interval)
        if probe():
            return f"编辑器已启动并就绪（{project.name}，等待 {clock() - started:.0f}s）"
    raise LaunchError(
        f"编辑器进程已启动但 {timeout:.0f}s 内桥端口未就绪——"
        "可能仍在加载，稍后用 editor_status 复查，不要重复启动"
    )
