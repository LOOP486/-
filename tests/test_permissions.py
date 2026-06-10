"""权限网关：只读放行 / 写需确认 / 危险默认拒绝 / 白名单豁免。"""

import pytest

from ue5agent.core.permissions import PermissionGate, PermissionLevel, ToolDenied


def test_read_auto_allowed():
    gate = PermissionGate()
    gate.check("asset_search", PermissionLevel.READ, {})


def test_write_denied_without_confirmer():
    gate = PermissionGate()
    with pytest.raises(ToolDenied):
        gate.check("actor_spawn", PermissionLevel.WRITE, {})


def test_write_allowed_when_confirmed():
    gate = PermissionGate(confirmer=lambda name, args: True)
    gate.check("actor_spawn", PermissionLevel.WRITE, {})


def test_write_denied_when_user_refuses():
    gate = PermissionGate(confirmer=lambda name, args: False)
    with pytest.raises(ToolDenied):
        gate.check("actor_spawn", PermissionLevel.WRITE, {})


def test_dangerous_denied_even_with_confirmer():
    gate = PermissionGate(confirmer=lambda name, args: True)
    with pytest.raises(ToolDenied):
        gate.check("asset_delete", PermissionLevel.DANGEROUS, {})


def test_allowlist_bypasses_gate():
    gate = PermissionGate(allowlist={"asset_delete"})
    gate.check("asset_delete", PermissionLevel.DANGEROUS, {})
