# GitHub CI 自动修复 Agent 项目讲解

目标：快速讲清楚这个项目做了什么、一次 CI 修复任务怎么跑、多 Agent 如何协作、Hybrid RAG 和修复记忆怎么工作、GitHub 写回闭环怎么落地，以及面试/简历中应该怎么表述。

这份文档按当前代码事实整理，不把还没做的 webhook、复杂线上服务化包装成已经完成的能力；自动 merge 已实现为默认关闭的高权限门禁能力。

## 1. 项目一句话

这是一个面向 GitHub Actions CI 失败场景的多 Agent 自动修复系统。它接收一个失败的 GitHub PR / workflow run / job URL，自动读取 PR 和 CI 上下文，在本地隔离 workspace 复现失败，生成多个候选 patch，逐个运行测试验证，选择风险最低的可行修复，并在有权限的仓库中自动推送修复分支、创建修复 PR。

更适合简历或面试第一句话的表述：

> 设计并实现基于 Hybrid RAG 与多 Agent 协作的 GitHub CI 失败诊断与自动修复系统，支持从真实 GitHub PR / Actions job 读取失败上下文，自动完成失败复现、Failure Fingerprint 生成、历史修复案例检索、候选 patch 生成、测试验证、风险排序和修复 PR 创建；已在个人 GitHub demo 仓库中跑通从失败 PR 到修复 PR 合并后原 PR CI 恢复通过的闭环。

这里的重点不是“让大模型写代码”，而是：

- 把 CI 失败从一堆分散日志变成结构化 failure fingerprint。
- 把历史修复经验做成可检索的 repair memory。
- 用多候选 patch + 真实测试验证替代单次盲改。
- 把修复结果写回 GitHub，形成可审查、可回滚的 PR 闭环；低风险场景下可以显式开启自动 merge 修复 PR。
- 用 eval / dashboard / trace 让系统效果可以复盘和展示。

## 2. 当前已经跑通的真实闭环

真实 demo 仓库：

- 主项目仓库：`qsjv587-666/github-ci-repair-agent`
- 测试仓库：`qsjv587-666/ci-repair-agent-demo`

已验证链路：

```text
源失败 PR #3
-> Agent 读取 GitHub PR / CI 上下文
-> 本地复现 npm test 失败
-> 生成多个候选 patch
-> 逐个应用到临时 workspace 并运行测试
-> 选择通过测试且风险最低的 patch
-> 推送修复分支
-> 自动创建修复 PR #4
-> 合并 #4 到 #3 的失败分支
-> #3 自动重新跑 CI
-> CI completed / success
```

对应 GitHub 记录：

- 源失败 PR：`https://github.com/qsjv587-666/ci-repair-agent-demo/pull/3`
- Agent 自动创建的修复 PR：`https://github.com/qsjv587-666/ci-repair-agent-demo/pull/4`
- 修复后成功 CI run：`https://github.com/qsjv587-666/ci-repair-agent-demo/actions/runs/27607027519`

目前真实 GitHub demo 已覆盖 4 类失败：

| 场景 | 源失败 PR | Agent 修复 PR | 修复后状态 |
|---|---:|---:|---|
| login button disabled state | #1 | #2 | source PR CI success |
| counter increment assertion | #3 | #4 | source PR CI success |
| todo active filter assertion | #5 | #6 | source PR CI success |
| lint unused variable | #7 | #8 | source PR CI success |
| gated auto-merge counter demo | #9 | #11 | repair PR auto-merged, source PR CI success |

这说明当前项目不是只在本地 fixture 上跑通，而是已经完成真实 GitHub 仓库里的多类“失败 PR -> 修复 PR -> 合并修复 -> 原 PR CI 变绿”闭环。

## 3. 代码大结构

核心入口：

- `cifix/cli.py`：命令行入口，支持 `run`、`inspect`、`rag`、`eval`、`dashboard`、`doctor`。
- `cifix/run.py`：主编排入口，串起所有 agent。

核心 Agent：

