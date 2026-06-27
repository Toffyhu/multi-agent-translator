"""
法律翻译确定性校验引擎 + 学术保护规则

纯确定性算法（正则/树/字典/自动机），不调用任何 LLM。
提供条款树比对、术语一致性扫描、交叉引用验证、金额校验、标点格式检测、
高风险标记、文档骨架解析，以及学术翻译的公式/引用/误译保护等功能。
"""

from __future__ import annotations

import re
import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any


# ============================================================================
# 数据模型
# ============================================================================

@dataclass
class Issue:
    """校验问题"""
    type: str          # 问题类型: clause_tree / term / cross_ref / amount / punctuation / date
    location: str      # 位置描述（行号或文本片段）
    detail: str        # 详细描述
    severity: str      # 严重级别: error / warning / info


# ============================================================================
# 1. ClauseTreeVerifier — 条款树校验
# ============================================================================

class ClauseTreeVerifier:
    """
    条款树校验器。

    从文本行中提取条款编号，构建嵌套层级树，并对比源文与译文的条款结构。
    支持中英文编号模式。
    """

    # ---------- 编号识别正则 ----------
    # 英文/数字编号
    RE_ARTICLE = re.compile(
        r'^(?:Article|Section|Clause|CHAPTER|PART)\s+([IVXLCDM]+|\d+)[\s\.:：]', re.IGNORECASE
    )
    RE_NUMBERED = re.compile(
        r'^(\d{1,3}(?:\.\d{1,3}){0,2})[\s\.、．]'
    )
    RE_ALPHA = re.compile(
        r'^\(([a-z])\)\s'
    )
    RE_ROMAN = re.compile(
        r'^\(([ivxlcdm]+)\)\s', re.IGNORECASE
    )
    RE_PAREN_NUM = re.compile(
        r'^\((\d{1,2})\)\s'
    )

    # 中文编号
    RE_CH_ARTICLE = re.compile(
        r'^第[一二三四五六七八九十百千\d]+条[\s\u3000]*'
    )
    RE_CH_SECTION = re.compile(
        r'^第[一二三四五六七八九十百千\d]+款[\s\u3000]*'
    )
    RE_CH_PAREN = re.compile(
        r'^（([一二三四五六七八九十\d]+)）\s*'
    )
    RE_CH_ALPHA = re.compile(
        r'^（([a-z])）\s*'
    )
    RE_CH_NUM_DOT = re.compile(
        r'^([一二三四五六七八九十\d]+)[、．]\s*'
    )

    # ---------- 中文数字转换 ----------
    _CN_NUM_MAP: dict[str, int] = {
        '一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
        '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
        '十一': 11, '十二': 12, '十三': 13, '十四': 14, '十五': 15,
        '十六': 16, '十七': 17, '十八': 18, '十九': 19, '二十': 20,
    }

    @staticmethod
    def _cn_to_int(s: str) -> int:
        """中文数字字符串转整数（支持 一～九十九）"""
        if s.isdigit():
            return int(s)
        if s in ClauseTreeVerifier._CN_NUM_MAP:
            return ClauseTreeVerifier._CN_NUM_MAP[s]
        # 二十一、三十五 等
        m = re.match(r'^([二三四五六七八九]?十)([一二三四五六七八九])?$', s)
        if m:
            tens = m.group(1)
            ones = m.group(2) or ''
            result = 10 if tens == '十' else ClauseTreeVerifier._CN_NUM_MAP.get(tens[0], 0) * 10
            result += ClauseTreeVerifier._CN_NUM_MAP.get(ones, 0)
            return result
        return 0

    # ---------- 核心方法 ----------

    def build_tree(self, lines: list[str]) -> dict:
        """
        从文本行构建条款编号嵌套树。

        Args:
            lines: 文本行列表

        Returns:
            {
                'number': str,       # 编号（如 "1", "3.a" 等）
                'depth': int,        # 嵌套深度（0 为根）
                'parent': str | None,# 父编号
                'children': list,    # 子节点列表（同结构）
                'text': str,         # 该条款的文本片段
                'line': int,         # 行号（从 0 起）
            }
            根的 number 为 "__ROOT__", depth=-1。
        """
        root: dict[str, Any] = {
            'number': '__ROOT__',
            'depth': -1,
            'parent': None,
            'children': [],
            'text': '',
            'line': -1,
        }
        stack: list[dict] = [root]
        current_path: list[str] = []

        for line_idx, raw_line in enumerate(lines):
            line = raw_line.strip()
            if not line:
                continue

            # 尝试匹配各级编号
            match = self._match_clause_number(line)
            if match is None:
                # 非编号行，附加到当前节点的文本
                if stack:
                    stack[-1]['text'] += line + ' '
                continue

            number, depth, label_type = match

            # 确定此节点应挂在哪个深度
            while len(stack) > 1 and stack[-1]['depth'] >= depth:
                stack.pop()
                if current_path:
                    current_path.pop()

            parent_node = stack[-1]
            parent_number = parent_node['number']

            # 构建完整路径编号
            if depth == 0:
                full_number = number
            else:
                full_number = f"{current_path[-1]}.{number}" if current_path else number

            node: dict[str, Any] = {
                'number': number,
                'full_number': full_number,
                'depth': depth,
                'parent': parent_number,
                'children': [],
                'text': line,
                'line': line_idx,
            }

            parent_node['children'].append(node)
            stack.append(node)
            current_path.append(number)

        return root

    def _match_clause_number(self, line: str) -> tuple[str, int, str] | None:
        """
        匹配行首的条款编号。

        Returns:
            (编号字符串, 深度, 标签类型) 或 None
        """
        # 深度 0: Article N / Section N / Clause N / 第N条
        m = self.RE_ARTICLE.match(line)
        if m:
            return (m.group(1) or m.group(0).strip(), 0, 'article')

        # 深度 0: 中文"第N条"
        m = self.RE_CH_ARTICLE.match(line)
        if m:
            return (m.group(0).strip(), 0, 'article_cn')

        # 深度 0: 纯数字编号 (1., 2., 1.1. 等)
        m = self.RE_NUMBERED.match(line)
        if m:
            num = m.group(1)
            depth = num.count('.')
            return (num, depth, 'numbered')

        # 深度 1-2: (a), (1), (i) 等字母/数字括号
        m = self.RE_ALPHA.match(line)
        if m:
            return (m.group(1), 1, 'alpha')
        m = self.RE_PAREN_NUM.match(line)
        if m:
            return (m.group(1), 1, 'paren_num')
        m = self.RE_ROMAN.match(line)
        if m:
            return (m.group(1).lower(), 1, 'roman')

        # 中文括号编号
        m = self.RE_CH_PAREN.match(line)
        if m:
            num_str = m.group(1)
            if num_str.isdigit():
                return (num_str, 1, 'ch_paren_num')
            return (str(self._cn_to_int(num_str)), 1, 'ch_paren_num')
        m = self.RE_CH_ALPHA.match(line)
        if m:
            return (m.group(1), 1, 'ch_alpha')

        # 中文"第N款"
        m = self.RE_CH_SECTION.match(line)
        if m:
            return (m.group(0).strip(), 1, 'section_cn')

        # 中文顿号编号（一、二、三、）
        m = self.RE_CH_NUM_DOT.match(line)
        if m:
            num_str = m.group(1)
            if num_str.isdigit():
                return (num_str, 1, 'ch_num_dot')
            return (str(self._cn_to_int(num_str)), 1, 'ch_num_dot')

        return None

    def compare(
        self, source_tree: dict, target_tree: dict
    ) -> list[dict]:
        """
        对比源文和译文的条款树结构。

        Args:
            source_tree: 源文条款树（build_tree 输出）
            target_tree: 译文条款树（build_tree 输出）

        Returns:
            偏差列表，每项: {type, location, detail, severity}
        """
        issues: list[dict] = []

        # 1) 节点数量检查
        source_count = self._count_nodes(source_tree)
        target_count = self._count_nodes(target_tree)
        if source_count != target_count:
            issues.append({
                'type': 'clause_count',
                'location': '全文',
                'detail': f'条款数量不一致: 源文 {source_count} 条, 译文 {target_count} 条',
                'severity': 'error' if abs(source_count - target_count) > 1 else 'warning',
            })

        # 2) 逐节点对比
        source_nodes = self._flatten_nodes(source_tree)
        target_nodes = self._flatten_nodes(target_tree)
        target_by_full = {n.get('full_number', n['number']): n for n in target_nodes}

        for sn in source_nodes:
            sn_full = sn.get('full_number', sn['number'])
            if sn_full in target_by_full:
                tn = target_by_full[sn_full]
                # 深度检查
                if sn['depth'] != tn['depth']:
                    issues.append({
                        'type': 'clause_depth',
                        'location': f'条款 {sn_full}',
                        'detail': f'嵌套深度不一致: 源文 depth={sn["depth"]}, 译文 depth={tn["depth"]}',
                        'severity': 'error',
                    })
                # 父节点检查
                if sn.get('parent') != tn.get('parent'):
                    issues.append({
                        'type': 'clause_parent',
                        'location': f'条款 {sn_full}',
                        'detail': f'父节点不一致: 源文 parent={sn.get("parent")}, 译文 parent={tn.get("parent")}',
                        'severity': 'warning',
                    })
            else:
                issues.append({
                    'type': 'clause_missing',
                    'location': f'条款 {sn_full}',
                    'detail': f'译文中缺少条款 {sn_full}',
                    'severity': 'error',
                })

        # 3) 同级编号连续性检查
        for level in range(3):
            src_siblings = self._group_by_parent_and_depth(source_tree, level)
            tgt_siblings = self._group_by_parent_and_depth(target_tree, level)

            for parent_key, src_list in src_siblings.items():
                if parent_key not in tgt_siblings:
                    continue
                tgt_list = tgt_siblings[parent_key]

                src_nums = self._extract_number_sequence(src_list)
                tgt_nums = self._extract_number_sequence(tgt_list)

                if src_nums != tgt_nums:
                    issues.append({
                        'type': 'clause_sequence',
                        'location': f'父节点 {parent_key} 下的子条款',
                        'detail': f'编号序列不一致: 源文 {src_nums}, 译文 {tgt_nums}',
                        'severity': 'warning',
                    })

        return issues

    def _count_nodes(self, tree: dict) -> int:
        """统计树中（不含根节点）的节点总数"""
        count = 0
        for child in tree.get('children', []):
            count += 1 + self._count_nodes(child)
        return count

    def _flatten_nodes(self, tree: dict) -> list[dict]:
        """将树展开为扁平列表（不含根节点）"""
        result: list[dict] = []
        for child in tree.get('children', []):
            result.append(child)
            result.extend(self._flatten_nodes(child))
        return result

    def _group_by_parent_and_depth(
        self, tree: dict, depth: int
    ) -> dict[str, list[dict]]:
        """按父节点分组指定深度的子节点"""
        groups: dict[str, list[dict]] = defaultdict(list)
        queue: deque[tuple[dict, str]] = deque()
        for child in tree.get('children', []):
            queue.append((child, child.get('parent', '__ROOT__')))
        while queue:
            node, parent_key = queue.popleft()
            if node.get('depth') == depth:
                groups[parent_key].append(node)
            for child in node.get('children', []):
                queue.append((child, node.get('full_number', node['number'])))
        return dict(groups)

    def _extract_number_sequence(self, nodes: list[dict]) -> list[int]:
        """从节点列表中提取编号序列（整数化后排序）"""
        nums: list[int] = []
        for n in nodes:
            num_str = n['number']
            try:
                nums.append(int(num_str))
            except ValueError:
                # 罗马数字转整数
                nums.append(self._roman_to_int(num_str))
        return sorted(nums)

    @staticmethod
    def _roman_to_int(s: str) -> int:
        """罗马数字转整数"""
        roman_map = {'i': 1, 'v': 5, 'x': 10, 'l': 50, 'c': 100, 'd': 500, 'm': 1000}
        s = s.lower()
        result = 0
        prev = 0
        for ch in reversed(s):
            val = roman_map.get(ch, 0)
            if val >= prev:
                result += val
            else:
                result -= val
            prev = val
        return result


