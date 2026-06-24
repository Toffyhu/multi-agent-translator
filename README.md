# Translation Pipeline — 多智能体翻译流水线

> **出版级翻译质控系统** | 5 Agent · 全局资产底座 · 五阶段流水线 · 三级质控

基于多智能体协作的翻译质量控制系统。对齐专业出版的 **初译→统稿→终审** 三级质控标准，通过专用Agent分工 + 中心化术语/风格规范 + 确定性校验引擎，实现远超单模型翻译的质量天花板。

---

## 架构概览

```
                        全局翻译资产库（中心底座）
                        ├── 原文档案（章节+伏笔关系图）
                        ├── 术语规范表（强制/灵活分级）
                        ├── 风格执行手册（五维量化规则）
                        └── 参考译本库

    ┌──────────────────────┼──────────────────────┐
    ▼                       ▼                       ▼
①全书分析Agent          ②术语风格Agent          术语校验引擎
 模型: DeepSeek V4-Pro    模型: DeepSeek V4-Pro    (确定性中间件)
    │                       │                       │
    └───────────┬───────────┴───────────┬───────────┘
                │                       │
                ▼                       ▼
        ③分章翻译Agent集群            初译质检卡点
         并行·分层模型策略             术语/漏译/通顺度
                │
    ┌───────────┴───────────┐
    ▼                       ▼
④主编终审Agent          ⑤事实核查Agent
 统稿+评审双模式          搜索+交叉验证
    │                       │
    └───────────┬───────────┘
                ▼
            最终交付稿
```

---

## 核心设计

### 5 Agent 精确分工

| # | Agent | 单一职责 | 首选模型 |
|---|-------|---------|---------|
| ① | 全书分析 | 脉络梳理 + 伏笔标记 + 文本类型判定 | DeepSeek V4-Pro |
| ② | 术语风格 | 术语表 + 风格五维量化手册 | DeepSeek V4-Pro |
| ③ | 分章翻译(×N) | 单章初译 + 术语遵循 + 存疑标注 | DeepSeek V4-Pro + 分层策略 |
| ④ | 主编终审 | 统稿润色 + 四维评审 + 回退裁决 | DeepSeek V4-Pro |
| ⑤ | 事实核查 | 人名/地名/数字/概念/引用交叉验证 | DeepSeek V4-Pro + 搜索 |

### 分层模型策略（翻译环节）

| 场景 | 使用模型 | 理由 |
|------|---------|------|
| 80%通用章节 | DeepSeek V4-Pro | 学术4.9分，性价比最优 |
| 法律/合同章节 | GPT-5.4 | 条件嵌套结构保留唯一满分5.0 |
| 关键文学段落 | Claude Opus 4.7 | 文学翻译4.7分最高 |
| 术语密集型章节 | Qwen-MT | 内置术语干预+翻译记忆 |

> 模型选型基于 BelinDoc 2026.04 六模型盲评 + BISU 2025翻译质量评测数据。

### 风格五维量化

将模糊的"大师风格"拆解为可执行、可校验的规则：

| 维度 | 量化指标 |
|------|---------|
| 句式结构 | 平均句长、长短句比例、主动/被动语态比 |
| 词汇密度 | 实词/虚词比、四字格频率、成语密度 |
| 修辞偏好 | 比喻/排比/反问等修辞格频率 |
| 语气调性 | 口语化/书面化比值、敬语/随意度 |
| 文化适配 | 归化/异化策略比、文化负载词处理 |

### 回退机制

```
术语遵循率 < 95%  → 回退初译Agent
信度 < 60/100    → 回退初译Agent
风格一致性 < 70   → 回退主编Agent
平行版 > 30%段落优于主编版 → 回退主编Agent

同章节最多回退2次，超限 → 人工介入队列
```

---

## 快速开始

### 安装

```bash
git clone https://github.com/your-org/translation-pipeline.git
cd translation-pipeline
pip install -e .
```

### 配置

```bash
cp .env.example .env
# 编辑 .env，填入至少一个 API Key（DeepSeek 必须）
```

