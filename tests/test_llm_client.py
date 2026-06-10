"""LiteLLMClient：重试退避、降级链、错误分类。"""

import pytest
from litellm import exceptions as litellm_errors

from ue5agent.config import ModelsConfig
from ue5agent.llm.client import LiteLLMClient, LLMUnavailable
from ue5agent.llm.types import AssistantTurn


def make_config(fallbacks: dict | None = None) -> ModelsConfig:
    return ModelsConfig(
        providers={
            "anthropic": {"api_key_env": "ANTHROPIC_API_KEY"},
            "deepseek": {"api_key_env": "DEEPSEEK_API_KEY"},
        },
        roles={"planner": "anthropic/claude-x"},
        fallbacks=fallbacks or {},
    )


def rate_limit() -> Exception:
    return litellm_errors.RateLimitError(
        message="rate limited", llm_provider="anthropic", model="claude-x"
    )


def auth_error() -> Exception:
    return litellm_errors.AuthenticationError(
        message="bad key", llm_provider="anthropic", model="claude-x"
    )


class ScriptedClient(LiteLLMClient):
    """覆盖真实调用点，按脚本依次返回/抛出；记录调用与退避。"""

    def __init__(self, config: ModelsConfig, script: list, **kwargs):
        self.sleeps: list[float] = []

        async def record_sleep(seconds: float) -> None:
            self.sleeps.append(seconds)

        super().__init__(config, sleep=record_sleep, **kwargs)
        self._script = list(script)
        self.calls: list[str] = []

    async def _call_model(self, model_ref, messages, tools):
        self.calls.append(model_ref)
        action = self._script.pop(0)
        if isinstance(action, Exception):
            raise action
        return action


OK = AssistantTurn(content="ok")


async def test_retry_with_backoff_then_success():
    client = ScriptedClient(make_config(), [rate_limit(), rate_limit(), OK])
    result = await client.acomplete("planner", [])
    assert result.content == "ok"
    assert client.calls == ["anthropic/claude-x"] * 3
    assert client.sleeps == [1.0, 2.0]


async def test_retries_exhausted_falls_to_next_model():
    config = make_config(fallbacks={"planner": ["deepseek/deepseek-chat"]})
    client = ScriptedClient(config, [rate_limit(), rate_limit(), rate_limit(), OK])
    result = await client.acomplete("planner", [])
    assert result.content == "ok"
    assert client.calls[-1] == "deepseek/deepseek-chat"
    assert client.calls[:3] == ["anthropic/claude-x"] * 3


async def test_auth_error_skips_to_fallback_immediately():
    config = make_config(fallbacks={"planner": ["deepseek/deepseek-chat"]})
    client = ScriptedClient(config, [auth_error(), OK])
    result = await client.acomplete("planner", [])
    assert result.content == "ok"
    assert client.calls == ["anthropic/claude-x", "deepseek/deepseek-chat"]
    assert client.sleeps == []


async def test_all_models_failed():
    config = make_config(fallbacks={"planner": ["deepseek/deepseek-chat"]})
    client = ScriptedClient(config, [auth_error(), auth_error()])
    with pytest.raises(LLMUnavailable, match="deepseek"):
        await client.acomplete("planner", [])


async def test_non_transient_error_propagates():
    """请求本身有问题时降级无意义，直接抛出。"""
    bad_request = litellm_errors.BadRequestError(
        message="bad request", llm_provider="anthropic", model="claude-x"
    )
    config = make_config(fallbacks={"planner": ["deepseek/deepseek-chat"]})
    client = ScriptedClient(config, [bad_request])
    with pytest.raises(litellm_errors.BadRequestError):
        await client.acomplete("planner", [])
    assert client.calls == ["anthropic/claude-x"]


def test_fallback_role_must_exist():
    with pytest.raises(ValueError, match="fallbacks"):
        ModelsConfig(
            providers={"deepseek": {"api_key_env": "K"}},
            roles={"planner": "deepseek/deepseek-chat"},
            fallbacks={"vision": ["deepseek/deepseek-chat"]},
        )
