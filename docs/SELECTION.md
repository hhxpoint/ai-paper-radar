# 选文规则

本文件说明每天约 5 篇论文如何产生、如何排序、如何避免重复，以及这些规则的能力边界。配置项在 `config/profile.yml` 与 `config/sources.yml`，改完提交即生效。「有观点」的机器判定只是初筛，不代替亲自读论文。

## 目标

每天从 arXiv 的新论文里挑出约 5 篇值得花 30 分钟读的工作。核心偏好是：**有观点、有争议、能改变认知**；其次是顶会录用与信息密度。宁缺毋滥，不追热点。

## 数据源

只有两个数据源，另加一类元数据解析，全部公开、无需登录：

### 1. arXiv API（主源）

- 接口：`https://export.arxiv.org/api/query`。
- 分类：`cs.AI`、`cs.LG`、`cs.CL`、`cs.CV`、`cs.RO`、`cs.MA`、`cs.NE`、`eess.AS`、`cs.SD`，可在 `profile.categories` 调整。
- 日期窗口：默认回看 4 天（`profile.lookback_days`），按提交日期倒序抓取，最多翻 3 页、每页 500 条（约 1500 条上限）。
- 请求礼仪：请求间隔 3.2 秒、超时 90 秒、最多重试 4 次，全部在 `config/sources.yml` 的 `arxiv` 段。
- 作用：主候选池，所有入选论文都从这里产生。

### 2.「有观点」关键词专项检索（arXiv 补充池）

用 `profile.opinion.strong_keywords` 中的短语（如 `rethinking`、`critique`、`negative result`、`we argue`）在最近 21 天（`profile.opinion_lookback_days`）内做补充检索：多词短语按摘要匹配，单词按标题匹配，最多取 100 条。它只补充主窗口可能错过的观点型论文，不绕过后续评分。

### 3. HuggingFace Daily Papers（热度辅助）

- 通过 `hf-mirror.com` 镜像获取（`https://hf-mirror.com/api/daily_papers?limit=100`，失败时回退 `huggingface.co`）。
- 只取列表中的 arXiv ID、upvotes、GitHub 仓库与 HF 讨论页，按 arXiv ID 合并进主候选池。
- 热度只作为低权重辅助信号；获取失败不影响主流程。

### 4. 顶会信息（元数据解析）

顶会信息不是独立数据源，而是从每条 arXiv 记录的 `comment` 与 `journal_ref` 字段解析 venue 名称（如 "Accepted at NeurIPS 2025"）。当前识别 NeurIPS、ICML、ICLR、CVPR、ICCV、ECCV、ACL、EMNLP、NAACL、AAAI、IJCAI、Interspeech、ICASSP、COLM、TMLR、JMLR、TPAMI，规则在 `profile.venue_signals`。解析是文本匹配，可能误判，详见「局限」。

## 候选池与筛选流程

1. **抓取与合并**：按分类与日期窗口拉取 arXiv 元数据，合并 HF Daily 的论文，并按 arXiv ID 去重。
2. **去重**：先剔除 `data/seen.json` 中记录过的 arXiv ID 或近似标题。
3. **硬过滤**：
   - 命中 `profile.negative_keywords` 的行政类内容直接剔除（call for papers、workshop proposal、editorial、erratum 等）；
   - 摘要少于 200 字符（`profile.min_abstract_chars`）的不参与评分；
   - 与 AI 主题无关的条目剔除（按分类与主题关键词判断；HF Daily 来源默认视为相关）。
4. **评分**：按下一节的信号加权求和。
5. **排序与多样性约束**：按分数从高到低，受主题限流与保底约束选出约 5 篇；入选时与已选论文标题相似度超过 0.92 的视为重复，只保留分数更高的一篇。
6. **备选**：未入选但分数靠前的论文进入 digest 的「备选」（最多 8 条，`profile.max_digest_backups`），只给标题、主题、分数与链接。

## 评分信号

> 下列默认权重来自 `config/profile.yml` 的 `scoring`、`opinion`、`topics`，可以直接调整；这里说明的是信号含义与方向。

| 信号 | 含义 | 默认权重/上限 |
| --- | --- | --- |
| 顶会录用 | comment/journal_ref 命中 `venue_signals`，命中即加 | `venue_bonus: 4.0`（不区分会议等级） |
| 观点强度 | 标题、摘要、comment 命中 strong/medium/weak 关键词或正则 | strong 3.0（上限 6.0）、medium 1.8（上限 3.6）、weak 0.8（上限 2.4）；标题命中再乘 1.3 |
| 兴趣匹配 | 命中 `topics` 关键词，标题命中计 2、摘要命中计 1 | `topic_scale: 0.25`，上限 `topic_cap: 2.2` |
| 社区热度 | HuggingFace Daily 的 upvotes，取 `log1p` | `hf_upvote_log_scale: 0.9`，上限 `hf_upvote_cap: 2.5` |
| 新鲜度 | 距目标日期越近分越高 | 0-2 天 1.0、3-7 天 0.5、8-14 天 0.2 |
| 摘要质量 | 摘要长度在 700-2400 字符之间 | `abstract_bonus: 0.3` |
| 代码链接 | 摘要/comment 中有 GitHub 链接，或 HF 记录带代码仓库 | `code_bonus: 0.4` |
| 反信号 | 命中 `negative_keywords` | 评分前直接剔除；`negative_penalty: 8.0` 为保留兜底项 |