### 使用

```python
import asyncio
from src import TranslationPipeline

async def main():
    pipeline = TranslationPipeline()

    # 读取原文
    source_text = open("my_book.txt").read()

    # 执行流水线
    result = await pipeline.run(
        source_text=source_text,
        title="示例书名",
        author="作者",
        source_lang="en",
        target_lang="zh",
        target_style="傅雷风格 — 流畅地道的中文",
    )

    print(result.summary)

    # 获取最终译文
    if result.final_output:
        print(result.final_output["final_text"][:500])

asyncio.run(main())
```

### 分阶段执行

```python
pipeline = TranslationPipeline()

# 仅运行前置筹备
prep = await pipeline._run_preparation(
    source_text, "书名", "作者", "en", "zh", "傅雷风格", None, True,
)

# 确认术语表后继续
# ... 后续阶段 ...
```

---

## 项目结构

```
translation-pipeline/
├── config/
│   ├── default.yaml          # 流水线配置
│   └── models.yaml           # 模型注册表 + Agent-模型映射
├── src/
│   ├── __init__.py
│   ├── agents/
│   │   ├── base.py           # Agent基类
│   │   ├── book_analyzer.py  # ①全书分析Agent
│   │   ├── term_stylist.py   # ②术语风格Agent
│   │   ├── chapter_translator.py  # ③分章翻译Agent
│   │   ├── chief_editor.py   # ④主编终审Agent
│   │   └── fact_checker.py   # ⑤事实核查Agent
│   ├── assets/
│   │   ├── schema.py         # 全局资产库数据结构
│   │   └── term_enforcer.py  # 术语校验引擎
│   ├── models/
│   │   ├── registry.py       # 模型注册表
│   │   └── prompts/          # Prompt模板
│   ├── pipeline/
│   │   └── orchestrator.py   # 流水线调度器
│   └── utils/
│       └── version_tracker.py # 版本追溯
├── tests/
├── pyproject.toml
├── .env.example
└── README.md
```

---

## 设计原则

1. **Agent原子化** — 每个Agent只承担单一职责，不越界（量化工厂S07验证：因子越少越不易过拟合）
2. **中心化资产底座** — 术语表/风格手册是所有Agent的"宪法"，只能读取、禁止私改
3. **确定性校验优先** — 术语校验引擎是确定性NLP管道，零幻觉，Agent自查不可替代
4. **量化评审杜绝主观** — 四维打分（信/达/雅/一致性），择优融合而非主观选优
5. **全程可追溯** — 每句话保留 `原文→初译→统稿→终审` 全链路版本

---

## 模型成本估算

以一本10万字英文书为例：

| 阶段 | 模型调用 | 预估成本 |
|------|---------|---------|
| 全书分析 | DeepSeek V4-Pro ×1 | ~$0.15 |
| 术语风格 | DeepSeek V4-Pro ×1 | ~$0.05 |
| 分章翻译(20章) | DeepSeek V4-Pro ×20 | ~$1.50 |
| 主编统稿 | DeepSeek V4-Pro ×1 | ~$0.30 |
| 事实核查 | DeepSeek V4-Pro ×1 | ~$0.10 |
| **合计** | | **~$2.10** |

> 对比：人工翻译同类书籍市场价 $3000-8000。即使全量加入GPT-5.4法律章节 + Claude文学段落，总成本也不超过$5。

---

## 路线图

- [x] MVP: 5 Agent架构 + 模型注册表 + 流水线调度器
- [ ] 术语校验引擎增强（模糊匹配+智能替换）
- [ ] 多版本并行翻译集群（抽检式3模型对比）
- [ ] Hermes框架完整集成
- [ ] 风格校验引擎（五维基准值自动对比）
- [ ] Web UI（流水线可视化 + 人工审批界面）
- [ ] 翻译Agent竞技场（多模型同文PK，择优录用）

---

## 许可证

MIT License

---

*Built with ❤️ by WorkBuddy量化工场*
