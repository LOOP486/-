"""工具调用容错：坏 JSON 修复与参数 schema 校验。

弱模型的 function calling 会产出 markdown 包裹、带尾逗号或截断的 JSON，
或漏必填参数。可机械修复的在这里修掉，修不掉的转成结构化错误回传模型重试。
"""

from __future__ import annotations

import json
import re
from typing import Any

from jsonschema import Draft202012Validator

_FENCE = re.compile(r"^```(?:json)?\s*(?P<body>.*?)\s*```\s*$", re.DOTALL)
_TRAILING_COMMA = re.compile(r",\s*(?=[}\]])")


def parse_arguments(raw: str) -> dict[str, Any]:
    """解析模型产出的参数 JSON，按常见损坏形态逐级尝试修复。

    Raises:
        ValueError: 修复后仍不可解析，或解析结果不是对象。
    """
    candidates = [raw.strip()]
    fence = _FENCE.match(candidates[0])
    if fence:
        candidates.append(fence.group("body"))
    candidates.extend(_TRAILING_COMMA.sub("", text) for text in list(candidates))
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            value = json.loads(candidate or "{}")
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if isinstance(value, dict):
            return value
        raise ValueError(f"参数应为 JSON 对象，得到 {type(value).__name__}")
    raise ValueError(f"参数不是合法 JSON（已尝试修复代码块包裹/尾逗号）：{last_error}")


def validate_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> list[str]:
    """按工具的 JSON Schema 校验参数，返回违例描述列表（空列表 = 通过）。

    schema 本身不合规时不拦调用（宁可放过交给工具自己报错）。
    """
    try:
        validator = Draft202012Validator(schema)
        errors = list(validator.iter_errors(arguments))
    except Exception:
        return []
    violations = []
    for error in errors:
        path = ".".join(str(part) for part in error.absolute_path) or "(根)"
        violations.append(f"{path}: {error.message}")
    return sorted(violations)
