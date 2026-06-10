"""config 模块：models.yaml / agent.yaml 的加载与校验。"""

import pytest

from ue5agent.config import load_agent_settings, load_models_config

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


def test_agent_settings_all_optional(tmp_path):
    path = tmp_path / "agent.yaml"
    path.write_text("limits: {max_iterations: 10}\n", encoding="utf-8")
    settings = load_agent_settings(path)
    assert settings.engine is None
    assert settings.limits.max_iterations == 10
    assert settings.mcp_servers == {}


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
