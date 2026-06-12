"""运行锁（D2.1）：同一 UE 工程同时只允许一个 runner。

插件 TCP 桥本就单连接，两个 runner 并发会互相踩场景/编译。锁的目的是把"莫名拒连/
场景错乱"换成一条可读的错误。锁文件记 PID + 时间戳；进程已死的陈旧锁自动回收
（崩溃/强杀后不至于永久卡死）。单机右尺寸，不做分布式锁。
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path


class RunLockError(RuntimeError):
    """已有活跃 runner 持锁。"""


def _pid_alive(pid: int) -> bool:
    """跨平台判断进程是否存活（best-effort）。"""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(process_query_limited, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 存在但无权限 = 活着
    return True


@contextlib.contextmanager
def run_lock(
    lock_path: Path,
    *,
    is_alive: Callable[[int], bool] = _pid_alive,
) -> Iterator[None]:
    """获取运行锁；已有活跃 runner 持锁则抛 RunLockError。陈旧锁（持有进程已死）自动回收。"""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.exists():
        holder = _read_pid(lock_path)
        if holder is not None and holder != os.getpid() and is_alive(holder):
            raise RunLockError(
                f"已有 runner 在运行（PID {holder}，锁 {lock_path}）。"
                "同一工程只能跑一个 runner；请等其结束或确认无误后删除该锁文件。"
            )
        # 陈旧锁（进程已死或就是自己）：回收
    lock_path.write_text(
        json.dumps({"pid": os.getpid(), "ts": round(time.time(), 3)}), encoding="utf-8"
    )
    try:
        yield
    finally:
        # 只删自己持有的锁，避免误删他人回收后重建的锁
        if _read_pid(lock_path) == os.getpid():
            lock_path.unlink(missing_ok=True)


def _read_pid(lock_path: Path) -> int | None:
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
        return int(data.get("pid"))
    except (OSError, ValueError, TypeError):
        return None
