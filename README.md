# CIFix Agent MVP

Python implementation of a local runnable MVP for the CI failure self-healing agent described in `docs/project-plan.md`.

The Agent code is under `cifix/`. The `fixtures/*` directories are sample projects being repaired; they are not the Agent implementation.

Full project walkthrough: `docs/project-walkthrough.md`.

MVP delivery status and demo script: `docs/mvp-status.md`.

Run the fixture demo:

```bash
python3 -m cifix.cli run \
  --repo fixtures/react-button-broken \
  --command "npm test" \
  --log fixtures/react-button-broken/ci-fail.log \
  --out artifacts
```

Run the smoke test:

```bash
python3 -m unittest discover -s tests
```

Run local eval:

```bash
python3 -m cifix.cli eval --cases fixtures --out artifacts/eval
```

Run the Python benchmark eval:

```bash
python3 -m cifix.cli eval \
  --cases fixtures-python \
  --out artifacts/eval-python15 \
  --memory-path artifacts/memory/verified-repairs.json
```

Run cold-start vs warm-start RAG evaluation:

```bash
python3 -m cifix.cli eval \
  --cases fixtures-python \
  --out artifacts/eval-python15-rag-modes \
  --memory-path artifacts/memory/verified-repairs.json \
  --rag-eval-modes
```

Run eval with ablation baselines:

```bash
python3 -m cifix.cli eval --cases fixtures --out artifacts/eval-baselines --compare-baselines
```

Run model-assisted eval:

```bash
python3 -m cifix.cli eval --cases fixtures --out artifacts/eval-model --use-model
```

Generate a static dashboard from local artifacts:

```bash
python3 -m cifix.cli dashboard --artifacts artifacts
```

Capture the latest GitHub PR / CI status as an artifact:

```bash
python3 -m cifix.cli status \
  --url https://github.com/owner/repo/pull/123 \
  --token-env GITHUB_TOKEN
```

Query the repair RAG index directly:

```bash
python3 -m cifix.cli rag \
  --query "ERR_ASSERTION disabled false true login button" \
  --memory-path artifacts/memory/verified-repairs.json
```

Use ChromaDB as the vector database:

```bash
python3 -m pip install "chromadb>=0.5"

python3 -m cifix.cli rag \
  --query "ERR_ASSERTION disabled false true login button" \
  --memory-path artifacts/memory/verified-repairs.json \
  --vector-db chroma
```

Use Qwen / DashScope embeddings with ChromaDB:

```bash
export DASHSCOPE_API_KEY="your_dashscope_key"
export CIFIX_VECTOR_DB=chroma
export CIFIX_EMBEDDING_PROVIDER=dashscope
export CIFIX_EMBEDDING_MODEL=text-embedding-v4
export CIFIX_EMBEDDING_DIMENSIONS=1024

python3 -m cifix.cli rag \
  --query "ERR_ASSERTION disabled false true login button" \
  --memory-path artifacts/memory/verified-repairs.json
```

Use Zhipu `embedding-3` with ChromaDB:

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

Current scope:

- Local workspace execution for demo repo fixtures, with optional Docker sandboxing for setup, reproduction, and patch verification commands.
- GitHub read-only context loading for PR metadata, changed files, workflow jobs, and job logs when a token is provided.
- Optional Poe model mode for Claude Opus via OpenAI-compatible `/v1/chat/completions`.
- Failure Fingerprint generation.
- Hybrid RAG memory retrieval: BM25 keyword retrieval + vector database retrieval + hybrid reranking.
- ChromaDB vector database backend via `--vector-db chroma` or `CIFIX_VECTOR_DB=chroma`; SQLite brute-force vector scan remains as the zero-dependency fallback.
- Embedding providers: local hashing fallback, DashScope/Qwen `text-embedding-v4`, and Zhipu `embedding-3`.
- Verified repair memory written only after tests pass, then indexed into the RAG store.
- Patch Tournament with at least two candidates.
- Command safety policy with an allowlist for test/lint/typecheck commands.
- Structured artifacts: report, trace, patch candidates, selected patch, risk report, PR comment draft, GitHub write-back result.
- Optional GitHub write-back via `--create-pr`: commit the verified patch, push a repair branch, and create a PR when `GITHUB_TOKEN` has write permissions.
- Optional local watcher via `watch`: poll open GitHub PRs, detect failed CI, trigger the repair workflow once per failed head SHA / workflow run, and optionally comment back on the source PR.
- Eval runner over multiple CI failure fixtures.
- Python-only benchmark suite under `fixtures-python`, currently covering 15 unittest-based CI failure cases.
- RAG evidence metrics in eval reports: semantic Recall@5, Useful@3, nDCG@5, MRR, plus legacy fixed-id Hit@1/Hit@3 for reference.
- Baseline comparison for `full`, `no_memory`, and `single_candidate` eval variants.
- Static Chinese dashboard for run/eval/inspect/status artifact browsing, including latest eval and RAG metrics.

