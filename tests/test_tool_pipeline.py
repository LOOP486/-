"""K2：参数规范化、结果信封、失败签名熔断（A3 起含 facts 证据信封，B2 起含副作用治理）。"""

from ue5agent.agent.tool_pipeline import (
    FailureTracker,
    ToolPipeline,
    extract_facts,
    normalize_arguments,
)
from ue5agent.core.permissions import PermissionGate, PermissionLevel
from ue5agent.tools.effects import ToolEffects, default_effects, effects_for
from ue5agent.tools.registry import ToolRegistry, ToolSpec

SCHEMA = {
    "type": "object",
    "properties": {
        "count": {"type": "integer"},
        "ratio": {"type": "number"},
        "enabled": {"type": "boolean"},
        "file_path": {"type": "string"},
        "text": {"type": "string"},
    },
}


class TestNormalize:
    def test_numeric_and_bool_coercion(self):
        result = normalize_arguments(SCHEMA, {"count": " 42 ", "ratio": "2.5", "enabled": "True"})
        assert result == {"count": 42, "ratio": 2.5, "enabled": True}

    def test_path_separators_unified(self):
        result = normalize_arguments(SCHEMA, {"file_path": "C:\\Game\\Source\\Foo.cpp"})
        assert result["file_path"] == "C:/Game/Source/Foo.cpp"

    def test_non_path_strings_untouched(self):
        result = normalize_arguments(SCHEMA, {"text": "a\\b 保持原样"})
        assert result["text"] == "a\\b 保持原样"

    def test_unconvertible_left_for_schema_check(self):
        assert normalize_arguments(SCHEMA, {"count": "many"})["count"] == "many"


def make_pipeline(handler_results: list[str]) -> tuple[ToolPipeline, ToolRegistry]:
    registry = ToolRegistry(PermissionGate())
    results = list(handler_results)

    async def flaky(text: str = "") -> str:
        return results.pop(0)

    registry.register(
        ToolSpec(
            name="flaky",
            description="按脚本返回",
            parameters={"type": "object", "properties": {"text": {"type": "string"}}},
            level=PermissionLevel.READ,
            handler=flaky,
        )
    )
    return ToolPipeline(registry, PermissionGate(), tracker=FailureTracker(threshold=3)), registry


class TestFailureEscalation:
    async def test_consecutive_same_error_escalates(self):
        pipeline, _ = make_pipeline(["[error] boom", "[error] boom", "[error] boom"])
        await pipeline.run("flaky", "{}")
        second = await pipeline.run("flaky", "{}")
        assert second.consecutive == 2
        assert "[提示]" not in second.text
        third = await pipeline.run("flaky", "{}")
        assert third.consecutive == 3
        assert "连续 3 次" in third.text
        assert third.error_kind == "tool_error"

    async def test_success_resets_counter(self):
        pipeline, _ = make_pipeline(["[error] boom", "ok", "[error] boom"])
        await pipeline.run("flaky", "{}")
        ok = await pipeline.run("flaky", "{}")
        assert ok.ok and ok.consecutive == 0
        again = await pipeline.run("flaky", "{}")
        assert again.consecutive == 1

    async def test_unknown_tool_counted(self):
        pipeline, _ = make_pipeline([])
        for _ in range(3):
            outcome = await pipeline.run("ghost", "{}")
        assert outcome.error_kind == "unknown_tool"
        assert "连续 3 次" in outcome.text


class TestToolEffects:
    """B2：副作用声明的解析与默认推导。"""

    def test_default_effects_follow_permission_level(self):
        assert default_effects(PermissionLevel.WRITE_PROJECT).requires_checkpoint
        assert not default_effects(PermissionLevel.READ).requires_checkpoint
        assert default_effects(PermissionLevel.WRITE_SAFE).idempotent

    def test_known_table_lookup_and_fallback(self):
        wb = effects_for("wb_build", PermissionLevel.WRITE_SAFE)
        assert not wb.idempotent and wb.rollback_tool == "wb_clear"
        unknown = effects_for("never_heard", PermissionLevel.WRITE_PROJECT)
        assert unknown.requires_checkpoint and unknown.idempotent

    def test_toolspec_resolves_effects_at_construction(self):
        async def noop() -> str:
            return "ok"

        spec = ToolSpec("t", "", {"type": "object"}, PermissionLevel.WRITE_PROJECT, noop)
        assert spec.effects is not None and spec.effects.requires_checkpoint