- `cifix/agents/setup_agent.py`：安装依赖或执行准备命令。
- `cifix/agents/reproducer_agent.py`：复现 CI 失败。
- `cifix/agents/failure_triage_agent.py`：把日志和复现结果转成 Failure Fingerprint。
- `cifix/agents/repair_memory_agent.py`：从静态 playbook 和历史 repair memory 里做 Hybrid RAG 检索。
- `cifix/agents/patch_agent.py`：生成候选 patch，包含模型生成和规则 fallback。
- `cifix/agents/test_agent.py`：逐个应用候选 patch 并运行测试验证。
- `cifix/agents/review_agent.py`：排序候选 patch，选择最小可行修复。
- `cifix/agents/memory_writer_agent.py`：把验证通过的修复写入历史记忆。
- `cifix/agents/github_writer_agent.py`：在开启 `--create-pr` 时推修复分支并创建 PR；在开启 `--auto-merge-repair-pr` 且通过门禁时自动 merge 修复 PR。
- `cifix/agents/report_writer_agent.py`：输出报告、trace、patch、验证结果等 artifacts。

支撑模块：

- `cifix/github.py`：GitHub PR / Actions API 读取和 PR 创建。
- `cifix/rag.py`：BM25、向量检索、ChromaDB / SQLite 向量后端、embedding provider。
- `cifix/model.py`：Poe / Claude Opus 模型调用和候选 patch JSON 解析。
- `cifix/tools/workspace.py`：clone / copy repo、checkout head commit、推断命令、repo map。
- `cifix/tools/command.py`：命令 allowlist 和安全执行。
- `cifix/tools/patch.py`：patch 应用、恢复 baseline、生成 git diff。
- `cifix/eval.py`：本地 fixture 评测和 ablation。
- `cifix/dashboard.py`：从 artifacts 生成静态 dashboard。

可以把整个项目理解成一个离线可运行的 CI 修复工作台，而不是一个常驻后端服务。

## 4. 一次 `run` 到底发生了什么

主流程在 `cifix/run.py` 的 `run_cifix()`：

```text
解析输入和环境变量
-> 读取 GitHub 上下文
-> 准备隔离 workspace
-> 安装依赖或准备环境
-> 运行原始测试复现失败
-> 生成 Failure Fingerprint
-> Hybrid RAG 检索历史修复依据
-> Patch Agent 生成多个候选 patch
-> Test Agent 逐个验证候选 patch
-> Review Agent 选择最佳 patch
-> Memory Writer 写入验证过的 repair memory
-> GitHub Writer 可选创建修复 PR
-> Report Writer 写 artifacts
```

Python 类比：

```python
def run_cifix(flags):
    github_context = load_github_context(flags)
    workspace = prepare_workspace(flags, github_context)
    setup_result = setup_agent(workspace)
    reproduction = reproducer_agent(workspace, command)
    fingerprint = triage_agent(reproduction, github_context)
    repair_hits = memory_agent(fingerprint)
    candidates = patch_agent(fingerprint, repair_hits)
    test_results = test_agent(candidates, command)
    selected = review_agent(test_results)
    memory_write = memory_writer_agent(selected)
    github_write = github_writer_agent(selected)
    report_writer_agent(...)
```

这里的每一步都会写入 `trace.json`，所以不是黑盒执行。面试时可以强调：系统里每个 Agent 的输入输出都可以回放。

## 5. 输入和输出

### 5.1 输入

支持三类真实 GitHub 输入：

```bash
python3 -m cifix.cli run --url https://github.com/owner/repo/pull/123
python3 -m cifix.cli run --url https://github.com/owner/repo/actions/runs/456789
python3 -m cifix.cli run --url https://github.com/owner/repo/actions/runs/456789/job/987654
```

也支持本地 fixture：

```bash
python3 -m cifix.cli run \
  --repo fixtures/react-button-broken \
  --command "npm test" \
  --log fixtures/react-button-broken/ci-fail.log
```

真实 GitHub 写回需要：

- `GITHUB_TOKEN`：用于 GitHub API 读取 PR、Actions、创建 PR。
- SSH key 或可 push 的 git 凭证：用于推送修复分支。
- `--create-pr`：显式开启写回。默认只读，不会推分支或创建 PR。

### 5.2 输出

每次运行会生成一个目录：

