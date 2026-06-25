# GitHub CI 自动修复 Agent 项目讲解

目标：快速讲清楚这个项目做了什么、一次 CI 修复任务怎么跑、多 Agent 如何协作、Hybrid RAG 和修复记忆怎么工作、GitHub 写回闭环怎么落地，以及面试/简历中应该怎么表述。

这份文档按当前代码事实整理，不把还没做的 webhook、复杂线上服务化包装成已经完成的能力；自动 merge 已实现为默认关闭的高权限门禁能力。

## 1. 项目一句话

这是一个面向 GitHub Actions CI 失败场景的多 Agent 自动修复系统。它可以接收一个失败的 GitHub PR / workflow run / job URL，也可以由本地 watcher 周期性扫描目标仓库 open PR，在发现 CI failed 后自动触发修复流程。系统会读取 PR 和 CI 上下文，在本地隔离 workspace 复现失败，生成多个候选 patch，逐个运行测试验证，选择风险最低的可行修复，并在有权限的仓库中自动推送修复分支、创建修复 PR。

更适合简历或面试第一句话的表述：

> 设计并实现基于 Hybrid RAG 与多 Agent 协作的 GitHub CI 失败诊断与自动修复系统，支持从真实 GitHub PR / Actions job 读取失败上下文，自动完成失败复现、Failure Fingerprint 生成、历史修复案例检索、候选 patch 生成、测试验证、风险排序和修复 PR 创建；已在个人 GitHub demo 仓库中跑通从失败 PR 到修复 PR 合并后原 PR CI 恢复通过的闭环。

这里的重点不是“让大模型写代码”，而是：

- 把 CI 失败从一堆分散日志变成结构化 failure fingerprint。
- 把历史修复经验做成可检索的 repair memory。
- 用多候选 patch + 真实测试验证替代单次盲改。
- 把修复结果写回 GitHub，形成可审查、可回滚的 PR 闭环；低风险场景下可以显式开启自动 merge 修复 PR。
- 在本地运行 watcher，轮询 GitHub PR 的 CI 状态，发现新失败后自动进入修复流程。
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
| local watcher counter demo | #12 | #13 | watcher detected failed CI, created repair PR, commented on source PR |
| Python profile contract KeyError | #14 | #15 | repair PR merged into source branch, source PR CI success |

这说明当前项目不是只在本地 fixture 上跑通，而是已经完成真实 GitHub 仓库里的多类“失败 PR -> 修复 PR -> 合并修复 -> 原 PR CI 变绿”闭环。

其中 watcher demo 的关键记录：

- 源失败 PR：`https://github.com/qsjv587-666/ci-repair-agent-demo/pull/12`
- Watcher 自动创建的修复 PR：`https://github.com/qsjv587-666/ci-repair-agent-demo/pull/13`
- Watcher 自动写回的源 PR 评论：`https://github.com/qsjv587-666/ci-repair-agent-demo/pull/12#issuecomment-4777357720`
- 本地 watcher artifact：`artifacts/watch-live-test/watch_20260623084205_c85f2035/watch-summary.json`

Python profile contract demo 的关键记录：

- 源失败 PR：`https://github.com/qsjv587-666/ci-repair-agent-demo/pull/14`
- Agent 自动创建的修复 PR：`https://github.com/qsjv587-666/ci-repair-agent-demo/pull/15`
- 修复后成功 CI run：`https://github.com/qsjv587-666/ci-repair-agent-demo/actions/runs/28100212440`
- 本地修复 artifact：`artifacts/run-python-pr14-fixed/run_20260624125745_e5b857f0/report.md`

## 3. 代码大结构

核心入口：

- `cifix/cli.py`：命令行入口，支持 `run`、`watch`、`inspect`、`status`、`rag`、`eval`、`dashboard`、`doctor`。
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
- `cifix/watch.py`：本地轮询目标仓库 open PR，检测 CI failure，并对新失败自动触发修复。
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

如果希望“PR 一失败就自动处理”，当前采用本地 watcher 轮询方案：