# ============================================================================
# 2. DefinedTermScanner — 定义术语扫描器
# ============================================================================

class DefinedTermScanner:
    """
    定义术语扫描器。

    从定义章节提取术语表，在全篇扫描术语使用的一致性。
    使用字典多模式匹配检测术语不一致。
    """

    # 英文定义模式: "Term" shall mean ... 或 "Term" means ...
    RE_ENG_DEFINITION = re.compile(
        r'"([A-Z][A-Za-z\s]+?)"\s+(?:shall\s+mean|means)\s+[^.]+\.',
        re.IGNORECASE
    )
    # 中文定义模式: "术语"是指...
    RE_CH_DEFINITION = re.compile(
        r'[「「"]([^「」""]+)[」」"]\s*是指\s*[^。；]+[。；]'
    )
    # 术语出现模式: 引号内的首字母大写的词
    RE_TERM_USAGE = re.compile(
        r'"([A-Z][A-Za-z\s]+?)"'
    )
    # 中文术语出现模式
    RE_CH_TERM = re.compile(
        r'[「「"]{1}([^「」""]{1,20})[」」"]{1}'
    )

    def extract_definitions(self, definitions_section: str) -> dict[str, str]:
        """
        从定义章节提取术语表。

        解析 English "Term" shall mean... 和中文「术语」是指... 模式，
        按出现顺序配对，构建 {英文术语: 中文译法} 字典。

        Args:
            definitions_section: 定义章节文本（可含中英文）

        Returns:
            {英文术语: 中文译法}
        """
        glossary: dict[str, str] = {}

        # 提取英文定义
        eng_terms: list[str] = []
        for m in self.RE_ENG_DEFINITION.finditer(definitions_section):
            term = m.group(1).strip()
            if term:
                eng_terms.append(term)

        # 提取中文定义
        ch_terms: list[str] = []
        for m in self.RE_CH_DEFINITION.finditer(definitions_section):
            ch_term = m.group(1).strip()
            if ch_term:
                ch_terms.append(ch_term)

        # 按顺序配对
        for i, eng in enumerate(eng_terms):
            if i < len(ch_terms):
                glossary[eng] = ch_terms[i]
            else:
                glossary[eng] = ''

        return glossary

    def scan_consistency(
        self, text: str, glossary: dict[str, str]
    ) -> list[dict]:
        """
        全篇扫描术语使用一致性。

        检测：
        1) 同一英文术语被翻译为多种不同中文译法
        2) 出现术语表中的英文原文但未使用对应的中文译法

        Args:
            text: 待扫描的中文译文文本
            glossary: {英文术语: 标准中文译法}

        Returns:
            问题列表: {term, expected, found, location}
        """
        issues: list[dict] = []

        # 对每个术语，在文本中查找所有出现
        for eng_term, expected_ch in glossary.items():
            if not expected_ch:
                continue

            # 查找英文术语原文残留（不应该在译文中出现）
            eng_pattern = re.compile(re.escape(f'"{eng_term}"'), re.IGNORECASE)
            for m in eng_pattern.finditer(text):
                issues.append({
                    'term': eng_term,
                    'expected': expected_ch,
                    'found': m.group(0),
                    'location': f'位置 {m.start()}: "{eng_term}" 原文残留',
                    'severity': 'error',
                })

            # 在译文中查找中文术语的各种可能译法
            # 用引号模式找到所有可能的译文术语
            potential_translations: set[str] = set()
            for m in self.RE_CH_TERM.finditer(text):
                candidate = m.group(1).strip()
                if len(candidate) >= 2 and len(candidate) <= 15:
                    potential_translations.add(candidate)

            # 检查期望的中文术语是否出现
            if expected_ch not in text:
                issues.append({
                    'term': eng_term,
                    'expected': expected_ch,
                    'found': '未找到',
                    'location': f'术语 "{eng_term}" 的标准译法 "{expected_ch}" 在译文中未出现',
                    'severity': 'warning',
                })

            # 简单检测：找疑似同一英文本语的变体译法
            # 这需要更智能的匹配，这里做基础版本
            # 查找引号中的词，如果看起来像是对应术语但不是标准译法
            for m in self.RE_CH_TERM.finditer(text):
                candidate = m.group(1).strip()
                # 跳过已经是标准译法的
                if candidate == expected_ch:
                    continue
                # 如果候选词长度接近且包含标准译法的字符，可能是变体
                if (len(candidate) >= 2 and
                        self._char_similarity(candidate, expected_ch) > 0.5):
                    issues.append({
                        'term': eng_term,
                        'expected': expected_ch,
                        'found': candidate,
                        'location': f'位置 {m.start()}: 疑似术语变体 "{candidate}"（标准: "{expected_ch}"）',
                        'severity': 'warning',
                    })

        return issues

    @staticmethod
    def _char_similarity(a: str, b: str) -> float:
        """基于字符重叠的简单相似度"""
        set_a = set(a)
        set_b = set(b)
        if not set_a and not set_b:
            return 0.0
        intersection = set_a & set_b
        union = set_a | set_b
        return len(intersection) / len(union) if union else 0.0


