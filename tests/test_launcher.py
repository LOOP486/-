"""ue_lifecycle 启动器：probe/spawn/clock 全替身，不碰真进程与网络。"""

from itertools import count

import pytest

from ue5agent.mcp_servers.ue_lifecycle.launcher import LaunchError, editor_exe, launch_editor


def fake_install(tmp_path):
    """造出假引擎可执行文件与假工程文件。"""
    engine = tmp_path / "UE"
    exe = editor_exe(engine)
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    project = tmp_path / "demo.uproject"
    project.write_text("{}")
    return engine, project


def test_already_running_is_idempotent(tmp_path):
    engine, project = fake_install(tmp_path)
    spawned: list[list[str]] = []
    result = launch_editor(engine, project, probe=lambda: True, spawn=spawned.append)
    assert "已在运行" in result
    assert spawned == []


def test_launch_then_poll_until_ready(tmp_path):
    engine, project = fake_install(tmp_path)
    spawned: list[list[str]] = []
    probes = iter([False, False, True])  # 启动前离线，第二次轮询就绪
    result = launch_editor(
        engine,
        project,
        timeout=60,
        poll_interval=5,
        probe=lambda: next(probes),
        spawn=spawned.append,
        sleep=lambda _s: None,
        clock=lambda: 0.0,
    )
    assert "就绪" in result
    assert spawned == [[str(editor_exe(engine)), str(project)]]


def test_missing_exe_raises(tmp_path):
    project = tmp_path / "demo.uproject"
    project.write_text("{}")
    with pytest.raises(LaunchError, match="可执行文件"):
        launch_editor(tmp_path / "nope", project, probe=lambda: False, spawn=lambda _c: None)


def test_missing_uproject_raises(tmp_path):
    engine, _ = fake_install(tmp_path)
    with pytest.raises(LaunchError, match="工程文件"):
        launch_editor(
            engine, tmp_path / "none.uproject", probe=lambda: False, spawn=lambda _c: None
        )


def test_timeout_raises_with_no_relaunch_hint(tmp_path):
    engine, project = fake_install(tmp_path)
    ticks = count(0, 10)  # 假时钟每读一次前进 10s
    with pytest.raises(LaunchError, match="未就绪"):
        launch_editor(
            engine,
            project,
            timeout=30,
            probe=lambda: False,
            spawn=lambda _c: None,
            sleep=lambda _s: None,
            clock=lambda: float(next(ticks)),
        )
