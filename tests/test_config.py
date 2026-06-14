"""config 模块：models.yaml / agent.yaml 的加载与校验。"""

from pathlib import Path

import pytest

from ue5agent.config import (
    AgentSettings,
    build_runtime_env,
    load_agent_settings,
    load_models_config,
    with_model_for_roles,
    with_role_model,
)

VALID_MODELS = """\
providers:
  deepseek:
    base_url: https://api.deepseek.com
    api_key_env: DEEPSEEK_API_KEY
roles:
  planner: deepseek/deepseek-chat
"""


def test_load_valid_models(tmp_path):
    path = tmp_path / "models.yaml"
    path.write_text(VALID_MODELS, encoding="utf-8")
    config = load_models_config(path)
    assert config.roles["planner"] == "deepseek/deepseek-chat"
    assert config.providers["deepseek"].base_url == "https://api.deepseek.com"


def test_role_with_unknown_provider_rejected(tmp_path):
    path = tmp_path / "models.yaml"
    path.write_text(
        VALID_MODELS + "  vision: openai/gpt-4o\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="openai"):
        load_models_config(path)


def test_missing_planner_rejected(tmp_path):
    path = tmp_path / "models.yaml"
    path.write_text(
        """\
providers:
  deepseek: {api_key_env: DEEPSEEK_API_KEY}
roles:
  coder: deepseek/deepseek-chat
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="planner"):
        load_models_config(path)


def test_vision_flag(tmp_path):
    path = tmp_path / "models.yaml"
    path.write_text(VALID_MODELS, encoding="utf-8")
    assert load_models_config(path).has_vision is False


def test_provider_params_parsed(tmp_path):
    path = tmp_path / "models.yaml"
    path.write_text(
        """\
providers:
  moonshot:
    base_url: https://api.moonshot.cn/v1
    api_key_env: MOONSHOT_API_KEY
    params: {temperature: 1}
roles:
  planner: moonshot/kimi-k2.6
  vision: moonshot/kimi-k2.6
""",
        encoding="utf-8",
    )
    config = load_models_config(path)
    assert config.providers["moonshot"].params == {"temperature": 1}
    assert config.has_vision is True
    # 默认无 params
    assert load_models_config(_write(tmp_path, VALID_MODELS)).providers["deepseek"].params == {}


def test_with_role_model_overrides_without_mutating_original(tmp_path):
    config = load_models_config(_write(tmp_path, VALID_MODELS))

    overridden = with_role_model(config, "planner", "deepseek/deepseek-v4-pro")

    assert overridden.roles["planner"] == "deepseek/deepseek-v4-pro"
    assert config.roles["planner"] == "deepseek/deepseek-chat"


def test_with_role_model_rejects_unknown_provider(tmp_path):
    config = load_models_config(_write(tmp_path, VALID_MODELS))

    with pytest.raises(ValueError, match="openai"):
        with_role_model(config, "planner", "openai/gpt-4o")


def test_with_model_for_roles_overrides_multiple_roles(tmp_path):
    path = tmp_path / "models.yaml"
    path.write_text(
        """\
providers:
  deepseek: {api_key_env: DEEPSEEK_API_KEY}
roles:
  planner: deepseek/deepseek-chat
  coder: deepseek/deepseek-chat
  judge: deepseek/deepseek-chat
""",
        encoding="utf-8",
    )
    config = load_models_config(path)

    overridden = with_model_for_roles(
        config, ["planner", "coder", "judge"], "deepseek/deepseek-v4-pro"
    )

    assert overridden.roles == {
        "planner": "deepseek/deepseek-v4-pro",
        "coder": "deepseek/deepseek-v4-pro",
        "judge": "deepseek/deepseek-v4-pro",
    }


def _write(tmp_path, text):
    path = tmp_path / "models2.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_agent_settings_all_optional(tmp_path):
    path = tmp_path / "agent.yaml"
    path.write_text("limits: {max_iterations: 10}\n", encoding="utf-8")
    settings = load_agent_settings(path)
    assert settings.engine is None
    assert settings.limits.max_iterations == 10
    assert settings.mcp_servers == {}


def test_permissions_allowlist_parsed(tmp_path):
    path = tmp_path / "agent.yaml"
    path.write_text(
        "permissions: {allowlist: [ue_lifecycle__editor_launch]}\n",
        encoding="utf-8",
    )
    settings = load_agent_settings(path)
    assert settings.permissions.allowlist == ["ue_lifecycle__editor_launch"]
    # 缺省空白名单：dangerous 工具全部拒绝
    assert AgentSettings().permissions.allowlist == []


def test_mcp_server_permission_validated(tmp_path):
    path = tmp_path / "agent.yaml"
    path.write_text(
        """\
mcp_servers:
  bad:
    command: ["python", "-m", "x"]
    permission: sudo
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_agent_settings(path)


def test_tool_permissions_override_parsed(tmp_path):
    path = tmp_path / "agent.yaml"
    path.write_text(
        """\
mcp_servers:
  ue_editor:
    command: ["python", "-m", "x"]
    permission: read
    tool_permissions:
      navmesh_rebuild: write_project
""",
        encoding="utf-8",
    )
    settings = load_agent_settings(path)
    server = settings.mcp_servers["ue_editor"]
    assert server.tool_permissions == {"navmesh_rebuild": "write_project"}
    # 缺省为空：全部工具沿用 server 级授权
    assert server.permission == "read"


def test_tool_permissions_bad_level_rejected(tmp_path):
    path = tmp_path / "agent.yaml"
    path.write_text(
        """\
mcp_servers:
  ue_editor:
    command: ["python", "-m", "x"]
    tool_permissions: {navmesh_rebuild: sudo}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="navmesh_rebuild"):
        load_agent_settings(path)


def test_runtime_env_uses_agent_project_paths_and_token_file(tmp_path):
    project_dir = tmp_path / "Game"
    saved_dir = project_dir / "Saved"
    saved_dir.mkdir(parents=True)
    uproject = project_dir / "Game.uproject"
    uproject.write_text("{}", encoding="utf-8")
    token_file = saved_dir / "ue5agent_bridge_token.txt"
    token_file.write_text("secret\n", encoding="utf-8")
    settings = AgentSettings.model_validate(
        {
            "engine": {"root": str(tmp_path / "UE_5.7"), "version": "5.7"},
            "project": {"uproject": str(uproject)},
        }
    )

    env = build_runtime_env(settings, base_env={})

    assert env["UE_ENGINE_ROOT"] == str(Path(tmp_path / "UE_5.7"))
    assert env["UE_UPROJECT"] == str(uproject)
    assert env["UE_MCP_TOKEN_FILE"] == str(token_file)


def test_runtime_env_does_not_override_explicit_token(tmp_path):
    project_dir = tmp_path / "Game"
    saved_dir = project_dir / "Saved"
    saved_dir.mkdir(parents=True)
    uproject = project_dir / "Game.uproject"
    uproject.write_text("{}", encoding="utf-8")
    (saved_dir / "ue5agent_bridge_token.txt").write_text("file-secret\n", encoding="utf-8")
    settings = AgentSettings.model_validate({"project": {"uproject": str(uproject)}})

    env = build_runtime_env(settings, base_env={"UE_MCP_TOKEN": "explicit-secret"})

    assert env["UE_MCP_TOKEN"] == "explicit-secret"
    assert "UE_MCP_TOKEN_FILE" not in env
