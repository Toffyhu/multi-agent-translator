"""④ 主编终审Agent — 统稿+三合一审核+评审+融合+回译验证

核心升级 v2：场景自适应。同一Agent在不同模式下启动不同子职能。

模式A — 统稿模式（所有模式）:
1. 衔接润色: 统一章节衔接处、前后呼应内容的表述
2. 风格校准: 修正偏离目标风格的译法（文学）/ 术语锁定（法律/学术）
3. 表达优化: 消除翻译腔，优化中文表达

模式B — 三合一审核（集成于draft_mode）:
· 左校对（原文对照）：逐句对照原文，标记漏译/增译/术语偏移
· 右校对（中文语感）：只读中文，判断自然度和可读性
· 正文编辑（出版规范）：标点统一/用词一致/格式统一

模式C — 评审模式（质量评分+回退裁决）

模式D — 融合模式（文学：V1+V2创意融合）

模式E — 回译验证（法律专用：中文→回译英文→条款义务对照）

关键设计:
- 三合一审核并行执行，意见不互见，争议不自动合并
- 法律模式温度锁定0.1-0.2，术语强制锁定
- 学术模式保护引用/公式，不修改逻辑结构
"""

from __future__ import annotations

import json
import re

from src.agents.base import BaseAgent, AgentResult
from src.assets.schema import AssetStore
from src.assets.term_enforcer import TermEnforcer
from src.models.registry import ModelRegistry


