# Multi-Agent Translator — 多智能体文学翻译流水线

> 7个Agent分工协作 · 三版融合（V1直译 → V2再创 → Fused融合） · 创作导向

用AI模拟出版社分工体系，将文学翻译从"单模型一次输出"升级为"多Agent流水线协作"。核心创新在于**用再创作替代查错**——不是改bug，是换一种写法。

---

## 架构概览

![架构图](docs/architecture_diagram.png)

7个智能体按流水线依次执行，分为六个阶段：

```
源文本 → 全书分析师 → 术语风格师
         ├── 章节译者（V1直译，温度0.6）
         ├── 再创作者（V2再创，温度0.7）
         └── 融合器（V1+V2 → Fused，温度0.4）
         → 主编终审 → 事实核查 → PDF交付
```

详见 [架构文档](docs/ARCHITECTURE.md)。

---

## 快速开始

```bash
git clone https://github.com/Toffyhu/multi-agent-translator.git
cd multi-agent-translator
pip install -e .
```

### 配置模型

编辑 `config/models.yaml`，填入至少一个 API Key（阿里云 Qwen 系列推荐）：

```yaml
models:
  qwen3.7-max:
    api_key: "your-api-key"
    endpoint: "https://dashscope.aliyuncs.com/compatible-mode/v1"
```

### 一键翻译

```bash
python run_translation.py --story BROTHERS[2]
```

支持预注册的5篇故事：
- `THE_OTHER_WOMAN` — 舍伍德·安德森《另一个女人》
- `BROTHERS[2]` — 舍伍德·安德森《兄弟》
- `GHITZA` — 康拉德·贝尔科维奇《吉查》
- `THE_EXPERIMENT` — 麦克斯韦尔·伯特《实验》
- `FREE_MANS_WORSHIP` — 伯特兰·罗素《自由人的崇拜》

自定义文件：
```bash
python run_translation.py --file /path/to/source.txt --words 3000
```

---

## 三版融合流程

| 版本 | 角色 | 模型 | 温度 | 目标 |
|------|------|------|------|------|
| **V1** | 章节译者 | Qwen3.7-Plus | 0.6 | 准确直译，保留所有事实细节 |
| **V2** | 再创作者 | Qwen-Plus | 0.7 | 中文作家思维重述，流畅自然 |
| **Fused** | 融合器 | Qwen-Plus | 0.4 | "以V1为骨，以V2为肉"，精准融合 |

> 已验证4种文学风格：乡村心理（Anderson）/ 社交喜剧（Burt）/ 东欧民间（Bercovici）/ 哲学散文（Russell）
> 6篇实验译文综合评分 8.4/10，融合版显著优于纯直译（8.0）和纯再创（7.5）

---

## 项目结构

```
.
├── config/              # 模型配置
│   ├── default.yaml
│   └── models.yaml
├── src/
│   ├── agents/          # 7个Agent源码
│   │   ├── base.py
│   │   ├── book_analyzer.py     # ①全书分析师
│   │   ├── term_stylist.py      # ②术语风格师
│   │   ├── chapter_translator.py # ③章节译者
│   │   ├── rewriter.py          # ④再创作者
│   │   ├── chief_editor.py      # ⑤主编终审（含融合器）
│   │   └── fact_checker.py      # ⑦事实核查
│   ├── models/
│   │   ├── registry.py          # 模型注册表
│   │   └── prompts/             # 完整提示词
│   ├── pipeline/
│   │   └── orchestrator.py      # 流水线调度器
│   └── utils/
│       ├── post_processor.py    # 后处理（引号/注释清洗）
│       ├── text_splitter.py     # 文本分割
│       └── version_tracker.py   # 版本追溯
├── skills/              # 智能体技能定义
├── shared_knowledge_base.md     # 共享知识库（含风格指南）
├── run_translation.py           # 一键翻译启动器
├── gen_pdf_v2.py                # PDF生成工具
├── docs/                        # 实验报告/架构文档
└── pyproject.toml
```

---

## 与业界方案对比

| 维度 | Andrew Ng翻译Agent | MAATS（arXiv 2025） | **本方案** |
|------|-------------------|-------------------|-----------|
| 智能体数量 | 1个（3步反射式） | 9个（1译+7查+1合） | **7个**（含再创作者） |
| 核心方法论 | 反思式自我修正 | MQM七维度逐项审查 | **再创作+融合** |
| 文学翻译适配 | ❌ 不适合 | ❌ MQM不适合文学 | **✅ 专门为文学设计** |
| 温度控制 | 固定 | 低温（0–0.3） | **分阶段：译0.6/创0.7/合0.4** |
| 最佳适用场景 | 快速技术文档 | 合同/医疗/法律 | **文学/散文/创意类** |

详见[实验报告](docs/article_literary_translation_agent.md)。

---

## 安装依赖

```bash
pip install -e .
```

需要 Python 3.11+，模型依赖外部 API（阿里云 DashScope 或兼容 OpenAI 接口的服务）。

---

## 许可证

MIT License