def make_effect_pipeline(
    handler_results: list[str],
    *,
    effects: ToolEffects | None = None,
    level: PermissionLevel = PermissionLevel.WRITE_SAFE,
    gate: PermissionGate | None = None,
) -> ToolPipeline:
    gate = gate or PermissionGate()
    registry = ToolRegistry(gate)
    results = list(handler_results)

    async def scripted() -> str:
        result = results.pop(0)
        if result == "<raise>":
            raise RuntimeError("执行炸了")
        return result

    registry.register(
        ToolSpec(
            name="scripted",
            description="",
            parameters={"type": "object", "properties": {}},
            level=level,
            handler=scripted,
            effects=effects,
        )
    )
    return ToolPipeline(registry, gate)


class TestNonIdempotentGovernance:
    """B2：非幂等工具执行失败阈值降为 2，回传文本禁止原样重试。"""

    NON_IDEMPOTENT = ToolEffects(idempotent=False, rollback_tool="wb_clear")

    async def test_second_execution_failure_forbids_retry(self):
        pipeline = make_effect_pipeline(
            ["[error] 落地失败", "[error] 落地失败"], effects=self.NON_IDEMPOTENT
        )
        first = await pipeline.run("scripted", "{}")
        assert "[提示]" not in first.text
        second = await pipeline.run("scripted", "{}")
        assert "禁止原样重试" in second.text
        assert "wb_clear" in second.text, "熔断文本应给出 rollback 工具指引"

    async def test_exception_also_counts_as_execution_failure(self):
        pipeline = make_effect_pipeline(["<raise>", "<raise>"], effects=self.NON_IDEMPOTENT)
        await pipeline.run("scripted", "{}")
        second = await pipeline.run("scripted", "{}")
        assert "禁止原样重试" in second.text

    async def test_pre_execution_failures_keep_default_threshold(self):
        """schema 错误没碰到副作用，修正参数重试是安全的——阈值不降。"""
        registry = ToolRegistry(PermissionGate())

        async def strict(count: int) -> str:
            return str(count)

        registry.register(
            ToolSpec(
                name="strict",
                description="",
                parameters={
                    "type": "object",
                    "properties": {"count": {"type": "integer"}},
                    "required": ["count"],
                },
                level=PermissionLevel.WRITE_SAFE,
                handler=strict,
                effects=self.NON_IDEMPOTENT,
            )
        )
        pipeline = ToolPipeline(registry, PermissionGate())
        await pipeline.run("strict", "{}")
        second = await pipeline.run("strict", "{}")
        assert second.error_kind == "schema"
        assert "[提示]" not in second.text

    async def test_idempotent_tool_keeps_threshold_three(self):
        pipeline = make_effect_pipeline(
            ["[error] x", "[error] x", "[error] x"],
            effects=ToolEffects(idempotent=True),
        )
        await pipeline.run("scripted", "{}")
        second = await pipeline.run("scripted", "{}")
        assert "[提示]" not in second.text
        third = await pipeline.run("scripted", "{}")
        assert "连续 3 次" in third.text


class TestCheckpointDrivenByEffects:
    """B2：自动 checkpoint 改由 effects.requires_checkpoint 驱动，默认行为与旧版等价。"""

    async def test_write_project_default_triggers_checkpoint(self):
        calls = []
        gate = PermissionGate(checkpoint=lambda: calls.append(1) or True)
        pipeline = make_effect_pipeline(["ok"], level=PermissionLevel.WRITE_PROJECT, gate=gate)
        outcome = await pipeline.run("scripted", "{}")
        assert outcome.ok and calls == [1]

    async def test_declared_false_skips_checkpoint(self):
        """声明权威：requires_checkpoint=False 的工程写工具不打快照
        （如白盒类，git 保护不了 actor）。"""
        gate = PermissionGate()  # 无 checkpoint hook，旧行为下 WRITE_PROJECT 必拒
        pipeline = make_effect_pipeline(
            ["ok"],
            level=PermissionLevel.WRITE_PROJECT,
            gate=gate,
            effects=ToolEffects(idempotent=False, requires_checkpoint=False),
        )
        outcome = await pipeline.run("scripted", "{}")
        assert outcome.ok

    async def test_declared_true_enforces_checkpoint_on_write_safe(self):
        gate = PermissionGate()  # 无 hook → 应拒绝
        pipeline = make_effect_pipeline(
            ["ok"],
            level=PermissionLevel.WRITE_SAFE,
            gate=gate,
            effects=ToolEffects(requires_checkpoint=True),
        )
        outcome = await pipeline.run("scripted", "{}")
        assert not outcome.ok and outcome.error_kind == "denied"


