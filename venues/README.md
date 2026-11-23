# 顶会论文库

`venues/` 是仓库的第二入口：每日 digest 回答「今天读什么」，这里回答「这个领域有哪些稳定的会议入口」和「长期该补哪些经典」。它不追求收录每一届的全部论文，只维护与仓库主题相关、值得回看的部分。

## 目录内容

| 文件 | 内容 |
| --- | --- |
| [top-venues.md](top-venues.md) | 主流 AI 顶会与期刊的官方入口表：官方主页、论文集入口、大致时间规律、一句话特点 |
| [reading-list.md](reading-list.md) | 值得优先补课的经典论文清单（15 篇），每篇给出 arXiv 链接、一句话贡献与今天仍值得读的理由 |
| `stats.md` | 顶会/主题统计，由 `finalize` 自动生成，请勿手动编辑 |

## 收录范围

- **会议**：NeurIPS、ICML、ICLR、CVPR、ICCV、ECCV、ACL、EMNLP、NAACL、AAAI、IJCAI、Interspeech、ICASSP、COLM。
- **期刊**：TMLR、JMLR、TPAMI。
- **论文**：只收录与仓库关注方向（大模型与推理、语音与音频、多模态与视觉、强化学习与智能体、评测与效率等）相关、且能在官方论文集稳定访问的工作；不追求覆盖每届全部论文，也不主动收录 workshop 与 demo 类论文。
- 论文集以官方渠道为准：NeurIPS Proceedings、PMLR、OpenReview、CVF Open Access、ACL Anthology、ISCA Archive、IJCAI Proceedings、AAAI 官网、IEEE/计算机学会数字图书馆等。

## 每日摘要里的 venue 标签如何工作

1. 抓取 arXiv 元数据时，从每条记录的 `comment` 与 `journal_ref` 字段匹配顶会名称（规则在 `config/profile.yml` 的 `venue_signals`）。
2. 命中后生成形如 `NeurIPS 2025` 的标签（能识别到年份时带年份），显示在 `papers/` 精读页与当日 `digests/` 的论文信息行，方便按会议回看。
3. 顶会录用同时作为评分信号加分（`venue_bonus`，见 [../docs/SELECTION.md](../docs/SELECTION.md)）；没有标签的论文照常参与评分，标签只影响加分与展示。
4. 标签来自文本匹配，存在误判可能（例如 comment 里提到的是 workshop、投稿中或往届版本）。引用时以官方论文集为准；发现错误直接修 `papers/` 页面并提交。

## 如何添加一个顶会

1. 在 [top-venues.md](top-venues.md) 表格中追加一行：名称、领域、官方入口（优先官方主页与论文集汇总页）、大致时间规律、一句话特点。
2. 如果希望它参与每日标签与加分：在 `config/profile.yml` 的 `venue_signals` 中按现有格式追加 `name` 与 `pattern`；只加表格不会影响抓取与评分。
3. 本地验证：运行 `python scripts/paper_radar.py check --strict`；venue 解析是文本匹配，改完 `pattern` 后可以先用一段样例 comment 测试正则是否命中，再提交，不要为此重跑已发布的日期。
4. 链接只放公开稳定的官方地址，不直接链到某一届的临时页面（年份页面会失效）；不确定时放会议官网主页。
5. 提交后下一次 Actions 运行或本地智能体执行时生效。

## 如何增补阅读清单

- 只收真正改变过后续研究的经典工作，不按引用数凑数，也不收「入门教程」。
- 必须有可访问的 arXiv 页面；没有 arXiv 版本的（如 AlphaFold）在清单末尾单独说明并给官方链接。不确定的条目不要放。
- 保持 [reading-list.md](reading-list.md) 的字段格式：链接、一句话贡献、今天为什么仍值得读。

## 相关文档

- 仓库说明：[../README.md](../README.md)
- 选文规则：[../docs/SELECTION.md](../docs/SELECTION.md)
- 每日精读任务：[../docs/DAILY_AGENT.md](../docs/DAILY_AGENT.md)