```text
artifacts/run_<timestamp>_<id>/
  workspace/                   # 隔离出来的代码工作区
  report.md                    # 总报告
  patch.diff                   # 推荐修复 patch
  patch-candidates/*.diff      # 所有候选 patch
  failure-fingerprint.json     # 结构化失败指纹
  repair-playbook-hits.json    # RAG 召回结果
  model-diagnosis.json         # 模型诊断和候选生成摘要
  verification.json            # 复现和候选 patch 验证结果
  risk-report.md               # 风险说明
  pr-comment.md                # 可复制到 PR 的评论草稿
  github-context.json          # GitHub PR / CI 上下文
  github-write.json            # 写回结果，包含 repair branch / PR URL / auto-merge 结果
  trace.json                   # 所有 agent 的执行轨迹
```

## 6. GitHub 接入怎么做

代码在 `cifix/github.py`。

当输入是 PR URL 时，系统会：

1. 解析 owner、repo、pull number。
2. 调 GitHub Pulls API 读取 PR metadata。
3. 读取 PR changed files。
4. 根据 PR head sha 查找关联的 workflow run。
5. 获取 workflow run 下的 jobs。
6. 选择失败 job。
7. 尝试下载 job log。

返回的 `github_context` 包含：

- `owner` / `repo`
- `pullNumber`
- `pullHtmlUrl`
- `cloneUrl`
- `headRef` / `baseRef`
- `headSha` / `baseSha`
- `changedFiles`
- `runId` / `jobId`
- `runHtmlUrl` / `jobHtmlUrl`
- `rawLog`

这个上下文会被后续 triage、workspace、report、GitHub write-back 复用。

注意：公开仓库可以匿名读取一部分信息，但会遇到 GitHub API rate limit；真实演示最好配置 `GITHUB_TOKEN`。

## 7. Workspace 和安全执行

代码在：

- `cifix/tools/workspace.py`
- `cifix/tools/command.py`
- `cifix/tools/patch.py`

真实 GitHub 模式下，系统不会直接在用户仓库里改代码，而是：

```text
git clone PR head repo
-> checkout PR head commit
-> 在 artifacts/run_xxx/workspace 里执行测试和 patch
```

本地 fixture 模式则会复制一份 fixture 到 workspace，并初始化 git baseline。

命令执行有 allowlist：

- 测试类：`npm test`、`npm run test`、`pnpm test`、`yarn test`、`node --test` 等。
- lint / typecheck 类：`npm run lint`、`npm run typecheck` 等。
- setup 类：`npm ci`、`pnpm install --frozen-lockfile`、`yarn install --frozen-lockfile` 等。

并且拒绝 shell 控制符：