# ============================================================================
# 3. CrossReferenceValidator — 交叉引用验证器
# ============================================================================

class CrossReferenceValidator:
    """
    交叉引用验证器。

    提取文本中的交叉引用编号，验证引用目标在条款树中是否存在。
    """

    # 英文交叉引用模式
    RE_ENG_REF = re.compile(
        r'(?:Article|Section|Clause|Exhibit|Schedule|Part|Chapter|Paragraph)\s+'
        r'([IVXLCDM]+|\d+(?:\.\d+)*)',
        re.IGNORECASE
    )
    # 省略 "(a)" / "(i)" 的子条款引用
    RE_SUB_REF = re.compile(
        r'(?:sub[- ]?(?:section|clause|paragraph))\s+\(([a-z])\)',
        re.IGNORECASE
    )
    # 中文交叉引用
    RE_CH_REF = re.compile(
        r'(?:根据|依照|按照|参照|参见|见|详见)\s*'
        r'(?:第)?([一二三四五六七八九十\d]+)(?:条|款|节|章|项)'
    )
    # 中文附件引用
    RE_CH_EXHIBIT = re.compile(
        r'(?:附件|附录|附表|附则)\s*([一二三四五六七八九十\d]+)'
    )
    # 简单数字引用 (Section 2.1, Clause 3)
    RE_SIMPLE_REF = re.compile(
        r'(?:第\s*)?(\d+(?:\.\d+)*)\s*(?:条|款|节|章|项)'
    )

    def extract_references(self, text: str) -> set[str]:
        """
        从文本中提取所有交叉引用编号。

        Args:
            text: 待分析文本

        Returns:
            引用编号集合（标准化后的编号字符串）
        """
        refs: set[str] = set()

        # 英文引用
        for m in self.RE_ENG_REF.finditer(text):
            ref = m.group(1)
            # 罗马数字转整数
            if re.match(r'^[IVXLCDM]+$', ref, re.IGNORECASE):
                ref = str(self._roman_to_int(ref))
            refs.add(ref)

        # 子条款引用
        for m in self.RE_SUB_REF.finditer(text):
            refs.add(m.group(1))

        # 中文引用
        for m in self.RE_CH_REF.finditer(text):
            num_str = m.group(1)
            if num_str.isdigit():
                refs.add(num_str)
            else:
                refs.add(str(self._cn_to_int(num_str)))

        # 附件引用
        for m in self.RE_CH_EXHIBIT.finditer(text):
            num_str = m.group(1)
            if num_str.isdigit():
                refs.add(num_str)
            else:
                refs.add(str(self._cn_to_int(num_str)))

        # 简单数字引用
        for m in self.RE_SIMPLE_REF.finditer(text):
            refs.add(m.group(1))

        return refs

    def validate(
        self, extracted_refs: set[str], clause_tree: dict
    ) -> list[dict]:
        """
        验证交叉引用目标是否在条款树中存在。

        Args:
            extracted_refs: 提取出的引用编号集合
            clause_tree: 条款树（build_tree 输出）

        Returns:
            验证结果列表: {ref, exists: bool, location}
        """
        # 收集条款树中所有编号
        all_numbers: set[str] = set()
        flattened = self._flatten_tree(clause_tree)
        for node in flattened:
            num = node['number']
            full_num = node.get('full_number', num)
            all_numbers.add(num)
            all_numbers.add(full_num)
            # 也添加不带前缀的版本
            if '.' in full_num:
                all_numbers.add(full_num.split('.')[-1])

        results: list[dict] = []
        for ref in sorted(extracted_refs):
            exists = ref in all_numbers
            if not exists:
                # 尝试模糊匹配
                exists = self._fuzzy_match(ref, all_numbers)

            results.append({
                'ref': ref,
                'exists': exists,
            })

        return results

    def _flatten_tree(self, tree: dict) -> list[dict]:
        """扁平化条款树"""
        result: list[dict] = []
        for child in tree.get('children', []):
            result.append(child)
            result.extend(self._flatten_tree(child))
        return result

    @staticmethod
    def _fuzzy_match(ref: str, all_numbers: set[str]) -> bool:
        """模糊匹配引用编号"""
        # 尝试补零、去零
        parts = ref.split('.')
        for num in all_numbers:
            num_parts = num.split('.')
            if len(parts) == len(num_parts):
                if all(p.lstrip('0') == np.lstrip('0') or p == np
                       for p, np in zip(parts, num_parts)):
                    return True
        return False

    @staticmethod
    def _roman_to_int(s: str) -> int:
        roman_map = {'i': 1, 'v': 5, 'x': 10, 'l': 50, 'c': 100, 'd': 500, 'm': 1000}
        s = s.lower()
        result = 0
        prev = 0
        for ch in reversed(s):
            val = roman_map.get(ch, 0)
            if val >= prev:
                result += val
            else:
                result -= val
            prev = val
        return result

    @staticmethod
    def _cn_to_int(s: str) -> int:
        """中文数字转整数"""
        return ClauseTreeVerifier._cn_to_int(s)


# ============================================================================
# 4. AmountVerifier — 金额校验器
# ============================================================================

