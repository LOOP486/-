"""B3 错误分类 taxonomy：标记往返 + 启发式兜底分类。"""

from ue5agent.core.errors import (
    ErrorCategory,
    classify,
    is_env_unready,
    mark_env_unready,
    mark_error,
)


def test_mark_error_roundtrip_all_categories():
    """每个类别经 mark_error 标记后都能被 classify 还原（含 env_unready 的历史标记）。"""
    for category in ErrorCategory:
        text = mark_error(category, "出错了")
        assert text.startswith("[error]")
        assert classify(text) is category


def test_env_unready_backward_compat():
    text = mark_env_unready("编辑器桥连接被拒")
    assert is_env_unready(text)
    assert "[env:unready]" in text
    assert classify(text) is ErrorCategory.ENV_UNREADY


def test_classify_heuristics_without_explicit_marker():
    # 桥通信失败的旧文本（无显式标记）→ bridge_down
    assert classify("[error] 编辑器桥通信失败：WinError 10054") is ErrorCategory.BRIDGE_DOWN
    # 权限/契约拒绝
    assert classify("[denied] 本步骤契约不允许调用该工具") is ErrorCategory.PERMISSION_DENIED
    assert classify("[denied] 超出权限上限") is ErrorCategory.PERMISSION_DENIED
    # 半截副作用
    assert classify("[error] 落地失败（编辑器开着吗？）") is ErrorCategory.PARTIAL_SIDE_EFFECT
    assert classify("[error] 清理未删净，仍残留 3 个") is ErrorCategory.PARTIAL_SIDE_EFFECT
    # 瞬时
    assert classify("[error] request timeout") is ErrorCategory.TRANSIENT
    assert classify("[error] 操作超时") is ErrorCategory.TRANSIENT
    # 参数错误
    assert classify("[error] layout_json 不是合法 JSON") is ErrorCategory.TOOL_ARG_ERROR


def test_classify_unknown_and_empty_default_to_transient():
    # 分不出的错误保守归为 transient（按可重试处理，绝不误判成快速终止）
    assert classify("[error] 某种没见过的故障") is ErrorCategory.TRANSIENT
    assert classify("") is ErrorCategory.TRANSIENT


def test_explicit_marker_beats_heuristic():
    """显式标记优先于启发式：文本里同时出现'超时'但标了 bridge_down，应判 bridge_down。"""
    text = mark_error(ErrorCategory.BRIDGE_DOWN, "连接超时 timeout")
    assert classify(text) is ErrorCategory.BRIDGE_DOWN
