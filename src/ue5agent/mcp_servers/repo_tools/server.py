"""repo_tools MCP server：git checkpoint / 状态 / 还原。

启动：uv run python -m ue5agent.mcp_servers.repo_tools（stdio）
缺省仓库取 UE_UPROJECT 所在目录；也可显式传 repo_path。
注意：repo_restore 应在 agent.yaml 以 dangerous 级别单独挂载（默认拒绝）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from ue5agent.mcp_servers.repo_tools import gitops

mcp = FastMCP("repo-tools")


def _repo(repo_path: str | None) -> Path | None:
    if repo_path:
        return Path(repo_path)
    uproject = os.environ.get("UE_UPROJECT")
    return Path(uproject).parent if uproject else None


@mcp.tool()
def repo_checkpoint(label: str = "checkpoint", repo_path: str | None = None) -> str:
    """对仓库做全量快照（含未跟踪文件），返回可用于还原的 ref。"""
    repo = _repo(repo_path)
    if repo is None or not gitops.is_git_repo(repo):
        return f"[error] {repo} 不是 git 仓库（先 git init 并提交一次）"
    result = gitops.checkpoint(repo, label)
    facts = {"kind": "repo_checkpoint", "ok": True, "ref": result["ref"]}
    return (
        f"checkpoint 完成：{result['ref']}（{result['sha'][:10]}）"
        f"\n[facts] {json.dumps(facts, ensure_ascii=False)}"
    )


@mcp.tool()
def repo_status(repo_path: str | None = None) -> str:
    """当前分支与未提交改动。"""
    repo = _repo(repo_path)
    if repo is None or not gitops.is_git_repo(repo):
        return f"[error] {repo} 不是 git 仓库"
    return gitops.status(repo)


@mcp.tool()
def repo_list_checkpoints(repo_path: str | None = None) -> str:
    """列出全部 ue5agent checkpoint。"""
    repo = _repo(repo_path)
    if repo is None or not gitops.is_git_repo(repo):
        return f"[error] {repo} 不是 git 仓库"
    return gitops.list_checkpoints(repo)


@mcp.tool()
def repo_restore(ref: str, repo_path: str | None = None) -> str:
    """把工作区还原到指定 checkpoint（危险：覆盖未提交改动）。"""
    repo = _repo(repo_path)
    if repo is None or not gitops.is_git_repo(repo):
        return f"[error] {repo} 不是 git 仓库"
    return gitops.restore(repo, ref)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
