"""流水线调度器 v2 — 场景自适应多Agent翻译流水线

核心升级：支持三种翻译模式（文学/法律/学术），同一套Agent根据模式自动切换职能。

模式切换规则：
- LITERARY（文学）: 三版融合（V1直译→V2再创→Fused融合），温度 0.4-0.7
- LEGAL（法律）  : 精度优先（V1直译→回译验证），温度 0.1-0.2，禁用再创作
- ACADEMIC（学术）: 混合策略（V1直译→引用校验→领域润色），温度 0.2-0.4

设计原则：
- Agent数量不变（7个），职能随模式自适应
- 主编终审内置左校对/右校对/正文编辑三合一审校
- 争议项向上抛，不外合并
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable

from src.agents.base import AgentResult, PipelineMode
from src.agents.book_analyzer import BookAnalyzerAgent
from src.agents.term_stylist import TermStylistAgent
from src.agents.chapter_translator import ChapterTranslatorAgent
from src.agents.chief_editor import ChiefEditorAgent
from src.agents.fact_checker import FactCheckerAgent
from src.agents.rewriter import RewriterAgent
from src.assets.schema import AssetStore
from src.assets.term_enforcer import TermEnforcer
from src.models.registry import ModelRegistry
from src.utils.text_splitter import safe_chapter_context
from src.utils.post_processor import post_process_delivery
from src.utils.version_tracker import VersionTracker


# ═══════════════════════════════════════════
# PipelineMode 和 PipelineStage
# ═══════════════════════════════════════════


class PipelineStage(str, Enum):
    """流水线阶段"""
    PREPARATION = "preparation"
    TRANSLATION = "translation"
    REWRITE     = "rewrite"
    FUSION      = "fusion"
    EDITING     = "editing"        # 主编终审（内置左/右/编三合一）
    REVIEW      = "review"
    DELIVERY    = "delivery"


@dataclass
class StageResult:
    stage: PipelineStage
    success: bool
    data: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    rollback_triggered: bool = False


@dataclass
class PipelineResult:
    success: bool
    stages: dict[PipelineStage, StageResult] = field(default_factory=dict)
    total_duration_seconds: float = 0.0
    total_cost_usd: float = 0.0
    final_output: Optional[dict] = None
    summary: str = ""


class TranslationPipeline:
    """
    场景自适应翻译流水线调度器 v2

    使用方式：
    ```python
    pipeline = TranslationPipeline(mode=PipelineMode.LEGAL)
    result = await pipeline.run(
        source_text=contract_text,
        title="采购合同",
        author="甲方/乙方",
    )
    ```
    """

    def __init__(
        self,
        mode: PipelineMode = PipelineMode.LITERARY,
        config_path: str = "config/default.yaml",
        models_config_path: str = "config/models.yaml",
        assets_dir: str = "./assets_store",
    ):
        self.mode = mode
        self.registry = ModelRegistry(models_config_path)
        self.assets = AssetStore(assets_dir)
        self.tracker = VersionTracker()

        # 按模式加载专属知识
        if self.mode.value == "legal":
            import os
            for candidate in [
                "skills/legal_translation.skill.json",
                "skills/legal_translation_knowledge.md",
            ]:
                path = os.path.join(os.path.dirname(assets_dir), candidate)
                if os.path.exists(path):
                    self.assets.load_legal_knowledge(path)
                    self.tracker.log("init", f"已加载法律翻译知识: {candidate}")
                    break
        elif self.mode.value == "academic":
            import os
            for candidate in [
                "skills/academic_translation.skill.json",
                "skills/academic_translation_knowledge.md",
            ]:
                path = os.path.join(os.path.dirname(assets_dir), candidate)
                if os.path.exists(path):
                    self.assets.load_academic_knowledge(path)
                    self.tracker.log("init", f"已加载学术翻译知识: {candidate}")
                    break

        self._book_analyzer: Optional[BookAnalyzerAgent] = None
        self._term_stylist: Optional[TermStylistAgent] = None
        self._translator_cluster: list[ChapterTranslatorAgent] = []
        self._chief_editor: Optional[ChiefEditorAgent] = None
        self._fact_checker: Optional[FactCheckerAgent] = None
        self._term_enforcer: Optional[TermEnforcer] = None

    # ═══════════════════════════════════════════
    # 属性（延迟初始化，注入 mode）
    # ═══════════════════════════════════════════

    @property
    def book_analyzer(self) -> BookAnalyzerAgent:
        if self._book_analyzer is None:
            self._book_analyzer = BookAnalyzerAgent(
                self.registry, self.assets, mode=self.mode,
            )
        return self._book_analyzer

    @property
    def term_stylist(self) -> TermStylistAgent:
        if self._term_stylist is None:
            self._term_stylist = TermStylistAgent(
                self.registry, self.assets, mode=self.mode,
            )
        return self._term_stylist

    @property
    def chief_editor(self) -> ChiefEditorAgent:
        if self._chief_editor is None:
            self._chief_editor = ChiefEditorAgent(
                self.registry, self.assets, mode=self.mode,
            )
        return self._chief_editor

    @property
    def fact_checker(self) -> FactCheckerAgent:
        if self._fact_checker is None:
            self._fact_checker = FactCheckerAgent(
                self.registry, self.assets, mode=self.mode,
            )
        return self._fact_checker

    @property
    def term_enforcer(self) -> TermEnforcer:
        if self._term_enforcer is None:
            self._term_enforcer = TermEnforcer(self.assets)
        return self._term_enforcer

    # ═══════════════════════════════════════════
    # 流水线主入口
    # ═══════════════════════════════════════════

    async def run(
        self,
        source_text: str,
        title: str = "",
        author: str = "",
        source_lang: str = "en",
        target_lang: str = "zh",
        target_style: str = "",
        chapters: Optional[list[dict]] = None,
        on_stage_complete: Optional[Callable] = None,
        require_human_approval: bool = True,
        use_rewrite: Optional[bool] = None,  # None→根据mode自动决定
    ) -> PipelineResult:
        """
        执行完整翻译流水线。

        use_rewrite 默认跟随模式自动决定：
        - LITERARY → True（启用三版融合）
        - LEGAL/ACADEMIC → False（禁用再创作）
        """
        start_time = time.time()
        stages: dict[PipelineStage, StageResult] = {}
        total_cost = 0.0

        # 自动决定是否启用三版融合
        if use_rewrite is None:
            use_rewrite = self.mode.allow_rewrite

        self.tracker.log("init", f"流水线启动 | 模式: {self.mode.value} | 再创作: {use_rewrite}")

        # ─── 阶段0：前置筹备 ───
        prep_result = await self._run_preparation(
            source_text, title, author, source_lang, target_lang,
            target_style, chapters, require_human_approval,
        )
        stages[PipelineStage.PREPARATION] = prep_result
        total_cost += self._extract_cost(prep_result)
        if on_stage_complete:
            on_stage_complete(PipelineStage.PREPARATION, prep_result)
        if not prep_result.success:
            return PipelineResult(
                success=False, stages=stages,
                total_duration_seconds=time.time() - start_time,
                summary="前置筹备失败，流水线中断",
            )

        chapters = prep_result.data.get("chapters", chapters or [])

        # ─── 阶段1：初译 ───
        trans_result = await self._run_translation(chapters, source_lang, target_lang)
        stages[PipelineStage.TRANSLATION] = trans_result
        total_cost += self._extract_cost(trans_result)
        if on_stage_complete:
            on_stage_complete(PipelineStage.TRANSLATION, trans_result)

        # ─── 阶段1.5：再创作（仅文学模式） ───
        rewrite_result = None
        if use_rewrite:
            rewrite_result = self._run_rewrite(
                chapters,
                trans_result.data.get("translations", []),
                source_lang,
            )
            stages[PipelineStage.REWRITE] = rewrite_result
            total_cost += self._extract_cost(rewrite_result)
            if on_stage_complete:
                on_stage_complete(PipelineStage.REWRITE, rewrite_result)

        # ─── 阶段1.8：融合/验证 ───
        if use_rewrite and rewrite_result and rewrite_result.success:
            # 文学模式：创意融合
            fusion_result = self._run_fusion(
                chapters, trans_result.data.get("translations", []),
                rewrite_result.data.get("rewrites", []), target_lang,
            )
            stages[PipelineStage.FUSION] = fusion_result
            total_cost += self._extract_cost(fusion_result)
            translated_chapters = fusion_result.data.get("fused_chapters", [])
            translated_texts = [tc.get("fused", tc.get("translation", ""))
                              for tc in translated_chapters]
        elif self.mode.use_back_translation_verify:
            # 法律模式：回译验证
            fusion_result = self._run_back_translation_verify(
                trans_result.data.get("translations", []),
                chapters, source_lang, target_lang,
            )
            stages[PipelineStage.FUSION] = fusion_result
            total_cost += self._extract_cost(fusion_result)
            translated_chapters = fusion_result.data.get("verified_chapters", [])
            translated_texts = [tc.get("verified", tc.get("translation", ""))
                              for tc in translated_chapters]
        else:
            translated_chapters = trans_result.data.get("translations", [])
            translated_texts = [tc.get("translation", "") for tc in translated_chapters]

        # ─── 阶段2：主编终审（内置左/右/编三合一审核）───
        edit_result = await self._run_editing(
            translated_chapters, source_lang, target_lang,
        )
        stages[PipelineStage.EDITING] = edit_result
        total_cost += self._extract_cost(edit_result)
        if on_stage_complete:
            on_stage_complete(PipelineStage.EDITING, edit_result)

        # ─── 阶段3：评审 + 事实核查（并行）───
        review_result, fact_result = await self._run_review_and_fact_check(
            edit_result.data.get("edited_chapters", []),
        )
        stages[PipelineStage.REVIEW] = review_result
        total_cost += self._extract_cost(review_result) + self._extract_cost(fact_result)
        if on_stage_complete:
            on_stage_complete(PipelineStage.REVIEW, review_result)

        review_data = review_result.data.copy()
        review_data["fact_check"] = fact_result.data

        # ─── 阶段4：交付 ───
        delivery_result = self._run_delivery(
            chapters=edit_result.data.get("edited_chapters", []),
            review_report=review_data,
            fact_errors=fact_result.data.get("all_errors", []),
        )
        stages[PipelineStage.DELIVERY] = delivery_result

        total_duration = time.time() - start_time
        success = all(s.success for s in stages.values())
        summary = self._build_summary(stages, total_cost, total_duration)

        return PipelineResult(
            success=success,
            stages=stages,
            total_duration_seconds=total_duration,
            total_cost_usd=total_cost,
            final_output=delivery_result.data,
            summary=summary,
        )

    # ═══════════════════════════════════════════
    # 各阶段实现
    # ═══════════════════════════════════════════

    async def _run_preparation(
        self, source_text: str, title: str, author: str,
        source_lang: str, target_lang: str, target_style: str,
        chapters: Optional[list[dict]], require_approval: bool,
    ) -> StageResult:
        """阶段0：前置筹备 — 全书分析 + 术语风格制定"""
        t0 = time.time()

        self.tracker.log("preparation", f"开始前置筹备 | 模式: {self.mode.value}")

        # 全书分析（模式感知：产出不同维度的分析报告）
        analysis_result = self.book_analyzer.execute(
            full_text=source_text, title=title, author=author,
            source_lang=source_lang, target_lang=target_lang,
        )
        if not analysis_result.success:
            return StageResult(
                stage=PipelineStage.PREPARATION, success=False,
                errors=[analysis_result.error],
                duration_seconds=time.time() - t0,
            )

        # 术语与风格制定（模式感知：文学→柔性词表，法律→刚性锁定表）
        style_result = self.term_stylist.execute(
            source_lang=source_lang, target_lang=target_lang,
            target_style=target_style or self._default_style_for_mode(),
        )
        if not style_result.success:
            return StageResult(
                stage=PipelineStage.PREPARATION, success=False,
                errors=[style_result.error],
                duration_seconds=time.time() - t0,
            )

        book_analysis = self.assets.get_book_analysis()
        chapters_info = []
        if book_analysis:
            chapters_info = [
                {"chapter_id": c.chapter_id, "title": c.title, "word_count": c.word_count}
                for c in book_analysis.chapters
            ]

        return StageResult(
            stage=PipelineStage.PREPARATION, success=True,
            data={
                "book_analysis": book_analysis.model_dump() if book_analysis else {},
                "terminology": style_result.data.get("terminology", {}),
                "style_manual": style_result.data.get("style_manual", {}),
                "chapters": chapters_info,
                "approval_pending": require_approval,
                "mode": self.mode.value,
            },
            duration_seconds=time.time() - t0,
        )

    def _default_style_for_mode(self) -> str:
        """根据模式返回默认风格描述"""
        return {
            PipelineMode.LITERARY: "专业出版级翻译风格",
            PipelineMode.LEGAL: "法律文书翻译 — 术语锁定、条款一致、零歧义",
            PipelineMode.ACADEMIC: "学术文献翻译 — 领域术语准确、引用完整、论证清晰",
        }[self.mode]

    async def _run_translation(
        self, chapters: list[dict], source_lang: str, target_lang: str,
    ) -> StageResult:
        """阶段1：并行初译 — 温度随模式自动调整"""
        t0 = time.time()

        if not chapters:
            return StageResult(
                stage=PipelineStage.TRANSLATION, success=False,
                errors=["无章节数据"], duration_seconds=time.time() - t0,
            )

        self.tracker.log("translation", f"并行翻译 {len(chapters)} 章 | 模式: {self.mode.value}")

        tasks = []
        for i, ch in enumerate(chapters):
            translator = ChapterTranslatorAgent(self.registry, self.assets, mode=self.mode)
            tasks.append(self._translate_chapter_async(
                translator, ch, i, chapters, source_lang, target_lang,
            ))

        translations = await asyncio.gather(*tasks)
        total = sum(1 for t in translations if t)

        self.tracker.log("translation", f"初译完成: {total}/{len(chapters)} 章")
        return StageResult(
            stage=PipelineStage.TRANSLATION, success=True,
            data={"translations": translations, "chapters_translated": total},
            duration_seconds=time.time() - t0,
        )

    async def _translate_chapter_async(
        self, translator: ChapterTranslatorAgent, chapter: dict,
        index: int, all_chapters: list[dict],
        source_lang: str, target_lang: str,
    ) -> dict:
        """单章异步翻译"""
        chapter_id = chapter.get("chapter_id", index + 1)
        prev_text = all_chapters[index - 1].get("content", "") if index > 0 else ""
        next_text = all_chapters[index + 1].get("content", "") if index < len(all_chapters) - 1 else ""
        prev_tail, next_head = safe_chapter_context(prev_text, next_text, max_chars=500)

        result = translator.execute(
            chapter_id=chapter_id,
            chapter_text=chapter.get("content", ""),
            chapter_title=chapter.get("title", ""),
            source_lang=source_lang,
            target_lang=target_lang,
            prev_chapter_tail=prev_tail,
            next_chapter_head=next_head,
        )
        return result.data if result.success else {"chapter_id": chapter_id, "error": result.error}

    async def _run_editing(
        self, translations: list[dict], source_lang: str, target_lang: str,
    ) -> StageResult:
        """
        阶段2：主编终审 — 内置左校对/右校对/正文编辑三合一审核。

        根据模式自动启用不同子职能：
        - 所有模式：统稿润色（基础职能）
        - 法律模式额外：术语强制匹配扫描、条款结构校验
        - 学术模式额外：引用完整性检查
        """
        t0 = time.time()

        if not translations:
            return StageResult(
                stage=PipelineStage.EDITING, success=False,
                errors=["无翻译结果"], duration_seconds=time.time() - t0,
            )

        self.tracker.log("editing", f"主编终审 | 模式: {self.mode.value}")

        # 主编统稿（基础职能，所有模式）
        result = self.chief_editor.draft_mode(
            chapter_translations=translations,
            source_lang=source_lang,
            target_lang=target_lang,
        )
        edited_chapters = result.data.get("edited_chapters", [])

        # 三合一审核（模式感知）
        proofing_report = self.chief_editor.integrated_proofread(
            edited_chapters=edited_chapters,
            source_texts=self._get_source_texts_from_chapters(translations),
        )

        # 合并审核发现到交付数据
        data = {
            "edited_chapters": edited_chapters,
            "style_report": result.data.get("style_report", {}),
            "violations_found": result.data.get("violations_found", 0),
            "proofing_report": proofing_report.data if proofing_report.success else {},
        }

        return StageResult(
            stage=PipelineStage.EDITING, success=True, data=data,
            duration_seconds=time.time() - t0,
        )

    def _get_source_texts_from_chapters(self, translations: list[dict]) -> list[str]:
        """从翻译章节提取原文文本"""
        # 尝试从章节数据中获取原始文本
        sources = []
        for t in translations:
            src = t.get("source_text", t.get("original", ""))
            if not src:
                # Fallback: 原文可能存储在其他字段
                src = t.get("chapter_text", t.get("raw_source", ""))
            sources.append(src)
        return sources

    def _run_rewrite(
        self, chapters: list[dict], translations: list[dict], source_lang: str,
    ) -> StageResult:
        """阶段1.5：再创作 — 仅文学模式启用"""
        t0 = time.time()

        if not translations:
            return StageResult(
                stage=PipelineStage.REWRITE, success=False,
                errors=["无翻译结果"], duration_seconds=time.time() - t0,
            )

        rewriter = RewriterAgent(self.registry, self.assets, mode=self.mode)
        rewrites = []

        for chapter, trans_item in zip(chapters, translations):
            chapter_text = chapter.get("text", "")
            trans_text = trans_item.get("translation", "")
            if not trans_text:
                continue

            result = rewriter.rewrite(
                source_text=chapter_text,
                translator_output=trans_text,
                style_hint="让文字像中国作家写的一样自然",
            )
            rewrites.append({
                "chapter_id": trans_item.get("chapter_id"),
                "title": trans_item.get("title", ""),
                "original": trans_text,
                "rewrite": result.data.get("rewritten_text", ""),
            })

        return StageResult(
            stage=PipelineStage.REWRITE, success=True,
            data={"rewrites": rewrites},
            duration_seconds=time.time() - t0,
        )

    def _run_fusion(
        self, chapters: list[dict], translations: list[dict],
        rewrites: list[dict], target_lang: str,
    ) -> StageResult:
        """阶段1.8：创意融合 — V1+V2→Fused"""
        t0 = time.time()

        if not translations or not rewrites:
            return StageResult(
                stage=PipelineStage.FUSION, success=False,
                errors=["缺少直译版或再创版"], duration_seconds=time.time() - t0,
            )

        fused_chapters = []
        for idx, trans_item in enumerate(translations):
            chapter_id = trans_item.get("chapter_id", idx + 1)
            v1 = trans_item.get("translation", "")

            rw = next((r for r in rewrites if r.get("chapter_id") == chapter_id), None)
            v2 = rw.get("rewrite", "") if rw else ""

            if not v1 or not v2:
                fused_chapters.append({
                    "chapter_id": chapter_id,
                    "translation": v1, "fused": v1 or v2,
                    "fusion_note": "仅单版本可用",
                })
                continue

            result = self.chief_editor.fusion_mode(
                chapter_id=chapter_id,
                chapter_title=trans_item.get("title", ""),
                v1_direct=v1, v2_rewrite=v2,
                style_note=f"目标语言: {target_lang}",
            )
            fused_chapters.append({
                "chapter_id": chapter_id,
                "translation": v1, "rewrite": v2,
                "fused": result.data.get("fused_text", v1),
                "fusion_note": "V1直译 + V2再创 → 主编融合",
            })

        return StageResult(
            stage=PipelineStage.FUSION, success=True,
            data={"fused_chapters": fused_chapters},
            duration_seconds=time.time() - t0,
        )

    def _run_back_translation_verify(
        self, translations: list[dict], chapters: list[dict],
        source_lang: str, target_lang: str,
    ) -> StageResult:
        """
        法律模式专用：回译验证。

        流程：中文译文 → 回译英文 → 与原文做语义对齐检查。
        发现条款义务偏离时标记风险等级。
        """
        t0 = time.time()

        verified = []
        for trans_item, chapter in zip(translations, chapters):
            ch_id = trans_item.get("chapter_id", "?")
            translation = trans_item.get("translation", "")
            source = chapter.get("text", chapter.get("content", ""))

            if not source or not translation:
                verified.append({
                    "chapter_id": ch_id,
                    "translation": translation,
                    "verified": translation,
                    "risk": "high",
                    "note": "缺少原文对照，跳过回译验证",
                })
                continue

            # 回译验证：将中文译文回译英文，与原文做条款义务对照
            result = self.chief_editor.back_translate_verify(
                chapter_id=ch_id,
                source_text=source,
                translation_text=translation,
                source_lang=source_lang,
                target_lang=target_lang,
            )

            verified.append({
                "chapter_id": ch_id,
                "translation": translation,
                "verified": result.data.get("adjusted_text", translation),
                "risk": result.data.get("risk_level", "low"),
                "deviations": result.data.get("deviations", []),
            })

        risk_count = sum(1 for v in verified if v.get("risk") == "high")
        self.tracker.log("back_translate", f"回译验证完成 | 高风险条款: {risk_count}")

        return StageResult(
            stage=PipelineStage.FUSION, success=True,
            data={"verified_chapters": verified, "high_risk_count": risk_count},
            duration_seconds=time.time() - t0,
        )

    async def _run_review_and_fact_check(
        self, edited_chapters: list[dict],
    ) -> tuple[StageResult, AgentResult]:
        """阶段3：评审 + 事实核查并行"""
        t0 = time.time()

        self.tracker.log("review", "评审+事实核查（并行）")

        review_task = asyncio.create_task(
            asyncio.to_thread(self.chief_editor.review_mode, edited_chapters)
        )
        fact_task = asyncio.create_task(
            asyncio.to_thread(self.fact_checker.execute, edited_chapters)
        )

        review_result, fact_result = await asyncio.gather(review_task, fact_task)

        review_stage = StageResult(
            stage=PipelineStage.REVIEW, success=True,
            data=review_result.data,
            duration_seconds=time.time() - t0,
            rollback_triggered=review_result.data.get("needs_rollback", False),
        )

        return review_stage, fact_result

    def _run_delivery(
        self, chapters: list[dict], review_report: dict, fact_errors: list[dict],
    ) -> StageResult:
        """阶段4：交付归档"""
        t0 = time.time()

        self.tracker.log("delivery", "生成交付稿")
        raw_text = "\n\n".join(
            f"## 第{ch.get('chapter_id', '?')}章 {ch.get('title', '')}\n\n{ch.get('edited', '')}"
            for ch in chapters
        )
        final_text = post_process_delivery(raw_text)

        return StageResult(
            stage=PipelineStage.DELIVERY, success=True,
            data={
                "final_text": final_text,
                "chapter_count": len(chapters),
                "review_summary": review_report.get("review_report", {}),
                "fact_errors_count": len(fact_errors),
                "critical_errors": sum(1 for e in fact_errors if e.get("severity") == "critical"),
                "version_log": self.tracker.get_log(),
                "total_cost_usd": self.tracker.total_cost,
            },
            duration_seconds=time.time() - t0,
        )

    # ═══════════════════════════════════════════
    # 辅助
    # ═══════════════════════════════════════════

    def _extract_cost(self, result) -> float:
        if isinstance(result, AgentResult) and result.context:
            return result.context.cost_usd
        return 0.0

    def _build_summary(self, stages, total_cost, total_duration) -> str:
        lines = [
            "=" * 60,
            f"  翻译流水线执行摘要 | 模式: {self.mode.value}",
            "=" * 60,
            f"  总耗时: {total_duration:.1f}s",
            f"  总成本: ${total_cost:.4f}",
            "",
        ]
        stage_names = {
            PipelineStage.PREPARATION: "阶段0: 前置筹备",
            PipelineStage.TRANSLATION:  "阶段1: 并行初译",
            PipelineStage.REWRITE:      "阶段1.5: 再创重述",
            PipelineStage.FUSION:       "阶段1.8: 融合/验证",
            PipelineStage.EDITING:       "阶段2: 主编终审（含三合一审核）",
            PipelineStage.REVIEW:        "阶段3: 终审比对",
            PipelineStage.DELIVERY:      "阶段4: 交付归档",
        }
        for stage, result in stages.items():
            status = "✅" if result.success else "❌"
            lines.append(f"  {status} {stage_names.get(stage, stage.value)} ({result.duration_seconds:.1f}s)")
        lines.append("")
        lines.append("=" * 60)
        return "\n".join(lines)

    def get_status(self) -> dict:
        return {
            "mode": self.mode.value,
            "assets_initialized": self.assets.book_analysis is not None,
            "terminology_entries": len(self.assets.get_terminology().entries) if self.assets.get_terminology() else 0,
            "version_log": self.tracker.get_log()[-5:] if self.tracker.get_log() else [],
        }
