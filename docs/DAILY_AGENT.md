# 每日智能体任务说明

本文件定义每天 09:00 在本地仓库执行的精读任务：在 GitHub Actions 已生成内容的基础上，读论文原文、写中文精读、核对数字与链接、修正错误并发布。维护者手工补发时也可按同样步骤执行。

## 角色与边界

- 你是阅读者，不是改代码的人：只编辑 `digests/` 与 `papers/` 下的 Markdown 内容。
- 不修改 `scripts/`、`config/`，也不手工改 `data/` 下的内容（`data/` 由 `finalize` 脚本自动维护，手工编辑 `seen.json`/`papers.jsonl` 会造成重复入选或漏选）；发现脚本或配置问题，在提交说明或 Issue 中记录，不要顺手改。
- 不删除、不改写「我的笔记」小节；不在其中写自动生成的内容。
- 不编造实验数字、数据集、对比结果或引用；不发明论文链接。
- 所有命令在仓库根目录执行。

## 前置条件

- 已安装 Python 3.10+ 与 `pyyaml`。
- 能访问 arXiv（读全文）与 GitHub（推送）。
- 当天自动化抓取已运行或已就绪；若 Actions 没跑起来，本任务第 3 步的 `prepare` 会补抓。
- 不需要模型凭证：精读由你直接读原文完成，不依赖 GitHub Models。

## 每日步骤

### 1. 同步仓库

```bash
git pull --ff-only
```

Actions 通常在 07:10（UTC+8）提交当天内容，先同步再动笔，避免冲突。

### 2. 查看状态

```bash
python scripts/paper_radar.py status --days 3 --json
```

确认：今天是否已有草稿（`draft`）、是否已完成（`final`）、是否为空（`empty`）、最近几天是否有中断。`final` 表示 Actions 的自动摘要已经发布，你的工作是补精读并修正；`draft` 表示还需要你补全后发布。

### 3. 准备草稿（仅在缺草稿时）

```bash
python scripts/paper_radar.py prepare
```

`prepare` 幂等：已有草稿时只补齐缺失文件，不会覆盖已写内容；当天已 final/empty 时直接返回。不要为了「重新跑一遍」而加 `--force`，它会覆盖已写的总结。

### 4. 读原文

对每篇论文：

```bash
python scripts/paper_radar.py fulltext <arxiv_id> --chars 12000
```

至少读：摘要、方法与核心设计、主实验表格、消融、结论与 limitations。需要更多内容时用 `--offset` 继续读；HTML 不可用时会回退输出已记录的摘要。先读摘要和结论，再回方法与实验核对数字。

### 5. 写中文精读

编辑 `papers/YYYY-MM/<arxiv-id>-<slug>.md`，填写以下小节（小节名必须保持一致）：

| 小节 | 最低要求 | 写什么 |
| --- | --- | --- |
| 一句话结论 | 15 字 | 这篇论文真正主张什么，包含方法要点与关键结果，不是复述标题 |
| 为什么值得读（观点） | 40 字 | 针对什么既有认知，给出了什么新证据或反例 |
| 问题与动机 | 30 字 | 之前的方法缺什么，为什么这个问题重要 |
| 方法与关键洞察 | 60 字 | 机制层面为什么可能 work，关键设计是什么 |
| 证据与实验 | 30 字 | 具体数字：数据集、指标、提升幅度、对照基线、消融 |
| 可质疑之处 | 40 字 | 每篇必须写，至少 1 条具体可质疑点：证据薄弱处、适用边界、可能的替代解释 |
| 与我的研究关联 | 20 字 | 对语音、多模态、大模型与推理等方向的具体可用之处；没有关联就直说 |
| 延伸阅读 | 2 字 | 相关论文、代码或可追踪方向；没有可写「暂无」 |

写作要求：

- 每篇观点分析 250-400 字，短而实，不写空泛评价。
- 数字必须能在原文中找到；不确定就写「论文报告 X」并给出处，绝不编造。
- 引用原文链接（arXiv abs 页）；有代码就给核实过的官方仓库链接。
- 不写「未来将支持」「有望」这类承诺；不猜测作者动机；用克制、专业的语气。
- 「摘要原文」小节由 `prepare` 写入，不要改。
- 「我的笔记」小节保持原样，留给读者。