class AmountVerifier:
    """
    金额校验器。

    将英文金额大写转换为数字，扫描数字+大写组合验证一致性。
    """

    # 基础数字词
    _ONES: dict[str, int] = {
        'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4,
        'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9,
        'ten': 10, 'eleven': 11, 'twelve': 12, 'thirteen': 13,
        'fourteen': 14, 'fifteen': 15, 'sixteen': 16, 'seventeen': 17,
        'eighteen': 18, 'nineteen': 19,
    }
    _TENS: dict[str, int] = {
        'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50,
        'sixty': 60, 'seventy': 70, 'eighty': 80, 'ninety': 90,
    }
    _SCALES: dict[str, int] = {
        'hundred': 100, 'thousand': 1000,
        'million': 1000000, 'billion': 1000000000,
        'trillion': 1000000000000,
    }

    # 金额模式：$N,NNN,NNN.NN (Word Amount) 或 USD N,NNN (Word Amount)
    RE_AMOUNT_PAIR = re.compile(
        r'(?:\$|USD\s*)?([\d,]+(?:\.\d{2})?)\s*'  # 数字部分
        r'(?:元|[元整]|USD|dollars?)?\s*'
        r'[\(（]\s*'                                 # 左括号
        r'([A-Za-z][A-Za-z\s-]+)'                    # 英文大写
        r'\s*[\)）]',                                 # 右括号
        re.IGNORECASE
    )
    # 大写金额提取（不在括号中的单独金额词）
    RE_WORD_AMOUNT = re.compile(
        r'\b((?:[A-Z][a-z]+\s)+(?:[A-Z][a-z]+))\b'
    )
    # 数字金额模式
    RE_NUM_AMOUNT = re.compile(
        r'(?:\$|USD\s*|CNY\s*|RMB\s*)?([\d,]+(?:\.\d{2})?)\s*(?:元|[元整]|USD|CNY|RMB)?'
    )

    def word_to_num(self, word: str) -> int:
        """
        将英文金额大写转换为整数。

        例如: "One Million Two Hundred Thousand" → 1200000
              "Twelve" → 12
              "One Hundred and Fifty" → 150

        Args:
            word: 英文金额大写字串

        Returns:
            对应的整数值
        """
        # 清理: 去除 "and", "dollars", 标点
        cleaned = re.sub(
            r'\b(and|dollars?|cents?|only|exactly|point)\b',
            '', word, flags=re.IGNORECASE
        )
        cleaned = cleaned.replace('-', ' ').replace(',', '').strip()
        tokens = cleaned.split()

        if not tokens:
            return 0

        result = 0
        current = 0

        for token in tokens:
            token_lower = token.lower()
            if token_lower in self._ONES:
                current += self._ONES[token_lower]
            elif token_lower in self._TENS:
                current += self._TENS[token_lower]
            elif token_lower in self._SCALES:
                scale = self._SCALES[token_lower]
                if current == 0:
                    current = 1
                if scale >= 100:
                    # hundred: 前面的值乘以 100
                    current *= scale
                    if scale > 100:
                        # thousand/million/billion: 加到结果并重置
                        result += current
                        current = 0
                else:
                    current *= scale
            else:
                # 尝试解析未知词（如 "twenty-one" 中被 split 的 "one"）
                pass

        result += current
        return result

    def verify(self, text: str) -> list[dict]:
        """
        扫描文本中的金额数字+大写组合，验证一致性。

        匹配模式: $N,NNN,NNN (Word Amount) 或 N,NNN 元 (大写金额)

        Args:
            text: 待验证文本

        Returns:
            验证结果: {number, words, match: bool, location}
        """
        issues: list[dict] = []

        for m in self.RE_AMOUNT_PAIR.finditer(text):
            num_str = m.group(1)
            word_str = m.group(2).strip()

            # 解析数字部分（去除千位分隔符）
            number = self._parse_number(num_str)

            # 解析大写部分
            word_value = self.word_to_num(word_str)

            # 比对
            match = (number == word_value)

            # 提取位置上下文
            start = max(0, m.start() - 20)
            end = min(len(text), m.end() + 20)
            context = text[start:end].replace('\n', ' ').strip()

            if not match:
                issues.append({
                    'number': num_str,
                    'numeric_value': number,
                    'words': word_str,
                    'word_value': word_value,
                    'match': False,
                    'location': context,
                    'severity': 'error',
                })

        return issues

    @staticmethod
    def _parse_number(num_str: str) -> int:
        """解析带千位分隔符的数字字符串"""
        cleaned = num_str.replace(',', '').strip()
        try:
            # 支持小数
            if '.' in cleaned:
                return int(float(cleaned))
            return int(cleaned)
        except ValueError:
            return 0


# ============================================================================
# 5. PatternEnhancer — 模式增强检测器
# ============================================================================

class PatternEnhancer:
    """
    模式增强检测器（全静态方法）。

    提供增强版的英文标点残留检测、日期格式检测、金额模式提取。
    """

    # ---------- 标点检测 ----------

    @staticmethod
    def detect_english_punctuation(text: str) -> list[dict]:
        """
        增强版英文标点残留检测。

        检测:
        - 中文字符间的英文逗号
        - 英文括号残留（中文段落中的 ( ) ）
        - 全角半角混用
        - 中文字符间不必要的空格

        Args:
            text: 待检测文本

        Returns:
            问题列表: {type, location, detail, severity}
        """
        issues: list[dict] = []

        # 1) 中文之间的英文逗号
        # 模式: 汉字,汉字（应该是 汉字，汉字）
        for m in re.finditer(r'[\u4e00-\u9fff],[\u4e00-\u9fff]', text):
            context = PatternEnhancer._get_context(text, m.start(), 15)
            issues.append({
                'type': 'english_comma',
                'location': context,
                'detail': '中文字符间发现英文逗号，应使用中文逗号（，）',
                'severity': 'warning',
            })

        # 2) 中文行文中的英文句号
        for m in re.finditer(r'[\u4e00-\u9fff]\.[\u4e00-\u9fff]', text):
            context = PatternEnhancer._get_context(text, m.start(), 15)
            issues.append({
                'type': 'english_period',
                'location': context,
                'detail': '中文字符间发现英文句号，应使用中文句号（。）',
                'severity': 'warning',
            })

        # 3) 中文段落中的英文括号
        # 查找中文行中出现 ( 或 ) 但不在英文单词附近的情况
        lines = text.split('\n')
        for line_idx, line in enumerate(lines):
            # 检测英文括号在中文上下文中
            ch_count = len(re.findall(r'[\u4e00-\u9fff]', line))
            en_count = len(re.findall(r'[a-zA-Z]', line))
            if ch_count > en_count:
                # 中文为主的行，检测英文括号
                en_parens = re.findall(r'[\(\)]', line)
                if en_parens:
                    # 排除金额模式中的括号 (如 $1,000 (One Thousand))
                    if not re.search(r'\$\s*[\d,]+', line) and not re.search(r'USD', line):
                        issues.append({
                            'type': 'english_paren',
                            'location': f'第 {line_idx + 1} 行: {line.strip()[:50]}',
                            'detail': f'中文行中发现英文括号，应使用中文括号（）: {", ".join(set(en_parens))}',
                            'severity': 'info',
                        })

        # 4) 全角半角混用
        # 检测全角文本中的半角标点
        fullwidth_line_count = 0
        for m in re.finditer(
            r'[\u4e00-\u9fff][\u3000-\u303f\uff00-\uffef]*[!?;][\u3000-\u303f\uff00-\uffef]*[\u4e00-\u9fff]',
            text
        ):
            context = PatternEnhancer._get_context(text, m.start(), 20)
            issues.append({
                'type': 'halfwidth_punct',
                'location': context,
                'detail': '全角文本中发现半角标点（! ? ;），建议使用全角标点',
                'severity': 'info',
            })
            fullwidth_line_count += 1

        # 5) 中文字符间不必要的空格
        for m in re.finditer(r'[\u4e00-\u9fff]\s{2,}[\u4e00-\u9fff]', text):
            context = PatternEnhancer._get_context(text, m.start(), 20)
            issues.append({
                'type': 'extra_spaces',
                'location': context,
                'detail': '中文字符间存在多余空格',
                'severity': 'info',
            })

        return issues

    # ---------- 日期格式检测 ----------

    @staticmethod
    def detect_date_formats(text: str) -> list[dict]:
        """
        日期格式检测。

        检测:
        - 英文月份日期: June 26, 2026
        - 中文日期: 2026年6月26日
        - 纯数字日期: 06/26/2026

        Args:
            text: 待检测文本

        Returns:
            日期格式列表: {format_type, value, location}
        """
        results: list[dict] = []

        # 英文月份日期: Month DD, YYYY 或 Month DD YYYY
        eng_month_pattern = re.compile(
            r'\b(January|February|March|April|May|June|July|August|'
            r'September|October|November|December)\s+(\d{1,2})(?:,?\s+)(\d{4})\b',
            re.IGNORECASE
        )
        for m in eng_month_pattern.finditer(text):
            month = m.group(1)
            day = m.group(2)
            year = m.group(3)
            context = PatternEnhancer._get_context(text, m.start(), 30)
            results.append({
                'format_type': 'english_month_date',
                'value': f'{month} {day}, {year}',
                'location': context,
            })

        # 英文缩写月份: Mon DD, YYYY
        eng_abbr_pattern = re.compile(
            r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2}),?\s+(\d{4})\b',
            re.IGNORECASE
        )
        for m in eng_abbr_pattern.finditer(text):
            context = PatternEnhancer._get_context(text, m.start(), 30)
            results.append({
                'format_type': 'english_abbr_date',
                'value': f'{m.group(1)} {m.group(2)}, {m.group(3)}',
                'location': context,
            })

        # 中文日期: YYYY年M月D日
        ch_date_pattern = re.compile(
            r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日'
        )
        for m in ch_date_pattern.finditer(text):
            context = PatternEnhancer._get_context(text, m.start(), 30)
            results.append({
                'format_type': 'chinese_date',
                'value': f'{m.group(1)}年{m.group(2)}月{m.group(3)}日',
                'location': context,
            })

        # 纯数字日期: MM/DD/YYYY 或 YYYY/MM/DD 或 YYYY-MM-DD
        num_date_pattern = re.compile(
            r'\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b|'
            r'\b(\d{4}[/-]\d{1,2}[/-]\d{1,2})\b'
        )
        for m in num_date_pattern.finditer(text):
            date_str = m.group(0)
            context = PatternEnhancer._get_context(text, m.start(), 30)
            results.append({
                'format_type': 'numeric_date',
                'value': date_str,
                'location': context,
            })

        return results

    # ---------- 金额模式提取 ----------

    @staticmethod
    def detect_amount_patterns(text: str) -> list[dict]:
        """
        金额数字模式提取。

        检测:
        - $N,NNN 或 $N,NNN.NN
        - USD N,NNN
        - N,NNN 元
        - N,NNN.NN (可能的金额小数)

        Args:
            text: 待检测文本

        Returns:
            金额列表: {amount_type, value, currency, location}
        """
        results: list[dict] = []

        # $N,NNN 或 $N,NNN.NN
        dollar_pattern = re.compile(
            r'\$([\d,]+(?:\.\d{2})?)'
        )
        for m in dollar_pattern.finditer(text):
            context = PatternEnhancer._get_context(text, m.start(), 40)
            results.append({
                'amount_type': 'dollar',
                'value': m.group(1),
                'currency': 'USD',
                'location': context,
            })

        # USD N,NNN 或 USD N,NNN.NN
        usd_pattern = re.compile(
            r'USD\s+([\d,]+(?:\.\d{2})?)',
            re.IGNORECASE
        )
        for m in usd_pattern.finditer(text):
            context = PatternEnhancer._get_context(text, m.start(), 40)
            results.append({
                'amount_type': 'usd',
                'value': m.group(1),
                'currency': 'USD',
                'location': context,
            })

        # N,NNN 元 或 N,NNN.NN 元
        rmb_pattern = re.compile(
            r'([\d,]+(?:\.\d{2})?)\s*元'
        )
        for m in rmb_pattern.finditer(text):
            context = PatternEnhancer._get_context(text, m.start(), 40)
            results.append({
                'amount_type': 'rmb',
                'value': m.group(1),
                'currency': 'CNY',
                'location': context,
            })

        # 人民币 或 RMB 后跟数字
        rmb_prefix_pattern = re.compile(
            r'(?:人民币|RMB|CNY)\s*([\d,]+(?:\.\d{2})?)',
            re.IGNORECASE
        )
        for m in rmb_prefix_pattern.finditer(text):
            context = PatternEnhancer._get_context(text, m.start(), 40)
            results.append({
                'amount_type': 'rmb_prefix',
                'value': m.group(1),
                'currency': 'CNY',
                'location': context,
            })

        return results

    @staticmethod
    def _get_context(text: str, pos: int, window: int = 20) -> str:
        """提取指定位置的上下文文本"""
        start = max(0, pos - window)
        end = min(len(text), pos + window)
        context = text[start:end].replace('\n', ' ').replace('\r', ' ')
        # 压缩多余空格
        context = re.sub(r'\s+', ' ', context)
        return context.strip()


