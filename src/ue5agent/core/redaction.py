"""敏感信息掩码（D1.2）：落盘 trace/report/progress 前掩掉 .env 中的 key 值。

威胁模型按单机右尺寸：防止 API key 因日志/报告被无意分享（贴给他人、传 GitHub
issue）而泄露。不做企业级 secret manager。掩码在 RunWriter 落盘单点统一施加。
"""

from __future__ import annotations

import os
from collections.abc import Iterable

PLACEHOLDER = "***REDACTED***"
_MIN_SECRET_LEN = 6
"""过短的值不掩码——避免把恰好等于 key 的普通短词（如 'abc'）误伤成 REDACTED。"""


def collect_secret_values(env_var_names: Iterable[str]) -> set[str]:
    """从环境变量取出需掩码的真实 key 值（按 provider.api_key_env 提供的变量名）。"""
    secrets: set[str] = set()
    for name in env_var_names:
        value = os.environ.get(name)
        if value and len(value) >= _MIN_SECRET_LEN:
            secrets.add(value)
    return secrets


def redact(text: str, secrets: Iterable[str], *, placeholder: str = PLACEHOLDER) -> str:
    """把 text 中出现的 secret 值替换为占位符。长值优先替换，避免子串重叠残留。"""
    if not text:
        return text
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        if secret in text:
            text = text.replace(secret, placeholder)
    return text
