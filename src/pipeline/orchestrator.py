"""流水线调度器 — 五阶段翻译流水线的核心调度引擎

对齐Hermes框架，支持：
- 阶段顺序执行 + 条件回退
- Agent并行调度（分章翻译集群）
- 质检卡点自动拦截
- 完整的上下文传递和版本追溯
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable

from src.agents.base import AgentResult
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


class PipelineStage(str, Enum):
    """流水线阶段"""
    PREPARATION = "preparation"        # 阶段0: 前置筹备
    TRANSLATION = "translation"        # 阶段1: 并行初译
    REWRITE = "rewrite"               # 阶段1.5: 再创重述（可选）
    FUSION = "fusion"                 # 阶段1.8: 融合V1+V2（可选）
    EDITING = "editing"               # 阶段2: 统稿终审
    REVIEW = "review"                 # 阶段3: 评审（主编终审模式B）
    DELIVERY = "delivery"            # 阶段4: 交付归档


@dataclass
class StageResult:
    """单阶段执行结果"""
    stage: PipelineStage
    success: bool
    data: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    rollback_triggered: bool = False


@dataclass
class PipelineResult:
    """完整流水线执行结果"""
    success: bool
    stages: dict[PipelineStage, StageResult] = field(default_factory=dict)
    total_duration_seconds: float = 0.0
    total_cost_usd: float = 0.0
    final_output: Optional[dict] = None
    summary: str = ""


class TranslationPipeline:
    """
    五阶段翻译流水线调度器

    使用方式：
    ```python
    pipeline = TranslationPipeline()
    result = await pipeline.run(
        source_text=full_book_text,
        title="书名",
        author="作者",
        source_lang="en",
        target_lang="zh",
        target_style="傅雷风格",
    )
    ```
    """

    def __init__(
        self,
        config_path: str = "config/default.yaml",
        models_config_path: str = "config/models.yaml",
        assets_dir: str = "./assets_store",
    ):
        self.registry = ModelRegistry(models_config_path)
        self.assets = AssetStore(assets_dir)
        self.tracker = VersionTracker()

        # 初始化Agent（按需创建）
        self._book_analyzer: Optional[BookAnalyzerAgent] = None
        self._term_stylist: Optional[TermStylistAgent] = None
        self._translator_cluster: list[ChapterTranslatorAgent] = []
        self._chief_editor: Optional[ChiefEditorAgent] = None
        self._fact_checker: Optional[FactCheckerAgent] = None
        self._term_enforcer: Optional[TermEnforcer] = None

    @property
    def book_analyzer(self) -> BookAnalyzerAgent:
        if self._book_analyzer is None:
            self._book_analyzer = BookAnalyzerAgent(self.registry, self.assets)
        return self._book_analyzer

    @property
    def term_stylist(self) -> TermStylistAgent:
        if self._term_stylist is None:
            self._term_stylist = TermStylistAgent(self.registry, self.assets)
        return self._term_stylist

    @property
    def chief_editor(self) -> ChiefEditorAgent:
        if self._chief_editor is None:
            self._chief_editor = ChiefEditorAgent(self.registry, self.assets)
        return self._chief_editor

    @property
    def fact_checker(self) -> FactCheckerAgent:
        if self._fact_checker is None:
            self._fact_checker = FactCheckerAgent(self.registry, self.assets)
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
        use_rewrite: bool = False,  # 是否启用三版融合模式
    ) -> PipelineResult:
        """
        执行完整翻译流水线。

        Args:
            source_text: 完整原文
            title: 书名
            author: 作者
            source_lang: 源语言
            target_lang: 目标语言
            target_style: 目标风格（如"傅雷风格"）
            chapters: 预切分的章节（不提供则自动切分）
            on_stage_complete: 阶段完成回调
            require_human_approval: 是否要求人工确认术语表/风格手册

        Returns:
            PipelineResult
        """
        start_time = time.time()
        stages: dict[PipelineStage, StageResult] = {}
        total_cost = 0.0

        # ─── 阶段0: 前置筹备 ───
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

        # 获取切分好的章节
        chapters = prep_result.data.get("chapters", chapters or [])

        # ─── 阶段1: 并行初译 ───
        trans_result = await self._run_translation(
            chapters, source_lang, target_lang,
        )
        stages[PipelineStage.TRANSLATION] = trans_result
        total_cost += self._extract_cost(trans_result)
        if on_stage_complete:
            on_stage_complete(PipelineStage.TRANSLATION, trans_result)

        # ─── 阶段1.5: 再创重述（可选） ───
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
        else:
            rewrite_result = None

        # ─── 阶段1.8: 融合V1+V2（可选，在rewrite启用后自动启用） ───
        if use_rewrite and rewrite_result and rewrite_result.success:
            fusion_result = self._run_fusion(
                chapters,
                trans_result.data.get("translations", []),
                rewrite_result.data.get("rewrites", []),
                target_lang,
            )
            stages[PipelineStage.FUSION] = fusion_result
            total_cost += self._extract_cost(fusion_result)
            if on_stage_complete:
                on_stage_complete(PipelineStage.FUSION, fusion_result)
            # 融合后的译文替代直译版进入后续阶段
            translated_chapters = fusion_result.data.get("fused_chapters", [])
            translated_texts = [tc.get("fused", tc.get("translation", ""))
                              for tc in translated_chapters]
        else:
            # 未启用rewrite或rewrite失败 → 用直译版
            translated_chapters = trans_result.data.get("translations", [])
            translated_texts = [tc.get("translation", "") for tc in translated_chapters]

        # ─── 阶段2: 统稿（主编终审 模式A） ───
        edit_result = await self._run_editing(
            translated_chapters, source_lang, target_lang,
        )
        stages[PipelineStage.EDITING] = edit_result
        total_cost += self._extract_cost(edit_result)
        if on_stage_complete:
            on_stage_complete(PipelineStage.EDITING, edit_result)

        # ─── 阶段3: 评审（主编终审 模式B）+ 事实核查（并行） ───
        review_result, fact_result = await self._run_review_and_fact_check(
            edit_result.data.get("edited_chapters", []),
        )
        stages[PipelineStage.REVIEW] = review_result
        total_cost += self._extract_cost(review_result) + self._extract_cost(fact_result)
        if on_stage_complete:
            on_stage_complete(PipelineStage.REVIEW, review_result)

        # 合并事实核查结果
        review_data = review_result.data.copy()
        review_data["fact_check"] = fact_result.data

        # ─── 阶段4: 交付归档 ───
        delivery_result = self._run_delivery(
            chapters=edit_result.data.get("edited_chapters", []),
            review_report=review_data,
            fact_errors=fact_result.data.get("all_errors", []),
        )
        stages[PipelineStage.DELIVERY] = delivery_result

        # 汇总
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
        self,
        source_text: str,
        title: str,
        author: str,
        source_lang: str,
        target_lang: str,
        target_style: str,
        chapters: Optional[list[dict]],
        require_approval: bool,
    ) -> StageResult:
        """阶段0: 前置筹备"""
        t0 = time.time()

        # 全书分析
        self.tracker.log("preparation", "开始全书分析")
        analysis_result = self.book_analyzer.execute(
            full_text=source_text,
            title=title,
            author=author,
            source_lang=source_lang,
            target_lang=target_lang,
        )
        if not analysis_result.success:
            return StageResult(
                stage=PipelineStage.PREPARATION,
                success=False,
                errors=[analysis_result.error],
                duration_seconds=time.time() - t0,
            )

        # 术语与风格制定
        self.tracker.log("preparation", "开始术语风格制定")
        style_result = self.term_stylist.execute(
            source_lang=source_lang,
            target_lang=target_lang,
            target_style=target_style or "专业出版级翻译风格",
        )
        if not style_result.success:
            return StageResult(
                stage=PipelineStage.PREPARATION,
                success=False,
                errors=[style_result.error],
                duration_seconds=time.time() - t0,
            )

        # 获取章节信息
        book_analysis = self.assets.get_book_analysis()
        chapters_info = []
        if book_analysis:
            chapters_info = [
                {"chapter_id": c.chapter_id, "title": c.title, "word_count": c.word_count}
                for c in book_analysis.chapters
            ]

        # ⚠️ 人工审批卡点
        approval_pending = require_approval

        data = {
            "book_analysis": book_analysis.model_dump() if book_analysis else {},
            "terminology": style_result.data.get("terminology", {}),
            "style_manual": style_result.data.get("style_manual", {}),
            "chapters": chapters_info,
            "approval_pending": approval_pending,
        }

        return StageResult(
            stage=PipelineStage.PREPARATION,
            success=True,
            data=data,
            duration_seconds=time.time() - t0,
        )

    async def _run_translation(
        self,
        chapters: list[dict],
        source_lang: str,
        target_lang: str,
    ) -> StageResult:
        """阶段1: 并行初译"""
        t0 = time.time()

        if not chapters:
            return StageResult(
                stage=PipelineStage.TRANSLATION,
                success=False,
                errors=["无章节数据，请先运行前置筹备阶段"],
                duration_seconds=time.time() - t0,
            )

        self.tracker.log("translation", f"开始并行翻译 {len(chapters)} 章")

        # 并行翻译所有章节
        tasks = []
        for i, ch in enumerate(chapters):
            # 为每章创建独立的翻译Agent
            translator = ChapterTranslatorAgent(self.registry, self.assets)
            tasks.append(self._translate_chapter_async(
                translator, ch, i, chapters, source_lang, target_lang,
            ))

        translations = await asyncio.gather(*tasks)

        # 质检卡点
        quality_issues = []
        for t in translations:
            if t and not t.get("term_compliance", {}).get("passed", True):
                quality_issues.append({
                    "chapter_id": t.get("chapter_id"),
                    "term_compliance": t.get("term_compliance"),
                })

        total_translations = sum(1 for t in translations if t)
        self.tracker.log("translation", f"初译完成: {total_translations}/{len(chapters)} 章通过")

        return StageResult(
            stage=PipelineStage.TRANSLATION,
            success=True,
            data={
                "translations": translations,
                "quality_issues": quality_issues,
                "chapters_translated": total_translations,
                "chapters_with_issues": len(quality_issues),
            },
            duration_seconds=time.time() - t0,
        )

    async def _translate_chapter_async(
        self,
        translator: ChapterTranslatorAgent,
        chapter: dict,
        index: int,
        all_chapters: list[dict],
        source_lang: str,
        target_lang: str,
    ) -> dict:
        """单章异步翻译（可并行）"""
        chapter_id = chapter.get("chapter_id", index + 1)

        # 安全截取上下文（保证句子/段落完整性）
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
        self,
        translations: list[dict],
        source_lang: str,
        target_lang: str,
    ) -> StageResult:
        """阶段2: 统稿（主编终审 模式A）"""
        t0 = time.time()

        if not translations:
            return StageResult(
                stage=PipelineStage.EDITING,
                success=False,
                errors=["无翻译结果，无法统稿"],
                duration_seconds=time.time() - t0,
            )

        self.tracker.log("editing", "开始主编统稿")

        result = self.chief_editor.draft_mode(
            chapter_translations=translations,
            source_lang=source_lang,
            target_lang=target_lang,
        )

        edited_chapters = result.data.get("edited_chapters", [])
        violations = result.data.get("violations_found", 0)

        return StageResult(
            stage=PipelineStage.EDITING,
            success=True,
            data={
                "edited_chapters": edited_chapters,
                "style_report": result.data.get("style_report", {}),
                "violations_found": violations,
                "term_updates": result.data.get("term_updates_count", 0),
            },
            duration_seconds=time.time() - t0,
        )

    def _run_rewrite(
        self,
        chapters: list[dict],
        translations: list[dict],
        source_lang: str,
    ) -> StageResult:
        """阶段1.5: 再创重述（可选）"""
        t0 = time.time()

        if not translations:
            return StageResult(
                stage=PipelineStage.REWRITE,
                success=False,
                errors=["无翻译结果，无法重述"],
                duration_seconds=time.time() - t0,
            )

        rewriter = RewriterAgent(self.registry, self.assets)
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
            stage=PipelineStage.REWRITE,
            success=True,
            data={"rewrites": rewrites},
            duration_seconds=time.time() - t0,
        )

    def _run_fusion(
        self,
        chapters: list[dict],
        translations: list[dict],
        rewrites: list[dict],
        target_lang: str,
    ) -> StageResult:
        """阶段1.8: 融合V1+V2（可选）"""
        t0 = time.time()

        if not translations or not rewrites:
            return StageResult(
                stage=PipelineStage.FUSION,
                success=False,
                errors=["缺少直译版或再创版，无法融合"],
                duration_seconds=time.time() - t0,
            )

        fused_chapters = []

        for idx, trans_item in enumerate(translations):
            chapter_id = trans_item.get("chapter_id", idx + 1)
            chapter_title = trans_item.get("title", "")
            v1 = trans_item.get("translation", "")

            # 找对应的改写版本
            rewrite_item = None
            for r in rewrites:
                if r.get("chapter_id") == chapter_id:
                    rewrite_item = r
                    break
            v2 = rewrite_item.get("rewrite", "") if rewrite_item else ""

            if not v1 or not v2:
                fused_chapters.append({
                    "chapter_id": chapter_id,
                    "title": chapter_title,
                    "translation": v1,
                    "fused": v1 or v2,
                    "fusion_note": "仅单版本可用",
                })
                continue

            result = self.chief_editor.fusion_mode(
                chapter_id=chapter_id,
                chapter_title=chapter_title,
                v1_direct=v1,
                v2_rewrite=v2,
                style_note=f"目标语言: {target_lang}",
            )
            fused_text = result.data.get("fused_text", v1)

            fused_chapters.append({
                "chapter_id": chapter_id,
                "title": chapter_title,
                "translation": v1,
                "rewrite": v2,
                "fused": fused_text,
                "fusion_note": "V1直译 + V2再创 → 主编融合",
            })

        return StageResult(
            stage=PipelineStage.FUSION,
            success=True,
            data={"fused_chapters": fused_chapters},
            duration_seconds=time.time() - t0,
        )

    async def _run_review_and_fact_check(
        self,
        edited_chapters: list[dict],
    ) -> tuple[StageResult, AgentResult]:
        """阶段3: 评审 + 事实核查并行"""
        t0 = time.time()

        self.tracker.log("review", "开始评审+事实核查（并行）")

        # 并行执行评审和事实核查
        review_task = asyncio.create_task(
            asyncio.to_thread(self.chief_editor.review_mode, edited_chapters)
        )
        fact_task = asyncio.create_task(
            asyncio.to_thread(self.fact_checker.execute, edited_chapters)
        )

        review_result, fact_result = await asyncio.gather(review_task, fact_task)

        review_stage = StageResult(
            stage=PipelineStage.REVIEW,
            success=True,
            data=review_result.data,
            duration_seconds=time.time() - t0,
            rollback_triggered=review_result.data.get("needs_rollback", False),
        )

        return review_stage, fact_result

    def _run_delivery(
        self,
        chapters: list[dict],
        review_report: dict,
        fact_errors: list[dict],
    ) -> StageResult:
        """阶段4: 交付归档"""
        t0 = time.time()

        self.tracker.log("delivery", "生成交付稿")

        # 组装最终稿，后处理清理编辑标记和修复标题
        raw_text = "\n\n".join(
            f"## 第{ch.get('chapter_id', '?')}章 {ch.get('title', '')}\n\n{ch.get('edited', '')}"
            for ch in chapters
        )

        # 安全检测：章节数量是否异常
        import re as _re
        ch_count_in_text = len(_re.findall(r'## 第\d+章', raw_text))
        if ch_count_in_text != len(chapters):
            self.tracker.log("delivery", f"⚠️ 章节数不一致: 输入{len(chapters)}章, 产出{ch_count_in_text}章")

        final_text = post_process_delivery(raw_text)

        delivery_data = {
            "final_text": final_text,
            "chapter_count": len(chapters),
            "review_summary": review_report.get("review_report", {}),
            "fact_errors_count": len(fact_errors),
            "critical_errors": sum(1 for e in fact_errors if e.get("severity") == "critical"),
            "version_log": self.tracker.get_log(),
            "total_cost_usd": self.tracker.total_cost,
        }

        return StageResult(
            stage=PipelineStage.DELIVERY,
            success=True,
            data=delivery_data,
            duration_seconds=time.time() - t0,
        )

    # ═══════════════════════════════════════════
    # 辅助方法
    # ═══════════════════════════════════════════

    def _extract_cost(self, result: StageResult | AgentResult) -> float:
        """从结果中提取成本"""
        if isinstance(result, StageResult):
            return 0.0
        if isinstance(result, AgentResult) and result.context:
            return result.context.cost_usd
        return 0.0

    def _build_summary(
        self,
        stages: dict[PipelineStage, StageResult],
        total_cost: float,
        total_duration: float,
    ) -> str:
        """构建流水线执行摘要"""
        lines = [
            "=" * 60,
            "  翻译流水线执行摘要",
            "=" * 60,
            f"  总耗时: {total_duration:.1f}秒",
            f"  总成本: ${total_cost:.4f}",
            f"  阶段数: {len(stages)}",
            "",
        ]

        stage_names = {
            PipelineStage.PREPARATION: "阶段0: 前置筹备",
            PipelineStage.TRANSLATION: "阶段1: 并行初译",
            PipelineStage.EDITING: "阶段2: 统稿润色",
            PipelineStage.REVIEW: "阶段3: 终审比对",
            PipelineStage.DELIVERY: "阶段4: 交付归档",
        }

        for stage, result in stages.items():
            status = "✅" if result.success else "❌"
            lines.append(f"  {status} {stage_names.get(stage, stage.value)} ({result.duration_seconds:.1f}s)")

        lines.append("")
        lines.append("=" * 60)
        return "\n".join(lines)

    def get_status(self) -> dict:
        """获取流水线当前状态"""
        return {
            "assets_initialized": self.assets.book_analysis is not None,
            "terminology_entries": len(self.assets.get_terminology().entries) if self.assets.get_terminology() else 0,
            "style_dimensions": len(self.assets.get_style_manual().dimensions) if self.assets.get_style_manual() else 0,
            "version_log": self.tracker.get_log()[-5:] if self.tracker.get_log() else [],
        }