# ============================================================================
# 5.5 HighRiskTagger — 高风险条款自动标记
# ============================================================================

class HighRiskTagger:
    """
    高风险条款自动标记器。
    
    根据条款名称和关键词自动识别合同中的高风险条款，
    生成风险热力分布，帮助主编终审和事实核查聚焦核心区域。
    """
    
    # 风险标签定义: {标签名: (风险等级, 关键词列表)}
    RISK_TAGS = {
        "责任限制": ("critical", [
            "Limitation of Liability", "免��条款", "责任限制",
            "EXCLUSION", "DISCLAIMER", "CAP ON LIABILITY",
        ]),
        "赔偿义务": ("critical", [
            "Indemnification", "赔偿", "INDEMNIFY",
            "Hold Harmless", "INDEMNITY",
        ]),
        "管辖与争议": ("high", [
            "Governing Law", "管辖法律", "Dispute Resolution",
            "争议解决", "Arbitration", "仲裁",
            "Jurisdiction", "Venue",
        ]),
        "知识产权": ("high", [
            "Intellectual Property", "知识产权", "Patent",
            "Copyright", "Trademark", "Trade Secret",
            "Confidentiality", "保密",
        ]),
        "终止与违约": ("high", [
            "Termination", "终止", "Breach", "违约",
            "Default", "Material Adverse", "Term",
            "Right to Terminate", "解约",
        ]),
        "定义条款": ("medium", [
            "Definitions", "定义", "Defined Terms",
        ]),
        "转让与变更": ("medium", [
            "Assignment", "转让", "Amendment", "修订",
            "Successors", "继承",
        ]),
        "不可抗力": ("medium", [
            "Force Majeure", "不可抗力", "Act of God",
        ]),
        "保证与声明": ("medium", [
            "Representations and Warranties", "陈述与保证",
            "Warranty", "Covenant",
        ]),
    }
    
    def tag_clauses(self, text: str) -> list[dict]:
        """
        对文本中的条款进行风险标记。
        
        返回每个条款段的风险标签列表。
        """
        # 按条款编号分割
        lines = text.split('\n')
        clauses = []
        current = None
        
        for line in lines:
            stripped = line.strip()
            m = re.match(r'^\s*(\d+\.?)\s+(.+)', stripped)
            if m:
                if current:
                    clauses.append(current)
                current = {"number": m.group(1), "title": m.group(2)[:100], "text": stripped}
            elif current:
                current["text"] += " " + stripped
        
        if current:
            clauses.append(current)
        
        # 对每个条款打标签
        for clause in clauses:
            tags = []
            for tag_name, (severity, keywords) in self.RISK_TAGS.items():
                for kw in keywords:
                    if kw.lower() in clause["text"].lower():
                        tags.append({"tag": tag_name, "severity": severity})
                        break
            clause["risk_tags"] = tags
            clause["max_risk"] = self._max_risk(tags)
        
        return clauses
    
    def _max_risk(self, tags: list[dict]) -> str:
        """计算条款最高风险等级"""
        for level in ("critical", "high", "medium", "low"):
            if any(t.get("severity") == level for t in tags):
                return level
        return "none"
    
    def heatmap(self, clauses: list[dict]) -> dict:
        """生成全书风险热力分布"""
        counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "none": 0}
        for c in clauses:
            mr = c.get("max_risk", "none")
            counts[mr] = counts.get(mr, 0) + 1
        
        critical_clauses = [c["number"] for c in clauses if c.get("max_risk") == "critical"]
        high_clauses = [c["number"] for c in clauses if c.get("max_risk") == "high"]
        
        return {
            "total_clauses": len(clauses),
            "distribution": counts,
            "critical_clauses": critical_clauses,
            "high_clauses": high_clauses,
            "focus_areas": critical_clauses + high_clauses,
        }


# ============================================================================
# 5.6 DocumentSkeleton — 文档骨架解析（目录校验 + 附件映射）
# ============================================================================

