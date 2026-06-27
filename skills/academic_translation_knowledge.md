# AI/ML 学术论文翻译技能知识库 v1.0

> 生成: 2026-06-27 13:01:17
> 来源: IMA「机器学习与神经网络」+ 术语风格师萃取
> 架构: 通用学术框架 + AI/ML 领域层

## 设计理念

学术翻译采用「通用框架 + 领域层」双层架构：
- **通用层**（不变）: 引用保护、公式保护、论证完整性、学术规范
- **领域层**（可替换）: AI/ML 术语表、论文结构映射、领域写作规范

→ 切换到其他领域（生物/物理/法律）只需替换领域层，通用层保持不变。

## 通用学术框架

### 引用保护
- 参考文献编号 [N] / (Author, Year) 不得修改、遗漏或错位
- 公式编号 (1)(2) 必须与原文一致
- 图表编号 Figure X / Table Y 必须保留并正确对应
- 引用的论文标题如原文为英文，可保留英文或加中文翻译，但不能只翻不标原文

### 公式与数据保护
- 数学公式、LaTeX 表达式不得翻译或修改
- 统计数据（百分比、p值、置信区间）字符级精确对应
- 算法伪代码中的变量名、函数名保留英文

## AI/ML 领域术语

### 神经网络与架构

| 英文 | 中文译法 | 注意 |
|------|---------|------|
| Transformer | Transformer | 首字母大写，主流论文保留英文不翻译 |
| CNN | 卷积神经网络 | 首次出现建议标注缩写 |
| RNN | 循环神经网络 |  |
| LSTM | 长短期记忆网络 |  |
| GAN | 生成对抗网络 |  |
| ViT | 视觉Transformer | 架构名保留英文，中文语境常直译+保留原名 |
| ResNet | 残差网络 | 模型名称统一保留英文缩写/原名 |
| AE | 自编码器 |  |
| GNN | 图神经网络 |  |
| Diffusion Model | 扩散模型 | 近年通用译法 |
### 训练与优化

| 英文 | 中文译法 | 注意 |
|------|---------|------|
| Gradient Descent | 梯度下降 |  |
| Backpropagation | 反向传播 |  |
| Fine-Tuning | 微调 | 深度学习语境下固定译法；若指代码/配置级调整应译为‘细调’或‘参数适配’ |
| Epoch | 轮次 | 指完整数据集遍历一次 |
| Batch Size | 批次大小 | 简称‘批大小’ |
| Learning Rate | 学习率 |  |
| Loss Function | 损失函数 | 也称代价函数(Cost Function) |
| Regularization | 正则化 | 防止过拟合技术统称 |
### 评估与指标

| 英文 | 中文译法 | 注意 |
|------|---------|------|
| Accuracy | 准确率 | 注意与Precision区分 |
| Precision | 精确率 | 也称查准率 |
| Recall | 召回率 | 也称查全率 |
| F1 Score | F1分数 | 精确率与召回率的调和平均 |
| BLEU | BLEU | 机器翻译评估指标，通常保留大写英文 |
| Perplexity | 困惑度 | 语言模型评估指标 |
### NLP术语

| 英文 | 中文译法 | 注意 |
|------|---------|------|
| Tokenization | 分词 | 中文语境常称‘切词’或‘标记化’ |
| Embedding | 嵌入 | 词向量/特征表示 |
| Language Modeling | 语言建模 |  |
| Machine Translation | 机器翻译 | MT |
| Named Entity Recognition | 命名实体识别 | NER |
| Attention Mask | 注意力掩码 |  |
### CV术语

| 英文 | 中文译法 | 注意 |
|------|---------|------|
| Object Detection | 目标检测 |  |
| Semantic Segmentation | 语义分割 |  |
| Instance Segmentation | 实例分割 |  |
| Data Augmentation | 数据增强 |  |
| Bounding Box | 边界框 | BBox |

## 论文结构映射

| 英文 | 中文 |
|------|------|
| Abstract | 摘要 |
| Introduction | 引言 |
| Related Work | 相关工作 |
| Background | 背景 |
| Preliminaries | 预备知识 |
| Methodology / Method / Approach | 方法 |
| Experiments / Experimental Setup | 实验 |
| Results / Results and Analysis | 结果与分析 |
| Ablation Study | 消融实验 |
| Discussion | 讨论 |
| Conclusion / Concluding Remarks | 结论 |
| Acknowledgments | 致谢 |
| References | 参考文献 |
| Appendix / Supplementary Material | 附录 / 补充材料 |

## 学术写作规范
- 专业术语首次出现时应采用“中文译名（英文原名）”格式，后续统一使用中文或约定缩写。
- 数学公式中的变量必须使用斜体（如 $x$, $	heta$），常量、函数名及单位使用正体（如 $	an$, $	ext{kg}$）。
- 图表编号采用“图1”、“表2”格式，引用时写为“如图1所示”而非“见图1”。
- 数值范围使用连接号“~”或“至”，如“5~10 epochs”，禁止使用连字符“-”表示范围。
- 引用文献采用顺序编码制，上标标注于句末标点前，如“已有研究表明[1]。”
- 算法伪代码中的步骤、条件判断需保持动词原形或动名词开头，中文对应使用“计算”、“更新”、“判断”等规范动词。
- 单位符号与数值之间留半角空格（如 32 GB, 5 ms），复合单位遵循国际单位制规范。
- 避免主观夸大表述，如将“perfect results”译为“完美结果”，应客观译为“理想效果”或“基准水平”。

## 常见翻译错误
- 混淆“Accuracy”与“Precision”：前者为整体正确率，后者为预测为正类中真正为正类的比例，不可互换。
- 误译“Fine-tuning”：在深度学习语境下固定译为“微调”，若指代码逻辑调整应译为“参数适配”或“工程调优”。
- 滥用“Learning”一词：如“Deep Learning”标准译名为“深度学习”，而非“深度学”或“机器学习”的简单叠加。
- 忽略“State-of-the-Art (SOTA)”语境：应译为“当前最优/最先进水平”，避免直译为“艺术巅峰”或“最新技术”。
- 错误处理“Zero-shot/Few-shot”：分别译为“零样本/少样本学习”，不可按字面译为“零学习/少学习”。
- 混淆“Loss”与“Cost”：在优化理论中Loss指代具体任务的误差函数，Cost多指系统总开销或经济成本，需依上下文区分。
- 误译“Backbone”：在CV/NLP中均译为“主干网络”，指提取核心特征的底层结构，非“背部”或“支柱”。
