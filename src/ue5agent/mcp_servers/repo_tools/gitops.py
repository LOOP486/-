"""repo_tools 的纯 git 操作（可对临时仓库单测；server.py 只做接线）。

checkpoint 用 write-tree/commit-tree 实现：不动 HEAD、不动工作区，
快照存进 refs/ue5agent/checkpoints/，restore 时按 ref 还原工作区与暂存区。
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile
import time
from pathlib import Path

# 身份：仓库可能没配提交者；fsmonitor：守护子进程会继承管道句柄导致读取端
# 永久阻塞（2026-06-11 repo_status 挂死 3h 的根因类），一并禁用
_GIT_FLAGS = [
    "-c",
    "user.name=ue5agent",
    "-c",
    "user.email=ue5agent@local",
    "-c",
    "core.fsmonitor=false",
]

# 非交互环境硬约束：禁止 git 弹凭据/SSH 等任何提示。MCP server 无 TTY 且 stdin
# 被协议占用，git 若等待输入会永久阻塞直到超时（2026-06-11 误报"不是 git 仓库"
# 的真因——is_git_repo 内的 rev-parse 挂死被当成非仓库）。
_GIT_ENV = {
    **os.environ,
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "echo",
    "GCM_INTERACTIVE": "Never",
    "GIT_OPTIONAL_LOCKS": "0",
}


def _git(repo: Path, *args: str, timeout_seconds: int = 60) -> str:
    """跑一条 git 命令：输出落临时文件 + 超时杀进程树（管道死锁免疫）。"""
    fd, name = tempfile.mkstemp(suffix=".git.log")
    log_path = Path(name)
    try:
        with open(fd, "w", encoding="utf-8", errors="replace") as out:
            process = subprocess.Popen(
                ["git", *_GIT_FLAGS, "-C", str(repo), *args],
                stdin=subprocess.DEVNULL,  # 切断 stdin：git 无法等待交互输入而挂死
                stdout=out,
                stderr=subprocess.STDOUT,
                env=_GIT_ENV,
            )
            try:
                code = process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=30,
                )
                raise RuntimeError(
                    f"git {' '.join(args)} 超时（{timeout_seconds}s），已终止进程树"
                ) from None
        output = log_path.read_text(encoding="utf-8", errors="replace")
    finally:
        with contextlib.suppress(OSError):
            log_path.unlink()
    if code != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败：{output.strip()[:500]}")
    return output


def is_git_repo(repo: Path) -> bool:
    try:
        return _git(repo, "rev-parse", "--is-inside-work-tree").strip() == "true"
    except (RuntimeError, OSError):
        return False


def checkpoint(repo: Path, label: str = "checkpoint") -> dict[str, str]:
    """全量快照（含未跟踪文件），返回 ref 与 sha。"""
    _git(repo, "add", "-A")
    tree = _git(repo, "write-tree").strip()
    commit_args = ["commit-tree", tree, "-m", label]
    try:
        parent = _git(repo, "rev-parse", "HEAD").strip()
        commit_args = ["commit-tree", tree, "-p", parent, "-m", label]
    except RuntimeError:
        pass  # 空仓库没有 HEAD
    sha = _git(repo, *commit_args).strip()
    ref = f"refs/ue5agent/checkpoints/{time.strftime('%Y%m%d-%H%M%S')}-{sha[:7]}"
    _git(repo, "update-ref", ref, sha)
    return {"ref": ref, "sha": sha}


def restore(repo: Path, ref: str) -> str:
    """把工作区与暂存区还原到指定 checkpoint（快照后新建的文件不会被删除）。"""
    _git(repo, "restore", "--source", ref, "--worktree", "--staged", "--", ".")
    return f"已还原到 {ref}"


def status(repo: Path) -> str:
    branch = _git(repo, "branch", "--show-current").strip() or "(detached)"
    changes = _git(repo, "status", "--short").strip() or "(干净)"
    return f"分支：{branch}\n{changes}"


def list_checkpoints(repo: Path) -> str:
    output = _git(
        repo,
        "for-each-ref",
        "refs/ue5agent/checkpoints",
        "--format=%(refname:short)  %(subject)",
    ).strip()
    return output or "(还没有 checkpoint)"
