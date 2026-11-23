# 维护手册

本手册给仓库维护者（以及执行维护任务的智能体）使用，覆盖日常巡检、失败恢复、手工发布一天内容和常见问题。选文规则见 [SELECTION.md](SELECTION.md)，每日精读任务见 [DAILY_AGENT.md](DAILY_AGENT.md)。

## 本地环境

- Python 3.10 及以上；GitHub Actions 中使用 Python 3.12。
- 唯一第三方依赖是 YAML 解析：`pip install pyyaml`。
- 在仓库根目录执行所有命令，路径以仓库根为基准。
- 网络需要能访问 `export.arxiv.org`、`hf-mirror.com`（HuggingFace 镜像，失败时回退 `huggingface.co`）与 `github.com`。
- 自动摘要使用 GitHub Models，只在自动摘要链路中调用：Actions 中由内置 `GITHUB_TOKEN` 提供凭证，本地日常维护不需要模型凭证。只有手动运行 `scripts/summarize.py` 补某天摘要时，才需要 `GITHUB_TOKEN` / `GH_TOKEN` 或已登录的 `gh`。
- Windows 下用 PowerShell 或 CMD 均可；仓库通过 `.gitattributes` 统一为 LF 行尾。

## 日常巡检（约 2 分钟）

1. **看 Actions**：打开仓库 Actions 页面，确认 `daily-paper-radar` 最近一次运行成功；失败时看日志定位是抓取、摘要、finalize 还是提交阶段。`repo-check` 负责在每次 push 后跑仓库完整性检查。
2. **看状态**：`python scripts/paper_radar.py status --days 7`，确认最近几天状态是 `final`（或周末的 `empty`），没有卡在 `draft` 的草稿。
3. **抽查内容**：打开最新 `digests/YYYY-MM-DD.md`，至少抽查 2 篇：一句话结论是否具体、数字是否有出处、arXiv 链接是否可用、精读页与 digest 是否一致。
4. **跑校验**：`python scripts/paper_radar.py check --strict`，确认格式、链接、占位符与索引一致。
5. **确认提交**：`git log --oneline -5` 能看到当天提交，`git status` 干净（`library/`、`data/cache/` 已在 `.gitignore` 中）。

周末或假期可跳过巡检；下一次运行会通过抓取窗口自动回看最近几天。

## 失败恢复

### Actions 失败

`daily-paper-radar` 的步骤依次是：prepare → summarize → finalize → check → commit & push。按失败位置处理：

- **抓取阶段**（arXiv 429 或超时）：等几分钟后从 Actions 页面手动重跑工作流（`workflow_dispatch` 支持填写目标日期、篇数与「跳过摘要」）。不要连续手动触发。
- **摘要阶段**（GitHub Models 失败）：`summarize.py` 对单篇失败会退化为确定性速览，不会让流水线产出空文件；若整体失败，可在本地已有草稿的基础上按 [DAILY_AGENT.md](DAILY_AGENT.md) 手写精读，或重新跑 `python scripts/summarize.py --date YYYY-MM-DD`。
- **finalize / check 阶段**：多数是 note 占位符未填、小节过短、索引不一致。按报错修 `papers/` 与 `digests/` 后重跑 `finalize`，再跑 `check --strict`。
- **提交阶段**：工作流内部会对 push 失败做 rebase 重试；仍失败时本地 `git pull --ff-only` 后重跑 `finalize`（或手工提交）再推送。

Actions 也支持「只抓取、不摘要」：`workflow_dispatch` 勾选 `skip_summarize`，会生成一份待精读草稿，由本地智能体随后补全。

### 同一天重跑

- `prepare` 是幂等的：当天状态为 `final`/`empty` 时直接返回；状态为 `draft` 时只补齐缺失文件、不覆盖已写内容。确需重新生成草稿时加 `--force`，仅 `draft` 状态下允许（`final`/`empty` 会被拒绝），且会覆盖已写的总结并清理旧草稿的 note，执行前先提交或备份。
- `finalize` 也是幂等的：已 `final`/`empty` 的日期直接返回。它在发布前总会校验占位符和小节完整度，校验不过不会改状态。
- `--summary-mode` 只记录本期摘要的来源（`auto` / `llm` / `fallback` / `manual`），用于状态行和索引展示，不改变摘要内容。Actions 按 `summarize.py` 的实际结果记为 `llm` 或 `fallback`；本地精读建议记为 `manual`。
- `--git` 让 `finalize` 完成后自动 `commit & push`（提交信息为「每日论文雷达：日期」）；配合 `--dry-run` 可只演练不提交。
- **推送兜底**：`--git` 推送失败时会自动重试 5 次（间隔 10~40 秒），仍失败则调用 `python scripts/publish_api.py --message "提交信息"` 通过 GitHub REST API 发布（需要 `gh` 已登录或设置 `GITHUB_TOKEN`；单次限制 300 个文件 / 50MB；先用 `--dry-run` 可查看将发布哪些文件）。手动发布或 `git push` 长期不通时也可直接用它。

### 回滚某一天

