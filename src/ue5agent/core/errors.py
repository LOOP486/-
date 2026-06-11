"""跨进程错误分类。

MCP server 与 kernel 之间只有文本通道，错误类别靠稳定标记传递：
server 侧用 mark_env_unready 生成带标记的错误文本，kernel 侧用
is_env_unready 识别后快速失败（环境没就绪时重试只会空耗预算）。
"""

from __future__ import annotations

ENV_UNREADY_MARKER = "[env:unready]"


def mark_env_unready(message: str) -> str:
    """生成环境未就绪的错误文本（带 [error] 前缀，工具管线按失败处理）。"""
    return f"[error]{ENV_UNREADY_MARKER} {message}"


def is_env_unready(text: str) -> bool:
    """判断工具回传文本是否标记了环境未就绪。"""
    return ENV_UNREADY_MARKER in text
