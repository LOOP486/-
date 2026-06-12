"""C2 蓝图导出转换层：JSON → 紧凑文本（夹具取自真机 BP_ThirdPersonCharacter）。"""

from ue5agent.blueprint import format_overview, format_pseudocode, format_usages

_READ_SAMPLE = {
    "status": "success",
    "result": {
        "blueprint_path": "/Game/ThirdPerson/Blueprints/BP_ThirdPersonCharacter",
        "blueprint_name": "BP_ThirdPersonCharacter",
        "parent_class": "Character",
        "variables": [],
        "functions": [
            {"name": "Move", "graph_type": "Function", "node_count": 13},
            {"name": "Aim", "graph_type": "Function", "node_count": 7},
        ],
        "event_graph": {
            "name": "EventGraph",
            "node_count": 5,
            "nodes": [
                {
                    "name": "n1",
                    "class": "K2Node_EnhancedInputAction",
                    "title": "EnhancedInputAction IA_Jump",
                },
                {
                    "name": "n2",
                    "class": "K2Node_Event",
                    "title": "事件Touch Jump Start\n从 BPI Touch Interface",
                },
                {"name": "n3", "class": "K2Node_CallFunction", "title": "跳跃\n目标是角色"},
                {"name": "n4", "class": "EdGraphNode_Comment", "title": "Jump Input"},
                {"name": "n5", "class": "K2Node_VariableSet", "title": "设置 Speed"},
            ],
        },
        "components": [
            {"name": "CameraBoom", "class": "SpringArmComponent", "is_root": False},
            {"name": "FollowCamera", "class": "CameraComponent", "is_root": False},
        ],
        "interfaces": [{"name": "BPI_TouchInterface_C"}],
        "success": True,
    },
}

_GRAPH_SAMPLE = {
    "status": "success",
    "result": {
        "blueprint_path": "/Game/X",
        "graph_data": {
            "graph_name": "EventGraph",
            "graph_type": "EdGraph",
            "nodes": [
                {
                    "name": "c1",
                    "class": "EdGraphNode_Comment",
                    "title": "Movement Input",
                    "pins": [],
                },
                {
                    "name": "e1",
                    "class": "K2Node_EnhancedInputAction",
                    "title": "EnhancedInputAction IA_Move",
                    "pins": [
                        {
                            "name": "Triggered",
                            "type": "exec",
                            "direction": "Output",
                            "connections": 1,
                        }
                    ],
                },
                {
                    "name": "f1",
                    "class": "K2Node_CallFunction",
                    "title": "Move\n目标是角色",
                    "pins": [
                        {
                            "name": "execute",
                            "type": "exec",
                            "direction": "Input",
                            "connections": 1,
                        },
                        {"name": "then", "type": "exec", "direction": "Output", "connections": 1},
                    ],
                },
                {
                    "name": "f2",
                    "class": "K2Node_CallFunction",
                    "title": "AddMovementInput",
                    "pins": [
                        {"name": "execute", "type": "exec", "direction": "Input", "connections": 1}
                    ],
                },
            ],
            "connections": [
                # 插件双向记录；exec 正向：e1.Triggered→f1.execute, f1.then→f2.execute
                {"from_node": "e1", "from_pin": "Triggered", "to_node": "f1", "to_pin": "execute"},
                {"from_node": "f1", "from_pin": "execute", "to_node": "e1", "to_pin": "Triggered"},
                {"from_node": "f1", "from_pin": "then", "to_node": "f2", "to_pin": "execute"},
                {"from_node": "f2", "from_pin": "execute", "to_node": "f1", "to_pin": "then"},
            ],
        },
        "success": True,
    },
}

# 无连接信息的图（退回结构化摘要）
_GRAPH_NO_CONN = {
    "result": {
        "graph_data": {
            "graph_name": "EventGraph",
            "nodes": [
                {"name": "c1", "class": "EdGraphNode_Comment", "title": "Jump Input", "pins": []},
                {
                    "name": "e1",
                    "class": "K2Node_EnhancedInputAction",
                    "title": "IA_Jump",
                    "pins": [],
                },
                {"name": "f1", "class": "K2Node_CallFunction", "title": "跳跃", "pins": []},
            ],
            "connections": [],
        }
    }
}


class TestFormatOverview:
    def test_includes_core_facts(self):
        out = format_overview(_READ_SAMPLE)
        assert "BP_ThirdPersonCharacter" in out
        assert "父类 Character" in out
        assert "CameraBoom(SpringArmComponent)" in out
        assert "BPI_TouchInterface_C" in out
        assert "Move(13节点)" in out
        assert "变量：（无）" in out

    def test_event_graph_categorized_with_entries(self):
        out = format_overview(_READ_SAMPLE)
        assert "事件×1" in out
        assert "输入×1" in out
        assert "调用×1" in out
        # 事件/输入入口被列出（最能说明蓝图响应什么）
        assert "IA_Jump" in out

    def test_much_smaller_than_raw_json(self):
        import json

        raw_len = len(json.dumps(_READ_SAMPLE, ensure_ascii=False))
        assert len(format_overview(_READ_SAMPLE)) < raw_len  # 默认视图更紧凑

    def test_accepts_unwrapped_result(self):
        out = format_overview(_READ_SAMPLE["result"])
        assert "BP_ThirdPersonCharacter" in out

    def test_defensive_on_empty(self):
        assert "?" in format_overview({})


class TestFormatPseudocode:
    def test_control_flow_from_connections(self):
        out = format_pseudocode(_GRAPH_SAMPLE)
        assert "控制流伪代码" in out
        # 入口为事件/输入，沿 exec 边重建执行顺序：IA_Move → Move → AddMovementInput
        assert "入口" in out and "IA_Move" in out
        i_move = out.index("Move 目标是角色")
        i_add = out.index("AddMovementInput")
        assert i_move < i_add  # 执行顺序正确
        assert "Movement Input" in out  # 注释分区

    def test_falls_back_to_digest_without_connections(self):
        out = format_pseudocode(_GRAPH_NO_CONN)
        assert "结构化摘要" in out
        assert "输入（1）" in out
        assert "调用（1）" in out
        assert "Jump Input" in out  # 注释分区

    def test_empty_graph(self):
        assert "无节点" in format_pseudocode({"result": {"graph_data": {"nodes": []}}})


class TestFormatUsages:
    def test_lists_referencers(self):
        raw = {
            "result": {
                "package": "/Game/ThirdPerson/Blueprints/BP_ThirdPersonCharacter",
                "referencers": ["/Game/Maps/Lvl_ThirdPerson", "/Game/ThirdPerson/BP_GameMode"],
                "count": 2,
            }
        }
        out = format_usages(raw)
        assert "被 2 个资产引用" in out
        assert "/Game/Maps/Lvl_ThirdPerson" in out
        assert "BP_GameMode" in out

    def test_no_referencers(self):
        raw = {"result": {"package": "/Game/BP_Unused", "referencers": [], "count": 0}}
        out = format_usages(raw)
        assert "无其它资产引用" in out
