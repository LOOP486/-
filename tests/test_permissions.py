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
