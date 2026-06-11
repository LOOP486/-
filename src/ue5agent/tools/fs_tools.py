"""工程文件工具：读/写/改/列目录/检索，全部限定在工程根目录内。

写操作标 WRITE_PROJECT——经权限网关前置自动 checkpoint（K3）。
"""

from __future__ import annotations

import re
from pathlib import Path

from ue5agent.core.permissions import PermissionLevel
from ue5agent.tools.registry import ToolSpec

_SEARCH_SUFFIXES = {".h", ".cpp", ".cs", ".ini", ".uproject", ".uplugin", ".py", ".md"}
_SKIP_DIRS = {"Binaries", "Intermediate", "Saved", "DerivedDataCache", ".git", ".vs"}


def build_fs_tools(project_root: Path) -> list[ToolSpec]:
    root = project_root.resolve()

    def _safe(rel_path: str) -> Path:
        path = (root / rel_path).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"路径越界（必须在工程根 {root} 内）：{rel_path}")
        return path

    async def read_file(path: str, offset: int = 1, limit: int = 300) -> str:
        target = _safe(path)
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        window = lines[offset - 1 : offset - 1 + limit]
        body = "\n".join(f"{offset + i:>5} {line}" for i, line in enumerate(window))
        return body or "(空文件或越过末尾)"

    async def write_file(path: str, content: str) -> str:
        target = _safe(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"已写入 {path}（{len(content)} 字符）"

    async def replace_in_file(path: str, old: str, new: str) -> str:
        target = _safe(path)
        text = target.read_text(encoding="utf-8")
        count = text.count(old)
        if count == 0:
            return "[error] 未找到要替换的文本（old 必须与文件内容逐字一致）"
        if count > 1:
            return f"[error] old 出现 {count} 次，必须唯一——加长上下文再试"
        target.write_text(text.replace(old, new), encoding="utf-8")
        return f"已替换 {path} 中的一处文本"

    async def list_dir(path: str = ".") -> str:
        target = _safe(path)
        entries = []
        for item in sorted(target.iterdir()):
            if item.name in _SKIP_DIRS:
                continue
            entries.append(f"{item.name}/" if item.is_dir() else item.name)
        return "\n".join(entries) or "(空目录)"

    async def search_text(pattern: str, subdir: str = "Source") -> str:
        base = _safe(subdir)
        regex = re.compile(pattern)
        hits: list[str] = []
        for file in base.rglob("*"):
            if file.suffix not in _SEARCH_SUFFIXES or not file.is_file():
                continue
            if any(part in _SKIP_DIRS for part in file.parts):
                continue
            for number, line in enumerate(
                file.read_text(encoding="utf-8", errors="replace").splitlines(), 1
            ):
                if regex.search(line):
                    hits.append(f"{file.relative_to(root)}:{number}: {line.strip()[:160]}")
                    if len(hits) >= 50:
                        return "\n".join(hits) + "\n(已截断至 50 条)"
        return "\n".join(hits) or "(无匹配)"

    def _schema(**props: dict) -> dict:
        required = [k for k, v in props.items() if v.pop("_required", False)]
        return {"type": "object", "properties": props, "required": required}

    return [
        ToolSpec(
            "read_file",
            "读取工程内文件（带行号），path 相对工程根",
            _schema(
                path={"type": "string", "_required": True},
                offset={"type": "integer"},
                limit={"type": "integer"},
            ),
            PermissionLevel.READ,
            read_file,
        ),
        ToolSpec(
            "write_file",
            "新建或整体覆写工程内文件，path 相对工程根",
            _schema(
                path={"type": "string", "_required": True},
                content={"type": "string", "_required": True},
            ),
            PermissionLevel.WRITE_PROJECT,
            write_file,
        ),
        ToolSpec(
            "replace_in_file",
            "精确替换文件中唯一出现的一段文本",
            _schema(
                path={"type": "string", "_required": True},
                old={"type": "string", "_required": True},
                new={"type": "string", "_required": True},
            ),
            PermissionLevel.WRITE_PROJECT,
            replace_in_file,
        ),
        ToolSpec(
            "list_dir",
            "列出工程内目录内容",
            _schema(path={"type": "string"}),
            PermissionLevel.READ,
            list_dir,
        ),
        ToolSpec(
            "search_text",
            "在工程源码中按正则检索，返回 文件:行号:内容",
            _schema(
                pattern={"type": "string", "_required": True},
                subdir={"type": "string"},
            ),
            PermissionLevel.READ,
            search_text,
        ),
    ]
