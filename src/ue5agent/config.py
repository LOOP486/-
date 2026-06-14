"""配置加载与校验。

两份配置文件：
- models.yaml：LLM 服务商接入与按角色的模型路由（planner/coder/vision...）
- agent.yaml：引擎与工程路径、MCP server 挂载、运行限额
"""

from __future__ import annotations

import os
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
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="该 provider 全部请求附加的固定参数（如 temperature）；"
        "用于满足模型硬约束，例如 Kimi kimi-k2.6 只接受 temperature=1。",
    )


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

    def secret_env_names(self) -> list[str]:
        """全部 provider 的 api_key_env 变量名（D1.2 掩码取值用）。"""
        return [p.api_key_env for p in self.providers.values() if p.api_key_env]


class EngineConfig(BaseModel):
    root: Path = Field(description="UE 引擎根目录（其下应有 Engine/）")
    version: str = Field(description="引擎版本，如 5.5")


class ProjectConfig(BaseModel):
    uproject: Path = Field(description=".uproject 文件路径")


_PERMISSION_VALUES = ("read", "write", "write_safe", "write_project", "dangerous")


class McpServerConfig(BaseModel):
    command: list[str] = Field(description="stdio 启动命令")
    permission: str = Field(
        default="read",
        pattern="^(read|write|write_safe|write_project|dangerous)$",
        description="该 server 全部工具的授权级别；旧值 write 按 write_project 解释",
    )
    tool_permissions: dict[str, str] = Field(
        default_factory=dict,
        description="按工具名覆写授权级别（键为工具原名，不含 server 前缀），"
        "用于同一 server 内读写工具混存的场景（如 ue_editor 的 navmesh_rebuild）",
    )

    @model_validator(mode="after")
    def _check_tool_permissions(self) -> McpServerConfig:
        for tool, level in self.tool_permissions.items():
            if level not in _PERMISSION_VALUES:
                raise ValueError(
                    f"工具 {tool} 的授权级别非法：{level}（可选：{_PERMISSION_VALUES}）"
                )
        return self


class LimitsConfig(BaseModel):
    max_iterations: int = 40
    max_tool_result_chars: int = 30_000
    compact_budget_chars: int = 200_000


class PermissionsConfig(BaseModel):
    allowlist: list[str] = Field(
        default_factory=list,
        description="dangerous 工具白名单（完整工具名，如 ue_lifecycle__editor_launch）；"
        "放行仍需人工确认（双条件）",
    )


class AgentSettings(BaseModel):
    """agent.yaml：工程环境与运行参数，全部可缺省。"""

    engine: EngineConfig | None = None
    project: ProjectConfig | None = None
    mcp_servers: dict[str, McpServerConfig] = Field(default_factory=dict)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)


def _load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_models_config(path: Path) -> ModelsConfig:
    return ModelsConfig.model_validate(_load_yaml(path))


def with_role_model(config: ModelsConfig, role: str, model_ref: str) -> ModelsConfig:
    """返回角色模型被临时覆写的新配置；用于评测固定模型，不改本地 models.yaml。"""
    roles = dict(config.roles)
    roles[role] = model_ref
    return ModelsConfig.model_validate(
        {"providers": config.providers, "roles": roles, "fallbacks": config.fallbacks}
    )


def with_model_for_roles(config: ModelsConfig, roles: list[str], model_ref: str) -> ModelsConfig:
    """返回多个角色被同一模型临时覆写的新配置；用于黑盒评测固定文本模型。"""
    updated = config
    for role in roles:
        updated = with_role_model(updated, role, model_ref)
    return updated


def load_agent_settings(path: Path) -> AgentSettings:
    return AgentSettings.model_validate(_load_yaml(path))


def build_runtime_env(
    settings: AgentSettings, base_env: dict[str, str] | None = None
) -> dict[str, str]:
    """把 agent.yaml 的工程配置转换为 MCP 子进程运行环境。"""
    env = dict(os.environ if base_env is None else base_env)
    if settings.engine is not None:
        env.setdefault("UE_ENGINE_ROOT", str(settings.engine.root))
    if settings.project is not None:
        uproject = settings.project.uproject
        env.setdefault("UE_UPROJECT", str(uproject))
        token_file = uproject.parent / "Saved" / "ue5agent_bridge_token.txt"
        if "UE_MCP_TOKEN" not in env and "UE_MCP_TOKEN_FILE" not in env and token_file.exists():
            env["UE_MCP_TOKEN_FILE"] = str(token_file)
    return env
