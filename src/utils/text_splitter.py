"""文本切分与安全截断工具

解决段落/句子被硬截断导致上下文不完整的问题。
"""

import re


def safe_context(text: str, max_chars: int = 500, from_end: bool = True) -> str:
    """
    安全截取上下文，确保落在段落/句子边界上。

    Args:
        text: 原始文本
        max_chars: 目标截取长度
        from_end: True=从末尾向前取(上一章尾), False=从开头向后取(下一章头)

    Returns:
        截取后的文本，保证完整性
    """
    if not text:
        return ""
    if len(text) <= max_chars:
        return text

    if from_end:
        # 从末尾往前取，回退到最近的段落或句子边界
        truncated = text[-max_chars:]

        # 优先级1: 段落边界（双换行）
        first_para = truncated.find('\n\n')
        if 0 < first_para < max_chars:
            return truncated[first_para:].lstrip('\n')

        # 优先级2: 句子边界（句号/问号/感叹号+空格/换行）
        for delim in ['。', '！', '？', '.\n', '!\n', '?\n', '.\r\n']:
            pos = truncated.find(delim)
            if 0 < pos < max_chars:
                return truncated[pos + len(delim):]

        # 优先级3: 单换行
        first_nl = truncated.find('\n')
        if 0 < first_nl < max_chars:
            return truncated[first_nl:].lstrip('\n')

        # 都不满足，直接取最后max_chars
        return truncated.lstrip()

    else:
        # 从开头向后取，截断到最近的段落或句子边界
        truncated = text[:max_chars]

        # 优先级1: 段落边界
        last_para = truncated.rfind('\n\n')
        if last_para > max_chars // 2:  # 至少截取了一半以上
            return truncated[:last_para].rstrip('\n')

        # 优先级2: 句子边界（从后往前找）
        for delim in ['。', '！', '？', '.\n', '!\n', '?\n']:
            pos = truncated.rfind(delim)
            if pos > max_chars // 2:
                return truncated[:pos + len(delim)]

        # 优先级3: 单换行
        last_nl = truncated.rfind('\n')
        if last_nl > max_chars // 2:
            return truncated[:last_nl]

        return truncated


def safe_chapter_context(
    prev_text: str,
    next_text: str,
    max_chars: int = 500,
) -> tuple[str, str]:
    """
    安全的章节上下文截取（双端）。

    Args:
        prev_text: 上一章的完整文本
        next_text: 下一章的完整文本
        max_chars: 目标截取长度

    Returns:
        (safe_prev_tail, safe_next_head)
    """
    safe_prev = safe_context(prev_text, max_chars, from_end=True) if prev_text else ""
    safe_next = safe_context(next_text, max_chars, from_end=False) if next_text else ""
    return safe_prev, safe_next


def smart_chapter_boundary(
    full_text: str,
    rough_boundary: int,
    search_window: int = 500,
) -> int:
    """
    在粗略边界附近找到最佳断点（用于章节切分）。

    Args:
        full_text: 完整文本
        rough_boundary: 粗略边界位置（如章节标记"Chapter X"出现的位置）
        search_window: 搜索范围

    Returns:
        最佳断点位置
    """
    window = full_text[rough_boundary:rough_boundary + search_window]

    # 优先级: 段落换行 > 空行 > 句号
    for delim in ['\n\n\n', '\n\n', '\n---\n', '\n***\n']:
        pos = window.find(delim)
        if 0 < pos < search_window:
            return rough_boundary + pos + len(delim)

    # 句子边界
    for delim in ['。\n', '！\n', '？\n', '.\n\n']:
        pos = window.find(delim)
        if 0 < pos < search_window:
            return rough_boundary + pos + len(delim)

    # 回退：找最近的换行
    pos = window.find('\n')
    if 0 < pos < search_window:
        return rough_boundary + pos + 1

    return rough_boundary + 500  # 兜底
