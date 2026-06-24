"""模型注册表 — 统一管理所有API提供商的模型访问"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

import yaml
from openai import OpenAI


@dataclass
class ModelSpec:
    """模型规格"""
    provider: str
    model_id: str
    context_window: int
    cost_per_1k_input: float
    cost_per_1k_output: float
    tags: list[str] = field(default_factory=list)
    extra_options: dict = field(default_factory=dict)


class ModelRegistry:
    """模型注册表 — 从config/models.yaml加载"""

    _instance: Optional["ModelRegistry"] = None

    def __new__(cls, config_path: str = "config/models.yaml"):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, config_path: str = "config/models.yaml"):
        if self._initialized:
            return
        with open(config_path, encoding="utf-8") as f:
            self.config = yaml.safe_load(f)
        self._clients: dict[str, OpenAI] = {}
        self._specs: dict[str, ModelSpec] = {}
        self._agent_mapping: dict[str, dict] = self.config.get("agent_model_mapping", {})
        self._build_specs()
        self._initialized = True

    def _build_specs(self):
        """从配置构建ModelSpec索引"""
        for provider_name, provider_cfg in self.config["providers"].items():
            for model_name, model_cfg in provider_cfg.get("models", {}).items():
                key = f"{provider_name}/{model_name}"
                self._specs[key] = ModelSpec(
                    provider=provider_name,
                    model_id=model_cfg["id"],
                    context_window=model_cfg.get("context_window", 4096),
                    cost_per_1k_input=model_cfg.get("cost_per_1k_input", 0),
                    cost_per_1k_output=model_cfg.get("cost_per_1k_output", 0),
                    tags=model_cfg.get("tags", []),
                    extra_options=model_cfg.get("extra_options", {}),
                )

    def get_client(self, model_key: str) -> OpenAI:
        """获取指定模型的OpenAI兼容客户端"""
        if model_key in self._clients:
            return self._clients[model_key]

        spec = self._specs.get(model_key)
        if not spec:
            raise KeyError(f"未知模型: {model_key}")

        provider_cfg = self.config["providers"][spec.provider]
        api_key = os.getenv(provider_cfg["api_key_env"], "")
        if not api_key:
            raise ValueError(
                f"API Key未设置: {provider_cfg['api_key_env']}，"
                f"请在环境变量中设置或写入.env文件"
            )

        client = OpenAI(api_key=api_key, base_url=provider_cfg["base_url"])
        self._clients[model_key] = client
        return client

    def get_spec(self, model_key: str) -> ModelSpec:
        if model_key not in self._specs:
            raise KeyError(f"未知模型: {model_key}")
        return self._specs[model_key]

    def get_agent_model(self, agent_name: str, scenario: str = "default") -> tuple[str, ModelSpec]:
        """
        获取Agent对应的模型Key和Spec。

        Args:
            agent_name: Agent名称 (book_analyzer, term_stylist, chapter_translator, chief_editor, fact_checker)
            scenario: 场景 (default, legal, literary, terminology_heavy)

        Returns:
            (model_key, ModelSpec)
        """
        mapping = self._agent_mapping.get(agent_name)
        if not mapping:
            raise KeyError(f"未知Agent: {agent_name}")

        if scenario != "default" and scenario in mapping:
            model_key = mapping[scenario]
        else:
            model_key = mapping.get("default", mapping.get("primary", "deepseek/v4-pro"))

        return model_key, self._specs[model_key]

    def estimate_cost(self, model_key: str, input_tokens: int, output_tokens: int) -> float:
        """估算单次调用成本（美元）"""
        spec = self._specs[model_key]
        return (input_tokens * spec.cost_per_1k_input + output_tokens * spec.cost_per_1k_output) / 1000

    def list_agents(self) -> list[str]:
        return list(self._agent_mapping.keys())