```text
;  &&  ||  |  >  <  `  $(...)  换行
```

这块是项目落地价值的一部分：它不是让模型随便跑 shell，而是把可执行命令限制在测试、lint、typecheck 和依赖安装范围内。

## 8. Failure Fingerprint 是什么

代码在 `cifix/agents/failure_triage_agent.py`。

Failure Fingerprint 是对一次 CI 失败的结构化摘要。它把原始日志、复现输出、GitHub 上下文和仓库信息整理成类似这样的字段：

```json
{
  "platform": "github",
  "project": "qsjv587-666/ci-repair-agent-demo",
  "pullNumber": 3,
  "runId": 27606711130,
  "jobId": 123,
  "failureType": "test_assertion_failure",
  "errorCode": "ERR_ASSERTION",
  "failedFiles": ["test/counter.test.js"],
  "changedFiles": ["src/counter.js"],
  "command": "npm test",
  "language": "javascript",
  "packageManager": "npm",
  "normalizedSignature": "javascript:test_assertion_failure:ERR_ASSERTION:general"
}
```

它解决的问题是：CI 日志很长、格式很散，不适合直接作为系统状态。Fingerprint 提供了一个稳定中间表示，后面 RAG 检索、patch 生成、memory 写入都围绕它做。

面试说法：

> 我没有直接把 CI log 丢给模型，而是先抽取 failure type、error code、失败文件、变更文件、测试命令、语言和包管理器，形成 Failure Fingerprint。它既是 RAG query 的结构化输入，也是历史 repair memory 的索引 key。

## 9. Hybrid Repair RAG 怎么做

代码在：

- `cifix/agents/repair_memory_agent.py`
- `cifix/rag.py`
- `cifix/data/playbooks.json`
- `artifacts/memory/verified-repairs.json`

RAG 的文档来源有两类：

1. 静态 playbook：预置的典型修复策略，例如 assertion failure、lint unused var 等。
2. verified repair memory：系统之前真实验证通过的修复记录。

一次检索时会构造 query：

```text
normalizedSignature
failureType
errorCode
language
packageManager
failedFiles
changedFiles
command
ciLog preview
stdout preview
stderr preview
```

然后做混合检索：

```text
hybridScore = 0.55 * BM25 + 0.35 * vector + 0.10 * confidence
```

向量后端支持两种：

- `sqlite`：零依赖 fallback，把向量存在 SQLite JSON 字段里，做 cosine scan。
- `chroma`：使用 ChromaDB 作为向量数据库。

Embedding provider 支持：

- `hash`：本地 hash embedding，用于无 API key 测试。
- `dashscope`：阿里百炼 / Qwen embedding。
- `zhipu`：智谱 embedding。

面试说法：

> RAG 不是简单文本拼接，而是把静态 repair playbook 和真实验证过的 repair memory 统一成文档，用 BM25 召回精确关键词、用向量召回语义相似案例，再按 BM25、vector score 和历史置信度做 rerank。召回结果会进入 Patch Agent，作为生成候选修复的证据。

## 10. Patch Agent 怎么生成候选修复

代码在：

- `cifix/agents/patch_agent.py`
- `cifix/model.py`

Patch Agent 有两条来源：

1. 模型候选：通过 Poe 的 OpenAI-compatible `/v1/chat/completions` 调 Claude Opus，要求模型输出严格 JSON。
2. 规则候选：针对 demo 中常见失败提供 deterministic fallback，例如 button disabled、counter increment、todo filter、unused var。

模型 prompt 中会包含：

- Failure Fingerprint。
- RAG 召回的 repair playbook hits。
- 复现输出。
- CI log preview。
- 相关源码和测试文件片段。

模型必须输出这种结构：

```json
{
  "summary": "brief diagnosis",
  "candidates": [
    {
      "id": "fix_counter_increment",
      "hypothesis": "increment should return next count",
      "riskTags": ["source-change"],
      "edits": [
        {
          "file": "src/counter.js",
          "from": "return count;",
          "to": "return count + 1;"
        }
      ]
    }
  ]
}
```

为什么不用“模型直接改仓库”？

- 候选 patch 必须结构化，方便逐个验证和风险排序。
- `from` 必须是文件里存在的精确文本，避免模型凭空生成不可应用 diff。
- 模型失败时还有规则 fallback，系统不会完全依赖单次 LLM。

## 11. Patch Tournament 是什么

代码在：

- `cifix/agents/test_agent.py`
- `cifix/agents/review_agent.py`

Patch Tournament 的流程：

```text
候选 patch A
-> 恢复 baseline
-> 应用 A
-> git diff
-> npm test
-> 记录 passed / exitCode / stdout / stderr / riskScore

候选 patch B
-> 恢复 baseline
-> 应用 B
-> git diff
-> npm test
-> 记录结果

...

