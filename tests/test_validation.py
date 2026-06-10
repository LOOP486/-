"""容错层：坏 JSON 修复、schema 校验、近似工具名提示。"""

from typing import Any, ClassVar

import pytest

from ue5agent.core.permissions import PermissionGate, PermissionLevel
from ue5agent.tools.registry import ToolRegistry, ToolSpec
from ue5agent.tools.validation import parse_arguments, validate_arguments


class TestParseArguments:
    def test_clean_json(self):
        assert parse_arguments('{"a": 1}') == {"a": 1}

    def test_empty_string_means_no_args(self):
        assert parse_arguments("") == {}

    def test_markdown_fence_stripped(self):
        assert parse_arguments('```json\n{"a": 1}\n```') == {"a": 1}

    def test_fence_without_language_tag(self):
        assert parse_arguments('```\n{"a": 1}\n```') == {"a": 1}

    def test_trailing_comma_repaired(self):
        assert parse_arguments('{"a": 1, "b": [1, 2,],}') == {"a": 1, "b": [1, 2]}

    def test_fence_and_trailing_comma_combined(self):
        assert parse_arguments('```json\n{"a": 1,}\n```') == {"a": 1}

    def test_non_object_rejected(self):
        with pytest.raises(ValueError, match="JSON 对象"):
            parse_arguments("[1, 2]")

    def test_garbage_rejected(self):
        with pytest.raises(ValueError, match="不是合法 JSON"):
            parse_arguments("{definitely not json")


class TestValidateArguments:
    SCHEMA: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "mode": {"type": "string", "enum": ["fast", "full"]},
            "count": {"type": "integer"},
        },
        "required": ["path"],
    }

    def test_valid_passes(self):
        assert validate_arguments(self.SCHEMA, {"path": "/a", "mode": "fast"}) == []

    def test_missing_required(self):
        violations = validate_arguments(self.SCHEMA, {"mode": "fast"})
        assert len(violations) == 1
        assert "path" in violations[0]

    def test_wrong_type_and_bad_enum(self):
        violations = validate_arguments(self.SCHEMA, {"path": 1, "mode": "turbo"})
        assert len(violations) == 2

    def test_broken_schema_does_not_block(self):
        assert validate_arguments({"type": 42}, {"anything": True}) == []


class TestRegistryFaultTolerance:
    def make_registry(self) -> ToolRegistry:
        registry = ToolRegistry(PermissionGate())

        async def compile_target(target: str) -> str:
            return f"built {target}"

        registry.register(
            ToolSpec(
                name="ubt_compile",
                description="编译",
                parameters={
                    "type": "object",
                    "properties": {"target": {"type": "string"}},
                    "required": ["target"],
                },
                level=PermissionLevel.READ,
                handler=compile_target,
            )
        )
        return registry

    async def test_close_match_suggested(self):
        result = await self.make_registry().dispatch("ubt_compiler", "{}")
        assert "[error]" in result
        assert "ubt_compile" in result

    async def test_schema_violation_reported(self):
        result = await self.make_registry().dispatch("ubt_compile", '{"target": 5}')
        assert "[error]" in result
        assert "schema" in result

    async def test_fenced_arguments_accepted(self):
        result = await self.make_registry().dispatch(
            "ubt_compile", '```json\n{"target": "MyGameEditor"}\n```'
        )
        assert result == "built MyGameEditor"