The most reliable zero-credential smoke path is still the local fixture. For real GitHub projects, start with read-only mode. You can paste a PR URL and let CIFix resolve the related failed workflow run/job:

```bash
python3 -m cifix.cli inspect \
  --url https://github.com/owner/repo/pull/123 \
  --token-env GITHUB_TOKEN
```

`inspect` only reads GitHub metadata/logs and writes local artifacts. To run the full repair workflow, use `run`:

```bash
python3 -m cifix.cli run \
  --url https://github.com/owner/repo/pull/123 \
  --token-env GITHUB_TOKEN
```

For real projects, CIFix can run a safe dependency setup command before reproducing the failure. In GitHub mode it infers `npm ci`, `pnpm install --frozen-lockfile`, or `yarn install --frozen-lockfile` from lockfiles. You can also pass one explicitly:

```bash
python3 -m cifix.cli run \
  --url https://github.com/owner/repo/pull/123 \
  --setup-command "npm ci" \
  --token-env GITHUB_TOKEN
```

To run setup, reproduction, and candidate verification inside Docker instead of directly on the host, add `--sandbox docker`. CIFix selects `node:20` for Node projects and `python:3.12` for Python-only projects when no image is specified; use `--docker-image` to override it.

```bash
python3 -m cifix.cli run \
  --url https://github.com/owner/repo/pull/123 \
  --command "npm test" \
  --sandbox docker \
  --docker-image node:20 \
  --token-env GITHUB_TOKEN
```

The watcher passes the same sandbox options into every triggered repair run:

```bash
python3 -m cifix.cli watch \
  --repo owner/repo \
  --interval-seconds 300 \
  --sandbox docker \
  --docker-image node:20 \
  --create-pr \
  --comment-source-pr \
  --token-env GITHUB_TOKEN \
  --ssh-key ~/.ssh/github_ci_repair_agent
```

You can also paste a specific GitHub Actions job URL when you already know which job failed:

```bash
python3 -m cifix.cli run \
  --url https://github.com/owner/repo/actions/runs/456789/job/987654 \
  --token-env GITHUB_TOKEN
```

Equivalent flag-based input is still supported:

```bash
python3 -m cifix.cli run \
  --repo owner/repo \
  --pr 123 \
  --run-id 456789 \
  --job 987654 \
  --token-env GITHUB_TOKEN
```

GitHub mode is read-only by default: it reads PR metadata, changed files, workflow run/job metadata, and failed job logs; then it clones the head commit into a local artifact workspace and writes local patch/report artifacts. It does not comment on GitHub, push branches, create PRs, or merge anything unless write-back is explicitly enabled.

For repositories you own, you can enable the write-back path. CIFix commits only the selected patch that has passed verification, pushes a new repair branch, and then creates a PR back into the failing PR branch. If `GITHUB_TOKEN` is missing, it still pushes the branch when SSH is configured and writes a GitHub compare URL into `github-write.json`.

```bash
python3 -m cifix.cli run \
  --url https://github.com/owner/repo/pull/123 \
  --command "npm test" \
  --token-env GITHUB_TOKEN \
  --create-pr \
  --ssh-key ~/.ssh/github_ci_repair_agent
```

For low-risk repairs in repositories you own, the repair PR can also be auto-merged back into the failing source branch after gated checks pass:

