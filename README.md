# AI 论文雷达

> 每天 5 篇值得看的有观点论文：读结论、看争议、记局限。

这个仓库记录我每天实际在读、并且认为值得反复回看的 AI 论文。筛选、摘要与精读由自动化流水线和本地智能体协作完成，目标是沉淀一份可检索、可长期阅读的论文库，而不是又一份论文列表。

- 仓库：<https://github.com/hhxpoint/ai-paper-radar>
- 作者：hhxpoint
- 许可：MIT

## 最新一期

<!-- LATEST:START -->

### 最新一期：[2026-11-25 · 论文雷达](digests/2026-11-25.md)

> 5 篇 · 大模型与推理 2 · 理论、评测与数据 1 · 多模态与视觉 1 · 语音与音频 1 · 每日精读（智能体）

| 日期 | 精选 | 主题分布 | 模式 |
| --- | --- | --- | --- |
| [2026-11-25](digests/2026-11-25.md) | 5 篇 | 大模型与推理 2 · 理论、评测与数据 1 · 多模态与视觉 1 · 语音与音频 1 | 每日精读（智能体） |
| [2026-11-24](digests/2026-11-24.md) | 5 篇 | 大模型与推理 2 · 多模态与视觉 1 · 效率、系统与安全 1 · 语音与音频 1 | 每日精读（智能体） |
| [2026-11-23](digests/2026-11-23.md) | 5 篇 | 大模型与推理 2 · 强化学习与智能体 1 · 理论、评测与数据 1 · 语音与音频 1 | 每日精读（智能体） |

> 累计 15 篇 · 归档：[digests/index.md](digests/index.md) · 自动更新于 2026-11-25 10:53 (UTC+8)

<!-- LATEST:END -->

## 这个仓库是什么

- **经过筛选的论文清单**：每天从 arXiv 的 AI/ML/CV/NLP/语音等方向新论文中挑出约 5 篇，优先「有观点、有争议、能改变认知」的工作。
- **带评论的精读页**：每篇论文一页，写清楚它主张什么、证据是什么、争议与局限在哪里、和我的研究方向有什么关系。
- **长期笔记库**：可以在每篇论文页的「我的笔记」小节追加自己的想法，让阅读痕迹沉淀下来。
- **按顶会组织的入口**：`venues/` 提供主流顶会与期刊的官方入口，以及一份值得补课的经典论文清单。

## 这个仓库不是什么

- 不是论文搬运站：不转贴摘要、不堆 PDF、不做 arXiv 镜像。
- 不是「最新论文大全」：收录量远小于每日 arXiv 新增量。
- 不是引用排行榜或热度看板：社区热度只是辅助信号，不是选文依据。
- 不是权威综述：所有评论都带着我的偏好与判断，请以论文原文为准。

## 仓库结构

| 路径 | 说明 |
| --- | --- |
| `digests/` | 每日阅读清单 `YYYY-MM-DD.md`；周报在 `digests/weekly/YYYY-Www.md`；`digests/index.md` 为自动生成的归档 |
| `papers/` | 每篇论文的精读页，按月份归档：`YYYY-MM/<arxiv-id>-<slug>.md` |
| `venues/` | 顶会入口与长期阅读清单，见 [venues/README.md](venues/README.md) |
| `docs/` | 维护手册、选文规则与每日任务说明 |
| `config/` | 兴趣画像与数据源配置，改完提交即生效 |
| `data/` | 运行状态与索引：`data/daily/*.json`、`seen.json`、`papers.jsonl`，由脚本维护 |
| `data/cache/` | 抓取缓存，不提交 |
| `library/` | 本地 PDF 下载目录，不提交 |
| `.github/workflows/` | GitHub Actions 每日自动化与仓库检查 |

## 每天产出什么

一期 digest（`digests/YYYY-MM-DD.md`）通常包含 5 篇论文，开头是「今日看点」，每篇给出：

- **一句话结论**：这篇论文到底主张什么。
- **为什么值得读**：它回答的问题、提供的证据，或它对既有认知的挑战。
- **问题与动机**：之前的方法缺什么。
- **方法与关键洞察**：核心设计，以及机制上为什么可能成立。
- **证据与实验**：关键数字、实验设置与对照基线。
- **可质疑之处**：证据薄弱处、适用范围与具体可质疑点。
- **与我的研究关联**：对语音、多模态、大模型与推理等方向的具体可用之处。
- **原文与代码**：arXiv、PDF、HTML、代码仓库链接（有才给）。

当天未入选但值得扫一眼的论文放在 digest 的「备选」部分（最多 8 条）。入选论文在 `papers/` 下各有独立精读页；每周日再生成一份《本周观点雷达》，汇总本周论文清单、趋势与值得追踪的问题。

## 选文标准

概括成三条偏好：

1. **有观点**：明确挑战、修正或否定某个流行结论，或给出系统性的负面结果，而不是只把指标刷高。
2. **有出处**：已发表于主流顶会/期刊，或 arXiv 元数据（comment、journal_ref）中带有录用信息。
3. **高信息量**：新范式、新证据、新评测，或能落地的资源与代码。

不追热点：不按引用数或社交媒体热度排序，HuggingFace Daily Papers 的 upvotes 只作为低权重的辅助信号；没有足够好的论文时宁可少发甚至空发。完整规则（数据源、评分信号、多样性约束、去重与局限）见 [docs/SELECTION.md](docs/SELECTION.md)。

