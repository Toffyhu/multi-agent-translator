"""④ 主编终审Agent — 统稿+评审双模式（合并后）

核心职责（两阶段串行，同一Agent不同模式）：

模式A — 统稿模式:
1. 衔接润色: 统一章节衔接处、前后呼应内容的表述
2. 风格校准: 修正偏离目标风格的译法
3. 表达优化: 消除翻译腔，优化中文表达
4. 术语终审: 对存疑术语给出最终译法

模式B — 评审模式（统稿后间隔冷却期执行）:
1. 四维量化评审: 信度/达度/雅度/一致性
2. 多版本择优融合（如有平行版本）
3. 回退裁决: 判定质量不达标时触发回退

关键设计: 两遍之间需间隔冷却期，模拟人类编辑工作节奏
"""

from __future__ import annotations

import json

from src.agents.base import BaseAgent, AgentResult
from src.assets.schema import AssetStore
from src.assets.term_enforcer import TermEnforcer


class ChiefEditorAgent(BaseAgent):
    """主编终审Agent — 统稿模式 + 评审模式"""

    agent_name = "chief_editor"
    agent_description = "主编终审：统稿润色+多版本评审+回退裁决"

    # ─── 模式A: 统稿模式 ───

    def draft_mode(
        self,
        chapter_translations: list[dict],
        source_lang: str = "en",
        target_lang: str = "zh",
    ) -> AgentResult:
        """
        统稿模式：统一润色全部章节。

        Args:
            chapter_translations: [{"chapter_id": 1, "title": "...", "translation": "..."}, ...]
            source_lang: 源语言
            target_lang: 目标语言

        Returns:
            AgentResult.data 包含 {edited_chapters, term_updates, style_report}
        """
        style_manual = self.assets.get_style_manual()
        terminology = self.assets.get_terminology()
        book_analysis = self.assets.get_book_analysis()

        # 对每章执行统稿
        edited_chapters = []
        all_term_updates = []
        total_violations = 0

        for i, ch in enumerate(chapter_translations):
            # 获取前后章衔接上下文
            prev_translation = chapter_translations[i - 1]["translation"][-300:] if i > 0 else ""
            next_translation = chapter_translations[i + 1]["translation"][:300] if i < len(chapter_translations) - 1 else ""

            result = self._edit_chapter(
                ch, prev_translation, next_translation,
                style_manual, terminology, book_analysis,
            )
            edited_chapters.append(result)  # 保存完整字典，含edited/text/changes等字段
            all_term_updates.extend(result.get("term_updates", []))
            total_violations += len(result.get("violations", []))

        # 更新术语表（主编终审可以修改术语）
        for update in all_term_updates:
            try:
                self.assets.update_term_entry(
                    source=update["source"],
                    new_target=update["new_target"],
                    notes=f"主编终审核定: {update.get('reason', '')}",
                )
            except Exception:
                pass

        # 风格一致性扫描
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
        self,
        chapter: dict,
        prev_translation: str,
        next_translation: str,
        style_manual,
        terminology,
        book_analysis,
    ) -> dict:
        """统稿单个章节"""

        system = """你是一位出版社资深责任编辑。你的工作是审校初译稿，把好语言关和质量关。

## 你的角色定位

你就像是出版社的终审编委，面对译者的初稿，你需要用专业眼光发现并修正各类语言质量问题。初译者的工作是发挥文学创造力，你的工作是确保语言规范和质量达标。两者是合作关系，不是上下级。

## 审校原则

**1. 保护文学性，修正技术问题**
初译稿的语言风格、句式选择、修辞手法是译者的创作空间，不要改动。你只修正明确的语言质量和规范问题。

**2. 举一反三，全面把关**
不要只盯着某几类问题。你需要运用全面的语言判断力，发现并修正任何影响译文质量的问题，包括但不限于：

- **代词一致性**：同一角色在全文中指代一致（动物用"它"，人称对应准确）
- **标点规范**：中文标点使用是否正确（对话结束用句号/问号/感叹号，引号前用冒号）
- **中英标点差异检查**：中文译文中不应出现英文标点符号。特别注意：英文逗号,→中文逗号，；英文句号.→中文句号。；英文省略号...→中文省略号……；英文引号"→中文引号"；英文括号( )→中文括号（）；英文破折号-→中文破折号——。全文扫描，逐一修正。
- **语法通顺**：句子是否存在语病、成分残缺、搭配不当
- **术语统一**：同一概念前后表述是否一致，专有名词译法是否统一
- **表达自然度**：是否存在明显的翻译腔（欧化句式、生硬搭配）
- **逻辑连贯**：上下文衔接是否自然，因果关系是否清晰
- **数字规范**：数字、日期、单位的表达是否符合中文习惯
- **逻辑链校验**：遇到中文译文读起来逻辑不通或语义含混的地方，应追溯原文确认是否存在误译或遗漏的逻辑关系。例如英文中的条件关系（if/when/unless）、指代关系（it/he/they回指什么）、反讽语气，中文表达不清时要补全逻辑。不要直接在含混的译文上"润色"，要先理清原文含义再决定如何纠正。
- **代词追踪检查**：每段审阅结束后，反向追踪所有"他/她/它/他们/她们"的指代对象。如果某段中出现3个以上不同人物，"他"超过2次且未明确主语，应替换为具体人名。特别注意叙述者切换场景（如《另一个女人》中"我"（叙述者）和"他"（朋友）交替出现时）。
- **长句密度监控**：扫描全文中超过50字（约3行）的长句。如果连续出现3个以上长句，应在中间插入短句或拆分。优先拆解英文式从句嵌套结构（"当..."、"虽然..."、"如果..."等开头的长状语前置句）。

**3. 修改越少越好，每处修改必须有明确理由**
好的初译稿应该基本保持原样。你的修改应当像外科手术一样精准——只动有问题的地方，好地方不动。

## 输出格式
在优化后的译文后，用 `---CHANGES---` 分隔，列出所有修改及其理由。
格式: `行X: [原译] → [新译] | 理由:xxx`
然后，用 `---TERMS---` 列出术语终审决定。
格式: `存疑词 → 最终译法 | 理由:xxx`"""

        user = f"""请统稿润色以下章节译文：

### 第{chapter.get('chapter_id', '?')}章 {chapter.get('title', '')}

{self._maybe_add_context(prev_translation, '前章衔接参考（末尾）')}

### 本章译文：

{chapter.get('translation', chapter.get('raw_translation', ''))}

{self._maybe_add_context(next_translation, '后章衔接参考（开头）')}

请输出统稿后的译文。"""

        response, ctx = self._call_llm(system, user, max_tokens=8192)
        self._last_context = ctx

        return self._parse_edit_response(response, chapter)

    def _parse_edit_response(self, response: str, chapter: dict) -> dict:
        """解析统稿响应（兼容多种LLM输出格式）"""
        changes = []
        term_updates = []
        violations = []

        text = response
        edited_text = text.strip()

        # 尝试多种格式匹配CHANGES/TERMS分隔
        # 格式1: ---CHANGES--- ... ---TERMS---（默认格式）
        # 格式2: ### CHANGES ... ### TERMS
        # 格式3: 行号格式混入正文

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
                        parts_term = line.split("→", 1)
                        source_term = parts_term[0].strip()
                        target_info = parts_term[1].strip()
                        if "|" in target_info:
                            target, reason = target_info.split("|", 1)
                            term_updates.append({
                                "source": source_term,
                                "new_target": target.strip(),
                                "reason": reason.strip()[:100],
                            })

                break  # 只处理第一个匹配到的标记

        # 二次清理：确保edited_text不含任何标记行
        edited_text = '\n'.join(
            line for line in edited_text.split('\n')
            if not any(line.strip().startswith(m) for m in 
                       ['---CHANGE', '---TERM', '行', '### CHANGE', '### TERM', ''] )
            or not any(kw in line for kw in [' → ', '| 理由:', '保留此译', '无改动', '不作修改', '无语病'])
        )

        # 清理只有行号格式的行（"行37: "）
        import re
        edited_text = re.sub(r'\n\s*行\d+\s*:.*', '', edited_text)
        edited_text = edited_text.strip()

        # 术语快速扫描
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

    def _scan_style_consistency(self, chapters: list[dict], style_manual) -> dict:
        """风格一致性快速扫描"""
        if not style_manual:
            return {"status": "skipped", "reason": "无风格手册"}

        # 抽样检查（取每章开头和结尾）
        samples = []
        for ch in chapters:
            text = ch.get("edited", "")
            if len(text) > 200:
                samples.append(text[:100])
                samples.append(text[-100:])

        if not samples:
            return {"status": "ok", "score": 100}

        system = """你是风格一致性检查员。对比以下译文片段，评估风格统一度。

输出JSON：
{"consistency_score": 0-100, "issues": ["问题1", "问题2"]}"""

        user = f"""目标风格: {style_manual.target_style}

译文片段（来自不同章节）:
{chr(10).join(f'--- 片段{i+1} ---{chr(10)}{s}' for i, s in enumerate(samples[:10]))}

请评估风格一致性。"""

        response, ctx = self._call_llm(system, user, max_tokens=2048)
        self._last_context = ctx

        try:
            text = response.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
            return json.loads(text)
        except Exception:
            return {"status": "unknown", "score": None, "raw": response[:200]}

    @staticmethod
    def _maybe_add_context(text: str, label: str) -> str:
        if not text:
            return ""
        return f"\n## {label}\n```\n{text[:500]}\n```\n"

    # ─── 模式B: 评审模式 ───

    def review_mode(
        self,
        edited_chapters: list[dict],
        parallel_versions: list[dict] | None = None,
        reference_translations: list[str] | None = None,
    ) -> AgentResult:
        """
        评审模式：四维量化评审 + 择优融合 + 回退裁决。

        Args:
            edited_chapters: 主编统稿后的章节
            parallel_versions: 其他模型的平行翻译版本（如有）
            reference_translations: 已有公开译文片段

        Returns:
            AgentResult.data 包含 {final_chapters, review_report, rollback_decisions}
        """
        parallel_versions = parallel_versions or []
        reference_translations = reference_translations or []

        # 抽样评审（全书的20%段落做深度评审）
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

        # 汇总评审报告
        report = self._summarize_review(review_results)

        # 回退裁决
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
        """选择需深度评审的章节索引（均匀抽样）"""
        import random
        n = max(1, int(len(chapters) * ratio))
        if len(chapters) <= n:
            return list(range(len(chapters)))
        step = len(chapters) / n
        return [int(i * step) for i in range(n)]

    def _get_parallel_for_chapter(self, parallel_versions: list[dict], chapter_id: int) -> dict | None:
        for pv in parallel_versions:
            if pv.get("chapter_id") == chapter_id:
                return pv
        return None

    # ─── 模式C: 融合模式（三版融合） ───

    def fusion_mode(
        self,
        chapter_id: int,
        chapter_title: str,
        v1_direct: str,
        v2_rewrite: str,
        style_note: str = "",
    ) -> AgentResult:
        """
        融合模式：将直译版(V1)和再创版(V2)融合为最优版本。

        思路（已验证于Moxon's Master实验）：
        "以A为骨，以B为肉" — V1保真，V2保畅，融合取两者之长。

        Args:
            chapter_id: 章节编号
            chapter_title: 章节标题
            v1_direct: Translator直译版
            v2_rewrite: Rewriter再创版
            style_note: 风格提示

        Returns:
            AgentResult: 融合版译文
        """
        prompt = f"""你是一个顶级主编。你有两个版本的同章译文，需要你融合出最优版本。

## 短篇信息
- 章节: {chapter_title}
- 风格提示: {style_note or "保留原文叙事特色，用流畅中文表达"}

## 版本A（直译版）— 准确度高，保留了原文的句式和结构
``` 
{v1_direct[:3000]}
```

## 版本B（再创版）— 中文流畅，像中国作家写的，但可能偏离原文细节
```
{v2_rewrite[:3000]}
```

## 融合要求

你的任务是将两版融合为一版：

1. **以A为骨**：以版本A的**事实准确性和结构**为骨架
2. **以B为肉**：以版本B的**中文表达和语言血肉**填充
3. **融合规则**：
   - 凡涉及事实（人名、地名、时间、事件顺序、数字）→ 以A为准
   - 凡涉及遣词造句、句式结构、段落节奏 → 以B为准
   - 如果B有遗漏或曲解了关键细节 → 从A补回
   - 如果A有明显的翻译腔 → 用B的方式重写
4. **最终效果**：读起来像中国作家写的，但细节上每个字都经得起推敲

直接输出融合后的译文，不要输出说明。"""

        text, ctx = self._call_llm(
            system_prompt="你是顶级的文学翻译主编。你的特長是融合不同版本的译文，"
                         "取长补短，产出超越任一版本的终稿。",
            user_prompt=prompt,
            temperature=0.4,
        )

        return AgentResult(
            success=True,
            data={
                "fused_text": text,
                "chapter_id": chapter_id,
                "chapter_title": chapter_title,
            },
            context=ctx,
        )

    def _review_chapter(
        self,
        chapter: dict,
        parallel_version: dict | None,
        references: list[str],
    ) -> dict:
        """对单个章节执行四维评审"""

        system = """你是翻译质量评审专家。请对译文进行四维量化打分（每维0-100分）：

1. **信度** (fidelity): 忠实原文程度，有无错译、漏译、过度演绎
2. **达度** (fluency): 中文流畅度，是否符合中文语境
3. **雅度** (style): 文风契合度，是否匹配目标风格
4. **一致性** (consistency): 术语、人名、前后表述是否全局统一

输出JSON：
{
  "scores": {"fidelity": 0-100, "fluency": 0-100, "style": 0-100, "consistency": 0-100},
  "overall": 0-100,
  "strengths": ["优点"],
  "issues": ["问题"],
  "recommendation": "pass|revise|reject"
}"""

        text = chapter.get("edited", chapter.get("translation", ""))
        user = f"请评审以下译文：\n\n{text[:3000]}"

        if parallel_version:
            parallel_text = parallel_version.get("translation", "")
            user += f"\n\n对比版本:\n{parallel_text[:1500]}"

        response, ctx = self._call_llm(system, user, max_tokens=2048)
        self._last_context = ctx

        try:
            text_resp = response.strip()
            if text_resp.startswith("```"):
                lines = text_resp.split("\n")
                text_resp = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
            return json.loads(text_resp)
        except Exception:
            return {"scores": {}, "recommendation": "pass", "raw": response[:200]}

    def _summarize_review(self, results: list[dict]) -> dict:
        """汇总评审结果"""
        scores = {"fidelity": [], "fluency": [], "style": [], "consistency": [], "overall": []}
        for r in results:
            for k in scores:
                val = r.get("scores", {}).get(k)
                if val is not None:
                    scores[k].append(val)
            overall = r.get("overall")
            if overall is not None:
                scores["overall"].append(overall)

        return {
            "chapters_reviewed": len(results),
            "avg_scores": {k: round(sum(v) / len(v), 1) if v else None for k, v in scores.items()},
            "recommendations": {
                "pass": sum(1 for r in results if r.get("recommendation") == "pass"),
                "revise": sum(1 for r in results if r.get("recommendation") == "revise"),
                "reject": sum(1 for r in results if r.get("recommendation") == "reject"),
            },
        }

    def _make_rollback_decisions(
        self,
        review_results: list[dict],
        chapters: list[dict],
    ) -> list[dict]:
        """制定回退决策"""
        rollbacks = []

        for i, review in enumerate(review_results):
            scores = review.get("scores", {})
            chapter_id = chapters[i].get("chapter_id", i + 1) if i < len(chapters) else i + 1

            # 触发条件检查
            reasons = []
            if scores.get("fidelity", 100) < 60:
                reasons.append(f"信度不足 ({scores['fidelity']}/100)")
            if scores.get("consistency", 100) < 70:
                reasons.append(f"风格一致性不足 ({scores['consistency']}/100)")

            if reasons:
                rollbacks.append({
                    "chapter_id": chapter_id,
                    "target": "chapter_translator" if scores.get("fidelity", 100) < 60 else "chief_editor",
                    "reasons": reasons,
                    "scores": scores,
                })

        return rollbacks

    # ─── 统一入口 ───

    def execute(self, **kwargs) -> AgentResult:
        """统一入口，根据mode参数自动选择"""
        mode = kwargs.get("mode", "draft")
        if mode == "review":
            return self.review_mode(
                edited_chapters=kwargs["edited_chapters"],
                parallel_versions=kwargs.get("parallel_versions"),
                reference_translations=kwargs.get("reference_translations"),
            )
        else:
            return self.draft_mode(
                chapter_translations=kwargs["chapter_translations"],
                source_lang=kwargs.get("source_lang", "en"),
                target_lang=kwargs.get("target_lang", "zh"),
            )

    def _default_prompt(self) -> tuple[str, str]:
        return ("你是翻译主编。", "请统稿润色。")
