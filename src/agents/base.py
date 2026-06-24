"""Agent基类 — 所有翻译流水线Agent的统一接口

设计原则：
- 每个Agent只承担单一职责，不越界
- Agent通过AssetStore读取全局资产，不私自修改
- Agent支持模型切换（同接口，不同底层模型）
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from src.assets.schema import AssetStore
from src.models.registry import ModelRegistry


@dataclass
class AgentContext:
    """Agent执行上下文"""
    agent_name: str
    model_key: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    metadata: dict = field(default_factory=dict)


@dataclass
class AgentResult:
    """Agent执行结果"""
    success: bool
    data: dict
    context: AgentContext
    error: str = ""
    warnings: list[str] = field(default_factory=list)


class BaseAgent(ABC):
    """翻译流水线Agent基类"""

    # 子类必须定义
    agent_name: str = "base"
    agent_description: str = ""

    def __init__(
        self,
        model_registry: Optional[ModelRegistry] = None,
        asset_store: Optional[AssetStore] = None,
    ):
        self.registry = model_registry or ModelRegistry()
        self.assets = asset_store or AssetStore()
        self._last_context: Optional[AgentContext] = None

    def get_client(self, scenario: str = "default"):
        """获取模型客户端"""
        model_key, spec = self.registry.get_agent_model(self.agent_name, scenario)
        client = self.registry.get_client(model_key)
        return client, model_key, spec

    @abstractmethod
    def execute(self, **kwargs) -> AgentResult:
        """执行Agent核心任务，子类必须实现"""
        ...

    def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        scenario: str = "default",
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> tuple[str, AgentContext]:
        """
        调用LLM的统一接口。自动注入共享知识库。

        Returns:
            (response_text, AgentContext)
        """
        # 自动注入共享知识库（所有Agent共用）
        knowledge_block = self.assets.get_knowledge_prompt_block()
        if knowledge_block and len(knowledge_block) > 50:
            system_prompt = f"""{system_prompt}

## 📚 共享翻译技能知识（所有Agent必须遵循）
{knowledge_block}"""

        client, model_key, spec = self.get_client(scenario)

        response = client.chat.completions.create(
            model=spec.model_id,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )

        usage = response.usage
        input_tokens = usage.prompt_tokens if usage else 0
        output_tokens = usage.completion_tokens if usage else 0
        cost = self.registry.estimate_cost(model_key, input_tokens, output_tokens)

        context = AgentContext(
            agent_name=self.agent_name,
            model_key=model_key,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )
        self._last_context = context

        return response.choices[0].message.content or "", context

    def _load_prompt(self, template_name: str, **kwargs) -> tuple[str, str]:
        """
        加载Prompt模板文件。

        模板文件位于 src/models/prompts/ 目录，
        使用 --- 分隔system和user部分。
        """
        import os

        template_path = os.path.join(
            os.path.dirname(__file__), "..", "models", "prompts", template_name
        )
        if not os.path.exists(template_path):
            # Fallback: 使用内置默认Prompt
            return self._default_prompt()

        content = open(template_path, encoding="utf-8").read()
        parts = content.split("---", 1)
        system_part = parts[0].strip() if len(parts) > 0 else ""
        user_part = parts[1].strip() if len(parts) > 1 else ""

        # 模板变量替换
        for key, value in kwargs.items():
            system_part = system_part.replace(f"{{{key}}}", str(value))
            user_part = user_part.replace(f"{{{key}}}", str(value))

        return system_part, user_part

    @abstractmethod
    def _default_prompt(self) -> tuple[str, str]:
        """默认Prompt（当模板文件不存在时使用）"""
        return ("", "")