```bash
python3 -m cifix.cli watch \
  --repo owner/repo \
  --interval-seconds 300 \
  --create-pr \
  --comment-source-pr \
  --token-env GITHUB_TOKEN \
  --ssh-key ~/.ssh/github_ci_repair_agent
```

它不是让 GitHub 直接访问你的电脑，而是本地 Agent 每隔一段时间主动查询目标仓库 open PR 的最新 CI 状态。发现 `failure` 后，系统用 `PR number + head SHA + workflow run id` 作为去重 key，只对新的失败触发一次修复流程，并把处理记录写入 `artifacts/watch-state/`。如果同时传 `--comment-source-pr`，修复结束后还会把诊断摘要和 repair PR 链接写回源失败 PR。

如果希望把测试执行放进容器里，可以加：

```bash
--sandbox docker
```

这样 setup、失败复现和候选 patch 验证都会在 Docker 容器里执行，而不是直接在宿主机执行。workspace 仍然是本地 artifact 目录，但会以 volume 形式挂载到容器的 `/workspace`。当前会对 Node 项目默认选择 `node:20`，对纯 Python 项目默认选择 `python:3.12`；复杂项目也可以显式传 `--docker-image`。

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

当前也支持可选 Docker sandbox：

```bash
--sandbox docker
```

开启后，setup、失败复现和候选 patch 验证命令会通过 Docker 执行。这样可以把测试运行环境和宿主机隔离开，同时保留本地 artifact workspace 作为可查看的修复现场。系统会根据仓库语言做基础镜像选择，也允许用 `--docker-image` 手动覆盖。

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

如果传入 `--comment-source-pr`，watcher 在修复流程结束后还会把 `pr-comment.md` 的诊断摘要评论回源失败 PR，并附上 repair PR 链接。这个能力需要 token 具备 PR / issue comment 写权限。

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
- 最新 eval 成功率、case 数、平均耗时。
- RAG 评测指标：Recall@5、Useful@3、nDCG@5、MRR，并保留 legacy fixed-id Hit@1/Hit@3 作为参考。
- 最近 run 的失败类型、项目、源 PR、修复 PR。
- Patch Tournament 的候选数量、通过数量、最佳候选和风险分数。
- Top RAG evidence 的来源、分数和策略摘要。
- GitHub PR status snapshot，展示源 PR 的最新 CI 状态和 workflow run 链接。
- patch / trace / RAG / GitHub write-back 链接。

这部分的价值是把“跑过一次命令”变成“可展示、可复盘的系统记录”。

## 15. Eval 和 Baseline 怎么做

代码在 `cifix/eval.py`。

本地评测分两层：

第一层是 `fixtures/`，作为混合语言回归集，覆盖 5 类典型失败。这里的 fixture 不是“错误类型”本身，而是一个故意带有 CI 失败的最小样例项目，用来稳定复现、评测和回归：

- `react-button-broken`
- `counter-increment-broken`
- `todo-filter-broken`
- `lint-unused-var-broken`
- `python-unittest-broken`

第二层是 `fixtures-python/`，作为 Python-only benchmark，当前包含 15 个 unittest 场景，覆盖断言失败、业务规则错误、字段契约不一致、import refactor、None guard、缺失字段、过滤逻辑、序列化契约、配置单位、日期格式、环境变量默认值、聚合计算、权限逻辑、分页 offset 和多文件 pipeline normalization。

第三层是 `benchmarks/python-projects/`，作为项目级 Python benchmark。它不再是单文件小题，而是按小型 Python 项目组织 package、pyproject、requirements、测试/静态检查命令和 CI 失败日志，当前覆盖：

- `pytest` 多文件字段契约失败。
- `ruff check` F401 unused import 失败。
- `mypy` optional return-value 类型失败。

基础 eval：

```bash
python3 -m cifix.cli eval --cases fixtures --out artifacts/eval
```

Python benchmark eval：

```bash
python3 -m cifix.cli eval \
  --cases fixtures-python \
  --out artifacts/eval-python15 \
  --memory-path artifacts/memory/verified-repairs.json
```

Cold-start vs warm-start RAG eval：

