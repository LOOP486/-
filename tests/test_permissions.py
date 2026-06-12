"""权限网关（4 级）：读/安全写放行，工程写前置 checkpoint，危险双条件。"""

import pytest

from ue5agent.core.permissions import PermissionGate, PermissionLevel, ToolDenied


def test_read_and_write_safe_auto_allowed():
    gate = PermissionGate()
    gate.check("asset_search", PermissionLevel.READ, {})
    gate.check("write_report", PermissionLevel.WRITE_SAFE, {})


class TestWriteProject:
    def test_denied_without_checkpoint_hook(self):
        gate = PermissionGate()
        with pytest.raises(ToolDenied, match="checkpoint"):
            gate.check("modify_source", PermissionLevel.WRITE_PROJECT, {})

    def test_denied_when_checkpoint_fails(self):
        gate = PermissionGate(checkpoint=lambda: False)
        with pytest.raises(ToolDenied, match="前置 checkpoint 失败"):
            gate.check("modify_source", PermissionLevel.WRITE_PROJECT, {})

    def test_allowed_with_checkpoint_in_batch_mode(self):
        calls = []
        gate = PermissionGate(checkpoint=lambda: calls.append(1) or True)
        gate.check("modify_source", PermissionLevel.WRITE_PROJECT, {})
        assert calls == [1]

    def test_interactive_confirm_after_checkpoint(self):
        gate = PermissionGate(confirmer=lambda n, a: False, checkpoint=lambda: True)
        with pytest.raises(ToolDenied, match="未获用户确认"):
            gate.check("modify_source", PermissionLevel.WRITE_PROJECT, {})
        gate_yes = PermissionGate(confirmer=lambda n, a: True, checkpoint=lambda: True)
        gate_yes.check("modify_source", PermissionLevel.WRITE_PROJECT, {})


class TestDangerous:
    def test_denied_outside_allowlist_even_with_confirmer(self):
        gate = PermissionGate(confirmer=lambda n, a: True)
        with pytest.raises(ToolDenied, match="白名单"):
            gate.check("asset_delete", PermissionLevel.DANGEROUS, {})

    def test_denied_in_allowlist_without_confirmer(self):
        gate = PermissionGate(allowlist={"asset_delete"})
        with pytest.raises(ToolDenied, match="人工确认"):
            gate.check("asset_delete", PermissionLevel.DANGEROUS, {})

    def test_allowed_with_both_conditions(self):
        gate = PermissionGate(confirmer=lambda n, a: True, allowlist={"asset_delete"})
        gate.check("asset_delete", PermissionLevel.DANGEROUS, {})


def test_legacy_write_maps_to_write_project():
    assert PermissionLevel("write") is PermissionLevel.WRITE_PROJECT


class TestEffectsDrivenCheckpoint:
    """B2：checkpoint 由副作用声明驱动；未声明（None）时与旧规则等价。"""

    def test_write_project_default_still_checkpoints(self):
        calls = []
        gate = PermissionGate(checkpoint=lambda: calls.append(1) or True)
        gate.check("modify_source", PermissionLevel.WRITE_PROJECT, {}, requires_checkpoint=None)
        assert calls == [1]

    def test_declared_false_skips_checkpoint_on_write_project(self):
        # 声明权威：requires_checkpoint=False 的工程写工具不打快照（无 hook 也放行）
        gate = PermissionGate()
        gate.check("level_op", PermissionLevel.WRITE_PROJECT, {}, requires_checkpoint=False)

    def test_declared_true_enforces_checkpoint_on_write_safe(self):
        gate = PermissionGate()
        with pytest.raises(ToolDenied, match="checkpoint"):
            gate.check("special_write", PermissionLevel.WRITE_SAFE, {}, requires_checkpoint=True)
        calls = []
        gate_ok = PermissionGate(checkpoint=lambda: calls.append(1) or True)
        gate_ok.check("special_write", PermissionLevel.WRITE_SAFE, {}, requires_checkpoint=True)
        assert calls == [1]

    def test_dangerous_allowlist_checked_before_checkpoint(self):
        calls = []
        gate = PermissionGate(checkpoint=lambda: calls.append(1) or True)
        with pytest.raises(ToolDenied, match="白名单"):
            gate.check("asset_delete", PermissionLevel.DANGEROUS, {}, requires_checkpoint=True)
        assert calls == [], "不在白名单的危险工具不应触发快照"
