"""跨进程错误分类（B3：错误 taxonomy 与恢复策略）。

MCP server 与 kernel 之间只有文本通道，错误类别靠稳定标记传递：
server 侧用 mark_error/mark_env_unready 生成带 `[err:<类别>]` 标记的错误文本，
kernel 侧用 classify() 还原类别，再按恢复策略表差异化处理（环境没就绪时
重试只会空耗预算、编辑器掉线后空转重试是已知踩坑）。

向后兼容：env_unready 沿用历史标记 `[env:unready]`（旧代码/旧 trace 仍可识别）。
未显式标记的错误用启发式兜底分类，分不出则归为 transient（默认按可重试处理）。
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCategory(StrEnum):
    """工具/执行错误的类别。值用于 `[err:<value>]` 文本标记的跨进程传递。"""

    ENV_UNREADY = "env_unready"
    """环境未就绪：编辑器桥从未开启/连接被拒。重试无意义，应快速终止并给指引。"""
    BRIDGE_DOWN = "bridge_down"
    """编辑器桥中途掉线/瞬断（编辑器曾在线但连接断开）。应先探活再决定重连或终止。"""
    UBT_COMPILE_ERROR = "ubt_compile_error"
    """编译报错：带结构化错误进修复循环（属正常迭代，不是恢复失败）。"""
    PERMISSION_DENIED = "permission_denied"
    """权限/契约拒绝：不自动绕过，回传文本由模型改用合规工具。"""
    BUDGET_EXHAUSTED = "budget_exhausted"
    """预算耗尽：保存会话、产出 partial report（runner 内部状态，非工具文本）。"""
    EVIDENCE_MISSING = "evidence_missing"
    """证据不足：补采证据（验证工具）后重试，而非直接判失败。"""
    PARTIAL_SIDE_EFFECT = "partial_side_effect"
    """部分副作用：写操作半途失败（如 spawn 落了一半）。重试前宜先回滚清理。"""
    TOOL_ARG_ERROR = "tool_arg_error"
    """工具参数错误（坏 JSON/schema 不符）：修正参数后重试是安全的。"""
    TRANSIENT = "transient"
    """瞬时错误（偶发超时/瞬断）：直接重试。也是未知错误的保守默认。"""


ENV_UNREADY_MARKER = "[env:unready]"
"""历史标记，等价于 mark_error(ErrorCategory.ENV_UNREADY, ...)，保留向后兼容。"""


def _marker(category: ErrorCategory) -> str:
    return f"[err:{category.value}]"


def mark_error(category: ErrorCategory, message: str) -> str:
    """生成带类别标记的错误文本（带 [error] 前缀，工具管线按失败处理）。

    env_unready 沿用历史标记 [env:unready]（兼容旧识别逻辑）；其余用 [err:<类别>]。
    """
    if category is ErrorCategory.ENV_UNREADY:
        return f"[error]{ENV_UNREADY_MARKER} {message}"
    return f"[error]{_marker(category)} {message}"


def mark_env_unready(message: str) -> str:
    """生成环境未就绪的错误文本（向后兼容入口）。"""
    return mark_error(ErrorCategory.ENV_UNREADY, message)


def is_env_unready(text: str) -> bool:
    """判断工具回传文本是否标记了环境未就绪（向后兼容入口）。"""
    return ENV_UNREADY_MARKER in text


# 未显式标记时的启发式线索（小写匹配）。仅用于兜底，显式标记永远优先。
_BRIDGE_HINTS = (
    "编辑器桥通信失败",
    "桥连接关闭",
    "10054",  # WSAECONNRESET：连接被对方重置
    "10053",  # WSAECONNABORTED
    "connection reset",
    "connection aborted",
)
_TRANSIENT_HINTS = ("timeout", "超时", "timed out", "temporarily")
_PARTIAL_HINTS = ("落地失败", "spawn", "清理未删净", "未删净")
_ARG_HINTS = ("不是合法 json", "不是合法json", "json", "schema", "参数")


def classify(text: str) -> ErrorCategory:
    """把工具回传文本分类到 ErrorCategory。

    显式标记优先（[env:unready] / [err:<类别>]）；无标记时用启发式线索兜底；
    实在分不出则归为 TRANSIENT（保守按可重试处理，不会误判成"快速终止"）。
    """
    if not text:
        return ErrorCategory.TRANSIENT
    if ENV_UNREADY_MARKER in text:
        return ErrorCategory.ENV_UNREADY
    for category in ErrorCategory:
        if _marker(category) in text:
            return category
    if text.startswith("[denied]") or "本步骤契约不允许" in text or "权限上限" in text:
        return ErrorCategory.PERMISSION_DENIED
    low = text.lower()
    if any(hint in low for hint in _BRIDGE_HINTS):
        return ErrorCategory.BRIDGE_DOWN
    if any(hint in low for hint in _PARTIAL_HINTS):
        return ErrorCategory.PARTIAL_SIDE_EFFECT
    if any(hint in low for hint in _TRANSIENT_HINTS):
        return ErrorCategory.TRANSIENT
    if any(hint in low for hint in _ARG_HINTS):
        return ErrorCategory.TOOL_ARG_ERROR
    return ErrorCategory.TRANSIENT
