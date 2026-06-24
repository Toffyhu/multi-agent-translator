"""多智能体翻译流水线 — Translation Pipeline

基于Hermes框架的出版级翻译质控系统。
5 Agent + 全局资产底座 + 五阶段流水线 + 术语校验引擎。

Author: WorkBuddy量化工场
"""

from src.assets.schema import AssetStore
from src.assets.term_enforcer import TermEnforcer
from src.models.registry import ModelRegistry
from src.pipeline.orchestrator import TranslationPipeline, PipelineStage, PipelineResult

__version__ = "0.1.0"

__all__ = [
    "AssetStore",
    "TermEnforcer",
    "ModelRegistry",
    "TranslationPipeline",
    "PipelineStage",
    "PipelineResult",
]
