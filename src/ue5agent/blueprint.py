"""蓝图只读导出的 Python 转换层（C2）：把插件返回的 JSON 压成 token 高效的可读文本。

分工（ADR-0004 式解耦）：插件侧（C++）负责遍历 UBlueprint / EdGraph 产出结构化 JSON，
本模块负责把 JSON 压成"默认视图"文本（约 JSON 的 1/5 token），歧义时再用 bp_graph 下钻。

当前插件能力（2026-06-12 读插件源码确认）：
- read_blueprint_content 给出完整结构（父类/组件/变量/函数/事件图节点/接口）→ format_overview 忠实。
- analyze_blueprint_graph 输出 graph_data.{nodes[{name,class,title,pins[{name,type,direction,
  connections}]}], connections[{from_node,from_pin,to_node,to_pin}]}，按 `graph_name`（默认
  EventGraph）选图（事件图或同名函数图）→ format_pseudocode 据 connections 重建 exec 控制流；
  瘦桥需传 graph_name=函数名 才能取函数图（早期误传 function_name 被忽略）。
- 无引用查找命令 → bp_find_usages 待插件 C++（AssetRegistry）。
"""

from __future__ import annotations

from typing import Any

# 节点 class → 人类可读类别。前缀匹配，未知归"其它"。
_NODE_CATEGORY = {
    "EdGraphNode_Comment": "注释",
    "K2Node_Event": "事件",
    "K2Node_CustomEvent": "事件",
    "K2Node_InputAction": "输入",
    "K2Node_EnhancedInputAction": "输入",
    "K2Node_CallFunction": "调用",
    "K2Node_VariableGet": "读变量",
    "K2Node_VariableSet": "写变量",
    "K2Node_IfThenElse": "分支",
    "K2Node_ExecutionSequence": "顺序",
    "K2Node_MacroInstance": "宏",
    "K2Node_Knot": "连线",
}


def _unwrap(raw: dict[str, Any]) -> dict[str, Any]:
    """容错取出业务结果：{status,result:{...}} → {...}，或本身就是结果。"""
    if not isinstance(raw, dict):
        return {}
    inner = raw.get("result")
    return inner if isinstance(inner, dict) else raw


def _categorize(node_class: str) -> str:
    for prefix, label in _NODE_CATEGORY.items():
        if node_class.startswith(prefix):
            return label
    return "其它"


def _clean_title(title: object) -> str:
    """节点标题压成单行（蓝图标题常带换行的"目标是…"副标题）。"""
    return " ".join(str(title).split())


def format_overview(raw: dict[str, Any]) -> str:
    """read_blueprint_content 的结构 → 紧凑可读概览（父类/组件/接口/变量/函数/事件图分类）。"""
    data = _unwrap(raw)
    name = data.get("blueprint_name") or data.get("blueprint_path", "?")
    lines = [f"蓝图 {name}（父类 {data.get('parent_class', '?')}）"]

    components = data.get("components") or []
    if components:
        lines.append(
            "组件："
            + "、".join(
                f"{c.get('name')}({c.get('class')})" for c in components if isinstance(c, dict)
            )
        )
    interfaces = data.get("interfaces") or []
    if interfaces:
        lines.append("接口：" + "、".join(str(i.get("name", i)) for i in interfaces))

    variables = data.get("variables") or []
    if variables:
        lines.append(
            "变量："
            + "、".join(
                f"{v.get('name')}:{v.get('type', '?')}" if isinstance(v, dict) else str(v)
                for v in variables
            )
        )
    else:
        lines.append("变量：（无）")

    functions = data.get("functions") or []
    if functions:
        lines.append(
            "函数："
            + "、".join(
                f"{f.get('name')}({f.get('node_count', '?')}节点)"
                for f in functions
                if isinstance(f, dict)
            )
        )

    event_graph = data.get("event_graph") or {}
    nodes = event_graph.get("nodes") or []
    if nodes:
        lines.append(
            f"事件图 {event_graph.get('name', 'EventGraph')}"
            f"（{event_graph.get('node_count', len(nodes))} 节点）：{_summarize_nodes(nodes)}"
        )
    return "\n".join(lines)


def _summarize_nodes(nodes: list[dict[str, Any]]) -> str:
    """节点列表 → 按类别计数 + 列出事件/输入入口（最能说明"蓝图响应什么"）。"""
    counts: dict[str, int] = {}
    entries: list[str] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        label = _categorize(str(node.get("class", "")))
        counts[label] = counts.get(label, 0) + 1
        if label in ("事件", "输入"):
            entries.append(_clean_title(node.get("title", node.get("name", "?"))))
    parts = ["、".join(f"{label}×{n}" for label, n in sorted(counts.items()))]
    if entries:
        parts.append("入口：" + "；".join(entries))
    return " ｜ ".join(parts)