ReviewAgent 按 rankingScore 排序
-> 选择通过测试且风险最低的 patch
-> 把 selected patch 应用回 workspace
```

风险分数考虑：

- 测试是否通过。
- 是否改测试文件。
- 是否有 `possible-overfit` 风险标签。
- edit 数量和 diff 行数。
- 是否命中历史 playbook。

失败候选会被加大惩罚，测试改动和可能过拟合也会被加分惩罚。

这就是项目比普通 coding agent 更有说服力的地方：不是生成一个答案就结束，而是把多个候选放进真实测试环境里比赛。

面试说法：

> 我把修复生成拆成 candidate generation 和 tournament verification 两步。同一个 CI 失败会生成多个候选 patch，系统逐一应用到临时 workspace 并运行原测试命令，最后根据测试是否通过、改动范围、风险标签和历史案例匹配度选择最小可行修复。

## 12. Repair Memory 怎么写入

代码在 `cifix/agents/memory_writer_agent.py`。

只有当 selected patch 通过验证时，系统才写 memory。写入内容包括：

- fingerprint 摘要。
- 修复策略。
- 改动文件。
- edit 数量。
- 风险标签。
- 验证命令。
- 成功次数和置信度。

示意：

```json
{
  "id": "repair_xxx",
  "fingerprint": {
    "normalizedSignature": "javascript:test_assertion_failure:ERR_ASSERTION:general",
    "failureType": "test_assertion_failure",
    "errorCode": "ERR_ASSERTION",
    "language": "javascript",
    "packageManager": "npm"
  },
  "strategy": "increment should return the next count",
  "patchSummary": {
    "changedFiles": ["src/counter.js"],
    "editCount": 1,
    "riskTags": ["source-change"]
  },
  "verificationCommands": ["npm test"],
  "successCount": 1,
  "confidence": 0.7
}
```

下次类似失败出现时，这条记录会被 RAG 召回，影响候选 patch 生成和风险排序。

## 13. GitHub 写回怎么做

代码在 `cifix/agents/github_writer_agent.py`。

默认不写回 GitHub。只有显式传入：

```bash
--create-pr --token-env GITHUB_TOKEN --ssh-key ~/.ssh/github_ci_repair_agent
```

才会启动写回。

写回前会做三个检查：

1. 必须有 GitHub context。
2. 必须有 selected patch。
3. selected patch 必须测试通过。

写回过程：

```text
检查 workspace 里是否有 diff
-> 创建 repair branch，例如 ci-repair/pr-3-616090549_cc4887bb
-> git add .
-> git commit -m "Fix CI failure for PR #3"
-> git push origin repair branch
-> 调 GitHub Pulls API 创建 PR
```

PR 的 base 不是 `main`，而是源失败 PR 的 head branch。例如：

```text
ci-repair/pr-3-xxx -> fail/counter-increment
```

这样做的原因是：修复 PR 合并后会更新原失败 PR 的分支，然后 GitHub Actions 自动重新跑原 PR 的 CI。整个流程保持可审查、可回滚，不直接偷偷改 main。

如果没有 `GITHUB_TOKEN`，系统仍可以在 SSH 可用时推送 repair branch，并在 `github-write.json` 里给出 compare URL；如果 token 可用，则直接创建 PR。

### 13.1 自动 merge 修复 PR

自动 merge 默认关闭。只有显式传入：

```bash
--auto-merge-repair-pr
```

系统才会尝试把修复 PR 合回源失败分支。

自动 merge 的门禁：

- 修复 PR 必须是本次 agent 创建的。
- 修复 PR 的 base 必须是源失败 PR 的 head branch，不能是 `main`。
- selected patch 本地验证必须通过。
- 如果仓库会给修复 PR 跑 CI，则修复 PR 的 CI 必须 `success`。
- 如果仓库没有给“修复 PR -> 源失败分支”这类 PR 跑 CI，系统会短暂等待 checks 出现，随后记录 fallback，只在本地验证通过、patch 低风险、PR 可合并时继续 merge，并用源 PR 合并后的 CI rerun 作为最终验收。需要严格要求修复 PR checks 时，可以传 `--require-repair-ci`。
- patch 不能改测试文件。
- patch 不能带 `test-change`、`possible-overfit`、`noop` 风险标签。
- diff 改动行数默认不能超过 30 行。

通过门禁后，系统调用 GitHub merge API 合并修复 PR。合并完成后，如果没有传 `--no-wait-source-ci`，系统还会等待源失败 PR 的最新 head commit 重新跑 CI，并把结果写进 `github-write.json` 的 `autoMerge.sourceStatus`。

这项能力的定位不是“无人审查自动合 main”，而是：

> 对低风险、已验证、CI 通过的修复 PR，自动合回源失败分支，让源 PR 重新触发 CI，从而形成更完整的自愈闭环。

## 14. Report、Trace 和 Dashboard

报告输出在 `cifix/agents/report_writer_agent.py`。

一次 run 的关键报告：

- `report.md`：人能读的总报告。
- `trace.json`：机器可读的 agent 执行轨迹。
- `verification.json`：复现和所有候选 patch 的测试结果。
- `patch.diff`：最终推荐 patch。
- `github-write.json`：写回状态、repair branch、PR URL、可选 auto-merge 结果。

Dashboard 在 `cifix/dashboard.py`，它会扫描 artifacts 下的 run、eval、inspect 结果，生成静态 HTML。

当前 dashboard 主要展示：

- run 数量。
- 成功 run 数。
- eval 报告。
- 最近 run 的失败类型、项目、源 PR、修复 PR。
- Patch Tournament 的候选数量、通过数量、最佳候选和风险分数。
- Top RAG evidence 的来源、分数和策略摘要。
- GitHub PR status snapshot，展示源 PR 的最新 CI 状态和 workflow run 链接。
- patch / trace / RAG / GitHub write-back 链接。

这部分的价值是把“跑过一次命令”变成“可展示、可复盘的系统记录”。

## 15. Eval 和 Baseline 怎么做

代码在 `cifix/eval.py`。

本地 fixture 覆盖 4 类典型失败：

- `react-button-broken`
- `counter-increment-broken`
- `todo-filter-broken`
- `lint-unused-var-broken`

基础 eval：

```bash
python3 -m cifix.cli eval --cases fixtures --out artifacts/eval
```

Ablation eval：

```bash
python3 -m cifix.cli eval \
  --cases fixtures \
  --out artifacts/eval-baselines \
  --compare-baselines