- **只改文字**：直接编辑对应的 `digests/YYYY-MM-DD.md` 与 `papers/YYYY-MM/*.md` 并提交。注意 `finalize` 不会重写已发布的内容，改完不需要也不应该重跑 finalize。
- **整期作废**：找到该期提交后执行 `git revert <commit>`，会同时回退 digest、papers、`data/` 状态与 README 的最新一期区块；下一次 finalize 会自动重建索引。
- 不要对共享的 `main` 分支执行 `git reset --hard`，也不要用 `git push --force`。
- `data/*.json`、`data/papers.jsonl` 由脚本维护；除排障外不要手工编辑 `seen.json`，否则可能造成重复入选或漏选。

## 手工发布一天的流程

1. 同步远端：`git pull --ff-only`
2. 看状态：`python scripts/paper_radar.py status --days 3`
3. 当天没有草稿时生成：`python scripts/paper_radar.py prepare --date YYYY-MM-DD`（已有草稿会直接返回，不会覆盖）
4. 读原文：`python scripts/paper_radar.py fulltext <arxiv_id> --chars 12000`，按 [DAILY_AGENT.md](DAILY_AGENT.md) 的规范编辑 `papers/YYYY-MM/` 与 `digests/YYYY-MM-DD.md`
5. 汇总发布：`python scripts/paper_radar.py finalize --date YYYY-MM-DD --strict --summary-mode manual`
6. 校验：`python scripts/paper_radar.py check --strict`
7. 提交并推送：`git add -A` 后确认 `git status` 只包含当天相关内容（finalize 会更新 `data/`、`README.md`、`digests/index.md`、`venues/stats.md`，这些也要提交），再 `git commit`（信息写明日期，如 `digest: 2026-11-23`）并 `git push`；也可以在第 5 步加 `--git` 让脚本自动提交推送。
8. 若当天是周日：`python scripts/paper_radar.py weekly` 生成周报骨架，填写 `digests/weekly/YYYY-Www.md` 中的 `{{WEEKLY_OVERVIEW}}` 与 `{{WEEKLY_QUESTIONS}}`，不得留下占位符，然后重新 `check --strict` 并提交。

`scripts/summarize.py` 是可选的单日自动摘要脚本（GitHub Models），只在需要单独补某天摘要时使用，不是日常流程的必需步骤。

## 常见问题

### arXiv 返回 429 或请求超时

arXiv API 有速率限制。`config/sources.yml` 中已设置请求间隔（默认 3.2 秒）、超时（90 秒）与重试次数（4 次），HTTP 层还有指数退避。遇到 429 时等待 5-10 分钟再重跑，不要密集重试；持续失败可临时调大 `request_delay_seconds`，并在 Issue 里记一笔。

### 周末或当天没有新论文

抓取窗口默认回看 4 天（`config/profile.yml` 的 `lookback_days`），「有观点」专项检索回看 21 天（`opinion_lookback_days`）。如果窗口内没有可用候选，`prepare` 会写一份 `status: empty` 的空 digest 保留记录，这是预期行为，不要为了凑数降低标准。`status` 中看到 `empty` 属于正常状态。

### 摘要质量不理想

自动摘要只是第一版，本地智能体会补全精读。发现明显错误时：草稿阶段直接改 `papers/` 与该期 digest 再 `finalize`；已发布后直接改 Markdown 并提交（finalize 不会重写已发布内容）。如果错误来自模型对原文的误读，请在 digest 中保留更正说明，并把问题记录到 Issue。

### 如何补历史

CLI 没有「批量回填」命令，但可以按日期补：

1. `python scripts/paper_radar.py prepare --date YYYY-MM-DD`（指定目标日期抓取该日期窗口的候选；没有网络时可用 `--offline` 读 `data/cache/candidates-<date>.json`）
2. 按正常流程写精读或跑 `summarize.py --date YYYY-MM-DD`
3. `python scripts/paper_radar.py finalize --date YYYY-MM-DD --strict --summary-mode manual`

注意：补历史同样会写入 `seen.json` 与 `papers.jsonl`；不要对同一日期反复 `prepare --force`。补较早的日期不会顶掉 README 里的最新一期，但会出现在归档页。

### 如何加新数据源

当前抓取逻辑固定读取 `config/sources.yml` 的 `arxiv` 与 `hf_daily` 两段，`scripts/paper_radar.py` 的 `fetch_candidates()` 也只实现了这两类来源。新增来源需要同时改配置与代码，且必须遵守「任一源失败不影响整体流程」的约定；只加配置不会自动生效。

### 周报里还有占位符

`python scripts/paper_radar.py weekly` 只生成骨架，`{{WEEKLY_OVERVIEW}}` 与 `{{WEEKLY_QUESTIONS}}` 需要智能体填写；`check --strict` 会把残留占位符判为失败。`weekly --force` 会重建骨架并覆盖已填内容，慎用。

## 相关文档

- 仓库说明：[../README.md](../README.md)
- 选文规则：[SELECTION.md](SELECTION.md)
- 每日智能体：[DAILY_AGENT.md](DAILY_AGENT.md)
- 顶会库：[../venues/README.md](../venues/README.md)、[../venues/top-venues.md](../venues/top-venues.md)
