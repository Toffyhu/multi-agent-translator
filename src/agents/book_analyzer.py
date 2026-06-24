"""① 全书分析Agent — 脉络梳理 + 伏笔标记 + 文本类型判定

核心职责（只做三件事，不做其他）：
1. 梳理全书内容大纲、核心逻辑脉络
2. 标记章节关联关系、前后呼应的关键内容/伏笔
3. 判定文本类型（小说/学术/法律/技术等）

输入：完整原文
输出：BookAnalysis报告 → 写入全局资产库
"""

from __future__ import annotations

import json
from typing import Optional

from src.agents.base import BaseAgent, AgentResult, AgentContext
from src.assets.schema import (
    BookAnalysis, ChapterMeta, ForeshadowingLink, AssetStore,
)
from src.models.registry import ModelRegistry


class BookAnalyzerAgent(BaseAgent):
    """全书分析Agent — 长文本理解+结构化分析"""

    agent_name = "book_analyzer"
    agent_description = "全书结构分析：脉络梳理、伏笔标记、文本类型判定"

    def execute(
        self,
        full_text: str,
        title: str = "",
        author: str = "",
        source_lang: str = "en",
        target_lang: str = "zh",
        chapter_delimiters: Optional[list[str]] = None,
    ) -> AgentResult:
        """
        分析全书结构。

        Args:
            full_text: 完整原文文本
            title: 书名
            author: 作者
            source_lang: 源语言
            target_lang: 目标语言
            chapter_delimiters: 章节分隔标记列表（如 ["Chapter", "第.*章"]）

        Returns:
            AgentResult.data 包含 BookAnalysis 的字典
        """
        # 1. 切分章节
        chapters_raw = self._split_chapters(full_text, chapter_delimiters)

        # 2. 调用LLM分析全书
        system_prompt, user_prompt = self._build_analysis_prompt(
            full_text, title, author, source_lang, target_lang, chapters_raw
        )

        response_text, context = self._call_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=8192,
        )

        # 3. 解析LLM输出为结构化数据
        try:
            analysis = self._parse_response(response_text, title, author, source_lang, target_lang)
        except Exception as e:
            return AgentResult(
                success=False,
                data={"raw_response": response_text},
                context=context,
                error=f"解析LLM输出失败: {e}",
            )

        # 4. 写入全局资产库
        self.assets.set_book_analysis(analysis)

        return AgentResult(
            success=True,
            data=analysis.model_dump(),
            context=context,
            warnings=self._check_completeness(analysis, chapters_raw),
        )

    def _split_chapters(
        self, text: str, delimiters: Optional[list[str]] = None
    ) -> list[dict]:
        """切分章节（简化版，生产环境可扩展为更智能的切分算法）"""
        if not delimiters:
            delimiters = [
                r"(?:Chapter|CH\b|第[一二三四五六七八九十百千\d]+[章节回])",
                r"\n{2,}(?=[A-Z][A-Za-z\s]{3,50}\n)",
            ]

        import re

        chapters = []
        lines = text.split("\n")
        current_chapter = {"id": 1, "title": "第1章", "content": ""}

        for line in lines:
            is_new_chapter = any(re.match(d, line.strip()) for d in delimiters)
            if is_new_chapter and current_chapter["content"].strip():
                chapters.append(current_chapter)
                current_chapter = {
                    "id": len(chapters) + 1,
                    "title": line.strip()[:50],
                    "content": line + "\n",
                }
            else:
                current_chapter["content"] += line + "\n"

        if current_chapter["content"].strip():
            chapters.append(current_chapter)

        return chapters

    def _build_analysis_prompt(
        self,
        full_text: str,
        title: str,
        author: str,
        source_lang: str,
        target_lang: str,
        chapters: list[dict],
    ) -> tuple[str, str]:
        """构建LLM分析Prompt"""

        # 限制发送给LLM的文本长度（长文本取头尾+每章开头）
        max_chars = 80000
        if len(full_text) > max_chars:
            truncated = full_text[:max_chars // 2] + "\n\n... [中间省略] ...\n\n" + full_text[-max_chars // 2:]
        else:
            truncated = full_text

        chapter_summary = "\n".join(
            f"  第{c['id']}章: {c['title']} (约{len(c['content'])}字)"
            for c in chapters
        )

        system = f"""你是一位资深的文学编辑和文本分析专家。

你的任务是对一部即将翻译的书籍进行**全书结构分析**，为后续翻译工作提供全局上下文。

你需要完成：
1. **内容大纲**：梳理全书章节结构，概括每章核心内容
2. **逻辑脉络**：识别贯穿全书的核心线索、主题、论证链条
3. **伏笔关联**：标记前后呼应的关键内容——小说中的伏笔与回收、学术著作中的概念前后沿用
4. **人物/事件时间线**：如果是叙事文本，梳理核心人物状态变化和事件时间线
5. **文本类型判定**：判断文本类型（小说/学术专著/法律文本/技术文档/散文等）

你的输出必须是严格的JSON格式，结构如下：
{{
  "summary": "全书300字以内摘要",
  "text_type": "文本类型",
  "structure": ["第1章: xxx", "第2章: yyy"],
  "logic_threads": ["核心线索1: xxx", "核心线索2: yyy"],
  "chapters": [
    {{
      "chapter_id": 1,
      "title": "章节标题",
      "word_count": 字数,
      "character_states": {{}},
      "key_events": [],
      "foreshadowing_in": [],
      "foreshadowing_out": []
    }}
  ],
  "foreshadowing_graph": [
    {{
      "source_chapter": 章号,
      "source_context": "原文上下文",
      "target_chapter": 章号,
      "relation_type": "callback|reuse|parallel",
      "notes": "说明"
    }}
  ]
}}

注意事项：
- 只输出JSON，不要有任何解释或额外文字
- 源语言: {source_lang}，目标语言: {target_lang}
- 伏笔关系务必准确标注章节编号，不要猜测"""

        user = f"""请分析以下书籍：

书名: {title or "未指定"}
作者: {author or "未指定"}
源语言: {source_lang} → 目标语言: {target_lang}
总章节数: {len(chapters)}

章节概览:
{chapter_summary}

━━━━━━ 原文内容 ━━━━━━

{truncated}

━━━━━━ 分析要求 ━━━━━━

请输出上述JSON格式的全书分析报告。特别注意：
1. 跨章节的伏笔和呼应关系
2. 核心概念在全书中出现和演化的轨迹
3. 人物状态在章节间的变化"""

        return system, user

    def _parse_response(
        self, response: str, title: str, author: str, source_lang: str, target_lang: str
    ) -> BookAnalysis:
        """解析LLM JSON响应"""
        # 清理响应文本，提取JSON部分
        text = response.strip()
        if text.startswith("```"):
            # 去除markdown代码块标记
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])

        data = json.loads(text)

        # 构建ChapterMeta列表（容错处理LLM输出不规范的字段）
        chapters = []
        for ch in data.get("chapters", []):
            foreshadowing_in = []
            for f in ch.get("foreshadowing_in", []):
                if isinstance(f, dict):
                    try:
                        foreshadowing_in.append(ForeshadowingLink(**f))
                    except Exception:
                        pass  # 跳过格式不正确的记录
            foreshadowing_out = []
            for f in ch.get("foreshadowing_out", []):
                if isinstance(f, dict):
                    try:
                        foreshadowing_out.append(ForeshadowingLink(**f))
                    except Exception:
                        pass
            chapters.append(ChapterMeta(
                chapter_id=ch["chapter_id"],
                title=ch.get("title", ""),
                word_count=ch.get("word_count", 0),
                character_states=ch.get("character_states", {}),
                key_events=ch.get("key_events", []),
                foreshadowing_in=foreshadowing_in,
                foreshadowing_out=foreshadowing_out,
            ))

        # 构建全局伏笔关系图（容错处理）
        foreshadowing_graph = []
        for f in data.get("foreshadowing_graph", []):
            if isinstance(f, dict):
                try:
                    foreshadowing_graph.append(ForeshadowingLink(**f))
                except Exception:
                    pass

        return BookAnalysis(
            title=title,
            author=author,
            source_lang=source_lang,
            target_lang=target_lang,
            text_type=data.get("text_type", "未分类"),
            summary=data.get("summary", ""),
            structure=data.get("structure", []),
            logic_threads=data.get("logic_threads", []),
            chapters=chapters,
            foreshadowing_graph=foreshadowing_graph,
        )

    def _check_completeness(self, analysis: BookAnalysis, chapters: list[dict]) -> list[str]:
        """检查分析完整性"""
        warnings = []
        if len(analysis.chapters) != len(chapters):
            warnings.append(
                f"LLM分析到{len(analysis.chapters)}章，原文切分为{len(chapters)}章，可能有遗漏"
            )
        if not analysis.logic_threads:
            warnings.append("未提取到核心逻辑脉络，建议人工补充")
        if not analysis.foreshadowing_graph and analysis.text_type in ("小说", "散文", "叙事"):
            warnings.append("叙事文本未检测到伏笔关系，请确认是否遗漏")
        return warnings

    def _default_prompt(self) -> tuple[str, str]:
        return (
            "你是资深文学编辑，请分析书籍结构并输出JSON。",
            "请分析以下文本：{text}",
        )
