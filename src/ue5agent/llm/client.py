"""LiteLLM 封装：按角色路由模型，custom base_url 与 API key 来自 models.yaml。"""

from __future__ import annotations

import os
from typing import Any

import litellm

from ue5agent.config import ModelsConfig
from ue5agent.llm.types import AssistantTurn, ToolCall, Usage


class LiteLLMClient:
    def __init__(self, config: ModelsConfig):
        self._config = config

    def model_for(self, role: str) -> str:
        """角色未配置时回退到主控模型。"""
        return self._config.roles.get(role) or self._config.roles["planner"]

    def _provider_kwargs(self, model_ref: str) -> dict[str, Any]:
        provider = model_ref.split("/", 1)[0]
        provider_config = self._config.providers[provider]
        kwargs: dict[str, Any] = {}
        if provider_config.base_url:
            kwargs["api_base"] = provider_config.base_url
        api_key = os.environ.get(provider_config.api_key_env)
        if api_key:
            kwargs["api_key"] = api_key
        return kwargs

    async def acomplete(
        self,
        role: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AssistantTurn:
        model_ref = self.model_for(role)
        response = await litellm.acompletion(
            model=model_ref,
            messages=messages,
            tools=tools or None,
            **self._provider_kwargs(model_ref),
        )
        message = response.choices[0].message
        tool_calls = [
            ToolCall(id=call.id, name=call.function.name, arguments=call.function.arguments or "{}")
            for call in (message.tool_calls or [])
        ]
        return AssistantTurn(
            content=message.content,
            tool_calls=tool_calls,
            usage=_extract_usage(response),
            raw=response,
        )


def _extract_usage(response: Any) -> Usage | None:
    raw = getattr(response, "usage", None)
    if raw is None:
        return None
    return Usage(
        prompt_tokens=getattr(raw, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(raw, "completion_tokens", 0) or 0,
    )