```bash
python3 -m cifix.cli eval \
  --cases fixtures-python \
  --out artifacts/eval-python15-rag-modes \
  --memory-path artifacts/memory/verified-repairs.json \
  --rag-eval-modes
```

项目级 Python benchmark：

```bash
python3 -m cifix.cli eval \
  --cases benchmarks/python-projects \
  --out artifacts/eval-python-projects \
  --memory-path artifacts/memory/verified-repairs.json \
  --rag-eval-modes
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

RAG 效果评测也写入 `report.md` 和 `summary.json`。当前不再把固定 `expectedRagIds` 当成主指标，因为固定 id 大多指向人工预置 playbook，会低估更具体的历史 repair memory。新的主指标基于 semantic relevance：

- `Recall@5`：Top 5 至少有一个相关 evidence。
- `Useful@3`：Top 3 至少有一个能指导 patch 生成的 evidence。
- `nDCG@5`：按 0-3 分相关性评价 evidence 排名质量。
- `MRR`：第一个 useful evidence 的倒数排名。
- `Legacy Hit@1/Hit@3`：固定 id 命中指标，只作为回归参考，不作为主要 RAG 效果结论。

RAG eval 分成两种模式：

- `rag_cold_start`：只使用静态 playbook，不加载历史 repair memory，用来评估系统冷启动时有没有基础修复知识。
- `rag_warm_start`：使用历史 repair memory，但会按 case 过滤掉疑似同 case 自己产生的记忆，避免数据泄漏，更接近 leave-one-out 评测。

当前本地混合语言 eval 已验证 5/5 修复成功；Python benchmark 已验证 15/15 修复成功。最新 15-case RAG modes benchmark 为 30/30 修复成功，其中 cold-start Recall@5 1.0、nDCG@5 0.934；warm-start leave-one-out Recall@5 0.867、Useful@3 0.867、nDCG@5 0.758。这个结果说明静态 playbook 覆盖较好，但当前历史 memory 规模还小、相似 case 噪声偏高，过滤自记忆后 warm-start 仍有优化空间。

项目级 Python benchmark 已验证 6/6 修复成功，覆盖 pytest / ruff / mypy 三类常见 Python CI；对应 RAG 指标为 cold-start Recall@5 1.0、nDCG@5 0.991，warm-start Recall@5 1.0、nDCG@5 0.933。

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

## 18. 面试高频问答

这一节不是代码说明，而是面向没看过项目的面试官，解释你为什么这样设计。

### 18.1 为什么要用多 Agent，而不是一个单 Agent

可以这样回答：

> CI 自动修复不是单纯“让模型改代码”，而是一条工程流程：要先读取 GitHub 上下文，理解失败日志，复现失败，检索历史修复经验，生成候选 patch，再逐个验证和排序，最后写回 GitHub。单 Agent 也能尝试完成这些事，但过程容易变成黑盒，面试官很难判断它到底有没有复现、有没有测试、有没有乱改测试。
>
> 所以我把它拆成多个职责明确的 Agent，每个 Agent 只负责一个环节，并且都有结构化输入输出。上游 Agent 产出的 failure fingerprint、RAG evidence、candidate patches、verification results 会成为下游 Agent 的决策依据。这样整个修复流程可以被 trace 回放，也可以做 ablation，对比去掉记忆、去掉多候选验证后的效果。

如果面试官继续追问“这是不是只是普通工作流”，可以补一句：

> 是的，它本质上是一个 agentic workflow，而不是几个 Agent 随机聊天。我认为工程项目里更重要的是可控和可验证，所以采用 Supervisor 编排的状态流：每一步既可以调用工具，也可以调用模型，但最终都必须把结果落到结构化状态和测试证据上。

### 18.2 多 Agent 之间怎么协作

可以这样回答：

> 多 Agent 之间不是通过自然语言闲聊协作，而是通过结构化状态传递。比如诊断 Agent 产出 failure fingerprint，记忆 Agent 用 fingerprint 去检索历史修复案例，Patch Agent 基于日志、fingerprint 和 RAG evidence 生成多个候选 patch，Test Agent 逐个应用 patch 并运行测试，Review Agent 根据测试结果、改动范围、风险标签和历史依据选择最终修复。
>
> 所有步骤都会写入 trace，所以我可以解释一次修复为什么选择这个 patch，而不是另一个 patch。

可以用这个链路举例：

```text
GitHub failed PR
-> 失败日志和改动文件
-> Failure Fingerprint
-> Hybrid RAG 召回历史修复依据
-> 多个候选 patch
-> 每个 patch 独立测试
-> 风险排序和最小可行修复
-> 创建 repair PR / 评论源 PR
```

### 18.3 Agent 的四大组成部分怎么具体实现

如果面试官问 Agent 的基本组成，可以按“感知、规划、行动、记忆”来讲。

#### 感知模块

它负责把外部世界变成 Agent 能处理的上下文。

在这个项目里，感知模块包括：

- 读取 GitHub PR 信息：源分支、目标分支、PR 标题、changed files。
- 读取 GitHub Actions 信息：workflow run、failed job、job log。
- 把 PR 代码 clone 到本地隔离 workspace。
- 本地运行测试命令，确认失败是否能复现。

可以这样说：

> 我没有只依赖用户描述，而是直接从 GitHub API 读取 PR、CI run、job log 和 changed files，再在本地 workspace 复现失败。这样 Agent 看到的是实际 CI 证据，而不是人工转述。

#### 规划模块

它负责决定修复流程怎么走。

在这个项目里，规划不是让模型自由发挥，而是固定为可控状态机：

```text
读取上下文 -> 复现失败 -> 生成失败指纹 -> 检索记忆 -> 生成候选 patch -> 测试验证 -> 风险排序 -> 写回 GitHub
```

可以这样说：

> 我没有把规划完全交给 LLM，而是把 CI 修复拆成稳定的工程状态机。LLM 主要参与候选 patch 生成和解释，关键门禁，比如是否测试通过、是否改测试、是否能写回 GitHub，都由确定性规则和真实测试结果控制。

#### 行动模块

它负责真正对代码和 GitHub 做操作。

在这个项目里，行动包括：

- 在本地 workspace 运行测试命令。
- 应用候选 patch。
- 恢复 baseline，确保每个 patch 独立验证。
- 推送 repair branch。
- 创建 repair PR。
- 可选评论源 PR。
- 可选在低风险门禁下合并 repair PR。

可以这样说：

> 行动模块不是任意执行 shell，而是有命令 allowlist、timeout 和写回开关。默认只读，只有显式打开 create-pr 才会推分支和创建 PR；自动 merge 也只针对低风险 repair PR，并且不会直接合入 main。

#### 记忆模块

它负责让系统复用历史修复经验。

在这个项目里，记忆不是聊天历史，而是 verified repair memory：

- 每次修复成功后，记录失败类型、错误码、失败文件、变更文件、测试命令、修复策略、patch 摘要、验证结果。
- 下次遇到相似失败时，用 Failure Fingerprint 生成查询。
- 先用 BM25 做关键词精确召回，再用 ChromaDB + Qwen/DashScope embedding 做向量语义召回。
- 最后根据 BM25 分数、向量分数和历史置信度做混合排序。

可以这样说：

> 这个项目的记忆不是“用户偏好记忆”，而是工程修复记忆。只有测试通过的修复才会写入 memory。下次遇到类似 CI 失败时，RAG 会召回历史上验证过的修复案例，作为 patch 生成和风险排序的证据。

### 18.4 为什么需要 RAG

可以这样回答：

> CI 失败有很强的重复性，比如相同测试框架、相同错误码、相同文件模式会反复出现。如果每次都让模型从零分析，就浪费上下文，也缺少可追溯依据。RAG 的作用是把过去验证过的修复经验沉淀下来，让系统不仅知道“这次日志是什么”，还知道“过去类似失败是怎么被修好的”。
>
> 我用了 BM25 和向量检索的混合方案：BM25 擅长匹配错误码、文件名、测试命令这类精确关键词；向量检索擅长召回描述不同但语义相似的失败。两者结合，比单纯关键词或单纯向量更适合 CI 失败场景。

### 18.5 为什么要做多候选 patch 验证

可以这样回答：

> 单次生成 patch 最大的问题是过拟合。模型可能为了让测试通过去改测试，也可能做一个看似合理但范围过大的修复。所以我设计了多候选 patch 验证流程：同一个失败生成多个候选修复，每个候选都在干净 workspace 上独立应用和测试，然后根据测试是否通过、diff 大小、风险标签、是否命中历史修复经验来排序。
>
> 这样最终选择的是“最小可行修复”，而不是第一个看起来能改的 patch。

### 18.6 项目最大的难点是什么

可以讲三个难点。

第一，GitHub 上下文和本地复现不一定一致。

> GitHub Actions 运行在云端，本地运行在我的机器上，两边环境可能不同。所以我做了 workflow/job log 读取、依赖安装命令推断、测试命令推断和 workspace 隔离，让本地复现尽量接近 CI，同时把 GitHub 原始日志也作为诊断输入。

第二，模型生成 patch 不可靠。

> 模型可能生成无效 diff、改测试、做过大改动，或者解释正确但代码不通过。所以我没有直接信任模型输出，而是把 patch 变成候选项，必须经过 apply、test、risk scoring 和 review。只有验证通过的 selected patch 才能进入写回环节。

第三，自动写回 GitHub 有安全风险。

> 如果 Agent 直接推 main 或自动 merge 业务 PR，会很危险。所以系统默认只读；写回必须显式打开；repair PR 的 base 是源失败分支，不是 main；自动 merge 也只合并 repair PR 到源失败分支，并且要求本地验证通过、风险标签安全、diff 范围小。最终源 PR 是否合入 main 仍交给人或仓库保护规则。

### 18.7 如何证明这不是玩具 demo

可以这样回答：

> 我没有只做一个本地脚本，而是做了真实 GitHub 接入、失败 PR 监听、CI 日志读取、本地复现、RAG 记忆、多候选验证、PR 写回、源 PR 评论、dashboard 和 eval。项目已经在个人 GitHub demo 仓库跑通过多类真实 PR 闭环，包括失败 PR 自动检测、自动创建 repair PR、评论源 PR，以及低风险 repair PR 自动合并后让源 PR CI 重新变绿。

如果要更具体：

- 本地混合语言 fixture 覆盖 JavaScript 断言失败、lint 失败、Python unittest 断言失败等场景。
- Python benchmark 扩展到 15 个 unittest 场景，并能输出 semantic Recall@5、Useful@3、nDCG@5、MRR 和 legacy fixed-id Hit 指标。
- 项目级 Python benchmark 覆盖 pytest、ruff、mypy，不再只停留在 unittest 小样例。
- Memory governance 会跳过 noop / test-change / possible-overfit 这类低质量记忆，并对重复修复做更新而不是无限追加。
- RAG reranker 会在 BM25 + vector 之后结合 failureType、errorCode、文件重合、strategy 关键词和 memory quality 做二次排序。
- eval 可以对比完整系统、去掉记忆、只生成单候选三种模式。
- 真实 GitHub demo 覆盖手动触发、watcher 自动触发、repair PR 创建、源 PR 评论和 gated auto-merge。
- 每次 run 都有 trace、report、patch diff、RAG evidence 和 GitHub write-back artifact。

### 18.8 这个项目和 Codex / Claude 最大区别是什么

可以这样回答：

> Codex / Claude 是通用 Coding Agent，能力很强，但它们通常是围绕一次自然语言任务工作。我的项目关注的是一个垂直工程流程：GitHub CI failure repair。区别不在于“模型比它们强”，而在于我把真实 GitHub CI 上下文、Failure Fingerprint、Hybrid RAG、Patch Tournament、写回门禁、eval 和 dashboard 组合成了一套可审计的软件维护系统。
>
> 所以它解决的是“CI 失败后如何可控、可复盘、可重复地自动修复”，而不是单纯“让模型帮我改一段代码”。

### 18.9 当前不足和下一步优化

可以主动说：

> 当前版本已经覆盖 Node / JavaScript、Python unittest，以及项目级 Python pytest / ruff / mypy 场景。复杂多语言项目还需要继续扩展 repo mapper、测试命令推断和 patch 生成策略。当前触发方式是本地 watcher 轮询，不是 GitHub App webhook。Docker sandbox 已支持 setup、reproduce 和 patch verification 命令，并能对 Node 与 Python-only 项目做基础镜像选择。下一阶段更适合继续增加真实开源项目级别的 case、提高模型候选生成能力，并扩大 RAG 评测集。

这样回答会显得比较成熟：既说明项目价值，也知道边界。

## 19. 当前限制

为了面试时讲得可信，需要主动说明当前边界：

- 目前已验证 Node / JavaScript demo 项目、Python unittest demo 项目、15 个 Python fixture benchmark，以及 3 个项目级 Python benchmark case；更复杂的 Python 依赖、pytest 插件、monorepo 还需要继续扩展。
- patch 生成仍有规则 fallback，真实复杂项目需要更多语言和框架适配。
- 当前是 CLI 工作台，可以通过本地 watcher 轮询 GitHub 触发修复，但还不是 GitHub webhook / GitHub App 服务。
- Docker sandbox 需要本机安装 Docker；当前能为 Node 和 Python-only 项目选择基础镜像，复杂多语言项目仍建议显式指定镜像和命令。
- 默认不会自动 merge，merge 仍由人完成。
- DashScope embedding 如果账号未开通对应模型权限，需要用 `hash` provider 或换可用 embedding。
- GitHub 写回需要用户配置 token 和 SSH key。
- 自动 merge 只适合个人仓库或明确授权的低风险修复场景，默认关闭。

这些限制不影响 MVP 价值，反而说明你知道项目边界，没有夸大。

## 20. 面试讲解顺序

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
   - 本地混合语言 fixture 5/5，Python benchmark 15/15，项目级 Python benchmark 6/6。
   - 真实 GitHub demo：#1/#3/#5/#7 四类失败，agent 创建 #2/#4/#6/#8 修复 PR，合并修复 PR 后源 PR CI success；#12 watcher 自动发现失败 CI，创建 #13 repair PR，并评论回源 PR；#14 Python 字段契约失败由 agent 创建 #15 修复 PR，合并后源 PR CI success。

6. **工程边界**
   - 命令 allowlist。
   - 默认只读。
   - 显式 `--create-pr`。
   - 自动 merge 默认关闭，开启后也必须通过 CI、风险标签、diff 范围等门禁。

## 21. 快速运行命令

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

Python benchmark：

```bash
python3 -m cifix.cli eval \
  --cases fixtures-python \
  --out artifacts/eval-python15 \
  --memory-path artifacts/memory/verified-repairs.json
