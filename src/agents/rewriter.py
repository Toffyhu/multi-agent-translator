"""⑤ 再创作Agent — 用中文作家思维「重述」译文

核心职责：充当「顶级母语作家」，将直译稿改写为读不出翻译腔的纯正中文。

设计理念（来自Moxon's Master实验验证）：
- Version A（本章Translator直译）→ 保真、保留原文结构
- Version B（Rewriter再创作）→ 归化、用中文作家的方式重述
- 最终效果：A为骨，B为肉 → 主编融合输出

创作原则：
1. 保留所有事实、情节、人名、地名不变
2. 句子结构全面重构——不依赖原文句式
3. 用中文思维方式重述：重心在后、短句递进、话题链
4. 可以增删比喻、调整节奏——只要不扭曲原意
5. 如果不确定原文某个细节的准确含义，标记出来问主编
"""

from __future__ import annotations

from src.agents.base import BaseAgent, AgentResult


class RewriterAgent(BaseAgent):
    """再创作Agent — 以中文作家的身份「重述」译文"""

    agent_name = "rewriter"
    agent_description = "再创作：以中文作家思维重述译文，去除翻译腔"

    # ─── 再创作模式 ───

    def rewrite(self, source_text: str, translator_output: str, 
                style_hint: str = "让文字像中国作家写的一样自然") -> AgentResult:
        """
        对初译稿进行创造性重述。

        Args:
            source_text: 英文原文（参考用）
            translator_output: Translator的直译稿
            style_hint: 风格指引（如"鲁迅风""沈从文式""张爱玲笔调"）

        Returns:
            AgentResult: 再创作版译文
        """
        prompt = self._build_rewrite_prompt(source_text, translator_output, style_hint)
        
        text, ctx = self._call_llm(
            system_prompt="你是一位顶级的中国作家。你的任务不是翻译，而是「重述」——"
                         "读透原文情节后，用你的中文写作能力重新讲一遍这个故事。\n\n"
                         "核心原则：\n"
                         "1. 像作家一样写作，不是像翻译一样转换\n"
                         "2. 保留所有事实细节（人名、地名、事件顺序、数字），"
                         "但可以自由重组句式、调整节奏、替换比喻\n"
                         "3. 读起来要像原生中文——如果读出声来有「翻译腔」，改到没有为止\n"
                         "4. 可以适当增删修饰词、调整段落长短——只要不扭曲原意\n"
                         "5. 遇到原文有歧义的地方，发挥你的文学直觉做出选择，"
                         "并在括号中说明你的判断",
            user_prompt=prompt,
            temperature=0.7,  # 比直译更高温度，鼓励创造力
        )
        
        return AgentResult(
            success=True,
            data={
                "rewritten_text": text,
                "style": style_hint,
            },
            context=ctx,
        )

    def _build_rewrite_prompt(self, source_text: str, translator_output: str,
                              style_hint: str) -> str:
        return f"""以下是同一段落的两份材料：

## 英文原文（供理解用，不必逐句对照）

{source_text[:3000]}

## 初译稿（供参考。这是直译版本，保留了原文句式）

{translator_output[:3000]}

---
请按以下要求重述：

### 风格指引：{style_hint}

### 具体要求：
1. **通读**原文和初译稿，确保你完全理解了情节和细节
2. **合上原文**，用中文作家的思维重述这个故事
3. **保留以下内容不变**：所有人名、地名、数字、关键事实
4. **可以自由改变**：句子长短、段落划分、比喻说法、修饰词、语气轻重
5. **目标效果**：读起来像中国作家写的，看不出来是翻译的
6. **输出格式**：直接输出重述后的中文，不要加"翻译说明"或"创作说明"

开始重述："""

    def _default_prompt(self) -> tuple[str, str]:
        return (
            "你是一位顶级的中国作家，擅长用母语重述外国文学。",
            "请以中文作家的思维重新讲述以下段落。"
        )

    def execute(self, query: str = "") -> AgentResult:
        """兼容BaseAgent接口"""
        return self.rewrite(
            source_text="",
            translator_output=query,
            style_hint="让文字像中国作家写的一样自然",
        )