```

三种模式：

- `full`：完整系统。
- `no_memory`：关闭历史记忆，观察 RAG 的贡献。
- `single_candidate`：只保留一个候选 patch，观察 tournament 的贡献。

当前本地 eval 已验证 4/4 修复成功。对简历来说，这比“我做了一个 agent demo”更有说服力，因为它有可重复的评测样例和对照实验。

## 16. 权限模式

项目实际上有两种权限模式。

### Readonly Mode

适合：

- 别人的开源仓库。
- 没有写权限的项目。
- 只想诊断和本地验证。

能力：

- 读取 PR / Actions / job logs。
- clone 到本地 workspace。
- 复现失败。
- 生成 patch、报告和 PR comment draft。
- 不会推分支，不会创建 PR。

### Write Mode

适合：

- 自己的仓库。
- 自己 fork 的仓库。
- 明确希望自动创建修复 PR 的 demo。

能力：

- Readonly Mode 的全部能力。
- 推送 repair branch。
- 调 GitHub API 创建修复 PR。

安全边界：

- 必须显式 `--create-pr`。
- 只有验证通过的 selected patch 才会写回。
- 默认不自动 merge；只有显式 `--auto-merge-repair-pr` 且通过门禁才合并修复 PR。
- 不直接推 main。

## 17. 和 Codex / Claude 的区别

如果面试官问“Codex 或 Claude 也能修 bug，你这个有什么不同”，可以这样答：

1. **输入对象不同**
   - 通用 coding agent 通常接收自然语言任务。
   - 本项目接收真实 GitHub CI failure，将 PR、workflow run、job log、changed files 统一成结构化上下文。

2. **中间状态不同**
   - 通用 agent 可以给出解释，但往往没有稳定的 failure fingerprint。
   - 本项目把 CI 失败标准化为 fingerprint，后续 RAG、memory、eval 都围绕它展开。

3. **记忆机制不同**
   - 普通对话记忆是用户偏好或上下文。
   - 本项目记忆的是验证过的 repair memory：错误类型、失败文件、修复策略、patch summary、验证命令、成功次数。

4. **验证机制不同**
   - 单 agent 常常生成一个 patch 再跑测试。
   - 本项目做 Patch Tournament：多个候选 patch 分别应用、测试、评分，再选择最小可行修复。

5. **工程闭环不同**
   - 通用 agent 重点在“帮你改代码”。
   - 本项目把 GitHub 写回、PR 描述、trace、dashboard、eval 都纳入系统，面向可审查的软件维护流程。

一句话：

> Codex / Claude 是强大的通用 coding agent；这个项目是围绕 CI 失败修复这个垂直场景，把 GitHub 上下文、失败指纹、历史修复记忆、多候选验证和 PR 写回组合成一个可评测、可复盘、可展示的软件维护系统。

## 18. 当前限制

为了面试时讲得可信，需要主动说明当前边界：

- 目前主要验证 Node / JavaScript demo 项目。
- patch 生成仍有规则 fallback，真实复杂项目需要更多语言和框架适配。
- 当前是 CLI 工作台，不是常驻 webhook 服务。
- 默认不会自动 merge，merge 仍由人完成。
- DashScope embedding 如果账号未开通对应模型权限，需要用 `hash` provider 或换可用 embedding。
- GitHub 写回需要用户配置 token 和 SSH key。
- 自动 merge 只适合个人仓库或明确授权的低风险修复场景，默认关闭。

这些限制不影响 MVP 价值，反而说明你知道项目边界，没有夸大。

## 19. 面试讲解顺序

建议按这个顺序讲，最容易让人听懂：

1. **问题背景**
   - CI 失败日志分散，定位和修复成本高。
   - 通用 coding agent 修复过程不够可控，历史经验难复用。

2. **系统目标**
   - 输入 GitHub 失败 PR / Actions job。
   - 输出 verified patch、诊断报告、修复 PR。

3. **核心流程**
   - GitHub context -> workspace -> reproduce -> fingerprint -> RAG -> patch candidates -> verification -> ranking -> PR write-back。

4. **三个创新点**
   - Failure Fingerprint。
   - Hybrid Repair RAG。
   - Patch Tournament。

5. **落地验证**
   - 本地 fixture 4/4。
   - 真实 GitHub demo：#1/#3/#5/#7 四类失败，agent 创建 #2/#4/#6/#8 修复 PR，合并修复 PR 后源 PR CI success。

6. **工程边界**
   - 命令 allowlist。
   - 默认只读。
   - 显式 `--create-pr`。
   - 自动 merge 默认关闭，开启后也必须通过 CI、风险标签、diff 范围等门禁。

## 20. 快速运行命令

本地 smoke：

```bash
python3 -m cifix.cli run \
  --repo fixtures/react-button-broken \
  --command "npm test" \
  --log fixtures/react-button-broken/ci-fail.log \
  --out artifacts
