from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.artifacts import summarize_command
from ..core.trace import step
from ..tools.command import DEFAULT_ALLOWED_PREFIXES, run_command
from ..tools.patch import apply_candidate, git_diff, restore_baseline


def run_test_agent(*, workspace_dir: Path, candidates: list[dict[str, Any]], command: str, playbook_hits: list[dict[str, Any]], run_dir: Path, trace: list[dict]) -> list[dict[str, Any]]:
    candidate_dir = run_dir / "patch-candidates"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for candidate in candidates:
        restore_baseline(workspace_dir)
        diff = ""
        apply_error = None
        try:
            apply_candidate(workspace_dir, candidate)
            diff = git_diff(workspace_dir)
            verification = run_command(command, workspace_dir, 20, DEFAULT_ALLOWED_PREFIXES)
        except Exception as error:
            apply_error = str(error)
            verification = {"command": command, "passed": False, "stdout": "", "stderr": "", "exitCode": 1, "message": apply_error}
        risk_score = score_risk(candidate, verification, diff, playbook_hits)
        patch_file = candidate_dir / f"{candidate['id']}.diff"
        patch_file.write_text(diff or "# No diff\n")
        results.append({**candidate, "diff": diff, "patchFile": str(patch_file), "verification": verification, "applyError": apply_error, "riskScore": risk_score, "rankingScore": risk_score if verification["passed"] else risk_score + 1000})
    trace.append(step("TestAgent", {"candidateCount": len(candidates)}, [{"id": result["id"], "verification": summarize_command(result["verification"]), "riskScore": result["riskScore"], "applyError": result["applyError"]} for result in results]))
    return results


def score_risk(candidate: dict[str, Any], verification: dict[str, Any], diff: str, playbook_hits: list[dict[str, Any]]) -> int:
    score = 10
    if not verification["passed"]:
        score += 100
    if "test-change" in candidate.get("riskTags", []):
        score += 50
    if "possible-overfit" in candidate.get("riskTags", []):
        score += 30
    score += len(candidate.get("edits", [])) * 5
    score += len([line for line in diff.splitlines() if line.startswith("+") or line.startswith("-")])
    if candidate.get("playbookId") and any(hit["id"] == candidate["playbookId"] for hit in playbook_hits):
        score -= 10
    return max(0, score)