```bash
python3 -m cifix.cli run \
  --url https://github.com/owner/repo/pull/123 \
  --command "npm test" \
  --token-env GITHUB_TOKEN \
  --create-pr \
  --auto-merge-repair-pr \
  --ssh-key ~/.ssh/github_ci_repair_agent
```

Auto-merge is gated: the repair PR must target the source PR head branch, the selected patch must pass local verification, repair PR CI must pass when the repository triggers checks for that repair PR, the patch must not touch tests or carry overfit/noop risk tags, and the diff must stay under the configured line threshold. If the repository does not run checks for PRs targeting feature branches, CIFix waits briefly for checks to appear, records that fallback, and relies on local verification plus the source PR CI rerun after merge. Use `--require-repair-ci` to force strict repair PR checks.

For automatic PR creation, use a fine-grained GitHub token limited to the target repo with Contents read/write and Pull requests read/write permissions. For read-only inspect/run, Contents read, Actions read, and Pull requests read are enough.

To let a local machine react to CI failures without exposing a webhook endpoint, run the polling watcher. Start with `--dry-run` to confirm which PRs would be repaired:

```bash
python3 -m cifix.cli watch \
  --repo owner/repo \
  --once \
  --dry-run \
  --token-env GITHUB_TOKEN
```

Then enable repairs. The watcher stores processed failures in `artifacts/watch-state/<owner>__<repo>.json`, keyed by PR number, head SHA, and workflow run id, so the same failed run is not repaired repeatedly.

```bash
python3 -m cifix.cli watch \
  --repo owner/repo \
  --interval-seconds 300 \
  --create-pr \
  --comment-source-pr \
  --token-env GITHUB_TOKEN \
  --ssh-key ~/.ssh/github_ci_repair_agent
```

For local demos, add `--max-cycles 1` or `--once`. For source PR comments, the token also needs permission to write PR/issue comments.

Model mode uses environment variables. Do not put API keys in source files. Having `POE_API_KEY` in `.env` only makes the model available; the model is used only when `--use-model` or `CIFIX_USE_MODEL=1` is set.

```bash
export POE_API_KEY="your_poe_key"
export POE_MODEL="Claude-Opus-4.6"
export POE_BASE_URL="https://api.poe.com"

python3 -m cifix.cli run \
  --repo fixtures/react-button-broken \
  --command "npm test" \
  --log fixtures/react-button-broken/ci-fail.log \
  --use-model
```

If Poe reports that the model name is unavailable, set `POE_MODEL` to the exact model id shown in your Poe model list.

Current fixture set:

- `react-button-broken`
- `counter-increment-broken`
- `todo-filter-broken`
- `lint-unused-var-broken`
- `python-unittest-broken`

Python benchmark fixture set:

- `fixtures-python/py01_calculator_add`
- `fixtures-python/py02_discount_rate`
- `fixtures-python/py03_profile_contract`
- `fixtures-python/py04_import_refactor`
- `fixtures-python/py05_none_guard`
- `fixtures-python/py06_cart_missing_total`
- `fixtures-python/py07_filter_active_users`
- `fixtures-python/py08_serializer_contract`
- `fixtures-python/py09_timeout_unit`
- `fixtures-python/py10_date_format`
- `fixtures-python/py11_env_default`
- `fixtures-python/py12_order_total`
- `fixtures-python/py13_owner_permission`
- `fixtures-python/py14_pagination_offset`
- `fixtures-python/py15_pipeline_normalization`

Latest verified local eval:

```text
cases: 5
success: 5
success_rate: 1
```

Latest verified Python benchmark:

```text
cases: 15
success: 15
success_rate: 1
```

Latest RAG modes benchmark:

```text
python3 -m cifix.cli eval --cases fixtures-python --out artifacts/eval-python15-rag-modes --memory-path artifacts/memory/verified-repairs.json --rag-eval-modes
cases: 15
total_runs: 30
success: 30
success_rate: 1.0
rag_cold_start: Recall@5 1.0, Useful@3 1.0, nDCG@5 0.927, MRR 0.922
rag_warm_start: Recall@5 0.867, Useful@3 0.8, nDCG@5 0.71, MRR 0.55
```