```

本地 eval：

```bash
python3 -m cifix.cli eval --cases fixtures --out artifacts/eval
```

真实 GitHub 只读：

```bash
python3 -m cifix.cli inspect \
  --url https://github.com/owner/repo/pull/123 \
  --token-env GITHUB_TOKEN
```

真实 GitHub 修复并创建 PR：

```bash
python3 -m cifix.cli run \
  --url https://github.com/owner/repo/pull/123 \
  --command "npm test" \
  --out artifacts \
  --vector-db chroma \
  --embedding-provider dashscope \
  --embedding-model text-embedding-v4 \
  --embedding-dimensions 1024 \
  --use-model \
  --create-pr \
  --token-env GITHUB_TOKEN \
  --ssh-key ~/.ssh/github_ci_repair_agent
```

真实 GitHub 修复、创建 PR，并在门禁通过后自动 merge 修复 PR：

```bash
python3 -m cifix.cli run \
  --url https://github.com/owner/repo/pull/123 \
  --command "npm test" \
  --out artifacts \
  --vector-db chroma \
  --embedding-provider dashscope \
  --embedding-model text-embedding-v4 \
  --embedding-dimensions 1024 \
  --use-model \
  --create-pr \
  --auto-merge-repair-pr \
  --token-env GITHUB_TOKEN \
  --ssh-key ~/.ssh/github_ci_repair_agent
```

生成 dashboard：

```bash
python3 -m cifix.cli dashboard --artifacts artifacts
```

## 21. 简历表述对应关系

可以把简历里的四条和代码对应起来：

1. GitHub CI 失败诊断与自动修复系统
   - 对应 `cifix/cli.py`、`cifix/run.py`、`cifix/github.py`、`cifix/tools/workspace.py`。

2. Failure Fingerprint + Hybrid Repair RAG
   - 对应 `failure_triage_agent.py`、`repair_memory_agent.py`、`rag.py`、`memory_writer_agent.py`。

3. 多候选 patch 验证流程
   - 对应 `patch_agent.py`、`model.py`、`test_agent.py`、`review_agent.py`。

4. Eval / dashboard / GitHub 写回闭环
   - 对应 `eval.py`、`dashboard.py`、`github_writer_agent.py`、`report_writer_agent.py`。

这样讲的时候，每个简历点都能落到真实代码和真实 demo，不会显得空。
