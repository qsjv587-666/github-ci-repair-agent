# CIFix Agent MVP Status

## MVP Scope

This MVP is a read-first CI failure repair workflow for GitHub Actions and local fixtures, with explicit opt-in write-back for repositories the user owns.

It supports:

- Local fixture repair runs.
- Read-only GitHub PR / Actions URL inspection.
- GitHub PR / run / job context loading for the full repair workflow.
- Safe setup and verification commands through allowlists.
- Optional Docker sandbox for setup, reproduction, and patch verification commands.
- Failure fingerprint generation.
- Hybrid repair RAG retrieval with BM25, vector cosine search, and hybrid reranking.
- Verified repair memory written only after tests pass, then indexed for future RAG retrieval.
- Model-assisted patch generation through Poe / Claude when `--use-model` is set.
- Patch tournament across candidate repairs.
- Structured artifacts for report, trace, patch, risk report, PR comment draft, memory write, GitHub context, and GitHub write-back status.
- Optional `--create-pr` write-back: commit the verified patch, push a repair branch, and create a PR when a write-capable `GITHUB_TOKEN` is configured.
- Optional `watch` mode: poll a target GitHub repository for open PRs whose latest CI failed, trigger the repair workflow once per failed head SHA / workflow run, and optionally comment back on the source PR.
- Optional gated auto-merge of low-risk repair PRs with `--auto-merge-repair-pr`.
- Eval runner with baseline comparison.
- Python benchmark eval suite with 15 unittest-based CI failure cases.
- RAG evidence metrics: Hit@1, Hit@3, MRR, and Coverage.
- Static Chinese dashboard over run/eval/inspect/status artifacts, including latest eval and RAG metrics.

It does not support in MVP:

- GitHub write-back without explicit `--create-pr`.
- GitHub PR creation without a token that has Pull requests write permission.
- GitHub webhook / GitHub App event ingestion.
- Running arbitrary shell commands.
- Claiming support for all languages/build systems.
- Automatic Docker image selection for every language ecosystem beyond the current Node and Python-only defaults.

## Demo Commands

Local repair:

```bash
python3 -m cifix.cli run \
  --repo fixtures/react-button-broken \
  --command "npm test" \
  --log fixtures/react-button-broken/ci-fail.log \
  --out artifacts
```

Read-only GitHub inspect:

```bash
python3 -m cifix.cli inspect \
  --url https://github.com/octocat/Hello-World/pull/1 \
  --out artifacts
```

Eval:

```bash
python3 -m cifix.cli eval --cases fixtures --out artifacts/eval
```

Python benchmark eval:

```bash
python3 -m cifix.cli eval \
  --cases fixtures-python \
  --out artifacts/eval-python15 \
  --memory-path artifacts/memory/verified-repairs.json
```

Baseline comparison:

```bash
python3 -m cifix.cli eval \
  --cases fixtures \
  --out artifacts/eval-baselines \
  --compare-baselines
```

Dashboard:

```bash
python3 -m cifix.cli dashboard --artifacts artifacts
```

RAG query:

```bash
python3 -m cifix.cli rag \
  --query "ERR_ASSERTION disabled false true login button" \
  --memory-path artifacts/memory/verified-repairs.json
```

Chroma vector database mode:

```bash
python3 -m pip install "chromadb>=0.5"

python3 -m cifix.cli rag \
  --query "ERR_ASSERTION disabled false true login button" \
  --memory-path artifacts/memory/verified-repairs.json \
  --vector-db chroma
```

Qwen / DashScope embedding mode:

```bash
export DASHSCOPE_API_KEY="your_dashscope_key"

python3 -m cifix.cli rag \
  --query "ERR_ASSERTION disabled false true login button" \
  --memory-path artifacts/memory/verified-repairs.json \
  --vector-db chroma \
  --embedding-provider dashscope \
  --embedding-model text-embedding-v4 \
  --embedding-dimensions 1024
```

Zhipu embedding mode:

```bash
export ZHIPU_API_KEY="your_zhipu_key"

python3 -m cifix.cli rag \
  --query "ERR_ASSERTION disabled false true login button" \
  --memory-path artifacts/memory/verified-repairs.json \
  --vector-db chroma \
  --embedding-provider zhipu \
  --embedding-model embedding-3 \
  --embedding-dimensions 1024
```

Model-assisted repair:

```bash
python3 -m cifix.cli run \
  --repo fixtures/react-button-broken \
  --command "npm test" \
  --log fixtures/react-button-broken/ci-fail.log \
  --out artifacts \
  --use-model
```

Docker sandbox repair:

```bash
python3 -m cifix.cli run \
  --repo fixtures/react-button-broken \
  --command "npm test" \
  --log fixtures/react-button-broken/ci-fail.log \
  --out artifacts \
  --sandbox docker \
  --docker-image node:20
```

Local watcher dry-run:

```bash
python3 -m cifix.cli watch \
  --repo owner/repo \
  --once \
  --dry-run \
  --token-env GITHUB_TOKEN
```

Local watcher repair mode:

```bash
python3 -m cifix.cli watch \
  --repo owner/repo \
  --interval-seconds 300 \
  --create-pr \
  --comment-source-pr \
  --token-env GITHUB_TOKEN \
  --ssh-key ~/.ssh/github_ci_repair_agent
```

## Current Verification

Latest local verification:

```text
python3 -m compileall -q cifix tests
python3 -m unittest discover -s tests
29 tests OK

python3 -m cifix.cli eval --cases fixtures --out artifacts/eval
cases: 5
total_runs: 5
success: 5
success_rate: 1.0

python3 -m cifix.cli eval --cases fixtures-python --out artifacts/eval-python15 --memory-path artifacts/memory/verified-repairs.json
cases: 15
total_runs: 15
success: 15
success_rate: 1.0
RAG metrics: Hit@1 0.067, Hit@3 0.667, MRR 0.38, Coverage 0.867

python3 -m cifix.cli eval --cases fixtures --out artifacts/eval-baselines --compare-baselines
cases: 5
total_runs: 15
success: 15
success_rate: 1.0

python3 -m cifix.cli inspect --url https://github.com/octocat/Hello-World/pull/1 --out artifacts
inspect: succeeded in read-only mode
```

## Resume Framing

Suggested description:

> Built CIFix Agent, a Python multi-agent workflow for GitHub Actions CI failure diagnosis and repair. The system turns job logs into failure fingerprints, retrieves historical repair evidence through hybrid RAG (BM25 + vector cosine retrieval), generates multiple candidate patches, validates them in an isolated workspace, ranks patches by test evidence and risk, and emits structured reports, PR comment drafts, traces, and a dashboard. Implemented read-only GitHub PR/Actions ingestion, command allowlists, verified repair memory, model-assisted patching via Poe/Claude, and baseline evals across full/no-memory/single-candidate variants.

Suggested metrics to report from the current MVP:

- 5 mixed-language CI-failure fixtures, including Node / JavaScript and Python unittest cases.
- 15 Python-only benchmark fixtures under `fixtures-python`.
- 5 / 5 success on mixed-language full eval.
- 15 / 15 success on Python benchmark eval.
- 15 / 15 successful runs in baseline comparison.
- 29 unit/smoke tests.
- Read-only GitHub inspect verified on a public PR.
- Real GitHub Python demo: source PR #14 failed on `KeyError: 'name'`; CIFix created repair PR #15; after merging #15 into the source branch, PR #14 CI reran successfully.
- Hybrid RAG trace includes BM25 score, vector score, hybrid score, matched terms, vector backend, embedding provider/model, vector DB path, and index path.
- Latest Python benchmark RAG metrics: Hit@1 0.067, Hit@3 0.667, MRR 0.38, Coverage 0.867.

## Next Non-MVP Extensions

- Add more real-world Python cases beyond the current 15-case synthetic benchmark.
- Add a stable fork-based GitHub demo repo with intentionally failing PRs.
- Add optional GitHub draft PR creation behind explicit approval.
- Add automatic Docker image selection for more language ecosystems.
- Add richer TypeScript type-error repair cases.