class DocumentSkeleton:
    """
    法律文档骨架解析器。

    从法律文档中提取：
    - 目录 (Table of Contents) 及其与正文的一致性
    - 条款层级树 (Article/Section/Clause)
    - 附件/附表映射 (Exhibit/Schedule/Annex)
    - 交叉引用依赖图
    """

    # 条款编号模式（支持多级嵌套）
    CLAUSE_PATTERN = re.compile(
        r'^\s*'
        r'(?:'
        r'(?:Article|ARTICLE)\s+(\d+[A-Z]?)'         # Article 1, Article 1A
        r'|(?:Section|SECTION)\s+(\d+(?:\.\d+)*)'     # Section 1, Section 1.1
        r'|(?:Clause|CLAUSE)\s+(\d+(?:\.\d+)*)'      # Clause 1.1
        r'|(\d+)\.\s'                                  # 1. Title
        r'|(\d+\.\d+)\s'                               # 1.1 Title
        r'|(\([a-zA-Z]\))'                             # (a), (b)
        r'|(\([ivxlcdm]+\))'                           # (i), (ii)
        r')'
        r'\s*(.+)',                                    # 后续内容
        re.IGNORECASE
    )

    # 附件模式
    ATTACHMENT_PATTERN = re.compile(
        r'(?:Exhibit|EXHIBIT|Schedule|SCHEDULE|Annex|ANNEX|Appendix|APPENDIX)\s+([A-Z0-9]+)',
        re.IGNORECASE
    )

    # 目录条目模式
    TOC_PATTERN = re.compile(
        r'^\s*'
        r'(?:'
        r'(?:Article|Section|Clause)\s+\d+'
        r'|\d+(?:\.\d+)*'
        r')'
        r'\s*\.?\s+.+',
    )

    def parse_toc(self, text: str) -> list[dict]:
        """
        提取目录条目。

        返回：[{number, title, line_number}]
        """
        entries = []
        for i, line in enumerate(text.split('\n')):
            stripped = line.strip()
            if not stripped:
                continue
            # 目录行：带编号 + 后续文字 + 可能有点号引导线
            if self.TOC_PATTERN.match(stripped):
                # 尝试提取编号和标题
                m = re.match(r'^([\d.A-Za-z()]+)\s*\.?\s*(.+?)(?:\s*\.{2,}|\s*\d+)?$', stripped)
                if m:
                    entries.append({
                        "number": m.group(1),
                        "title": m.group(2).strip()[:100],
                        "line": i + 1,
                    })
        return entries

    def parse_skeleton(self, text: str) -> dict:
        """
        解析文档骨架。

        返回：
        {
            "toc_entries": [...],
            "clauses": [...],
            "attachments": {...},
            "cross_refs": [...],
        }
        """
        lines = text.split('\n')

        # 条款提取
        clauses = []
        current = None
        for line in lines:
            stripped = line.strip()
            m = self.CLAUSE_PATTERN.match(stripped)
            if m:
                if current:
                    clauses.append(current)
                # 提取编号（从第一个非None的group）
                number = next((g for g in m.groups()[:-1] if g is not None), m.group(0))
                title = m.groups()[-1][:120]
                current = {
                    "number": number,
                    "title": title,
                    "depth": self._clause_depth(number),
                    "text": stripped,
                }
            elif current:
                current["text"] += " " + stripped
        if current:
            clauses.append(current)

        # 附件提取
        attachments = {}
        for m in self.ATTACHMENT_PATTERN.finditer(text):
            letter = m.group(1)
            context = text[max(0, m.start()-20):m.end()+60]
            attachments[letter] = {
                "ref": m.group(0),
                "context": context.replace('\n', ' '),
            }

        # 交叉引用
        cross_refs = self._extract_cross_refs(text)

        return {
            "clauses": clauses,
            "clause_count": len(clauses),
            "attachments": attachments,
            "attachment_count": len(attachments),
            "cross_refs": cross_refs,
            "cross_ref_count": len(cross_refs),
        }

    def validate_skeleton(self, source_skel: dict, target_skel: dict) -> list[dict]:
        """
        比较源文和译文的文档骨架。

        检查项：
        1. 条款数量是否一致
        2. 附件数量/编号是否一致
        3. 交叉引用数是否偏差过大（>20%为异常）
        """
        issues = []

        sc = source_skel.get("clause_count", 0)
        tc = target_skel.get("clause_count", 0)
        if sc != tc and sc > 0 and tc > 0:
            issues.append({
                "type": "clause_count_mismatch",
                "detail": f"条款数量不一致: 源文{sc} vs 译文{tc}",
                "severity": "error" if abs(sc - tc) > 2 else "warning",
            })

        sa = source_skel.get("attachment_count", 0)
        ta = target_skel.get("attachment_count", 0)
        if sa != ta:
            issues.append({
                "type": "attachment_count_mismatch",
                "detail": f"附件数量不一致: 源文{sa} vs 译文{ta}",
                "severity": "error" if sa > 0 or ta > 0 else "warning",
            })

        sr = source_skel.get("cross_ref_count", 0)
        tr = target_skel.get("cross_ref_count", 0)
        if sr > 0 and tr > 0:
            ratio = abs(sr - tr) / max(sr, tr)
            if ratio > 0.2:
                issues.append({
                    "type": "cross_ref_deviation",
                    "detail": f"交叉引用数偏差: 源文{sr} vs 译文{tr} ({ratio:.0%})",
                    "severity": "warning" if ratio < 0.4 else "error",
                })

        return issues

    def _clause_depth(self, number: str) -> int:
        """计算条款编号的嵌套深度"""
        # Article/Section → depth 0
        # 1. → depth 0, 1.1 → depth 1, (a) → depth 2, (i) → depth 3
        if '(' in number:
            idx = number.find(')')
            inner = number[1:idx]
            if inner.isalpha() and len(inner) == 1:
                return 2
            elif re.match(r'^[ivxlcdm]+$', inner, re.IGNORECASE):
                return 3
            return 1
        dots = number.count('.')
        return dots

    def _extract_cross_refs(self, text: str) -> list[dict]:
        """提取交叉引用"""
        refs = []
        # 英文引用
        for m in re.finditer(
            r'(?:pursuant to|under|as set forth in|as defined in|referred to in)\s+'
            r'(?:Section|Article|Clause|Exhibit|Schedule|Annex)\s+([\d.A-Z]+)',
            text, re.IGNORECASE
        ):
            refs.append({"ref": m.group(0), "target": m.group(1), "lang": "en"})
        # 中文引用
        for m in re.finditer(
            r'(?:根据|按照|依据|见|参见)\s*'
            r'(?:第\s*[\d.]+条|附件\s*[A-Z]+|第\s*[\d.]+节)',
            text
        ):
            refs.append({"ref": m.group(0), "target": "", "lang": "zh"})
        return refs


# ============================================================================
# 6. LegalVerifier — 顶层调度器
# ============================================================================

class LegalVerifier:
    """
    法律翻译完整性校验顶层调度器。

    一次调用运行所有检查模块，输出结构化的校验报告。
    每个子模块也可独立调用。
    """

    def __init__(self):
        self.clause_verifier = ClauseTreeVerifier()
        self.term_scanner = DefinedTermScanner()
        self.cross_ref_validator = CrossReferenceValidator()
        self.amount_verifier = AmountVerifier()
        self.risk_tagger = HighRiskTagger()
        self.doc_skeleton = DocumentSkeleton()

    def run_all(
        self,
        source: str,
        target: str,
        definitions_section: str = "",
    ) -> dict:
        """
        运行全部校验检查。

        Args:
            source: 英文源文全文
            target: 中文译文全文
            definitions_section: 定义章节文本（可选）

        Returns:
            {
                'clause_tree': [...],      # 条款树比对问题
                'term_scan': [...],         # 术语一致性问题
                'cross_refs': [...],        # 交叉引用验证
                'amount': [...],            # 金额校验问题
                'punctuation': [...],       # 标点格式问题
                'dates': [...],             # 日期格式
                'amount_patterns': [...],   # 金额模式
                'summary': {
                    'total_issues': int,
                    'by_severity': {error, warning, info},
                }
            }
        """
        # 1) 条款树比对
        source_lines = source.split('\n')
        target_lines = target.split('\n')
        source_tree = self.clause_verifier.build_tree(source_lines)
        target_tree = self.clause_verifier.build_tree(target_lines)
        clause_issues = self.clause_verifier.compare(source_tree, target_tree)

        # 2) 术语一致性扫描
        glossary: dict[str, str] = {}
        if definitions_section:
            glossary = self.term_scanner.extract_definitions(definitions_section)
        term_issues = self.term_scanner.scan_consistency(target, glossary)

        # 3) 交叉引用验证
        source_refs = self.cross_ref_validator.extract_references(source)
        target_refs = self.cross_ref_validator.extract_references(target)
        all_refs = source_refs | target_refs
        cross_ref_results = self.cross_ref_validator.validate(all_refs, target_tree)

        # 4) 金额校验
        amount_issues = self.amount_verifier.verify(source)
        amount_issues.extend(self.amount_verifier.verify(target))

        # 5) 标点格式检测（仅对译文）
        punctuation_issues = PatternEnhancer.detect_english_punctuation(target)

        # 6) 日期格式检测
        date_issues = PatternEnhancer.detect_date_formats(source)
        date_issues.extend(PatternEnhancer.detect_date_formats(target))

        # 7) 金额模式提取
        amount_patterns = PatternEnhancer.detect_amount_patterns(source)
        amount_patterns.extend(PatternEnhancer.detect_amount_patterns(target))

        # 8) 高风险条款标记
        risk_clauses = self.risk_tagger.tag_clauses(source)
        risk_heatmap = self.risk_tagger.heatmap(risk_clauses)

        # 9) 文档骨架解析（目录校验+附件映射）
        source_skel = self.doc_skeleton.parse_skeleton(source)
        target_skel = self.doc_skeleton.parse_skeleton(target)
        skeleton_issues = self.doc_skeleton.validate_skeleton(source_skel, target_skel)

        # 汇总
        all_issues = (
            clause_issues + term_issues +
            [{'type': 'cross_ref', **r, 'severity': 'warning' if not r['exists'] else 'info'}
             for r in cross_ref_results] +
            amount_issues +
            punctuation_issues +
            [{'type': 'date', **d, 'severity': 'info'} for d in date_issues]
        )

        severity_counts: dict[str, int] = {'error': 0, 'warning': 0, 'info': 0}
        for issue in all_issues:
            sev = issue.get('severity', 'info')
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

        return {
            'clause_tree': clause_issues,
            'term_scan': term_issues,
            'cross_refs': [
                {'ref': r['ref'], 'exists': r['exists'], 'severity': 'warning' if not r['exists'] else 'info'}
                for r in cross_ref_results
            ],
            'amount': amount_issues,
            'punctuation': punctuation_issues,
            'dates': date_issues,
            'amount_patterns': amount_patterns,
            'high_risk_clauses': risk_clauses,
            'risk_heatmap': risk_heatmap,
            'doc_skeleton': {
                'source': source_skel,
                'target': target_skel,
                'issues': skeleton_issues,
            },
            'summary': {
                'total_issues': len(all_issues),
                'by_severity': severity_counts,
            },
        }