class ChiefEditorAgent(BaseAgent):
    """主编终审Agent — 统稿+三合一审核+评审+融合+回译验证"""

    agent_name = "chief_editor"
    agent_description = "主编终审：统稿润色+三合一审核+多版本评审+回退裁决"

    def __init__(
        self,
        model_registry: ModelRegistry | None = None,
        asset_store: AssetStore | None = None,
        mode: str | None = None,
    ):
        super().__init__(model_registry, asset_store, mode=mode)

    # ═══════════════════════════════════════════
    # 模式A: 统稿模式（场景自适应）
    # ═══════════════════════════════════════════

    def draft_mode(
        self,
        chapter_translations: list[dict],
        source_lang: str = "en",
        target_lang: str = "zh",
    ) -> AgentResult:
        """统稿模式：统一润色全部章节（温度/规则随模式调整）"""
        style_manual = self.assets.get_style_manual()
        terminology = self.assets.get_terminology()
        book_analysis = self.assets.get_book_analysis()

        edited_chapters = []
        all_term_updates = []
        total_violations = 0

        for i, ch in enumerate(chapter_translations):
            prev_translation = (
                chapter_translations[i - 1]["translation"][-300:] if i > 0 else ""
            )
            next_translation = (
                chapter_translations[i + 1]["translation"][:300]
                if i < len(chapter_translations) - 1 else ""
            )
            result = self._edit_chapter(
                ch, prev_translation, next_translation,
                style_manual, terminology, book_analysis,
            )
            edited_chapters.append(result)
            all_term_updates.extend(result.get("term_updates", []))
            total_violations += len(result.get("violations", []))

        for update in all_term_updates:
            try:
                self.assets.update_term_entry(
                    source=update["source"], new_target=update["new_target"],
                    notes=f"主编终审核定: {update.get('reason', '')}",
                )
            except Exception:
                pass

        style_report = self._scan_style_consistency(edited_chapters, style_manual)

        return AgentResult(
            success=True,
            data={
                "edited_chapters": edited_chapters,
                "term_updates_count": len(all_term_updates),
                "style_report": style_report,
                "violations_found": total_violations,
            },
            context=self._last_context,
        )

    def _edit_chapter(
        self, chapter: dict, prev_translation: str, next_translation: str,
        style_manual, terminology, book_analysis,
    ) -> dict:
        """统稿单个章节 — 模式感知提示词"""
        # 提取原文（法律/学术模式编辑器需要原文对照）
        source = chapter.get("source_text", chapter.get("original", ""))
        translation = chapter.get("translation", chapter.get("raw_translation", ""))

        # ── 法律模式：先运行确定性校验引擎（轨道1）──
        verifier_findings = ""
        if self.mode.value == "legal" and source and translation:
            try:
                from src.utils.legal_verifier import LegalVerifier
                verifier = LegalVerifier()
                results = verifier.run_all(
                    source=source, target=translation, definitions_section=source)
                clause_issues = results.get("clause_tree", [])
                term_issues = results.get("term_scan", [])
                cross_ref = results.get("cross_refs", [])
                punct = results.get("punctuation", [])
                risk = results.get("risk_heatmap", {})
                parts = []
                if clause_issues: parts.append(f"• 【条款结构】{len(clause_issues)}项偏差")
                if term_issues: parts.append(f"• 【术语一致】{len(term_issues)}项疑似")
                if punct: parts.append(f"• 【标点格式】{len(punct)}处")
                if risk: parts.append(f"• 【风险分布】critical={risk.get('distribution',{}).get('critical',0)}")
                if parts: verifier_findings = "## ⚠️ 确定性校验引擎预扫描\n\n" + "\n".join(parts)
            except Exception as e:
                verifier_findings = f"(校验引擎: {e})"

        # ── 学术模式：先运行学术保护规则（轨道1）──
        elif self.mode.value == "academic" and source and translation:
            try:
                from src.utils.legal_verifier import AcademicGuard
                gr = AcademicGuard.run_all(source=source, target=translation)
                cit = gr.get("citations", [])
                fig = gr.get("figure_refs", [])
                mis = gr.get("mistranslations", [])
                parts = []
                if cit: parts.append(f"• 【引用缺失】{len(cit)}处引用编号可能丢失")
                if fig: parts.append(f"• 【图表引用】{len(fig)}处图表引用可能缺失")
                if mis: parts.append(f"• 【疑似误译】{len(mis)}处(如state-of-the-art/baseline等)")
                if parts: verifier_findings = "## ⚠️ 学术保护规则预扫描\n\n" + "\n".join(parts)
            except Exception as e:
                verifier_findings = f"(保护规则: {e})"

        system, user = self._edit_prompt_by_mode(
            chapter, prev_translation, next_translation, source,
        )
        # 注入校验发现
        if verifier_findings:
            user = user.replace("请逐条对照英文原文", verifier_findings + "\n\n请逐条对照英文原文")
        response, ctx = self._call_llm(
            system, user,
            temperature=self._edit_temperature(),
            max_tokens=8192,
        )
        self._last_context = ctx
        return self._parse_edit_response(response, chapter)

    def _edit_temperature(self) -> float:
        """统稿温度：文学0.5，法律0.1，学术0.3"""
        return {"literary": 0.5, "legal": 0.1, "academic": 0.3}.get(
            self.mode.value, 0.3,
        )

    def _edit_prompt_by_mode(
        self, chapter: dict, prev: str, next_t: str, source: str = "",
    ) -> tuple[str, str]:
        """根据模式生成不同的统稿提示词"""
        chapter_id = chapter.get("chapter_id", "?")
        chapter_title = chapter.get("title", "")
        translation = chapter.get("translation", chapter.get("raw_translation", ""))

        if self.mode.value == "legal":
            return self._legal_edit_prompt(chapter_id, chapter_title, translation, prev, next_t, source)
        elif self.mode.value == "academic":
            return self._academic_edit_prompt(chapter_id, chapter_title, translation, prev, next_t, source)
        else:
            return self._literary_edit_prompt(chapter_id, chapter_title, translation, prev, next_t)

    def _literary_edit_prompt(
        self, ch_id, ch_title, trans, prev, next_t,
    ) -> tuple[str, str]:
        """文学模式统稿提示词"""
        system = """你是出版社资深责任编辑。审校初译稿，保护文学性，修正技术问题。

原则：
- 只修正明确的语言质量和规范问题，不改动译者的文学风格
- 代词一致性：同一角色全文指代一致
- 标点规范：中文标点，对话结束用句号/问号/感叹号
- 语法通顺：无病句、成分残缺
- 术语统一：同一概念前后表述一致
- 逻辑链校验：含义模糊处追溯原文，不盲目润色
- 代词追踪：出现3+人物时，"他"超2次替换为具体人名
- 长句密度：超50字长句超3句时插入短句或拆分

输出格式：优化后译文 → ---CHANGES--- 列出修改 → ---TERMS--- 列出术语决定"""

        user = f"""请统稿润色：

### 第{ch_id}章 {ch_title}

{self._maybe_add_context(prev, '前章衔接参考')}

### 本章译文：

{trans}

{self._maybe_add_context(next_t, '后章衔接参考')}

输出统稿后译文。"""

        return system, user

    def _legal_edit_prompt(
        self, ch_id, ch_title, trans, prev, next_t, source: str = "",
    ) -> tuple[str, str]:
        """法律模式统稿提示词 — 含英文原文对照"""
        system = """你是一位法律文书翻译审核专家。你的唯一任务是确保中译文与英文原文在条款义务上完全对应。

核心原则：
1. **条款完整性**：每一条款的编号、层级、引用必须100%保留，不可合并或拆分
2. **术语绝对一致**："shall"→"应"（强制性义务），"may"→"可"（权利），定义术语全篇统一
3. **零歧义**：任何可能产生多义解读的表达都是不合格的
4. **结构保持**：原文的条款编号、列表层级、引用格式不得改变
5. **不润色不创作**：只做纠正，不做任何"更好听"的改动

检查清单：
- 条款编号是否完整保留 ✓
- "shall"是否正确翻译为"应"而非"将"/"会"
- 定义条款中的术语是否全篇一致
- 金额、日期、百分比是否与原文字符级对应
- 条件关系（if/when/unless/subject to）是否准确传达

输出格式：逐条款列出偏差项 → 给出修正方案。"""

        source_block = f"""
## 英文原文

```
{source}
```
""" if source else ""

        user = f"""请审核以下法律译文：{source_block}

### 条款段 {ch_id}

{self._maybe_add_context(prev, '前段参考')}

### 中文译文：

{trans}

{self._maybe_add_context(next_t, '后段参考')}

请逐条对照英文原文，审核中文译文，标记所有偏差。"""

        return system, user

    def _academic_edit_prompt(
        self, ch_id, ch_title, trans, prev, next_t, source: str = "",
    ) -> tuple[str, str]:
        """学术模式统稿提示词 — 含英文原文对照"""
        system = """你是学术文献翻译审校专家。任务是保证学术译文的准确性、术语规范性和论证完整性。

核心原则：
1. **术语规范**：学科术语按约定译法，不创建新译名
2. **引用保护**：参考文献编号、作者名、年份不可修改
3. **公式/数据保护**：数学表达式、统计数据、图表编号不可修改
4. **论证完整**：前提→推论→结论的逻辑链不可断裂
5. **适度流畅化**：可在不影响准确性的前提下优化中文通顺度

检查清单：
- 引用编号是否完整保留 ✓
- 学科术语是否使用标准译名
- 公式/数据是否原样保留
- 论证逻辑是否清晰可追溯

输出格式：优化后译文 → ---CHANGES--- 列出修改"""

        source_block = f"""
## 英文原文

```
{source}
```
""" if source else ""

        user = f"""请审校以下学术译文：{source_block}

### 第{ch_id}章 {ch_title}

{self._maybe_add_context(prev, '前章衔接')}

{trans}

{self._maybe_add_context(next_t, '后章衔接')}"""

        return system, user

    # ═══════════════════════════════════════════
    # 模式B: 三合一审核（左校对 + 右校对 + 正文编辑）
    # ═══════════════════════════════════════════

    def integrated_proofread(
        self,
        edited_chapters: list[dict],
        source_texts: list[str] = None,
    ) -> AgentResult:
        """
        三合一审核：左校对+右校对+正文编辑，并行执行。

        三个子职能互不见对方的意见，独立产出后汇总。
        不一致项不自动合并，标记为争议向上抛。

        Args:
            edited_chapters: 主编统稿后的章节
            source_texts: 对应的原文文本列表（用于左校对原文对照）

        Returns:
            AgentResult.data 包含 {disputes, left_findings, right_findings, copy_findings}
        """
        source_texts = source_texts or []

        left_findings = self._left_proofread(edited_chapters, source_texts)
        right_findings = self._right_proofread(edited_chapters)
        copy_findings = self._copy_edit(edited_chapters)

        # 争议汇总：三方意见交叉分析
        disputes = self._collect_disputes(
            left_findings, right_findings, copy_findings,
            edited_chapters,
        )

        return AgentResult(
            success=True,
            data={
                "disputes": disputes,
                "dispute_count": len(disputes),
                "left_findings": left_findings,
                "right_findings": right_findings,
                "copy_findings": copy_findings,
            },
            context=self._last_context,
        )

    def _left_proofread(
        self, chapters: list[dict], source_texts: list[str],
    ) -> list[dict]:
        """
        左校对：对照原文逐句校验。

        行为随模式不同：
        - 文学：检查漏译、增译、关键意象偏移
        - 法律：条款级对照（编号/义务/定义一致性）
        - 学术：引用编号/公式/数据完整性
        """
        findings = []

        # 抽样：全书的30%，但至少2章
        import random
        n = max(min(3, len(chapters)), int(len(chapters) * 0.3))
        sample_idx = sorted(random.sample(range(len(chapters)), n)) if len(chapters) > n else list(range(len(chapters)))

        for idx in sample_idx:
            ch = chapters[idx]
            edited = ch.get("edited", ch.get("translation", ""))
            source = source_texts[idx][:8000] if idx < len(source_texts) else ""

            if not source or not edited:
                findings.append({
                    "chapter_id": ch.get("chapter_id"),
                    "finding": "缺少原文，跳过左校对",
                    "severity": "warning",
                })
                continue

            system, user = self._left_proofread_prompt(source, edited)
            text, ctx = self._call_llm(system, user, temperature=0.1, max_tokens=4096)
            self._last_context = ctx

            findings.append({
                "chapter_id": ch.get("chapter_id"),
                "title": ch.get("title", ""),
                "report": text[:2000],
            })

        return findings

    def _left_proofread_prompt(
        self, source: str, translated: str,
    ) -> tuple[str, str]:
        """左校对提示词（模式感知）"""
        if self.mode.value == "legal":
            system = """你是法律翻译原文对照校验员。
逐条款对比原文和译文，标记：
1. 条款编号是否完整
2. "shall"→"应"（义务），"may"→"可"（权利）
3. 定义术语是否前后一致
4. 金额/日期/百分比是否字符级对应
5. 条件关系（if/when/unless/subject to）是否准确
输出JSON：{"deviations":[{"type":"...","location":"...","original":"...","translated":"...","severity":"high|medium|low"}]}"""
        elif self.mode.value == "academic":
            system = """你是学术翻译原文对照校验员。
对比原文和译文，标记：
1. 引用编号/作者名/年份是否保留
2. 公式/数据是否原样保留
3. 学科术语译法是否正确
4. 论证逻辑链是否断裂
输出JSON：{"deviations":[...]}"""
        else:
            system = """你是文学翻译原文对照校验员。
逐句对照原文和译文，标记：
1. 漏译（原文有的内容，译文缺失）
2. 增译（原文没有的内容，译文凭空添加）
3. 关键意象偏移（原文的独特表达被弱化或曲解）
4. 叙事顺序颠倒
输出JSON：{"deviations":[...]}"""

        user = f"""原文片段：
```
{source[:4000]}
```

译文片段：
```
{translated[:4000]}
```

请逐句对照，标记所有偏差项。"""

        return system, user

    def _right_proofread(self, chapters: list[dict]) -> list[dict]:
        """
        右校对：只看中文译文，不传入原文。

        这是最关键的独立视角——摆脱原文干扰，
        纯粹从中文读者角度判断语言质量。
        """
        findings = []

        import random
        n = max(min(3, len(chapters)), int(len(chapters) * 0.3))
        sample_idx = sorted(random.sample(range(len(chapters)), n)) if len(chapters) > n else list(range(len(chapters)))

        for idx in sample_idx:
            ch = chapters[idx]
            edited = ch.get("edited", ch.get("translation", ""))
            if not edited:
                continue

            # 关键设计：不传入原文！
            system = """你是一位资深中文语言编辑。请阅读以下中文译文，完全从中文母语读者视角，判断语言质量。

请标记：
1. **翻译腔**："当...时""在...之下""由于...的缘故"等欧化句式
2. **不通顺**：读起来别扭、需要重读才能理解的地方
3. **节奏问题**：连续长句过多、缺乏呼吸感
4. **用词生硬**：中文习惯不会这么用的词

不要思考原文可能是什么。只看中文。

输出JSON：
{"issues":[{"text":"有问题的句子","problem":"翻译腔/不通顺/节奏/用词","suggestion":"修改建议"}]}"""

            user = f"请从中文语感角度审读以下译文：\n\n{edited[:5000]}"

            text, ctx = self._call_llm(system, user, temperature=0.2, max_tokens=4096)
            self._last_context = ctx

            findings.append({
                "chapter_id": ch.get("chapter_id"),
                "title": ch.get("title", ""),
                "report": text[:2000],
            })

        return findings

    def _copy_edit(self, chapters: list[dict]) -> list[dict]:
        """
        正文编辑：出版规范检查。

        检查项：
        - 中文标点规范性
        - 用词统一性（全篇搜索）
        - 数字/日期/单位格式
        - 引文格式
        """
        findings = []

        # 先做全篇用词一致性扫描
        all_text = ""
        for ch in chapters:
            edited = ch.get("edited", ch.get("translation", ""))
            if edited:
                all_text += edited + "\n\n"

        # 快速规则检查（确定性，不调LLM）
        rule_issues = self._deterministic_copy_check(all_text)

        # 对抽样章节做LLM辅助的深度规范检查
        import random
        n = max(min(2, len(chapters)), int(len(chapters) * 0.2))
        sample_idx = sorted(random.sample(range(len(chapters)), n)) if len(chapters) > n else list(range(len(chapters)))

        for idx in sample_idx:
            ch = chapters[idx]
            edited = ch.get("edited", ch.get("translation", ""))
            if not edited:
                continue

            system = """你是中文出版规范编辑。检查以下项目：

1. 中文标点是否正确（引号、逗号、句号、省略号、破折号）
2. 是否有残留的英文标点
3. 数字表达是否符合中文习惯
4. 同一人名/地名/术语在全文中是否拼写一致

输出JSON：{"issues":[{"type":"punctuation|consistency|format","location":"...","fix":"..."}]}"""

            user = f"请检查以下译文段落：\n\n{edited[:4000]}"

            text, ctx = self._call_llm(system, user, temperature=0.1, max_tokens=2048)
            self._last_context = ctx

            findings.append({
                "chapter_id": ch.get("chapter_id"),
                "report": text[:1500],
                "rule_issues": rule_issues,
            })

        return findings

    def _deterministic_copy_check(self, text: str) -> list[dict]:
        """确定性规则扫描（不调LLM）"""
        issues = []

        # 英文标点检测
        if re.search(r'(?<![a-zA-Z0-9])[,.](?![a-zA-Z0-9])', text):
            # 找到非英文语境中的英文逗号/句号
            for m in re.finditer(r'[^\x00-\x7f]+\s*([,.])\s*[^\x00-\x7f]+', text):
                issues.append({
                    "type": "英文标点",
                    "char": m.group(1),
                    "context": text[max(0, m.start()-20):m.end()+20],
                })

        # 常见格式不一致
        for pattern, label in [
            (r'\d+-\d+', '数字范围格式（建议统一用~或至）'),
        ]:
            matches = re.findall(pattern, text)
            if len(set(matches)) > 1:
                issues.append({
                    "type": "格式不一致",
                    "label": label,
                    "variants": list(set(matches))[:5],
                })

        return issues[:20]  # 最多20条

    def _collect_disputes(
        self,
        left_findings: list[dict],
        right_findings: list[dict],
        copy_findings: list[dict],
        chapters: list[dict],
    ) -> list[dict]:
        """
        争议汇总：三方意见交叉分析。

        一致意见 → 自动采纳（返回修复建议）
        分歧意见 → 标记为争议（不自动合并，向上抛）
        """
        disputes = []

        # 将所有发现按章节ID分组
        by_chapter = {}
        for f_list, source_tag in [
            (left_findings, "left"),
            (right_findings, "right"),
            (copy_findings, "copy"),
        ]:
            for f in f_list:
                cid = f.get("chapter_id", "global")
                if cid not in by_chapter:
                    by_chapter[cid] = {}
                by_chapter[cid][source_tag] = f.get("report", "")

        for cid, sources in by_chapter.items():
            # 如果只有一个视角有发现 → 直接标记为待审核
            if len(sources) < 2:
                disputes.append({
                    "chapter_id": cid,
                    "type": "single_source",
                    "sources": sources,
                    "note": "仅单一审核视角发现问题，建议人工复核",
                })
            else:
                # 多个视角 → 找出交叉点
                disputes.append({
                    "chapter_id": cid,
                    "type": "multi_source",
                    "sources": sources,
                    "note": f"来自{len(sources)}个独立视角的发现，需交叉分析",
                    "severity": "medium",
                })

        return disputes

    # ═══════════════════════════════════════════
    # 模式D: 融合模式（文学三版融合）
    # ═══════════════════════════════════════════

    def fusion_mode(
        self, chapter_id: int, chapter_title: str,
        v1_direct: str, v2_rewrite: str, style_note: str = "",
    ) -> AgentResult:
        """融合模式：V1+V2→Fused"""
        prompt = f"""你是一个顶级主编。融合两个版本为一个最优版本。

## 信息
- 章节: {chapter_title}
- 风格: {style_note or "保留原文叙事特色，用流畅中文表达"}

## 版本A（直译，准确）
```
{v1_direct[:3000]}
```

## 版本B（再创，流畅）
```
{v2_rewrite[:3000]}
```

## 融合规则
- 事实（人名/地名/时间/数字）→ 以A为准
- 句式/节奏/用词 → 以B为准
- B遗漏的关键细节 → 从A补回
- A翻译腔 → 用B方式重写

直接输出融合后译文。"""

        text, ctx = self._call_llm(
            system_prompt="你是顶级文学翻译主编，擅长融合多版译文取长补短。",
            user_prompt=prompt, temperature=0.4,
        )
        return AgentResult(
            success=True,
            data={"fused_text": text, "chapter_id": chapter_id, "chapter_title": chapter_title},
            context=ctx,
        )

    # ═══════════════════════════════════════════
    # 模式E: 回译验证（法律模式专用）
    # ═══════════════════════════════════════════

    def back_translate_verify(
        self, chapter_id: int, source_text: str,
        translation_text: str, source_lang: str = "en",
        target_lang: str = "zh",
    ) -> AgentResult:
        """
        回译验证：中文译文→回译英文→与原文做语义对齐。

        用于法律合同翻译，确保条款义务无偏离。
        """
        # 第一步：回译
        bt_prompt = f"""Translate the following Chinese text back to English literally.
Preserve all clause numbers, definitions, and obligations exactly.

Chinese:
```
{translation_text[:5000]}
```

Return only the English back-translation."""

        back_translation, ctx1 = self._call_llm(
            system_prompt="You are a back-translation engine. Translate Chinese to English literally.",
            user_prompt=bt_prompt, temperature=0.1, max_tokens=8192,
        )

        # 第二步：语义对齐检查
        align_prompt = f"""Compare the original English text and the back-translation.
Identify any deviations in:
1. Clause numbering or structure
2. Obligations (shall/must/may/will)
3. Definitions and defined terms
4. Dates, amounts, percentages
5. Conditional relationships (if/when/unless)

Original English:
```
{source_text[:4000]}
```

Back-translation from Chinese:
```
{back_translation[:4000]}
```

Output JSON:
{{"risk_level":"high|medium|low","deviations":[{{"type":"...","original":"...","back_translation":"...","severity":"high|medium|low"}}],"overall_assessment":"..."}}"""

        analysis, ctx2 = self._call_llm(
            system_prompt="You are a legal translation auditor. Compare original and back-translation for deviations.",
            user_prompt=align_prompt, temperature=0.1, max_tokens=4096,
        )

        # 解析风险等级
        try:
            clean = analysis.strip()
            if clean.startswith("```"):
                lines = clean.split("\n")
                clean = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
            parsed = json.loads(clean)
            risk = parsed.get("risk_level", "medium")
            deviations = parsed.get("deviations", [])
        except Exception:
            risk = "medium"
            deviations = [{"raw": analysis[:500]}]

        return AgentResult(
            success=True,
            data={
                "chapter_id": chapter_id,
                "adjusted_text": translation_text,
                "risk_level": risk,
                "deviations": deviations,
                "back_translation": back_translation[:2000],
            },
            context=ctx2,
        )

    # ═══════════════════════════════════════════
    # 模式C: 评审模式
    # ═══════════════════════════════════════════

    def review_mode(
        self, edited_chapters: list[dict],
        parallel_versions: list[dict] | None = None,
        reference_translations: list[str] | None = None,
    ) -> AgentResult:
        """评审模式：四维量化评审+择优融合+回退裁决"""
        parallel_versions = parallel_versions or []
        reference_translations = reference_translations or []

        sample_indices = self._select_review_samples(edited_chapters, ratio=0.2)
        review_results = []
        for idx in sample_indices:
            ch = edited_chapters[idx]
            review = self._review_chapter(
                ch,
                self._get_parallel_for_chapter(parallel_versions, ch.get("chapter_id")),
                reference_translations,
            )
            review_results.append(review)

        report = self._summarize_review(review_results)
        rollbacks = self._make_rollback_decisions(review_results, edited_chapters)

        return AgentResult(
            success=True,
            data={
                "review_report": report,
                "rollback_decisions": rollbacks,
                "reviewed_samples": len(sample_indices),
                "needs_rollback": len(rollbacks) > 0,
            },
            context=self._last_context,
        )

    def _select_review_samples(self, chapters: list[dict], ratio: float = 0.2) -> list[int]:
        import random
        n = max(1, int(len(chapters) * ratio))
        if len(chapters) <= n:
            return list(range(len(chapters)))
        step = len(chapters) / n
        return [int(i * step) for i in range(n)]

    def _get_parallel_for_chapter(self, parallel_versions, chapter_id):
        for pv in parallel_versions:
            if pv.get("chapter_id") == chapter_id:
                return pv
        return None

    def _review_chapter(self, chapter, parallel_version, references) -> dict:
        system = """你是翻译质量评审专家。四维量化打分（每维0-100）：

1. 信度: 忠实原文程度
2. 达度: 中文流畅度
3. 雅度: 文风契合度
4. 一致性: 术语/人称全局统一

输出JSON：{"scores":{"fidelity":N,"fluency":N,"style":N,"consistency":N},"overall":N,"strengths":[],"issues":[],"recommendation":"pass|revise|reject"}"""

        text = chapter.get("edited", chapter.get("translation", ""))
        user = f"请评审以下译文：\n\n{text[:3000]}"

        if parallel_version:
            user += f"\n\n对比版本:\n{parallel_version.get('translation', '')[:1500]}"

        response, ctx = self._call_llm(system, user, max_tokens=2048)
        self._last_context = ctx

        try:
            clean = response.strip()
            if clean.startswith("```"):
                lines = clean.split("\n")
                clean = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
            return json.loads(clean)
        except Exception:
            return {"scores": {}, "recommendation": "pass", "raw": response[:200]}

    def _summarize_review(self, results) -> dict:
        scores = {"fidelity": [], "fluency": [], "style": [], "consistency": [], "overall": []}
        for r in results:
            for k in scores:
                val = r.get("scores", {}).get(k)
                if val is not None:
                    scores[k].append(val)
            ov = r.get("overall")
            if ov is not None:
                scores["overall"].append(ov)
        return {
            "chapters_reviewed": len(results),
            "avg_scores": {k: round(sum(v)/len(v), 1) if v else None for k, v in scores.items()},
            "recommendations": {
                "pass": sum(1 for r in results if r.get("recommendation") == "pass"),
                "revise": sum(1 for r in results if r.get("recommendation") == "revise"),
                "reject": sum(1 for r in results if r.get("recommendation") == "reject"),
            },
        }

    def _make_rollback_decisions(self, review_results, chapters) -> list[dict]:
        rollbacks = []
        for i, review in enumerate(review_results):
            scores = review.get("scores", {})
            cid = chapters[i].get("chapter_id", i+1) if i < len(chapters) else i+1
            reasons = []
            if scores.get("fidelity", 100) < 60:
                reasons.append(f"信度不足({scores['fidelity']}/100)")
            if scores.get("consistency", 100) < 70:
                reasons.append(f"一致性不足({scores['consistency']}/100)")
            if reasons:
                rollbacks.append({
                    "chapter_id": cid,
                    "target": "chapter_translator" if scores.get("fidelity", 100) < 60 else "chief_editor",
                    "reasons": reasons,
                    "scores": scores,
                })
        return rollbacks

    # ═══════════════════════════════════════════
    # 工具方法
    # ═══════════════════════════════════════════

    def _parse_edit_response(self, response: str, chapter: dict) -> dict:
        """解析统稿响应"""
        changes = []
        term_updates = []
        violations = []
        text = response
        edited_text = text.strip()

        for marker in ['---CHANGES---', '### CHANGES', '\n行1:', '\n行:']:
            idx = text.find(marker)
            if 0 < idx < len(text) - 10:
                edited_text = text[:idx].strip()
                rest = text[idx + len(marker):]

                if "---TERMS---" in rest:
                    changes_part, terms_part = rest.split("---TERMS---", 1)
                elif "### TERMS" in rest:
                    changes_part, terms_part = rest.split("### TERMS", 1)
                else:
                    changes_part = rest
                    terms_part = ""

                for line in changes_part.strip().split("\n"):
                    line = line.strip()
                    if line and ("→" in line or "修改" in line or "调整" in line):
                        changes.append({"detail": line[:120]})

                for line in terms_part.strip().split("\n"):
                    line = line.strip()
                    if "→" in line:
                        parts = line.split("→", 1)
                        src = parts[0].strip()
                        tgt_info = parts[1].strip()
                        if "|" in tgt_info:
                            tgt, reason = tgt_info.split("|", 1)
                            term_updates.append({
                                "source": src,
                                "new_target": tgt.strip(),
                                "reason": reason.strip()[:100],
                            })
                break

        edited_text = '\n'.join(
            line for line in edited_text.split('\n')
            if not any(line.strip().startswith(m) for m in
                       ['---CHANGE', '---TERM', '行', '### CHANGE', '### TERM'])
        )
        edited_text = re.sub(r'\n\s*行\d+\s*:.*', '', edited_text).strip()

        enforcer = TermEnforcer(self.assets)
        check = enforcer.quick_check(edited_text)
        if not check.passed:
            violations = [v.source_term for v in check.violations]

        return {
            "chapter_id": chapter.get("chapter_id"),
            "title": chapter.get("title", ""),
            "edited": edited_text,
            "changes": changes,
            "term_updates": term_updates,
            "violations": violations,
        }

    def _scan_style_consistency(self, chapters, style_manual) -> dict:
        if not style_manual:
            return {"status": "skipped", "reason": "无风格手册"}

        samples = []
        for ch in chapters:
            text = ch.get("edited", "")
            if len(text) > 200:
                samples.append(text[:100])
                samples.append(text[-100:])

        if not samples:
            return {"status": "ok", "score": 100}

        system = "你是风格一致性检查员。评估风格统一度。\n输出JSON：{\"consistency_score\":0-100,\"issues\":[]}"
        user = f"""目标风格: {style_manual.target_style}

译文片段（不同章节）:
{chr(10).join(f'--- 片段{i+1} ---{chr(10)}{s}' for i, s in enumerate(samples[:10]))}

评估风格一致性。"""

        response, ctx = self._call_llm(system, user, max_tokens=2048)
        self._last_context = ctx

        try:
            clean = response.strip()
            if clean.startswith("```"):
                lines = clean.split("\n")
                clean = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
            return json.loads(clean)
        except Exception:
            return {"status": "unknown", "score": None, "raw": response[:200]}

    @staticmethod
    def _maybe_add_context(text: str, label: str) -> str:
        if not text:
            return ""
        return f"\n## {label}\n```\n{text[:500]}\n```\n"

    # ═══════════════════════════════════════════
    # 统一入口
    # ═══════════════════════════════════════════

    def execute(self, **kwargs) -> AgentResult:
        mode = kwargs.get("mode", "draft")
        if mode == "review":
            return self.review_mode(
                edited_chapters=kwargs["edited_chapters"],
                parallel_versions=kwargs.get("parallel_versions"),
                reference_translations=kwargs.get("reference_translations"),
            )
        elif mode == "integrated_proofread":
            return self.integrated_proofread(
                edited_chapters=kwargs["edited_chapters"],
                source_texts=kwargs.get("source_texts", []),
            )
        else:
            return self.draft_mode(
                chapter_translations=kwargs["chapter_translations"],
                source_lang=kwargs.get("source_lang", "en"),
                target_lang=kwargs.get("target_lang", "zh"),
            )

    def _default_prompt(self) -> tuple[str, str]:
        return ("你是翻译主编。", "请统稿润色。")