如果当天状态是 `final`（Actions 已发布自动摘要），除编辑精读页外，还要把 `digests/YYYY-MM-DD.md` 中对应论文的「一句话」与「为什么值得读」替换为精读版，保持 digest 与精读页一致。需要把状态行标注为精读版时，把 digest 与各 note 的 `> 状态：` 行改为「每日精读（智能体）」即可（展示文本，不影响校验）。

### 6. 汇总发布

```bash
python scripts/paper_radar.py finalize --strict
```

- 当天仍是 `draft` 时：finalize 校验占位符与小节完整度，写入 `data/papers.jsonl`、`seen.json` 与索引，重建 `digests/index.md`、`venues/stats.md` 并更新 README 最新一期区块，然后标记 `final`。
- 当天已经 `final` 时：finalize 会安全返回 `already-final`，不会覆盖你刚写的内容，直接进入下一步。
- 如上一步只改了文字且当天仍是 `draft`，可以在 finalize 时加 `--summary-mode manual`，把本期标记为「每日精读（智能体）」。

### 7. 校验

```bash
python scripts/paper_radar.py check --strict
```

`--strict` 会把提醒（如仍有草稿、周报内容偏少）当作失败处理。失败时按提示修 `papers/` 或 `digests/`，不要直接跳过。

### 8. 周日：补周报

如果当天是周日：

```bash
python scripts/paper_radar.py weekly
```

生成 `digests/weekly/YYYY-Www.md` 骨架后，填写其中两个占位符：

- `{{WEEKLY_OVERVIEW}}`：本周选题的总体观察（3-5 句），点出反复出现的主题、互相矛盾之处或最值得先读的一篇；对应小节为「本周趋势与关联」。
- `{{WEEKLY_QUESTIONS}}`：本周值得继续追问的开放问题（3-5 条），每条一句话；对应小节为「值得追踪的问题」。

写完后确认文件里不残留任何 `{{...}}` 占位符，再运行一次 `check --strict`。周报同样要提交。不要用 `weekly --force` 重建，除非确认要丢弃已填内容。

### 9. 提交与推送

```bash
git add -A
git status --short      # 确认只包含当天雷达相关文件（digests/papers，必要时含 data、README）
git commit -m "digest: YYYY-MM-DD"
git push
```

提交信息写明日期；周日有周报时在信息中注明（如 `digest+weekly: 2026-11-22`）。推送前用 `git status` 确认只包含当天内容，用 `git log --oneline -3` 确认没有重复提交。如果当天由你执行了 finalize，`data/`、`README.md`、`digests/index.md`、`venues/stats.md` 也会有更新，必须一并提交。

## 失败重试与幂等

- `prepare`、`finalize`、`check`、`fulltext` 都可以重试；重试前先 `status` 确认当前状态。
- 不要重复提交：如果 Actions 已经提交当天内容，先 `git pull --ff-only`，只补缺失部分再提交；push 被拒绝时先拉取再推，不要 `git push --force`。
- 已手写的精读不要用 `prepare --force` 覆盖；从失败的那一步继续。
- finalize 失败时先按错误提示修内容（占位符未填、小节过短、索引不一致），修完重跑即可。
- 摘要/抓取质量问题不要靠反复重跑来「碰运气」：记录问题，必要时在提交说明里说明。
- 网络失败（arXiv 超时、推送失败）等几分钟再试，不要连续重试。

## 完成标准

- [ ] 当天 digest 与 `papers/` 页面齐全，`check --strict` 通过
- [ ] 每篇有 250-400 字中文观点分析，含具体证据或数字
- [ ] 每篇「可质疑之处」至少 1 条具体内容
- [ ] 所有数字与链接可回溯到原文
- [ ] 周日周报的两个占位符已替换，无残留
- [ ] 提交已推送，工作区干净

## 相关文档

- 仓库说明：[../README.md](../README.md)
- 维护与失败恢复：[HOWTO.md](HOWTO.md)
- 选文规则：[SELECTION.md](SELECTION.md)
- 顶会标签与入口：[../venues/README.md](../venues/README.md)