# ============================================================================
# 自测入口
# ============================================================================

# ============================================================================
# 7. AcademicGuard — 学术翻译确定性保护规则
# ============================================================================

class AcademicGuard:
    """
    学术翻译确定性保护规则（零LLM调用）。
    
    检测项：
    - 引用编号 [N] 是否保留
    - 图表引用 Figure X / Table Y 是否保留
    - 常见论文误译（state-of-the-art/novel/baseline等）
    - 公式标记是否保留（需在翻译流程原文标记后再比对）
    """

    @staticmethod
    def protect_citations(source: str, target: str) -> list[dict]:
        """检测引用编号 [N] 是否保留"""
        src_cites = set(re.findall(r'\[(\d+(?:[,\s]*\d+)*)\]', source))
        tgt_cites = set(re.findall(r'\[(\d+(?:[,\s]*\d+)*)\]', target))
        missing = src_cites - tgt_cites
        extra = tgt_cites - src_cites
        return [
            {"type": "missing_citation", "detail": f"引用 [{x}] 在译文中缺失", "severity": "critical"}
            for x in missing
        ] + [
            {"type": "extra_citation", "detail": f"引用 [{x}] 凭空出现（可能误增）", "severity": "warning"}
            for x in extra
        ]

    @staticmethod
    def protect_figure_refs(source: str, target: str) -> list[dict]:
        """检测图表引用 Figure X / Table Y 是否保留"""
        src_figs = set(re.findall(r'(Figure|Table|Fig\.)\s*\d+', source, re.IGNORECASE))
        tgt_figs = set(re.findall(r'(图|表|Figure|Table|Fig\.)\s*\d+', target))
        issues = []
        for src in src_figs:
            # 尝试匹配中文翻译
            m = re.match(r'(Figure|Fig\.)\s*(\d+)', src, re.IGNORECASE)
            if m:
                cn_version = f'图{m.group(2)}'
                if cn_version not in tgt_figs and src not in tgt_figs:
                    issues.append({"type": "missing_figure_ref",
                        "detail": f"图表引用 {src} 可能缺失", "severity": "high"})
            else:
                if src not in tgt_figs:
                    issues.append({"type": "missing_table_ref",
                        "detail": f"表格引用 {src} 可能缺失", "severity": "high"})
        return issues

    @staticmethod
    def detect_mistranslations(text: str) -> list[dict]:
        """检测论文高频误译"""
        patterns = [
            (r'最先进的', '可能误译 state-of-the-art，学术语境应为"当前最优/最先进"', 'medium'),
            (r'(?<![新])全新的', '可能误译 novel，学术语境应为"新颖的"', 'medium'),
            (r'基础的(?:模型|方法)', '可能误译 baseline，应为"基线模型/方法"', 'low'),
            (r'表现(?:好的|不错的)', '避免口语化表述，应为"性能优越/表现良好"', 'low'),
            (r'最新(?:的|研究)', '可能误译 state-of-the-art 或 recent，应区分"当前最优"vs"最近"', 'medium'),
        ]
        results = []
        for pat, msg, sev in patterns:
            for m in re.finditer(pat, text):
                results.append({
                    "type": "potential_mistrans", "detail": msg, "severity": sev,
                    "context": text[max(0, m.start() - 20):m.end() + 20],
                })
        return results

    @staticmethod
    def run_all(source: str, target: str) -> dict:
        """一次运行全部学术保护检查"""
        citations = AcademicGuard.protect_citations(source, target)
        figures = AcademicGuard.protect_figure_refs(source, target)
        mistrans = AcademicGuard.detect_mistranslations(target)
        total = len(citations) + len(figures) + len(mistrans)
        return {
            "citations": citations,
            "figure_refs": figures,
            "mistranslations": mistrans,
            "total_issues": total,
            "summary": {
                "critical": sum(1 for x in citations if x["severity"] == "critical"),
                "high": len(figures),
                "medium": sum(1 for x in mistrans if x["severity"] == "medium"),
                "low": sum(1 for x in mistrans if x["severity"] == "low"),
            },
        }


