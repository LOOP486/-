"""LiteLLM 封装：按角色路由、custom base_url、重试退避与降级链。

故障策略：
- 瞬态错误（限流/网络/5xx/超时）：指数退避重试，耗尽后转下一个 fallback 模型；
- 鉴权错误：该 provider 已坏，立即转 fallback；
- 其它错误（如请求格式问题）：fallback 解决不了，直接抛给调用方。
"""

from __future__ import annotations

import asyncio
import os
import queue
import threading
from typing import Any

import litellm
from litellm import exceptions as litellm_errors

from ue5agent.config import ModelsConfig
from ue5agent.llm.types import AssistantTurn, ToolCall, Usage

RETRYABLE_ERRORS = (
    asyncio.TimeoutError,
    litellm_errors.RateLimitError,
    litellm_errors.APIConnectionError,
    litellm_errors.InternalServerError,
    litellm_errors.ServiceUnavailableError,
    litellm_errors.Timeout,
)
SKIP_TO_FALLBACK_ERRORS = (litellm_errors.AuthenticationError,)


class LLMUnavailable(Exception):
    """主模型与全部 fallback 均不可用。"""


class LiteLLMClient:
    def __init__(
        self,
        config: ModelsConfig,
        *,
        max_retries: int = 3,
        backoff_base_seconds: float = 1.0,
        request_timeout_seconds: float = 120.0,
        sleep=None,
    ):
        self._config = config
        self._max_retries = max_retries
        self._backoff_base = backoff_base_seconds
        self._request_timeout = request_timeout_seconds
        self._sleep = sleep or asyncio.sleep

    @property
    def has_vision(self) -> bool:
        """是否配置了多模态 vision 角色（缺失时截图视觉验证不可用）。"""
        return self._config.has_vision

    def model_for(self, role: str) -> str:
        """角色未配置时回退到主控模型。"""
        return self._config.roles.get(role) or self._config.roles["planner"]

    def models_for(self, role: str) -> list[str]:
        """主模型 + 该角色的降级链。"""
        return [self.model_for(role), *self._config.fallbacks.get(role, [])]

    async def acomplete(
        self,
        role: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AssistantTurn:
        failures: list[str] = []
        for model_ref in self.models_for(role):
            try:
                return await self._call_with_retry(model_ref, messages, tools)
            except (*RETRYABLE_ERRORS, *SKIP_TO_FALLBACK_ERRORS) as exc:
                failures.append(f"{model_ref}（{type(exc).__name__}）")
        raise LLMUnavailable(f"角色 {role} 的全部模型不可用：{'；'.join(failures)}")

    async def _call_with_retry(
        self,
        model_ref: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> AssistantTurn:
        for attempt in range(self._max_retries):
            try:
                return await self._call_model_hard_timeout(model_ref, messages, tools)
            except RETRYABLE_ERRORS:
                if attempt + 1 == self._max_retries:
                    raise
                await self._sleep(self._backoff_base * 2**attempt)
        raise AssertionError("unreachable")

    async def _call_model_hard_timeout(
        self,
        model_ref: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> AssistantTurn:
        """隔离真实 LLM 调用：即使 provider SDK 阻塞事件循环，也能按时放弃。"""
        results: queue.Queue[tuple[str, AssistantTurn | BaseException]] = queue.Queue(maxsize=1)

        def _blocking_call() -> None:
            try:
                results.put(("ok", asyncio.run(self._call_model(model_ref, messages, tools))))
            except BaseException as exc:  # 线程边界必须把异常带回主 loop
                results.put(("error", exc))

        thread = threading.Thread(target=_blocking_call, name=f"llm-{model_ref}", daemon=True)
        thread.start()
        deadline = asyncio.get_running_loop().time() + self._request_timeout
        while True:
            try:
                status, value = results.get_nowait()
            except queue.Empty:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError from None
                await asyncio.sleep(min(0.05, remaining))
                continue
            if status == "error":
                assert isinstance(value, BaseException)
                raise value
            assert isinstance(value, AssistantTurn)
            return value

    async def _call_model(
        self,
        model_ref: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> AssistantTurn:
        """单次真实调用，子类/测试替身的覆盖点。"""
        response = await litellm.acompletion(
            model=model_ref,
            messages=messages,
            tools=tools or None,
            timeout=self._request_timeout,
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

    def _provider_kwargs(self, model_ref: str) -> dict[str, Any]:
        provider = model_ref.split("/", 1)[0]
        provider_config = self._config.providers[provider]
        kwargs: dict[str, Any] = dict(provider_config.params)
        if provider_config.base_url:
            kwargs["api_base"] = provider_config.base_url
        api_key = os.environ.get(provider_config.api_key_env)
        if api_key:
            kwargs["api_key"] = api_key
        return kwargs


def _extract_usage(response: Any) -> Usage | None:
    raw = getattr(response, "usage", None)
    if raw is None:
        return None
    return Usage(
        prompt_tokens=getattr(raw, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(raw, "completion_tokens", 0) or 0,
    )
