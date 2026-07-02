#!/usr/bin/env python3
"""流水线翻译启动器 — 对指定公版短篇执行三版融合翻译

用法:
    python3.11 run_translation.py --story brothers --type anthology1921
    python3.11 run_translation.py --file /path/to/story.txt --title "Title" --author "Author"
"""

import os, sys, json, time, re, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# API Key — 从环境变量读取，不硬编码
if not os.environ.get('DASHSCOPE_API_KEY'):
    print("⚠️ 请设置 DASHSCOPE_API_KEY 环境变量")
    print("   export DASHSCOPE_API_KEY='你的阿里云APIKey'")
    sys.exit(1)

from src.agents.rewriter import RewriterAgent
from src.agents.chief_editor import ChiefEditorAgent
from src.models.registry import ModelRegistry
from src.assets.schema import AssetStore
from src.utils.post_processor import post_process_delivery

import requests

# 注册的故事来源
STORIES = {
    # 1920年选本
    "signal_tower":   {"title": "The Signal Tower", "author": "Wadsworth Camp", "file": "/workspace/evolution_logs/best_stories_1920.txt", "start": "THE SIGNAL TOWER"},
    "the_rending":    {"title": "The Rending", "author": "James Oppenheim", "file": "/workspace/evolution_logs/best_stories_1920.txt", "start": "THE RENDING"},
    "turkey_red":     {"title": "Turkey Red", "author": "Frances Gilchrist Wood", "file": "/workspace/evolution_logs/best_stories_1920.txt", "start": "TURKEY RED"},
    # 1921年选本
    "brothers":       {"title": "Brothers", "author": "Sherwood Anderson", "file": "/tmp/best1921_full.txt", "start": "BROTHERS[2]"},
    "fanutza":        {"title": "Fanutza", "author": "Konrad Bercovici", "file": "/tmp/best1921_full.txt", "start": "FANUTZA[3]"},
}

def extract_text(filepath: str, start_marker: str, max_words: int = 3000) -> str:
    """从选本文本中提取指定故事"""
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"源文件不存在: {filepath}")
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        text = f.read()
    pos = text.find(start_marker)
    if pos == -1:
        raise ValueError(f"在文件中找不到标记: {start_marker}")
    # 从故事标题后开始提取
    body_start = text.find('\n\n', pos)
    if body_start == -1:
        body_start = pos + len(start_marker)
    body = text[body_start:]
    words = body.split()[:max_words]
    return ' '.join(words)

def translate_story(english_text: str, title: str, author: str, use_rewrite: bool = True):
    """执行三版融合翻译"""
    start_time = time.time()
    print(f"\n{'='*60}")
    print(f"  翻译: {title}")
    print(f"  作者: {author}")
    print(f"  原文: {len(english_text.split())}词")
    print(f"{'='*60}")

    registry = ModelRegistry('config/models.yaml')
    assets = AssetStore('./.temp_assets')
    assets.load_knowledge_base('shared_knowledge_base.md')

    rewriter = RewriterAgent(registry, assets)
    editor = ChiefEditorAgent(registry, assets)

    # Step 1: 直译
    print(f"\n  [1/3] 直译...")
    resp = requests.post(
        'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
        headers={'Authorization': f'Bearer {os.environ.get("DASHSCOPE_API_KEY")}', 'Content-Type': 'application/json'},
        json={
            'model': 'qwen-plus',
            'messages': [{'role': 'user', 'content': f'你是一位专业的英→中文学翻译。请将以下英文翻译成优美的中文。\n\n翻译原则：像作家一样写作，像诗人一样遣词。保留原文的语气和节奏。人名音译并首次括号标注原名。\n\n原文：\n{english_text}'}],
            'max_tokens': 4000, 'temperature': 0.6
        }, timeout=120
    )
    v1 = resp.json().get('choices', [{}])[0].get('message', {}).get('content', '')
    print(f"  ✅ 直译完成 ({len(v1)}字)")

    if use_rewrite:
        # Step 2: 再创
        print(f"\n  [2/3] 再创重述...")
        rewrite_result = rewriter.rewrite(
            source_text=english_text[:3000],
            translator_output=v1[:3000],
            style_hint="让文字像中国作家写的一样自然，读不出翻译腔"
        )
        v2 = rewrite_result.data.get("rewritten_text", "")
        print(f"  ✅ 再创完成 ({len(v2)}字)")

        # Step 3: 融合
        print(f"\n  [3/3] 主编融合...")
        fusion_result = editor.fusion_mode(
            chapter_id=1, chapter_title=title,
            v1_direct=v1[:3000], v2_rewrite=v2[:3000],
            style_note="文学短篇小说",
        )
        fused = fusion_result.data.get("fused_text", v1)
        print(f"  ✅ 融合完成 ({len(fused)}字)")
        final = fused
    else:
        final = v1

    # 后处理
    final = post_process_delivery(final)
    duration = time.time() - start_time

    # 保存输出
    safe_name = title.lower().replace(" ", "_").replace("'", "").replace(",","")
    output_path = f"/workspace/evolution_logs/{safe_name}_production.md"
    
    output = f"""# {title}

**作者**: {author}  
**翻译流水线**: WorkBuddy 3-version fusion
**生成时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}
**耗时**: {duration:.0f}秒

---

{final}

---

*由 WorkBuddy 翻译流水线自动生成 — {time.strftime('%Y-%m-%d %H:%M:%S')}*
"""
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(output)
    print(f"\n  ✅ 已保存: {output_path}")
    print(f"  ⏱ 总耗时: {duration:.0f}秒")

    return output_path

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--story', help='预注册的故事名')
    parser.add_argument('--type', default='anthology1921', help='来源类型')
    parser.add_argument('--file', help='自定义文件路径')
    parser.add_argument('--title', default='Untitled', help='作品名')
    parser.add_argument('--author', default='Unknown', help='作者')
    parser.add_argument('--no-rewrite', action='store_true', help='不使用三版融合')
    parser.add_argument('--words', type=int, default=3000, help='最大词数')
    args = parser.parse_args()

    if args.story and args.story in STORIES:
        info = STORIES[args.story]
        text = extract_text(info['file'], info['start'], max_words=args.words)
        translate_story(text, info['title'], info['author'], use_rewrite=not args.no_rewrite)
    elif args.file:
        with open(args.file, 'r') as f:
            text = f.read()[:args.words*8]
        translate_story(text, args.title, args.author, use_rewrite=not args.no_rewrite)
    else:
        print("请指定 --story 或 --file")
        print(f"可选故事: {list(STORIES.keys())}")
        sys.exit(1)
