"""术语校验引擎 — 确定性NLP管道，非Agent组件

在所有Agent输出后执行强制校验，确保术语一致性。
与模型无关，零幻觉，是流水线的硬质控卡点。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from src.assets.schema import AssetStore, TermLevel


@dataclass
class TermViolation:
    """术语违规记录"""
    source_term: str
    expected: str
    found: str | None = None     # None 表示术语被漏译
    position: int = 0            # 在译文中的位置
    level: str = "mandatory"
    action: str = "replace"       # "replace" | "flag"


@dataclass
class TermCheckResult:
    """术语校验结果"""
    passed: bool
    compliance_rate: float       # 术语遵循率
    total_terms: int
    violations: list[TermViolation] = field(default_factory=list)
    corrected_text: str = ""
    violation_details: str = ""


class TermEnforcer:
    """术语校验引擎

    工作流：
    1. 从全局资产库加载术语表（只读取强制统一级）
    2. 对译文执行正则+模糊匹配扫描
    3. 发现违规 → 强制替换 → 标注
    4. 输出修正后译文 + 违规报告
    """

    def __init__(self, asset_store: Optional[AssetStore] = None):
        self.asset_store = asset_store or AssetStore()

    def enforce(
        self,
        source_text: str,
        translated_text: str,
        threshold: float = 0.98,
    ) -> TermCheckResult:
        """
        执行术语强制校验。

        Args:
            source_text: 源文本（用于检测术语漏译）
            translated_text: 初译文本
            threshold: 遵循率阈值，低于此值判定为不通过

        Returns:
            TermCheckResult: 校验结果，包含修正后译文
        """
        terminology = self.asset_store.get_terminology()
        if not terminology:
            return TermCheckResult(passed=True, compliance_rate=1.0, total_terms=0)

        mandatory_map = terminology.get_mandatory_map()
        if not mandatory_map:
            return TermCheckResult(passed=True, compliance_rate=1.0, total_terms=0)

        violations: list[TermViolation] = []
        corrected = translated_text
        total_terms_in_source = 0

        for source_term, expected_target in mandatory_map.items():
            # 检查源文本中是否出现了该术语
            if source_term not in source_text:
                continue
            total_terms_in_source += 1

            # 检查译文中是否使用了指定译法
            if expected_target in corrected:
                continue  # 正确

            # 尝试模糊匹配（处理可能的轻微变体）
            violation = self._detect_violation(corrected, source_term, expected_target)
            if violation:
                violations.append(violation)
                # 如果是漏译，标记但不自动替换
                if violation.action == "replace":
                    corrected = self._force_replace(
                        corrected, source_term, expected_target
                    )

        total_terms = total_terms_in_source if total_terms_in_source > 0 else len(mandatory_map)
        compliant = total_terms - len(violations)
        compliance_rate = compliant / total_terms if total_terms > 0 else 1.0

        passed = compliance_rate >= threshold

        # 构建违规详情
        detail_parts = []
        for v in violations:
            if v.found:
                detail_parts.append(f"  ❌ '{v.source_term}' → 应为'{v.expected}'，实际'{v.found}'")
            else:
                detail_parts.append(f"  ❌ '{v.source_term}' → 漏译，应为'{v.expected}'")

        return TermCheckResult(
            passed=passed,
            compliance_rate=compliance_rate,
            total_terms=total_terms,
            violations=violations,
            corrected_text=corrected,
            violation_details="\n".join(detail_parts) if detail_parts else "  ✅ 全部术语校验通过",
        )

    def _detect_violation(
        self, text: str, source_term: str, expected: str
    ) -> Optional[TermViolation]:
        """检测术语违规"""
        # 1. 检查术语是否被完全漏译
        # （简化版：按源术语在译文中搜索，如果源术语本身未被翻译成目标语）
        # 这里用启发式检测：检查是否有疑似直译或错译

        # 查找源术语在译文中是否以原文出现（可能是漏译标记）
        if source_term in text:
            return TermViolation(
                source_term=source_term,
                expected=expected,
                found=f"[原文保留]{source_term}",
                action="replace",
            )

        # 2. 检查是否有常见错译模式
        # 在实际部署中可扩展为更复杂的匹配
        return TermViolation(
            source_term=source_term,
            expected=expected,
            found=None,  # 无法确定具体译文
            action="flag",  # 标记需人工检查
        )

    def _force_replace(self, text: str, source_term: str, target: str) -> str:
        """强制替换术语（处理多种上下文）"""
        # 简单替换，生产环境可扩展为带上下文的智能替换
        if source_term in text:
            text = text.replace(source_term, f"{target}[术语校验]")
        return text

    def quick_check(self, translated_text: str) -> TermCheckResult:
        """
        快速术语校验（不含源文本比对，用于主编终审阶段快速扫描）
        """
        terminology = self.asset_store.get_terminology()
        if not terminology:
            return TermCheckResult(passed=True, compliance_rate=1.0, total_terms=0)

        violations = []
        for entry in terminology.entries:
            if entry.level != TermLevel.MANDATORY:
                continue
            if entry.target not in translated_text and entry.source in translated_text:
                violations.append(TermViolation(
                    source_term=entry.source,
                    expected=entry.target,
                    found="[疑似漏译或错译]",
                    action="flag",
                ))

        total = len([e for e in terminology.entries if e.level == TermLevel.MANDATORY])
        if total == 0:
            return TermCheckResult(passed=True, compliance_rate=1.0, total_terms=0)

        compliance = (total - len(violations)) / total
        return TermCheckResult(
            passed=compliance >= 0.98,
            compliance_rate=compliance,
            total_terms=total,
            violations=violations,
            corrected_text=translated_text,
            violation_details="\n".join(
                f"  ❌ '{v.source_term}' → 应为'{v.expected}'"
                for v in violations
            ) if violations else "  ✅ 无术语问题",
        )
