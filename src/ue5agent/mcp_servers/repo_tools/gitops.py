"""repo_tools 的纯 git 操作（可对临时仓库单测；server.py 只做接线）。

checkpoint 用 write-tree/commit-tree 实现：不动 HEAD、不动工作区，
快照存进 refs/ue5agent/checkpoints/，restore 时按 ref 还原工作区与暂存区。
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

# 仓库可能没有配置提交者身份（checkpoint 不应因此失败）
_IDENTITY = ["-c", "user.name=ue5agent", "-c", "user.email=ue5agent@local"]


def _git(repo: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", *_IDENTITY, "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if process.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败：{process.stderr.strip()}")
    return process.stdout


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
