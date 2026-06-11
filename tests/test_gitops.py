"""repo_tools：临时 git 仓库上的 checkpoint / 还原 / 状态。"""

import pytest

from ue5agent.mcp_servers.repo_tools import gitops


@pytest.fixture
def repo(tmp_path):
    gitops._git(tmp_path, "init", "-q")
    (tmp_path / "a.txt").write_text("v1", encoding="utf-8")
    gitops._git(tmp_path, "add", "-A")
    gitops._git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def test_is_git_repo(repo, tmp_path_factory):
    assert gitops.is_git_repo(repo)
    # 必须在仓库外建目录：rev-parse 会向上查父目录
    plain = tmp_path_factory.mktemp("plain")
    assert not gitops.is_git_repo(plain)


def test_checkpoint_and_restore(repo):
    result = gitops.checkpoint(repo, "改动前")
    assert result["ref"].startswith("refs/ue5agent/checkpoints/")

    (repo / "a.txt").write_text("v2-改坏了", encoding="utf-8")
    assert "a.txt" in gitops.status(repo)

    gitops.restore(repo, result["ref"])
    assert (repo / "a.txt").read_text(encoding="utf-8") == "v1"


def test_checkpoint_includes_untracked(repo):
    (repo / "new.txt").write_text("untracked", encoding="utf-8")
    result = gitops.checkpoint(repo, "含未跟踪")
    (repo / "new.txt").write_text("changed", encoding="utf-8")
    gitops.restore(repo, result["ref"])
    assert (repo / "new.txt").read_text(encoding="utf-8") == "untracked"


def test_list_checkpoints(repo):
    assert "还没有" in gitops.list_checkpoints(repo)
    gitops.checkpoint(repo, "第一个")
    listing = gitops.list_checkpoints(repo)
    assert "ue5agent/checkpoints" in listing
    assert "第一个" in listing