if __name__ == '__main__':
    # ---------- 测试文本 ----------
    source_english = """\
1. Definitions. "Software" shall mean the computer program known as "DataSync Pro" in object code form, including all Updates provided hereunder. "Licensee" shall mean the individual or entity accepting this Agreement.

2. Grant of License. Subject to Licensee's compliance with the terms and conditions of this Agreement, Licensor hereby grants to Licensee a non-exclusive, non-transferable, revocable license to install and use the Software solely for Licensee's internal business purposes for a period of twelve (12) months from the Effective Date.

3. Restrictions. Licensee shall not: (a) modify, adapt, translate, or create derivative works based on the Software; (b) reverse engineer, decompile, or disassemble the Software; (c) sublicense, rent, lease, or lend the Software to any third party; or (d) remove, alter, or obscure any proprietary notices on the Software.

4. Limitation of Liability. TO THE MAXIMUM EXTENT PERMITTED BY APPLICABLE LAW, IN NO EVENT SHALL LICENSOR BE LIABLE FOR ANY INDIRECT, INCIDENTAL, SPECIAL, PUNITIVE, OR CONSEQUENTIAL DAMAGES, INCLUDING BUT NOT LIMITED TO LOSS OF PROFITS, DATA, OR BUSINESS INTERRUPTION.

5. Governing Law. This Agreement shall be governed by and construed in accordance with the laws of the State of Delaware, without regard to its conflict of laws principles."""

    target_chinese = """\
1. 定义。"软件"是指名为"DataSync Pro"的计算机程序，以目标代码形式提供，包括本协议项下提供的所有更新。"被许可人"是指接受本协议的个人或实体。

2. 许可授予。在被许可人遵守本协议条款和条件的前提下，许可人特此授予被许可人一项非独占、不可转让、可撤销的许可，允许被许可人仅为内部业务目的安装和使用软件，期限为自生效日期起十二(12)个月。

3. 限制。被许可人不得：(a) 修改、改编、翻译或基于软件创作衍生作品；(b) 对软件进行逆向工程、反编译或反汇编；(c) 向任何第三方再许可、出租、租赁或出借软件；或 (d) 删除、更改或遮盖软件上的任何所有权声明。

4. 责任限制。在适用法律允许的最大范围内，许可方在任何情况下均不对任何间接、偶然、特殊、惩罚性或后果性损害承担责任，包括但不限于利润损失、数据丢失或业务中断。许可方的总累计责任不得超过主张索赔前十二个月内被许可方支付的许可费。

5. 管辖法律。本协议受特拉华州法律管辖并依据其解释，不考虑其冲突法原则。"""

    # 定义章节（从英文原文提取）
    definitions_section = (
        '"Software" shall mean the computer program known as "DataSync Pro" '
        'in object code form, including all Updates provided hereunder. '
        '"Licensee" shall mean the individual or entity accepting this Agreement.'
    )

    print("=" * 70)
    print("法律翻译确定性校验引擎 — 独立验证测试")
    print("=" * 70)

    # ---- 1. ClauseTreeVerifier ----
    print("\n" + "-" * 50)
    print("【1】条款树构建与比对")
    print("-" * 50)

    ctv = ClauseTreeVerifier()
    src_tree = ctv.build_tree(source_english.split('\n'))
    tgt_tree = ctv.build_tree(target_chinese.split('\n'))

    print(f"源文条款节点数: {ctv._count_nodes(src_tree)}")
    print(f"译文条款节点数: {ctv._count_nodes(tgt_tree)}")

    # 打印源文树结构
    def print_tree(node, indent=0):
        if node['number'] != '__ROOT__':
            print(f"{'  ' * indent}[{node['number']}] depth={node['depth']} parent={node.get('parent')} "
                  f"text={node['text'][:60]}...")
        for child in node.get('children', []):
            print_tree(child, indent + 1)

    print("\n源文条款树:")
    print_tree(src_tree)
    print("\n译文条款树:")
    print_tree(tgt_tree)

    clause_issues = ctv.compare(src_tree, tgt_tree)
    print(f"\n条款树比对发现 {len(clause_issues)} 个问题:")
    for issue in clause_issues:
        print(f"  [{issue['severity'].upper()}] {issue['type']}: {issue['detail']}")

    # ---- 2. DefinedTermScanner ----
    print("\n" + "-" * 50)
    print("【2】定义术语提取与一致性扫描")
    print("-" * 50)

    dts = DefinedTermScanner()
    glossary = dts.extract_definitions(definitions_section)
    print(f"从定义章节提取的术语: {glossary}")

    # 手动补充完整术语表（因为定义章节只包含部分定义）
    full_glossary = {
        **glossary,
        'Licensor': '许可人',
        'Updates': '更新',
        'Effective Date': '生效日期',
    }
    print(f"完整术语表: {full_glossary}")

    term_issues = dts.scan_consistency(target_chinese, full_glossary)
    print(f"\n术语一致性扫描发现 {len(term_issues)} 个问题:")
    for issue in term_issues:
        print(f"  [{issue.get('severity', 'info').upper()}] 术语 \"{issue['term']}\": "
              f"期望=\"{issue['expected']}\" 发现=\"{issue['found']}\" @ {issue['location']}")

    # ---- 3. CrossReferenceValidator ----
    print("\n" + "-" * 50)
    print("【3】交叉引用验证")
    print("-" * 50)

    crv = CrossReferenceValidator()
    # 添加含引用的文本
    text_with_refs = source_english + "\nSee Section 2.1 for details. According to Article 5, the governing law shall apply."
    extracted = crv.extract_references(text_with_refs)
    print(f"提取的引用编号: {sorted(extracted)}")

    ref_results = crv.validate(extracted, tgt_tree)
    print(f"\n交叉引用验证结果:")
    for r in ref_results:
        status = "存在" if r['exists'] else "不存在"
        print(f"  引用 \"{r['ref']}\": {status}")

    # ---- 4. AmountVerifier ----
    print("\n" + "-" * 50)
    print("【4】金额校验")
    print("-" * 50)

    av = AmountVerifier()
    # 测试 word_to_num
    test_cases = [
        "One Million Two Hundred Thousand",
        "Twelve",
        "One Hundred and Fifty",
        "Three Thousand Five Hundred",
        "Twenty Five",
    ]
    print("word_to_num 测试:")
    for tc in test_cases:
        result = av.word_to_num(tc)
        print(f"  \"{tc}\" → {result}")

    # 测试 verify
    amount_text = (
        "The license fee is $1,200,000 (One Million Two Hundred Thousand) per year. "
        "The initial payment is $50,000 (Fifty Thousand). "
        "Additional costs of $10,000 (Five Thousand) shall apply. "  # 故意错误: 10000 vs 5000
    )
    amount_issues = av.verify(amount_text)
    print(f"\n金额校验发现 {len(amount_issues)} 个不一致:")
    for issue in amount_issues:
        print(f"  [{issue['severity'].upper()}] 数字={issue['number']} "
              f"({issue['numeric_value']}) vs 大写=\"{issue['words']}\" "
              f"({issue['word_value']}) @ {issue['location'][:60]}...")

    # ---- 5. PatternEnhancer ----
    print("\n" + "-" * 50)
    print("【5】模式增强检测")
    print("-" * 50)

    # 标点检测
    punct_test = "这是中文文本,中间用了英文逗号.还有英文句号。另外这里有(英文括号)和 多余  的空格！还有半角!问号?"
    punct_issues = PatternEnhancer.detect_english_punctuation(punct_test)
    print(f"英文标点残留检测发现 {len(punct_issues)} 个问题:")
    for issue in punct_issues:
        print(f"  [{issue['severity'].upper()}] {issue['type']}: {issue['detail']} @ {issue['location'][:50]}")

    # 日期检测
    date_test = (
        "This Agreement is effective as of June 26, 2026. "
        "本协议自2026年6月26日起生效。"
        "The delivery date is 06/26/2026."
        "Also valid from Jan 15, 2025."
    )
    date_results = PatternEnhancer.detect_date_formats(date_test)
    print(f"\n日期格式检测发现 {len(date_results)} 个结果:")
    for d in date_results:
        print(f"  [{d['format_type']}] {d['value']} @ {d['location'][:50]}")

    # 金额模式检测
    amount_test = (
        "价格为$1,200,000元。USD 50,000。总计3,000,000元。"
        "人民币500,000元。此外还有$75,000.00的费用。"
    )
    amount_patterns = PatternEnhancer.detect_amount_patterns(amount_test)
    print(f"\n金额模式提取发现 {len(amount_patterns)} 个结果:")
    for ap in amount_patterns:
        print(f"  [{ap['amount_type']}] {ap['value']} {ap['currency']} @ {ap['location'][:50]}")

    # ---- 6. LegalVerifier 全量测试 ----
    print("\n" + "=" * 70)
    print("【6】LegalVerifier 全量校验")
    print("=" * 70)

    lv = LegalVerifier()
    full_result = lv.run_all(
        source=source_english,
        target=target_chinese,
        definitions_section=definitions_section,
    )

    print(f"\n--- 检验总结 ---")
    summary = full_result['summary']
    print(f"问题总数: {summary['total_issues']}")
    print(f"按严重级别: error={summary['by_severity'].get('error', 0)}, "
          f"warning={summary['by_severity'].get('warning', 0)}, "
          f"info={summary['by_severity'].get('info', 0)}")

    for category, key in [
        ('条款树比对', 'clause_tree'),
        ('术语一致性', 'term_scan'),
        ('交叉引用', 'cross_refs'),
        ('金额校验', 'amount'),
        ('标点格式', 'punctuation'),
        ('日期格式', 'dates'),
        ('金额模式', 'amount_patterns'),
    ]:
        items = full_result.get(key, [])
        if items:
            print(f"\n--- {category} ({len(items)} 项) ---")
            for item in items:
                sev = item.get('severity', 'info')
                detail = item.get('detail', item.get('location', str(item)[:80]))
                print(f"  [{sev.upper()}] {detail[:100]}")

    print("\n" + "=" * 70)
    print("全部测试完成。")
    print("=" * 70)