```

RAG cold/warm 评测：

```bash
python3 -m cifix.cli eval \
  --cases fixtures-python \
  --out artifacts/eval-python15-rag-modes \
  --memory-path artifacts/memory/verified-repairs.json \
  --rag-eval-modes
```

真实 GitHub 只读：

```bash
python3 -m cifix.cli inspect \
  --url https://github.com/owner/repo/pull/123 \
  --token-env GITHUB_TOKEN
```

本地 watcher 单次扫描，先 dry-run：

```bash
python3 -m cifix.cli watch \
  --repo owner/repo \
  --once \
  --dry-run \
  --token-env GITHUB_TOKEN
```

本地 watcher 持续轮询，发现失败后自动创建 repair PR 并评论源 PR：

```bash
python3 -m cifix.cli watch \
  --repo owner/repo \
  --interval-seconds 300 \
  --create-pr \
  --comment-source-pr \
  --token-env GITHUB_TOKEN \
  --ssh-key ~/.ssh/github_ci_repair_agent
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
  --sandbox docker \
  --docker-image node:20 \
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
  --sandbox docker \
  --docker-image node:20 \
  --create-pr \
  --auto-merge-repair-pr \
  --token-env GITHUB_TOKEN \
  --ssh-key ~/.ssh/github_ci_repair_agent
```

生成 dashboard：

```bash
python3 -m cifix.cli dashboard --artifacts artifacts
```

## 22. 简历表述对应关系

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