class TestEnvelope:
    async def test_ok_outcome(self):
        pipeline, _ = make_pipeline(["done"])
        outcome = await pipeline.run("flaky", '{"text": "x"}')
        assert outcome.ok
        assert outcome.text == "done"
        assert outcome.error_kind is None

    async def test_registry_dispatch_still_returns_text(self):
        _, registry = make_pipeline([])

        async def echo(text: str) -> str:
            return text

        registry.register(
            ToolSpec(
                name="echo",
                description="",
                parameters={"type": "object", "properties": {"text": {"type": "string"}}},
                level=PermissionLevel.READ,
                handler=echo,
            )
        )
        assert await registry.dispatch("echo", '{"text": "hi"}') == "hi"

    async def test_coercion_repairs_schema_violation(self):
        """规范化在校验前：数字字符串不再触发 schema 报错。"""
        registry = ToolRegistry(PermissionGate())

        async def add_one(count: int) -> str:
            return str(count + 1)

        registry.register(
            ToolSpec(
                name="add_one",
                description="",
                parameters={
                    "type": "object",
                    "properties": {"count": {"type": "integer"}},
                    "required": ["count"],
                },
                level=PermissionLevel.READ,
                handler=add_one,
            )
        )
        assert await registry.dispatch("add_one", '{"count": "41"}') == "42"


class TestGateFailureContained:
    async def test_confirmer_exception_becomes_denied(self):
        """确认器自身异常（如无 TTY 下 typer.Abort）按拒绝处理，绝不向上抛。"""

        def exploding_confirmer(tool_name, arguments):
            raise RuntimeError("无 TTY，确认器炸了")

        gate = PermissionGate(confirmer=exploding_confirmer, allowlist={"danger"})
        registry = ToolRegistry(gate)

        async def danger() -> str:
            return "不应执行到这里"

        registry.register(
            ToolSpec(
                name="danger",
                description="",
                parameters={"type": "object", "properties": {}},
                level=PermissionLevel.DANGEROUS,
                handler=danger,
            )
        )
        outcome = await ToolPipeline(registry, gate).run("danger", "{}")
        assert not outcome.ok
        assert outcome.error_kind == "denied"
        assert "权限检查异常" in outcome.text


class TestFactsEnvelope:
    def test_extract_facts_strips_marker(self):
        text, facts = extract_facts('编译成功\n[facts] {"kind": "compile", "ok": true}')
        assert text == "编译成功"
        assert facts == {"kind": "compile", "ok": True}

    def test_extract_facts_absent(self):
        assert extract_facts("普通结果") == ("普通结果", None)

    def test_extract_facts_bad_json_kept_verbatim(self):
        raw = "结果\n[facts] {broken"
        assert extract_facts(raw) == (raw, None)

    async def test_pipeline_outcome_carries_facts(self):
        pipeline, _ = make_pipeline(['搭建完成\n[facts] {"kind": "wb_build", "ok": true}'])
        outcome = await pipeline.run("flaky", "{}")
        assert outcome.ok
        assert outcome.facts == {"kind": "wb_build", "ok": True}
        assert "[facts]" not in outcome.text, "facts 行不回传模型"

    async def test_registry_run_exposes_facts_dispatch_text_only(self):
        _, registry = make_pipeline([])

        async def probe(text: str = "") -> str:
            return '完成\n[facts] {"kind": "path_test", "ok": false, "reachable": false}'

        registry.register(
            ToolSpec(
                name="probe",
                description="",
                parameters={"type": "object", "properties": {}},
                level=PermissionLevel.READ,
                handler=probe,
            )
        )
        outcome = await registry.run("probe", "{}")
        assert outcome.facts == {"kind": "path_test", "ok": False, "reachable": False}
        assert await registry.dispatch("probe", "{}") == "完成"
