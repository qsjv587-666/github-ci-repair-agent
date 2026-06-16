from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.artifacts import json_text, summarize_command
from ..github import public_github_context


def run_report_writer_agent(**kwargs: Any) -> None:
    run_dir: Path = kwargs["run_dir"]
    fingerprint = kwargs["fingerprint"]
    playbook_hits = kwargs["playbook_hits"]
    reproduction = kwargs["reproduction"]
    model_diagnosis = kwargs["model_diagnosis"]
    tournament = kwargs["tournament"]
    selected = kwargs["selected"]
    memory_write = kwargs["memory_write"]
    github_context = kwargs.get("github_context")
    command = kwargs["command"]
    setup_result = kwargs["setup_result"]
    trace = kwargs["trace"]
    run_id = kwargs["run_id"]
    started_at = kwargs["started_at"]

    (run_dir / "failure-fingerprint.json").write_text(json_text(fingerprint))
    if github_context:
        (run_dir / "github-context.json").write_text(json_text(public_github_context(github_context)))
        if github_context.get("rawLog"):
            (run_dir / "github-log.txt").write_text(github_context["rawLog"])
    if setup_result:
        (run_dir / "setup.json").write_text(json_text(summarize_command(setup_result)))
    (run_dir / "repair-playbook-hits.json").write_text(json_text(playbook_hits))
    (run_dir / "memory-write.json").write_text(json_text(memory_write))
    (run_dir / "model-diagnosis.json").write_text(json_text(model_diagnosis))
    (run_dir / "verification.json").write_text(
        json_text(
            {
                "reproduction": summarize_command(reproduction),
                "selected": summarize_command(selected["verification"]) if selected else None,
                "candidates": [{"id": c["id"], "verification": summarize_command(c["verification"]), "riskScore": c["riskScore"], "rankingScore": c["rankingScore"]} for c in tournament["candidates"]],
            }
        )
    )
    (run_dir / "trace.json").write_text(json_text(trace))
    (run_dir / "patch.diff").write_text(selected.get("diff") if selected else "# No patch selected\n")
    (run_dir / "risk-report.md").write_text(render_risk_report(selected, tournament))
    (run_dir / "pr-comment.md").write_text(render_pr_comment(fingerprint, selected, command))
    (run_dir / "report.md").write_text(render_report(run_id, started_at, fingerprint, playbook_hits, reproduction, model_diagnosis, tournament, selected, command, setup_result, memory_write, github_context))


def render_report(run_id: str, started_at: str, fingerprint: dict[str, Any], playbook_hits: list[dict[str, Any]], reproduction: dict[str, Any], model_diagnosis: dict[str, Any], tournament: dict[str, Any], selected: dict[str, Any] | None, command: str, setup_result: dict[str, Any] | None, memory_write: dict[str, Any], github_context: dict[str, Any] | None) -> str:
    playbook_lines = "\n".join(render_rag_hit(hit) for hit in playbook_hits) or "- No RAG memory matched."
    tournament_lines = "\n".join(f"{i + 1}. {c['id']}\n   - passed: {c['verification']['passed']}\n   - risk: {c['riskScore']}\n   - ranking: {c['rankingScore']}\n   - hypothesis: {c['hypothesis']}" for i, c in enumerate(tournament["candidates"]))
    github_lines = render_github_context(github_context)
    return f"""# CIFix Report

- Run: {run_id}
- Started: {started_at}
- Command: `{command}`
- Status: {"success" if selected and selected["verification"]["passed"] else "needs attention"}

## Failure Fingerprint

```json
{json_text(fingerprint)}```

{github_lines}

## Hybrid RAG Retrieval

{playbook_lines}

## Reproduction

- Setup: {setup_result["passed"] if setup_result else "skipped"}
- Passed: {reproduction["passed"]}
- Exit code: {reproduction["exitCode"]}

## Model Diagnosis

```json
{json_text(model_diagnosis)}```

## Patch Tournament

{tournament_lines}

## Recommended Patch

{f"`{selected['id']}`" if selected else "No patch selected."}

## Verified Memory Write

```json
{json_text(memory_write)}```
"""


def render_github_context(github_context: dict[str, Any] | None) -> str:
    if not github_context:
        return ""
    warnings = "\n".join(f"- {warning}" for warning in github_context.get("warnings", [])) or "- none"
    return f"""## GitHub Context

- Repository: {github_context.get("owner")}/{github_context.get("repo")}
- Pull request: {github_context.get("pullNumber") or "n/a"}
- Workflow run: {github_context.get("runId") or "n/a"} {github_context.get("runConclusion") or ""}
- Job: {github_context.get("jobId") or "n/a"} {github_context.get("jobName") or ""}
- Changed files: {len(github_context.get("changedFiles", []))}
- Log chars: {len(github_context.get("rawLog", ""))}
- Warnings:
{warnings}
"""


def render_rag_hit(hit: dict[str, Any]) -> str:
    score = hit.get("hybridScore", hit.get("score", 0))
    bm25 = hit.get("bm25Score", "n/a")
    vector = hit.get("vectorScore", "n/a")
    terms = ", ".join(hit.get("matchedTerms", [])[:6]) or "none"
    return f"- {hit['id']} ({hit.get('source', 'memory')}) hybrid={score}, bm25={bm25}, vector={vector}; terms={terms}\n  - {hit['strategy']}"


def render_risk_report(selected: dict[str, Any] | None, tournament: dict[str, Any]) -> str:
    if not selected:
        return "# Risk Report\n\nNo patch selected.\n"
    return f"""# Risk Report

- Recommended patch: {selected['id']}
- Risk score: {selected['riskScore']}
- Verification passed: {selected['verification']['passed']}
- Risk tags: {", ".join(selected.get("riskTags", [])) or "none"}

The selected patch ranked above {max(0, len(tournament["candidates"]) - 1)} other candidate(s).
"""


def render_pr_comment(fingerprint: dict[str, Any], selected: dict[str, Any] | None, command: str) -> str:
    return f"""## CIFix Agent Diagnosis

Failure type: `{fingerprint["failureType"]}`
Error code: `{fingerprint["errorCode"]}`
Command: `{command}`

Recommended patch: `{selected["id"] if selected else "none"}`
Verification: {"passed" if selected and selected["verification"]["passed"] else "needs attention"}

Artifacts include a full trace, failure fingerprint, repair playbook hits, patch candidates, and risk report.
"""
