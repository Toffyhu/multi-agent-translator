"""③ 分章翻译Agent — 单章初译 + 术语遵循 + 存疑标注

核心职责（只做翻译，不做润色不统稿）：
1. 翻译单个章节
2. 严格遵循全局术语表（强制级术语必须精确使用）
3. 参考上一章末尾和下一章开头保证衔接
4. 标注存疑译法和不确定的专有名词

设计要点：
- 上下文窗口: 自动带上前后章的边界片段
- 术语强制校验: 遇到规范内术语必须采用指定译法
- 伏笔注入: 从全书分析报告中提取本章相关伏笔关系
- 并行友好: 每个章节独立，可并行调度
"""

from __future__ import annotations

from src.agents.base import BaseAgent, AgentResult, AgentContext
from src.assets.schema import AssetStore
from src.assets.term_enforcer import TermEnforcer


class ChapterTranslatorAgent(BaseAgent):
    """分章翻译Agent — 单章初译专用"""

    agent_name = "chapter_translator"
    agent_description = "单章初译：精准翻译+术语遵循+存疑标注"

    def execute(
        self,
        chapter_id: int,
        chapter_text: str,
        chapter_title: str = "",
        source_lang: str = "en",
        target_lang: str = "zh",
        prev_chapter_tail: str = "",
        next_chapter_head: str = "",
        scenario: str = "default",
    ) -> AgentResult:
        """
        翻译单个章节。

        Args:
            chapter_id: 章节ID
            chapter_text: 章节原文
            chapter_title: 章节标题
            source_lang: 源语言
            target_lang: 目标语言
            prev_chapter_tail: 上一章末尾（用于衔接）
            next_chapter_head: 下一章开头（用于衔接）
            scenario: 翻译场景 (default/legal/literary/terminology_heavy)

        Returns:
            AgentResult.data 包含 {translation, doubts, term_compliance}
        """
        # 1. 获取全局上下文
        terminology = self.assets.get_terminology()
        style_manual = self.assets.get_style_manual()
        chapter_context = self.assets.get_context_for_chapter(chapter_id)

        # 2. 构建翻译Prompt
        system_prompt, user_prompt = self._build_translation_prompt(
            chapter_id, chapter_title, chapter_text,
            source_lang, target_lang,
            prev_chapter_tail, next_chapter_head,
            terminology, style_manual, chapter_context,
        )

        # 3. 调用LLM翻译（根据模式调节温度）
        translation_temp = {
            "literary": 0.6,
            "legal": 0.1,
            "academic": 0.3,
        }.get(self.mode.value if self.mode else "literary", 0.6)

        response_text, context = self._call_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            scenario=scenario,
            temperature=translation_temp,
            max_tokens=max(min(len(chapter_text) * 3, 8192), 256),
        )

        # 4. 解析翻译结果和存疑标注
        translation, doubts = self._parse_translation_response(response_text)

        # 5. 术语强制校验
        enforcer = TermEnforcer(self.assets)
        term_check = enforcer.enforce(chapter_text, translation)

        return AgentResult(
            success=True,
            data={
                "chapter_id": chapter_id,
                "chapter_title": chapter_title,
                "source_text": chapter_text,  # 保留原文供法律/学术模式编辑器对照
                "translation": term_check.corrected_text if not term_check.passed else translation,
                "raw_translation": translation,
                "doubts": doubts,
                "term_compliance": {
                    "rate": term_check.compliance_rate,
                    "passed": term_check.passed,
                    "details": term_check.violation_details,
                },
                "scenario": scenario,
            },
            context=context,
            warnings=(
                [f"术语校验未通过 ({term_check.compliance_rate:.0%}):\n{term_check.violation_details}"]
                if not term_check.passed
                else []
            ),
        )

    def _build_translation_prompt(
        self,
        chapter_id: int,
        chapter_title: str,
        chapter_text: str,
        source_lang: str,
        target_lang: str,
        prev_tail: str,
        next_head: str,
        terminology,
        style_manual,
        chapter_context: dict,
    ) -> tuple[str, str]:
        """构建翻译Prompt — 包含术语表+风格规则+伏笔上下文"""

        # 术语强制列表
        term_rules = ""
        if terminology:
            mandatory = terminology.get_mandatory_map()
            if mandatory:
                term_items = "\n".join(
                    f"  - {src} → **{tgt}** (强制统一)"
                    for src, tgt in list(mandatory.items())[:30]
                )
                term_rules = f"""
## 术语强制规范（必须严格遵循）
以下术语在翻译时必须使用指定译法，不得自行变更：

{term_items}

如遇到规范外的专有名词或不确定的术语，请标注 [存疑:xxx]。
"""
            flexible = [e for e in terminology.entries if e.level.value == "flexible"]
            if flexible:
                flex_items = "\n".join(
                    f"  - {e.source} → 建议: {e.target}（灵活处理）"
                    for e in flexible[:10]
                )
                term_rules += f"""
## 建议译法（灵活遵循）
{flex_items}
"""

        # 风格规则
        style_rules = ""
        if style_manual and style_manual.dimensions:
            style_rules = f"""
## 翻译风格要求
目标风格: {style_manual.target_style}
目标读者: {style_manual.target_audience}

具体规则:
"""
            for dim in style_manual.dimensions:
                style_rules += f"- **{dim.name}**: {dim.target_range}\n"
            if style_manual.forbidden_patterns:
                style_rules += f"\n禁止表述: {', '.join(style_manual.forbidden_patterns[:5])}"

        # 伏笔上下文
        foreshadowing_rules = ""
        if chapter_context:
            fo_in = chapter_context.get("foreshadowing_in", [])
            fo_out = chapter_context.get("foreshadowing_out", [])
            if fo_in or fo_out:
                foreshadowing_rules = "\n## 伏笔呼应标记\n"
                if fo_in:
                    foreshadowing_rules += "本章埋下的伏笔（请确保翻译时保持伏笔的可辨识性）:\n"
                    for f in fo_in:
                        foreshadowing_rules += f"  - 后续第{f.get('target_chapter', '?')}章呼应: {f.get('source_context', '')[:60]}\n"
                if fo_out:
                    foreshadowing_rules += "本章回应的前文伏笔（请注意与之前章节的表述一致）:\n"
                    for f in fo_out:
                        foreshadowing_rules += f"  - 呼应第{f.get('source_chapter', '?')}章: {f.get('source_context', '')[:60]}\n"

        # 上下文衔接（已由orchestrator安全截断，此处不再二次截断）
        context_rules = ""
        if prev_tail:
            context_rules += f"\n## 上一章末尾（供衔接参考）\n```\n{prev_tail}\n```\n"
        if next_head:
            context_rules += f"\n## 下一章开头（供衔接参考）\n```\n{next_head}\n```\n"

        system = f"""你是一位专业的{source_lang}→{target_lang}书籍翻译。

你的任务是翻译**一个章节**的初稿。要求：

1. **忠实原文**: 准确传达原文含义，不漏译、不增译、不过度演绎
2. **术语强制遵循**: 遇到规范表中指定的术语，必须使用指定译法
3. **风格对齐**: 遵循风格手册中的具体规则
4. **存疑标注**: 不确定的译法用 [存疑:原因] 标注
5. **保持格式**: 保留原文的段落划分、强调标记

{self._mode_prohibition_rules()}
{term_rules}
{style_rules}
{foreshadowing_rules}
{context_rules}

输出格式：
在译文正文后，用 `---DOUBTS---` 分隔，列出所有存疑标注的说明。
如果没有存疑，省略此分隔符。"""

        user = f"""请翻译以下章节：

### 第{chapter_id}章 {chapter_title}

{chapter_text}

请输出译文。"""

        return system, user

    def _mode_prohibition_rules(self) -> str:
        """
        模式专属禁止清单——告诉直译阶段绝对不能做什么。
        这些是硬约束，比术语表更底层，优先于所有其他规则。
        """
        if not self.mode:
            return ""

        if self.mode.value == "legal":
            return """
## ⚖️ 法律翻译禁止清单（绝对不可违反）

- **禁止口语化表达**：不得使用口语、俚语、网络用语
- **禁止省略条款编号**：Article/Section/Clause 的编号必须逐字保留，不得合并、拆分、重编号
- **禁止同义替换术语**：同一术语在全文中必须使用完全相同的译法，不得为了"文采"而换词
- **禁止改变条款结构**：条件句（if/unless/subject to）的逻辑顺序不可调整
- **禁止弱化/强化义务**：shall=应当/须（不可译为"将""会"），may=有权/可（不可译为"应该"）
- **禁止忽略大写强调**：全大写条款（如 IN NO EVENT SHALL...）必须在译文中以加粗或强调方式标记
- **禁止添加解释性文字**：原文没有的解释、补充、说明都不要加。法律文本不欢迎"翻译者的理解"
- **禁止改变数字格式**：金额、日期、百分比必须与原文字符级精确对应，不得四舍五入或转换单位"""

        if self.mode.value == "academic":
            return """
## 🎓 学术翻译禁止清单（绝对不可违反）

- **禁止翻译公式**：所有 LaTeX 数学公式（$$...$$ 或 $...$）原样保留，不得翻译任何符号
- **禁止修改引用编号**：[1][2,3][4-7] 等引用编号不得遗漏、错位或改动
- **禁止翻译算法变量名**：算法伪代码中的变量名、函数名保留英文
- **禁止翻译图表编号**：Figure X / Table Y 的编号必须原样保留，不能翻译为"图X/表Y"而丢失编号对应关系
- **禁止口语化**：学术论文不允许口语化表达，如"搞定了""差不多"等
- **禁止误译 state-of-the-art**：应译为"当前最优/最先进"，不可译为"最新的"（那是 latest）
- **禁止误译 baseline**：应译为"基线（模型/方法）"，不可译为"基础"或"基准"
- **禁止改变实验数据精度**：百分比、p值、标准差等数字必须字符级精确对应
- **禁止忽略缩略语规范**：英文缩写首次出现时必须标注全称，格式为"全称（ABBR）"或"ABBR（全称）"一致"""

        return ""

    def _parse_translation_response(self, response: str) -> tuple[str, list[dict]]:
        """解析翻译结果，分离正文和存疑标注"""
        if "---DOUBTS---" in response:
            parts = response.split("---DOUBTS---", 1)
            translation = parts[0].strip()
            doubts_text = parts[1].strip()
            doubts = self._parse_doubts(doubts_text)
        else:
            translation = response.strip()
            doubts = []

        return translation, doubts

    def _parse_doubts(self, text: str) -> list[dict]:
        """解析存疑标注"""
        doubts = []
        for line in text.split("\n"):
            line = line.strip()
            if line and ("存疑" in line or "不确定" in line):
                doubts.append({"note": line})
        return doubts

    def _default_prompt(self) -> tuple[str, str]:
        return (
            "你是专业翻译，请翻译以下文本。严格遵循术语表。",
            "原文：{text}",
        )
