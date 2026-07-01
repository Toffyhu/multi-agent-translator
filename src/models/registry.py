"""模型注册表 — 从 agent_core 继承，翻译项目直接使用

v0.2.0: 全部能力从 agent_core.ModelRegistry 继承，零额外逻辑。
"""

from agent_core.models.registry import ModelRegistry, ModelSpec

__all__ = ["ModelRegistry", "ModelSpec"]
