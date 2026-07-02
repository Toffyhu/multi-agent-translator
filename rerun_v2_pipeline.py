#!/usr/bin/env python3
"""三版融合流水线重译脚本 — 对《另一个女人》《吉查》重译验证"""

import os, sys, json, time, requests
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src import TranslationPipeline
from src.agents.rewriter import RewriterAgent
from src.agents.chief_editor import ChiefEditorAgent
from src.models.registry import ModelRegistry
from src.assets.schema import AssetStore

# 确保 API Key — 从环境变量读取，不硬编码
if not os.environ.get('DASHSCOPE_API_KEY'):
    print("⚠️ 请设置 DASHSCOPE_API_KEY 环境变量")
    print("   export DASHSCOPE_API_KEY='你的阿里云APIKey'")
    sys.exit(1)

registry = ModelRegistry('config/models.yaml')
assets = AssetStore('./test_assets_store')
assets.load_knowledge_base('shared_knowledge_base.md')

rewriter = RewriterAgent(registry, assets)
editor = ChiefEditorAgent(registry, assets)

stories = [
    {
        "title": "The Other Woman",
        "author": "Sherwood Anderson",
        "file": "/workspace/evolution_logs/the_other_woman.txt",
        "old_translation": "/workspace/evolution_logs/the_other_woman_zh.md",
    },
    {
        "title": "Ghitza",
        "author": "Konrad Bercovici",
        "file": "/workspace/evolution_logs/ghitza.txt",
        "old_translation": "/workspace/evolution_logs/ghitza_zh.md",
    },
]

def extract_english_source(path: str, max_words: int = 1000) -> str:
    with open(path, 'r') as f:
        text = f.read()
    # 跳过标题行，取纯正文
    lines = text.split('\n')
    body_start = 0
    for i, ln in enumerate(lines):
        if ln.strip() and not ln.startswith(('THE OTHER WOMAN', 'GHITZA', '#By#', 'BY', 'From')):
            body_start = i
            break
    body = '\n'.join(lines[body_start:])
    words = body.split()[:max_words]
    return ' '.join(words)


def old_trans_first_1000(path: str) -> str:
    with open(path, 'r') as f:
        text = f.read()
    for sep in ['\n\n---\n\n', '---\n\n', '\n\n* * * * *']:
        if sep in text:
            text = text.split(sep)[0]
            break
    text = re.sub(r'^## .+?\n', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*.*?\*\*', '', text)
    lines = text.strip().split('\n')
    body_lines = [l for l in lines if l.strip() and not l.startswith('#') and not l.startswith('[')]
    return '\n'.join(body_lines[:30])


def run_pipeline(english_text: str, story_title: str, story_author: str):
    """对一段英文原文执行：直译→再创→融合"""
    import re
    
    print(f"\n{'='*60}")
    print(f"  开始: {story_title}")
    print(f"{'='*60}")
    print(f"  原文长度: {len(english_text.split())} 词")
    
    # Step 1: 直译（用Translator的API调用方式）
    print(f"\n  [1/3] 直译...")
    resp1 = requests.post(
        'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
        headers={'Authorization': f'Bearer {os.environ.get("DASHSCOPE_API_KEY")}', 'Content-Type': 'application/json'},
        json={
            'model': 'qwen-plus',
            'messages': [{
                'role': 'user',
                'content': f'你是一位专业的英→中文学翻译。请将以下英文翻译成优美的中文。\n\n翻译原则：像作家一样写作，像诗人一样遣词。保留原文的语气和节奏。人名音译并首次括号标注原名。段落结构与原文保持一致。\n\n原文：\n{english_text}'
            }],
            'max_tokens': 3000,
            'temperature': 0.6
        },
        timeout=120
    )
    v1 = resp1.json().get('choices', [{}])[0].get('message', {}).get('content', '')
    print(f"  ✅ 直译完成 ({len(v1)} 字)")
    
    # Step 2: 再创（Rewriter）
    print(f"\n  [2/3] 再创重述...")
    rewrite_result = rewriter.rewrite(
        source_text=english_text[:3000],
        translator_output=v1[:3000],
        style_hint="让文字像中国作家写的一样自然，像沈从文或汪曾祺那样洗练"
    )
    v2 = rewrite_result.data.get("rewritten_text", "")
    print(f"  ✅ 再创完成 ({len(v2)} 字)")
    
    # Step 3: 融合（Chief Editor fusion_mode）
    print(f"\n  [3/3] 融合...")
    fusion_result = editor.fusion_mode(
        chapter_id=1,
        chapter_title=story_title,
        v1_direct=v1[:3000],
        v2_rewrite=v2[:3000],
        style_note="文学短篇小说，保留原叙事特色",
    )
    fused = fusion_result.data.get("fused_text", "")
    print(f"  ✅ 融合完成 ({len(fused)} 字)")
    
    return {"v1": v1, "v2": v2, "fused": fused}


def main():
    print("=" * 60)
    print("  三版融合流水线 — 重译验证")
    print("=" * 60)
    
    for story in stories:
        english = extract_english_source(story["file"], max_words=1000)
        result = run_pipeline(english, story["title"], story["author"])
        
        # 保存对比
        output = f"""# {story['title']} — 三版对照

**作者**: {story['author']}
**生成时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}

---

## 版本A（直译版 — V1）

{result['v1'][:3000]}

---

## 版本B（再创版 — V2）

{result['v2'][:3000]}

---

## 融合版（主编融合 — V1+V2）

{result['fused'][:3000]}

---

*WorkBuddy三版融合流水线 自动生成*
"""
        safe_name = story["title"].lower().replace(" ", "_").replace("'", "")
        path = f"/workspace/evolution_logs/{safe_name}_v2.md"
        with open(path, 'w', encoding='utf-8') as f:
            f.write(output)
        print(f"\n  ✅ 已保存: {path}")
    
    print(f"\n{'='*60}")
    print(f"  重译完成！")
    print(f"{'='*60}")


if __name__ == '__main__':
    import re
    main()