## 自动化怎么运作

仓库按两条链路每天运行：

1. **GitHub Actions（每天北京时间 07:10）**：抓取 arXiv 候选池与 HuggingFace Daily Papers 热度，解析 arXiv comment/journal_ref 中的顶会信息，筛选后调用 GitHub Models 生成中文速读摘要（模型不可用或输出校验失败时自动退化为确定性速览，并在状态行标注），并提交新的 `digests/` 与 `papers/`。
2. **本地精读智能体（每天 09:00）**：拉取当天草稿，读论文原文，逐篇补全精读、核对数字与链接、修正错误后提交；任务说明见 [docs/DAILY_AGENT.md](docs/DAILY_AGENT.md)。

两条链路互相兜底：Actions 失败时，本地可以重新 `prepare`/`finalize` 当天内容；本地智能体没有运行时，Actions 生成的自动摘要仍能保证当天有可读的一期。周末或当天没有足够新论文时，抓取窗口会自动回看最近几天，而不是硬凑数量。周日由本地智能体额外生成一份《本周观点雷达》周报。

## 怎么自己用

- **按日期读**：打开 `digests/` 按日期浏览，归档索引为自动生成的 `digests/index.md`；最新一期在 README 顶部有入口。
- **按论文读**：在 `papers/YYYY-MM/` 里按月份浏览，每篇论文一个页面。
- **按顶会读**：从 [venues/top-venues.md](venues/top-venues.md) 进入各会议官方论文集；经典补课清单在 [venues/reading-list.md](venues/reading-list.md)。
- **写自己的笔记**：在 `papers/YYYY-MM/<arxiv-id>-<slug>.md` 的「我的笔记」小节追加内容。这一节留给读者维护，自动流程不写入；请只在这一节写，避免与自动更新冲突。
- **搜索**：GitHub 仓库内直接用关键词、arXiv ID 或方法名搜索。

## 手动运行

以下命令均在仓库根目录执行：

```bash
python scripts/paper_radar.py prepare  [--date YYYY-MM-DD] [--count N] [--offline] [--force] [--json]
python scripts/paper_radar.py finalize [--date YYYY-MM-DD] [--strict] [--summary-mode auto|llm|fallback|manual] [--git] [--json]
python scripts/paper_radar.py status   [--days N] [--json]
python scripts/paper_radar.py check    [--strict]
python scripts/paper_radar.py fulltext <arxiv_id> [--chars N] [--offset N]
python scripts/paper_radar.py download [--date YYYY-MM-DD] [--out library]
python scripts/paper_radar.py weekly   [--week YYYY-Www] [--force]

# 可选：单独生成某天的自动摘要（GitHub Models）
python scripts/summarize.py [--date YYYY-MM-DD] [--model ...] [--json]
```

常见用法：

```bash
python scripts/paper_radar.py prepare --count 5      # 生成当天草稿
python scripts/paper_radar.py finalize --strict      # 校验并发布当天内容
python scripts/paper_radar.py status --days 7        # 查看最近一周状态
python scripts/paper_radar.py check --strict         # 发布前检查仓库内容
```

维护流程、失败恢复与手工发布一天的操作见 [docs/HOWTO.md](docs/HOWTO.md)。

## 配置怎么改

- `config/profile.yml`：兴趣画像。包括每日篇数、主题权重与关键词、语音方向的保底约束、「有观点」关键词、顶会信号、评分权重与摘要模型。
- `config/sources.yml`：数据源与网络参数。包括 arXiv 分类与翻页、请求间隔与重试、HuggingFace Daily Papers 镜像、网络超时。

两个文件都是普通 YAML，改完直接 Commit 即生效：下一次 Actions 运行或本地智能体执行时使用新配置，不需要改代码。

## 目录约定

- 每日：`digests/YYYY-MM-DD.md`；周报：`digests/weekly/YYYY-Www.md`
- 每篇：`papers/YYYY-MM/<arxiv-id>-<slug>.md`
- 状态：`data/daily/YYYY-MM-DD.json`、`data/seen.json`、`data/papers.jsonl`，均由脚本维护
- 缓存：`data/cache/`（不提交）
- 本地 PDF：`library/`（不提交）

## 声明

- 自动生成的摘要与评论可能有误，重要结论请以论文原文为准。
- 仓库仅收录 arXiv 公开链接，不存储版权 PDF；`library/` 只用于本地下载。
- 选文基于启发式评分与个人兴趣偏好，不代表论文质量的客观判断。
- 发现错误或想讨论，欢迎提 Issue。

## 文档索引

| 文档 | 内容 |
| --- | --- |
| [docs/HOWTO.md](docs/HOWTO.md) | 维护手册：日常巡检、失败恢复、手工发布流程、常见问题 |
| [docs/SELECTION.md](docs/SELECTION.md) | 选文规则：数据源、评分信号、多样性约束、去重与「有观点」定义 |
| [docs/DAILY_AGENT.md](docs/DAILY_AGENT.md) | 每日精读智能体的任务步骤与写作规范 |
| [venues/README.md](venues/README.md) | 顶会论文库的定位、收录范围与添加方式 |
| [venues/top-venues.md](venues/top-venues.md) | 主流 AI 顶会与期刊官方入口表 |
| [venues/reading-list.md](venues/reading-list.md) | 值得优先补课的经典论文清单 |

## License

MIT © 2026 hhxpoint