说明：

- 顶会录用只是加分项，不要求必须有；未挂顶会的新论文同样有机会入选。
- 观点关键词分级且有上限，避免靠堆关键词刷分：只有关键词、正文没有对应论证的论文仍然吃亏。
- 热度权重很低，热门论文最多比冷门论文多约 2.5 分；社区热度不能单独决定入选。

## 多样性约束

- **每日篇数**：默认 5 篇（`profile.daily_count`），候选不足时少于 5 篇，不凑数。
- **主题限流**：同一主题每天最多 2 篇（`max_per_topic`），避免整期都是 LLM 推理或整期都是视觉。
- **方向保底**：候选池中存在语音与音频方向论文时，至少入选 1 篇（`required_topics: 语音与音频: 1`）。这是本仓库面向语音研究的偏好；保底名额不够时会替换掉分数最低的非保底主题论文。
- **基本覆盖面**：主题权重决定论文归属主题（大模型与推理 1.35、语音与音频 1.6、强化学习与智能体 1.3、多模态与视觉 1.2、理论/评测/数据 1.1、效率/系统/安全 1.0），让不同方向都能进入候选。
- **标题去重**：入选阶段，与已选论文标题相似度超过 0.92 的论文只保留分数更高的那篇。

## 去重

- `data/seen.json` 持久记录已处理的 arXiv ID 与规范化标题；`prepare` 抓取后先按这两者剔除已见论文。
- 去重跨越日期窗口：周末回看、观点专项检索、补历史时命中的旧论文同样会被拦下；补历史时尤其注意这一点。
- `seen.json` 只在 `finalize` 成功时更新；草稿阶段不会占用去重状态。
- `seen.json`、`data/papers.jsonl` 由脚本维护。需要回滚某天时用 `git revert` 连带回退，不要单独手工编辑（见 [HOWTO.md](HOWTO.md)）。

## 「有观点」的操作性定义

**有观点**不等于语气强烈，而是同时满足两点：

1. **有明确立场**：论文对某个方法、评测、假设或社区共识提出可复述的判断（支持或反对）。
2. **有可检验的证据**：该判断由实验、分析或形式化论证支撑，读者能核对。

按信号强弱分三档：

- **强**：position paper、明确的 `we argue`/`we challenge`、`rethinking`/`critique`、negative result、直接给出「某个流行方法不成立」的证据。
- **中**：对既有范式的追问（`why does ...?`）、经验性 failure 分析、对某个基准或指标的再审视。
- **弱**：survey、benchmark、empirical study、insights 类工作；不是入选主力，但在其他信号强时可以进来。

**正例**（更可能入选）：

- 标题或摘要直接挑战一个流行结论，并用多组实验说明边界条件；
- 系统性地复现并指出某类方法在真实场景失效，给出失败原因分析；
- 提出新的评测或数据视角，揭示现有基准测不到的能力；
- 对「更大模型/更多数据一定更好」这类共识给出反例或修正。

**负例**（即使关键词命中也不优先）：

- 在既有模型上叠加模块，只报告基准分数提升，没有机制解释；
- 纯工程报告或数据集描述，没有观点与对照；
- 用「SOTA」措辞包装的增量工作；
- 摘要里出现 `we argue` 但正文没有对应论证（关键词与内容不一致）；
- 与仓库兴趣无关的热门方向，即使热度很高。

## 局限

- **启发式评分不等于质量判断**：关键词与正则只能近似「有观点」，会漏掉表述克制但实质重要的工作，也会高估措辞强硬的普通论文。
- **兴趣偏差**：主题权重与语音保底规则体现了作者的研究偏好，不是通用推荐。
- **热度偏差**：HuggingFace upvotes 反映短期社区关注，与长期价值不总是相关；已通过低上限降低影响。
- **元数据限制**：顶会信息只来自 arXiv 的 comment/journal_ref，不保证准确；只投顶会、未上 arXiv 的论文不会被收录；HF Daily 的列表也可能不完整。
- **摘要与评论可能有误**：自动摘要与智能体精读都可能误读原文，重要结论请回到论文核对（见 [README](../README.md) 的声明）。
- 规则会随阅读反馈调整；`config/profile.yml` 是唯一事实来源，本文件描述的是设计意图。

## 相关文档

- 维护与排障：[HOWTO.md](HOWTO.md)
- 每日精读任务：[DAILY_AGENT.md](DAILY_AGENT.md)
- 顶会库与 venue 标签：[../venues/README.md](../venues/README.md)
- 仓库说明：[../README.md](../README.md)