def format_pseudocode(raw: dict[str, Any]) -> str:
    """analyze_blueprint_graph 的节点图 → 伪代码文本（C2）。

    插件输出 graph_data.connections=[{from_node,from_pin,to_node,to_pin}]（双向记录）。
    据此筛出 exec 流向（from_pin 为 exec 输出）重建控制流：从事件/输入入口沿 exec 边走，
    缩进列出执行顺序。无 connections 时退回"结构化摘要"（按类别归并）。
    """
    data = _unwrap(raw)
    graph_data = data.get("graph_data")
    graph = graph_data if isinstance(graph_data, dict) else data
    nodes = [n for n in (graph.get("nodes") or []) if isinstance(n, dict)]
    connections = [c for c in (graph.get("connections") or []) if isinstance(c, dict)]
    name = graph.get("graph_name", "图")
    if not nodes:
        return f"// {name}：无节点"

    comments = [
        _clean_title(n.get("title", ""))
        for n in nodes
        if _categorize(str(n.get("class", ""))) == "注释"
    ]
    exec_edges = _exec_edges(nodes, connections)
    if exec_edges:
        return _control_flow(name, nodes, exec_edges, comments)
    return _structural_digest(name, nodes, comments)


def _exec_edges(
    nodes: list[dict[str, Any]], connections: list[dict[str, Any]]
) -> dict[str, list[str]]:
    """从 connections 筛出 exec 流向边：from_node → [to_node]（仅 from_pin 为 exec 输出）。"""
    pin_type: dict[tuple[str, str, str], str] = {}
    for node in nodes:
        for pin in node.get("pins") or []:
            if isinstance(pin, dict):
                key = (str(node.get("name")), str(pin.get("name")), str(pin.get("direction")))
                pin_type[key] = str(pin.get("type"))
    edges: dict[str, list[str]] = {}
    for conn in connections:
        from_node = str(conn.get("from_node"))
        from_pin = str(conn.get("from_pin"))
        to_node = str(conn.get("to_node"))
        if pin_type.get((from_node, from_pin, "Output")) == "exec":
            edges.setdefault(from_node, []).append(to_node)
    return edges


def _control_flow(
    name: str,
    nodes: list[dict[str, Any]],
    exec_edges: dict[str, list[str]],
    comments: list[str],
) -> str:
    title_by_name = {
        str(n.get("name")): _clean_title(n.get("title", n.get("name", "?"))) for n in nodes
    }
    # exec 入口：被 exec 边指向过的节点不是入口；有 exec 出边但无入边的即起点
    targets = {to for tos in exec_edges.values() for to in tos}
    entries = [n for n in exec_edges if n not in targets]
    lines = [f"// {name}（控制流伪代码，{len(nodes)} 节点）"]
    if comments:
        lines.append("// 分区：" + " / ".join(comments))

    def walk(node: str, depth: int, visited: set[str]) -> None:
        indent = "  " * depth
        lines.append(f"{indent}→ {title_by_name.get(node, node)}")
        if node in visited:  # 防环：已展开过则只标一行
            lines.append(f"{indent}  …（已出现，略）")
            return
        visited.add(node)
        for nxt in exec_edges.get(node, []):
            walk(nxt, depth + 1, visited)

    for entry in entries:
        lines.append(f"▸ 入口 {title_by_name.get(entry, entry)}")
        for nxt in exec_edges.get(entry, []):
            walk(nxt, 1, set())
    return "\n".join(lines)


def format_usages(raw: dict[str, Any]) -> str:
    """find_blueprint_references 的结果 → 紧凑可读列表（谁引用了这个蓝图）。"""
    data = _unwrap(raw)
    refs = [str(r) for r in (data.get("referencers") or [])]
    target = data.get("package") or data.get("blueprint_path", "?")
    if not refs:
        return f"{target}：无其它资产引用它（未被使用，或仅被关卡/代码间接使用）"
    lines = [f"{target} 被 {len(refs)} 个资产引用："]
    lines += [f"- {r}" for r in refs]
    return "\n".join(lines)


def _structural_digest(name: str, nodes: list[dict[str, Any]], comments: list[str]) -> str:
    """无连接信息时的退路：按注释分区 + 类别归并。"""
    by_category: dict[str, list[str]] = {}
    for node in nodes:
        label = _categorize(str(node.get("class", "")))
        if label == "注释":
            continue
        by_category.setdefault(label, []).append(
            _clean_title(node.get("title", node.get("name", "?")))
        )
    lines = [f"// {name}（结构化摘要，{len(nodes)} 节点；无连接信息，精确连线请用 bp_graph）"]
    if comments:
        lines.append("分区注释：" + " / ".join(comments))
    for label in ("事件", "输入", "分支", "调用", "读变量", "写变量", "顺序", "宏", "其它"):
        items = by_category.get(label)
        if items:
            lines.append(f"{label}（{len(items)}）：" + "；".join(items))
    return "\n".join(lines)
