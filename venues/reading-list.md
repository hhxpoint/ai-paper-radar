# 值得优先补课的经典论文

这是一份「先读这些」的清单，覆盖 Transformer 以来的关键节点，语音与多模态方向有所加权。每条给出 arXiv 链接、一句话贡献、以及今天仍值得读的理由。清单不追求完整，也不按引用数排序；选取标准是「改变过后续研究，且今天读原文仍有收获」。

## 架构基础

### 1. Attention Is All You Need（2017）
- 链接：<https://arxiv.org/abs/1706.03762>
- 贡献：提出 Transformer，完全用自注意力替代循环与卷积，在 WMT 翻译任务上用 8 块 GPU 训练 3.5 天达到 41.8 BLEU。
- 为什么今天读：现代大模型、语音与多模态模型的共同底座；读原文能看清位置编码、多头注意力与训练成本的原始权衡，而不是只记住「attention 很重要」。

### 2. Deep Residual Learning for Image Recognition（ResNet，2015）
- 链接：<https://arxiv.org/abs/1512.03385>
- 贡献：用残差连接训练极深网络（最深 152 层），集成模型在 ImageNet 上取得 3.57% top-5 错误率。
- 为什么今天读：残差几乎是所有深度网络的默认组件；原文对「退化问题」的分析与 bottleneck 设计，比后来教科书式的结论更细致。

### 3. An Image is Worth 16x16 Words（ViT，2020）
- 链接：<https://arxiv.org/abs/2010.11929>
- 贡献：把图像切成 patch 后直接用纯 Transformer 建模，在大规模预训练下迁移到多个视觉基准，且训练算力需求更低。
- 为什么今天读：理解视觉与语言架构统一的起点，也是「数据规模可以替代归纳偏置」这一观点最干净的实验样本。

### 4. Mamba: Linear-Time Sequence Modeling with Selective State Spaces（2023）
- 链接：<https://arxiv.org/abs/2312.00752>
- 贡献：让状态空间模型的参数随输入变化（选择性 SSM），在保持线性时间复杂度的同时追平同规模 Transformer；推理吞吐约为 Transformer 的 5 倍。
- 为什么今天读：长序列与线性复杂度路线的主要代表；读原文能理解 SSM 与注意力在「内容寻址」上的取舍，而不只是看 benchmark 数字。

## 大模型训练与对齐

### 5. Language Models are Few-Shot Learners（GPT-3，2020）
- 链接：<https://arxiv.org/abs/2005.14165>
- 贡献：训练 175B 参数自回归语言模型，展示不经梯度更新的 few-shot 上下文学习能力。
- 为什么今天读：上下文学习范式的起点；原文同时记录了失败任务与「训练数据污染」等方法论问题，比后续转述更诚实。

### 6. Scaling Laws for Neural Language Models（2020）
- 链接：<https://arxiv.org/abs/2001.08361>
- 贡献：给出语言模型损失随模型规模、数据量、算力变化的幂律关系，用于指导训练预算分配。
- 为什么今天读：今天讨论「该训多大模型、配多少数据」仍在使用这套语言；与 Chinchilla 对照读，能看到「算力最优」定义的变化。

### 7. Training Compute-Optimal Large Language Models（Chinchilla，2022）
- 链接：<https://arxiv.org/abs/2203.15556>
- 贡献：用 400 多个模型训练实验说明，计算最优时模型规模与训练 token 数应同倍增长；70B 的 Chinchilla 在 MMLU 上达到 67.5%，超过更大的 Gopher。
- 为什么今天读：纠正「参数越大越好」的直觉；是理解数据配比、训练预算与「过训练」讨论的关键参考。

### 8. Training Language Models to Follow Instructions with Human Feedback（InstructGPT，2022）
- 链接：<https://arxiv.org/abs/2203.02155>
- 贡献：用监督微调加人类反馈强化学习（RLHF）对齐语言模型；1.3B 的 InstructGPT 输出比 175B GPT-3 更受人类偏好。
- 为什么今天读：对齐与指令微调的标准流程来源；读原文能理解偏好数据、奖励模型与 KL 约束各自解决什么问题。

