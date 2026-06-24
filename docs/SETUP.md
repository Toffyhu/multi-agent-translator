# 部署指南 — WorkBuddy 翻译流水线

> 从零开始搭建并运行翻译流水线的完整步骤。

---

## 1. 环境准备

### 1.1 Python 环境

```bash
# 推荐使用 Python 3.11+
python3.11 --version

# 创建虚拟环境（可选但推荐）
python3.11 -m venv venv
source venv/bin/activate  # Linux/Mac
# .\venv\Scripts\activate  # Windows
```

### 1.2 安装依赖

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 1.3 安装中文字体（PDF生成用）

```bash
# Ubuntu/Debian
sudo apt install fonts-noto-cjk

# macOS
brew install font-noto-sans-cjk

# 验证字体
fc-list :lang=zh | grep Noto
```

---

## 2. API Key 配置

### 2.1 获取 API Key

至少需要一种模型提供商的API密钥：

#### 推荐：阿里云百炼（DashScope）
1. 访问 https://dashscope.aliyun.com/
2. 注册/登录 → 创建API Key
3. 免费额度：Qwen3.7-Plus 无限量，Qwen3.7-Max 每月100万tokens

#### 备选：DeepSeek
1. 访问 https://platform.deepseek.com/
2. 注册 → 创建API Key

### 2.2 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env` 文件：

```ini
# 阿里云 DashScope（推荐，有免费额度）
DASHSCOPE_API_KEY=sk-your-key-here

# DeepSeek（备选）
DEEPSEEK_API_KEY=sk-your-key-here

# QQ邮箱（可选，用于PDF自动发送）
EMAIL_ADDRESS=your@qq.com
EMAIL_PASSWORD=your-smtp-password
```

> QQ邮箱SMTP密码需在邮箱设置中生成授权码，非登录密码。

---

## 3. 快速测试

### 3.1 运行内置测试

```bash
# 测试API连通性
python test_run.py

# 若输出 "API 连接成功" 则配置正常
```

### 3.2 翻译示例短篇

```bash
# 翻译 Ambrose Bierce 的 "Moxon's Master"
python run_moxon.py

# 输出文件：moxon_comparison.md（原文+译文对照）
```

### 3.3 翻译长篇作品

```bash
# 指定书籍文件，启动全本翻译
python run_full_book.py --book /path/to/book.txt
```

---

## 4. 配置详解

### 4.1 模型选型

编辑 `config/models.yaml`：

```yaml
# 默认使用阿里云
default_provider: aliyun

# 各Agent可单独指定模型
book_analyzer:    aliyun/qwen3.7-max
term_stylist:     aliyun/qwen3.6-flash
chapter_translator: aliyun/qwen3.7-plus
chief_editor:     aliyun/qwen3.7-plus
fact_checker:     aliyun/qwen3.7-plus
```

可选的 provider/model 组合：

| Provider | 模型ID | 适用场景 |
|----------|--------|---------|
| aliyun | qwen3.7-max | 分析/文学翻译（付费） |
| aliyun | qwen3.7-plus | 主力翻译/编辑/核查（免费） |
| aliyun | qwen3.6-flash | 术语等简单任务（免费） |
| aliyun | deepseek-r1 | 需强推理的场景 |
| aliyun | deepseek-v3 | 深度分析 |
| deepseek | deepseek-chat | 备用 |

### 4.2 流水线参数

编辑 `config/default.yaml`：

```yaml
pipeline:
  max_retries: 3        # 最大重试次数
  temperature: 0.3      # 默认温度
  max_tokens: 4096      # 默认最大输出
  quality_gate: 7       # 最低质量评分（1-10）
```

---

## 5. 技能安装（可选）

项目内置了SkillHub技能包，用于IDE集成：

```bash
# 安装 SkillHub CLI
pip install skillhub

# 安装翻译技能
skill install skills/literary-translation.skill.md
skill install skills/translate-translator.skill.md
skill install skills/translation-proofreader.skill.md
```

---

## 6. 内容侦察（Explore 公版作品）

```bash
# 运行内容侦察
python -c "
from agents.content_scout import ContentScoutAgent
scout = ContentScoutAgent()
result = scout.execute(query='classic American short stories', max_results=10)
print(f'发现 {result.data[\"blanks_found\"]} 本中译空白作品')
"
```

---

## 7. 自动任务（可选）

系统内置两个自动进化任务，使用 CronCreate 设置：

```python
# 每30分钟自我进化
from cron_create import CronCreate
cron = CronCreate()
cron.schedule(every_30_minutes, task='read evolution_logs, reflect, search, pilot translate, log')

# 每小时内容侦察
cron.schedule(every_hour, task='scan gutenberg, check chinese translation, evaluate')
```

> 注意：CronCreate 在会话结束时自动清除，生产环境建议使用系统cron。

---

## 8. 常见问题

### Q: API返回"欠费"错误
A: 检查是否使用了付费模型（qwen3.7-max）。免费模型为 qwen3.7-plus / qwen3.6-flash。

### Q: PDF中文显示乱码
A: 确保已安装 Noto CJK 字体（`sudo apt install fonts-noto-cjk`）。

### Q: 翻译质量不够理想
A: 尝试：
- 将 `temperature` 调到 0.6-0.8 增加创造力
- 升级模型（plus → max）
- 丰富共享知识库内容

### Q: Gutenberg 下载缓慢
A: 镜像站下载或预先下载后放入 `data/` 目录。

---

## 9. 目录说明

```bash
# 数据存储
data/                    # 原文文本（gitignored）
evolution_logs/          # 进化日志（gitignored）
output/                  # 译文输出（gitignored）

# 资产存储（运行时生成）
assets_store/            # Book Analyzer的分析结果
assets_store_full/       # 全量术语/风格数据
test_assets_store/       # 测试用资产
```

首次运行时会自动创建上述目录。

---

## 10. 安全注意事项

1. **API Key 保护**：`.env` 文件已在 `.gitignore` 中，不会提交到仓库
2. **版权注意**：只翻译公版作品（美国：≤1929年出版；中国：作者逝世≥50年）
3. **输出标注**：AI生成的译文建议在使用前进行人工审校
