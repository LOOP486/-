"""配置加载与校验。

两份配置文件：
- models.yaml：LLM 服务商接入与按角色的模型路由（planner/coder/vision...）
- agent.yaml：引擎与工程路径、MCP server 挂载、运行限额
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

REQUIRED_ROLES = ("planner",)
VISION_ROLE = "vision"


class ProviderConfig(BaseModel):
    """一个 LLM 服务商（或自定义中转）的接入配置。"""

    base_url: str | None = None
    api_key_env: str = Field(description="存放 API key 的环境变量名")


class ModelsConfig(BaseModel):
    """models.yaml：providers + 角色到模型的路由表 + 角色级降级链。"""

    providers: dict[str, ProviderConfig]
    roles: dict[str, str] = Field(description="角色 -> 'provider/model'")
    fallbacks: dict[str, list[str]] = Field(
        default_factory=dict, description="角色 -> 主模型不可用时依次尝试的备选"
    )

    @model_validator(mode="after")
    def _check_roles(self) -> ModelsConfig:
        for role, ref in self.roles.items():
            self._check_provider(f"角色 {role}", ref)
        for required in REQUIRED_ROLES:
            if required not in self.roles:
                raise ValueError(f"缺少必需角色：{required}")
        for role, refs in self.fallbacks.items():
            if role not in self.roles:
                raise ValueError(f"fallbacks 中的角色 {role} 未在 roles 定义")
            for ref in refs:
                self._check_provider(f"角色 {role} 的 fallback", ref)
        return self

    def _check_provider(self, where: str, ref: str) -> None:
        provider = ref.split("/", 1)[0]
        if provider not in self.providers:
            raise ValueError(f"{where} 引用了未定义的 provider：{provider}")

    @property
    def has_vision(self) -> bool:
        """是否配置了多模态角色；缺失时截图视觉验证不可用。"""
        return VISION_ROLE in self.roles


class EngineConfig(BaseModel):
    root: Path = Field(description="UE 引擎根目录（其下应有 Engine/）")
    version: str = Field(description="引擎版本，如 5.5")


class ProjectConfig(BaseModel):
    uproject: Path = Field(description=".uproject 文件路径")


class McpServerConfig(BaseModel):
    command: list[str] = Field(description="stdio 启动命令")
    permission: str = Field(
        default="read",
        pattern="^(read|write|write_safe|write_project|dangerous)$",
        description="该 server 全部工具的授权级别；旧值 write 按 write_project 解释",
    )


class LimitsConfig(BaseModel):
    max_iterations: int = 40
    max_tool_result_chars: int = 30_000
    compact_budget_chars: int = 200_000


class AgentSettings(BaseModel):
    """agent.yaml：工程环境与运行参数，全部可缺省。"""

    engine: EngineConfig | None = None
    project: ProjectConfig | None = None
    mcp_servers: dict[str, McpServerConfig] = Field(default_factory=dict)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)


def _load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_models_config(path: Path) -> ModelsConfig:
    return ModelsConfig.model_validate(_load_yaml(path))


def load_agent_settings(path: Path) -> AgentSettings:
    return AgentSettings.model_validate(_load_yaml(path))