### 9. LoRA: Low-Rank Adaptation of Large Language Models（2021）
- 链接：<https://arxiv.org/abs/2106.09685>
- 贡献：冻结预训练权重、注入可训练低秩矩阵，把可训练参数减少约 10000 倍、GPU 显存需求降低约 3 倍，且不增加推理延迟。
- 为什么今天读：参数高效微调的事实标准；读原文能理解秩、目标模块选择与 adapter 方案的差异。

### 10. Direct Preference Optimization（DPO，2023）
- 链接：<https://arxiv.org/abs/2305.18290>
- 贡献：把 RLHF 的奖励建模重参数化为一个简单的分类损失，无需显式奖励模型与在线采样。
- 为什么今天读：偏好优化最常用的简洁替代方案；理解其推导假设，再看后续关于 DPO 局限与改进的讨论，会更清楚「简单」的代价。

### 11. Chain-of-Thought Prompting Elicits Reasoning in Large Language Models（2022）
- 链接：<https://arxiv.org/abs/2201.11903>
- 贡献：用少样本思维链示例激发大模型的多步推理；8 个 exemplar 使 540B 模型在 GSM8K 上超过带验证器的微调 GPT-3。
- 为什么今天读：推理模型与 test-time compute 的源头之一，也是「能力是否涌现」争论中常被引用的证据；读原文能看清实验边界。

## 多模态与生成

### 12. Learning Transferable Visual Models From Natural Language Supervision（CLIP，2021）
- 链接：<https://arxiv.org/abs/2103.00020>
- 贡献：在 4 亿图文对上做对比预训练，用自然语言监督实现零样本迁移，ImageNet 零样本精度与原始 ResNet-50 相当。
- 为什么今天读：多模态对齐与零样本分类的基础工作；理解对比目标、提示词工程与数据规模各自贡献了什么。

### 13. Denoising Diffusion Probabilistic Models（DDPM，2020）
- 链接：<https://arxiv.org/abs/2006.11239>
- 贡献：给出高质量图像生成的扩散概率模型，把扩散与去噪得分匹配联系起来，CIFAR-10 上取得 FID 3.17。
- 为什么今天读：扩散模型及后续图像、音频、视频生成的理论起点；读原文能理解变分界、噪声日程与采样过程，而不是只调用 API。

## 语音与音频

### 14. wav2vec 2.0（2020）
- 链接：<https://arxiv.org/abs/2006.11477>
- 贡献：在隐空间掩码并做对比学习，语音自监督预训练后微调；LibriSpeech 全量标注下 WER 1.8/3.3，仅用 10 分钟标注加 5.3 万小时无标注数据仍达 4.8/8.2。
- 为什么今天读：语音自监督的代表作；量化、掩码与对比任务的设计对低资源语音、病理语音等场景仍有直接启发。

### 15. Robust Speech Recognition via Large-Scale Weak Supervision（Whisper，2022）
- 链接：<https://arxiv.org/abs/2212.04356>
- 贡献：用 68 万小时多语言、多任务弱监督训练语音识别模型，零样本迁移到多个基准时接近人类的准确率与鲁棒性。
- 为什么今天读：语音识别大规模弱监督的里程碑；理解数据规模、多任务格式与鲁棒性评估方式，对今天的语音基础模型仍然适用。

## 未收录说明

- **AlphaFold**：正式论文发表在 Nature（2021），没有 arXiv 预印本，因此不在本表。官方页面：<https://www.nature.com/articles/s41586-021-03819-2>。它仍是结构生物学与「AI for Science」方向最值得补的一课。
- 清单会随阅读继续增补；增补规则见 [README.md](README.md)。选文规则与「有观点」的定义见 [../docs/SELECTION.md](../docs/SELECTION.md)。
