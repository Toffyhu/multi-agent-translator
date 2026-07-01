"""翻译流水线Agent基类 — 继承自agent-core，扩展翻译专属能力

v0.2.0: Agent基础设施从 agent_core 继承
- TranslatorAgent 继承 agent_core.BaseAgent，注入翻译专属知识
- PipelineMode 保留在翻译项目内
- BaseAgent 重导出为 TranslatorAgent（向后兼容）
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from agent_core.base import BaseAgent as CoreBaseAgent, AgentContext, AgentResult
from agent_core import ModelRegistry

from src.assets.schema import AssetStore


class PipelineMode(str, Enum):
    """翻译模式"""
    LITERARY  = "literary"
    LEGAL     = "legal"
    ACADEMIC  = "academic"

    @property
    def temperature_range(self) -> tuple[float, float]:
        return {
            PipelineMode.LITERARY:  (0.4, 0.7),
            PipelineMode.LEGAL:     (0.1, 0.2),
            PipelineMode.ACADEMIC:  (0.2, 0.4),
        }[self]

    @property
    def allow_rewrite(self) -> bool:
        return self == PipelineMode.LITERARY

    @property
    def require_terminology_lock(self) -> bool:
        return self in (PipelineMode.LEGAL, PipelineMode.ACADEMIC)

    @property
    def use_back_translation_verify(self) -> bool:
        return self == PipelineMode.LEGAL


class BaseAgent(CoreBaseAgent):
    """翻译流水线Agent基类 — 继承agent-core，注入翻译专属知识

    向后兼容：原名 BaseAgent，实际继承自 agent_core.BaseAgent。
    """

    def __init__(
        self,
        model_registry: Optional[ModelRegistry] = None,
        asset_store: Optional[AssetStore] = None,
        mode: Optional[str | PipelineMode] = None,
    ):
        if isinstance(mode, str):
            mode = PipelineMode(mode)
        elif mode is None:
            mode = PipelineMode.LITERARY
        super().__init__(
            model_registry=model_registry,
            asset_store=asset_store,
            mode=mode.value,
        )
        self.mode: PipelineMode = mode

    def _inject_knowledge(self, system_prompt: str) -> str:
        """注入翻译专属知识：共享知识库 + 模式专属知识"""
        if not self.assets or not isinstance(self.assets, AssetStore):
            return system_prompt

        knowledge_block = self.assets.get_knowledge_prompt_block()
        if knowledge_block and len(knowledge_block) > 50:
            system_prompt = f"""{system_prompt}

## 📚 共享翻译技能知识（所有Agent必须遵循）
{knowledge_block}"""

        if self.mode == PipelineMode.LEGAL:
            legal_block = self.assets.get_legal_knowledge_block()
            if legal_block:
                system_prompt = f"""{system_prompt}

## ⚖️ 法律翻译专业技能知识（法律模式强制遵循）
{legal_block}"""
        elif self.mode == PipelineMode.ACADEMIC:
            academic_block = self.assets.get_academic_knowledge_block()
            if academic_block:
                system_prompt = f"""{system_prompt}

## 🎓 学术翻译专业技能知识（学术模式强制遵循）
{academic_block}"""

        return system_prompt


__all__ = [
    "BaseAgent",
    "AgentContext",
    "AgentResult",
    "PipelineMode",
]
