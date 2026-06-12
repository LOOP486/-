"""在进程内重定向 stdout 跑 pytest，规避本机终端 writer 的 OSError 22。

用法：uv run python scripts/run_tests.py <pytest args...>
结果写 runs/pytest_out.txt。
"""

import sys
from pathlib import Path

import pytest

out_path = Path("runs/pytest_out.txt")
out_path.parent.mkdir(parents=True, exist_ok=True)
out = out_path.open("w", encoding="utf-8")
sys.stdout = out
sys.stderr = out

args = sys.argv[1:] or ["tests"]
code = pytest.main([*args, "-q", "--tb=short", "-p", "no:cacheprovider"])
out.write(f"\nEXIT={code}\n")
out.flush()
out.close()
