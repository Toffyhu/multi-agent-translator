"""产出后处理器 — 清理编辑标记、修复标题、收敛标点"""

import re


def clean_chapter_output(chapter_text: str) -> str:
    """
    清理单章编辑Agent产出，移除CHANGES/TERMS标记块。
    
    Args:
        chapter_text: 编辑Agent的原始输出
    
    Returns:
        清洁版章节文本
    """
    # 1. 移除 ---CHANGES--- 到 ---TERMS--- 或文末的编辑记录
    #    模式: ---CHANGES--- 后面跟着任意内容，直到 ---TERMS--- 或文末
    text = re.sub(
        r'\n*---CHANGES---.*?(?=---TERMS---|\Z)',
        '',
        chapter_text,
        flags=re.DOTALL
    )
    
    # 2. 移除 ---TERMS--- 及其后的术语修订记录
    text = re.sub(
        r'\n*---TERMS---.*',
        '',
        text,
        flags=re.DOTALL
    )
    
    # 3. 清理可能残留的空行和多余空白
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()
    
    return text


def extract_clean_title(chapter_text: str) -> tuple[str, str]:
    """
    从编辑输出中提取干净的内容和标题。
    编辑输出格式: "### 第X章 标题\n\n正文..."
    
    Returns:
        (title, body)
    """
    lines = chapter_text.split('\n')
    
    # 找第一个"第X章"作为标题
    title = ""
    body_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        # 匹配 H2, H3, 或者纯文本的"第X章"
        if re.match(r'^(#{1,3}\s*)?第[一二三四五六七八九十\d]+章', stripped):
            title = re.sub(r'^#{1,3}\s*', '', stripped)
            body_start = i + 1
            # 跳过紧随的空行
            while body_start < len(lines) and not lines[body_start].strip():
                body_start += 1
            break
    
    body = '\n'.join(lines[body_start:]).strip()
    return title, body


def post_process_delivery(raw_text: str) -> str:
    """
    完整后处理流水线:
    1. 清理编辑标记
    2. 修复重复标题
    3. 标点收敛
    
    Args:
        raw_text: _run_delivery 组装的原始最终文本
    
    Returns:
        清洁版最终译文
    """
    # 1. 逐章清理编辑标记
    text = clean_chapter_output(raw_text)
    
    # 2. 修复重复/空标题
    #    模式: "## 第X章 \n\n### 第X章 标题" → "## 第X章 标题"
    text = re.sub(
        r'## 第([\d一二三四五六七八九十]+)章\s*\n+#+\s*第\1章\s*',
        r'## 第\1章 ',
        text
    )
    
    # 3. 清理LLM输出的Meta说明文字（如"这是XX的短篇小说"等前缀）
    text = re.sub(
        r'^(这是一项(特殊|重要|关键)的(使命|任务|翻译)。*?\n\n)',
        '', text, count=1, flags=re.DOTALL
    )
    text = re.sub(
        r'^(这是(.+?)的(短篇小说|散文|作品|名篇)。*?\n\n)',
        '', text, count=1, flags=re.DOTALL
    )
    
    # 4. 统一标点：英文引号→中文引号
    text = text.replace('\u201c', '\u201c').replace('\u201d', '\u201d')
    text = text.replace("'", '\u2018').replace("'", '\u2019')
    
    # 5. 清理多余空行（但保留标题行，防止章节丢失）
    text = re.sub(r'\n{4,}', '\n\n\n', text)
    
    # 4. 标点收敛（保守处理）
    # text = re.sub(r'(?<!\n)——(?!\n)', '，', text)  # 可选
    
    # 4. 标点收敛: 中文段落中过多"——"替换为逗号
    #    (保守处理，只替换非对话中的破折号)
    # text = re.sub(r'(?<!\n)——(?!\n)', '，', text)  # 可选，视情况
    
    # 5. 清理"他"→"它"（保持代词一致）
    #    在描述Buck的段落中
    #    这个主要是正则应配，建议在主编终审阶段修复而非后处理
    
    # 6. 清理多余的连续空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    return text.strip()
