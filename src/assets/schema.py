"""全局翻译资产库 — 所有Agent的权威数据源

这是整条流水线的"宪法"，所有Agent只能读取遵循，无权私自修改。
更新必须通过术语风格制定Agent发起，经人工确认后生效。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════
# 数据结构定义
# ═══════════════════════════════════════════

class TermLevel(str, Enum):
    """术语强制等级"""
    MANDATORY = "mandatory"      # 强制统一：必须使用指定译法
    FLEXIBLE = "flexible"        # 灵活处理：允许Agent自主选择，但建议统一


class TermEntry(BaseModel):
    """单条术语定义"""
    source: str                              # 源语言术语
    target: str                              # 目标语言译法
    level: TermLevel = TermLevel.MANDATORY   # 强制等级
    domain: str = "general"                  # 所属领域
    notes: str = ""                          # 备注说明
    alternatives: list[str] = Field(default_factory=list)  # 备选译法
    version: int = 1                         # 版本号
    updated_at: str = ""                     # 更新时间


class TerminologyTable(BaseModel):
    """全局术语规范表"""
    source_lang: str
    target_lang: str
    entries: list[TermEntry] = Field(default_factory=list)
    version: int = 1
    created_at: str = ""
    updated_at: str = ""

    def get_mandatory_map(self) -> dict[str, str]:
        """返回强制统一术语的映射表 {source: target}"""
        return {e.source: e.target for e in self.entries if e.level == TermLevel.MANDATORY}

    def get_all_map(self) -> dict[str, str]:
        """返回全部术语映射表"""
        return {e.source: e.target for e in self.entries}


class StyleDimension(BaseModel):
    """风格五维拆解 — 单维度定义"""
    name: str                                         # 维度名称
    description: str                                   # 维度说明
    metrics: list[str] = Field(default_factory=list)  # 量化指标列表
    target_range: str = ""                            # 目标区间（如"平均句长25±5字"）
    reference_examples: list[dict] = Field(default_factory=list)  # 参考例句


class StyleManual(BaseModel):
    """风格执行手册 — 五维量化规则"""
    target_style: str                              # 目标风格描述（如"傅雷风格"）
    target_audience: str = ""                     # 目标读者画像
    dimensions: list[StyleDimension] = Field(default_factory=list)
    forbidden_patterns: list[str] = Field(default_factory=list)  # 禁止表述
    reference_works: list[str] = Field(default_factory=list)     # 参考标杆译作
    version: int = 1
    created_at: str = ""


class ForeshadowingLink(BaseModel):
    """伏笔-呼应关系"""
    source_chapter: int
    source_context: str
    target_chapter: int
    relation_type: str  # "callback" | "reuse" | "parallel"
    notes: str = ""


class ChapterMeta(BaseModel):
    """章节元数据"""
    chapter_id: int
    title: str
    word_count: int
    character_states: dict[str, str] = Field(default_factory=dict)  # 人物状态快照
    key_events: list[str] = Field(default_factory=list)
    foreshadowing_in: list[ForeshadowingLink] = Field(default_factory=list)   # 本章埋下的伏笔
    foreshadowing_out: list[ForeshadowingLink] = Field(default_factory=list)  # 本章回应的伏笔


class BookAnalysis(BaseModel):
    """全书分析报告"""
    title: str
    author: str
    source_lang: str
    target_lang: str
    text_type: str                            # 文本类型（小说/学术/法律/技术等）
    summary: str                              # 全书摘要
    structure: list[str] = Field(default_factory=list)  # 结构大纲
    logic_threads: list[str] = Field(default_factory=list)  # 核心逻辑脉络
    chapters: list[ChapterMeta] = Field(default_factory=list)
    foreshadowing_graph: list[ForeshadowingLink] = Field(default_factory=list)  # 全局伏笔关系图
    created_at: str = ""


# ═══════════════════════════════════════════
# 共享知识库 — 所有Agent可读取的技能知识
# ═══════════════════════════════════════════

class KnowledgeChapter(BaseModel):
    """知识章节"""
    title: str
    content: str


class SharedKnowledge(BaseModel):
    """共享翻译知识库 — 所有Agent的公共技能知识"""
    name: str = "翻译通用技能知识库"
    version: str = "1.0.0"
    description: str = ""
    chapters: list[KnowledgeChapter] = Field(default_factory=list)

    def get_full_text(self) -> str:
        """获取完整知识文本（用于注入Agent prompt）"""
        parts = [f"# {self.description}"]
        for ch in self.chapters:
            parts.append(f"\n## {ch.title}\n{ch.content}")
        return "\n".join(parts)

    def search(self, keyword: str) -> list[dict]:
        """按关键词搜索知识内容"""
        results = []
        for ch in self.chapters:
            if keyword.lower() in ch.title.lower() or keyword.lower() in ch.content.lower():
                results.append({"chapter": ch.title, "content": ch.content[:500]})
        return results


# ═══════════════════════════════════════════
# 资产库管理器
# ═══════════════════════════════════════════

class AssetStore:
    """全局翻译资产库 — 单例模式，所有Agent的唯一数据源"""
    _instance: Optional["AssetStore"] = None

    def __new__(cls, base_dir: str = "./assets_store"):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, base_dir: str = "./assets_store"):
        if self._initialized:
            return
        self.base_dir = Path(base_dir)
        self.source_dir = self.base_dir / "source"
        self.term_dir = self.base_dir / "terminology"
        self.style_dir = self.base_dir / "style"
        self.reference_dir = self.base_dir / "reference"
        self.knowledge_dir = self.base_dir / "knowledge"
        self._ensure_dirs()
        self.book_analysis: Optional[BookAnalysis] = None
        self.terminology: Optional[TerminologyTable] = None
        self.style_manual: Optional[StyleManual] = None
        self.shared_knowledge: Optional[SharedKnowledge] = None
        self.legal_knowledge_text: Optional[str] = None  # 法律模式专属知识（从skill.json/md加载）
        self._initialized = True

    def _ensure_dirs(self):
        for d in [self.source_dir, self.term_dir, self.style_dir, self.reference_dir, self.knowledge_dir]:
            d.mkdir(parents=True, exist_ok=True)

    # ─── 读取接口（所有Agent调用） ───

    def get_book_analysis(self) -> Optional[BookAnalysis]:
        return self.book_analysis

    def get_terminology(self) -> Optional[TerminologyTable]:
        return self.terminology

    def get_style_manual(self) -> Optional[StyleManual]:
        return self.style_manual

    def get_context_for_chapter(self, chapter_id: int) -> dict:
        """为分章翻译Agent构建上下文注入数据"""
        if not self.book_analysis:
            return {}
        chapter = next((c for c in self.book_analysis.chapters if c.chapter_id == chapter_id), None)
        if not chapter:
            return {}
        return {
            "chapter_meta": chapter.model_dump(),
            "foreshadowing_in": [f.model_dump() for f in chapter.foreshadowing_in],
            "foreshadowing_out": [f.model_dump() for f in chapter.foreshadowing_out],
            "global_foreshadowing": [
                f.model_dump() for f in self.book_analysis.foreshadowing_graph
                if f.target_chapter == chapter_id or f.source_chapter == chapter_id
            ],
        }

    # ─── 更新接口（仅供术语风格Agent + 主编终审调用，需审批） ───

    def set_book_analysis(self, analysis: BookAnalysis):
        analysis.created_at = datetime.now().isoformat()
        self.book_analysis = analysis
        self._persist("source/book_analysis.json", analysis.model_dump())

    def set_terminology(self, terminology: TerminologyTable):
        terminology.updated_at = datetime.now().isoformat()
        self.terminology = terminology
        self._persist("terminology/terms.json", terminology.model_dump())

    def set_style_manual(self, manual: StyleManual):
        manual.created_at = manual.created_at or datetime.now().isoformat()
        self.style_manual = manual
        self._persist("style/manual.json", manual.model_dump())

    def update_term_entry(self, source: str, new_target: str, notes: str = ""):
        """单条术语更新（主编终审专用）"""
        if not self.terminology:
            raise ValueError("术语表未初始化")
        for entry in self.terminology.entries:
            if entry.source == source:
                entry.version += 1
                entry.target = new_target
                entry.notes = notes
                entry.updated_at = datetime.now().isoformat()
                break
        else:
            self.terminology.entries.append(TermEntry(
                source=source, target=new_target,
                version=1, notes=notes,
                updated_at=datetime.now().isoformat(),
            ))
        self.terminology.updated_at = datetime.now().isoformat()
        self.terminology.version += 1
        self._persist("terminology/terms.json", self.terminology.model_dump())

    def _persist(self, relative_path: str, data: dict):
        path = self.base_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ─── 共享知识库（所有Agent可读） ───

    def load_knowledge_base(self, md_path: str):
        """从Markdown文件加载共享知识库"""
        text = Path(md_path).read_text(encoding="utf-8")
        chapters = []
        current_title = ""
        current_content = []
        for line in text.split("\n"):
            if line.startswith("## "):
                if current_title:
                    chapters.append(KnowledgeChapter(title=current_title, content="\n".join(current_content).strip()))
                current_title = line.replace("## ", "").strip()
                current_content = []
            else:
                current_content.append(line)
        if current_title:
            chapters.append(KnowledgeChapter(title=current_title, content="\n".join(current_content).strip()))
        self.shared_knowledge = SharedKnowledge(chapters=chapters)
        self._persist("knowledge/knowledge_base.json", self.shared_knowledge.model_dump())

    def get_knowledge_base(self) -> Optional[SharedKnowledge]:
        """获取共享知识库（所有Agent调用）"""
        return self.shared_knowledge

    def get_knowledge_prompt_block(self) -> str:
        """获取可注入到Agent Prompt的知识块"""
        kb = self.shared_knowledge
        if not kb:
            return ""
        return kb.get_full_text()

    # ─── 法律模式专属知识加载 ───

    def load_legal_knowledge(self, md_path: str):
        """加载法律翻译专业知识（从 Markdown 或 JSON 文件）"""
        import json
        from pathlib import Path
        path = Path(md_path)
        if not path.exists():
            return
        text = path.read_text(encoding="utf-8")
        # 如果是 JSON 技能文件，提取关键信息转为 Markdown
        if md_path.endswith('.json'):
            try:
                data = json.loads(text)
                lines = []
                t = data.get("terminology", {})
                for cat_name, items in t.items():
                    if isinstance(items, dict):
                        lines.append(f"### {cat_name}")
                        for k, v in items.items():
                            if isinstance(v, dict):
                                zh = v.get("译法", "")
                                desc = v.get("说明", "")
                                lines.append(f"- {k} → {zh} | {desc}")
                            elif isinstance(v, str):
                                lines.append(f"- {k} → {v}")
                        lines.append("")
                rules = data.get("defect_rules", [])
                if rules:
                    lines.append("### 法律翻译缺陷检测规则")
                    for r in rules:
                        if isinstance(r, dict):
                            lines.append(f"- [{r.get('severity','')}] {r.get('name','')}: {r.get('target','')}")
                    lines.append("")
                qc = data.get("quality_checklist", {})
                for level in ("must_pass", "should_pass"):
                    items = qc.get(level, [])
                    if items:
                        lines.append(f"### {level}")
                        for it in items:
                            lines.append(f"- {it}")
                        lines.append("")
                text = "\n".join(lines)
            except:
                pass
        self.legal_knowledge_text = text

    def get_legal_knowledge_block(self) -> str:
        """获取法律翻译专属知识块（法律模式Agent自动注入）"""
        return self.legal_knowledge_text or ""

    # ─── 加载已持久化的资产 ───

    def load_all(self):
        """从磁盘加载所有已保存的资产"""
        for name, cls, attr in [
            ("source/book_analysis.json", BookAnalysis, "book_analysis"),
            ("terminology/terms.json", TerminologyTable, "terminology"),
            ("style/manual.json", StyleManual, "style_manual"),
            ("knowledge/knowledge_base.json", SharedKnowledge, "shared_knowledge"),
        ]:
            path = self.base_dir / name
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                setattr(self, attr, cls(**data))
