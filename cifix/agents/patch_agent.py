from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..core.trace import step
from ..model import generate_model_patch_candidates, model_config_from_env


def run_patch_agent(*, flags: dict[str, Any], workspace_dir: Path, fingerprint: dict[str, Any], playbook_hits: list[dict[str, Any]], raw_log: str, reproduction: dict[str, Any], repo_map: dict[str, Any], trace: list[dict]) -> dict[str, Any]:
    model_config = model_config_from_env(flags)
    model_result = safe_generate_model_patch_candidates(
        workspace_dir=workspace_dir,
        fingerprint=fingerprint,
        playbook_hits=playbook_hits,
        raw_log=raw_log,
        reproduction=reproduction,
        repo_map=repo_map,
        model_config=model_config,
    )
    trace.append(step("ModelPatchAgent", {"enabled": model_config["enabled"], "provider": model_config["provider"], "model": model_config["model"]}, model_result["diagnosis"]))
    rule_candidates = generate_rule_patch_candidates(workspace_dir, playbook_hits)
    candidates = merge_patch_candidates(model_result["candidates"], rule_candidates)
    trace.append(step("PatchAgent", {"candidateCount": len(candidates)}, [public_candidate(candidate) for candidate in candidates]))
    return {"candidates": candidates, "modelDiagnosis": model_result["diagnosis"]}


def safe_generate_model_patch_candidates(**kwargs: Any) -> dict[str, Any]:
    try:
        return generate_model_patch_candidates(**kwargs)
    except Exception as error:
        return {"candidates": [], "diagnosis": {"error": str(error), "fallback": "rule candidates"}}


def merge_patch_candidates(model_candidates: list[dict[str, Any]], rule_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {}
    for candidate in [*model_candidates, *rule_candidates]:
        candidate_id = candidate["id"]
        if candidate_id in by_id:
            candidate_id = f"{candidate_id}_{len(by_id) + 1}"
        by_id[candidate_id] = {**candidate, "id": candidate_id}
    values = list(by_id.values())
    return values[: max(2, min(4, len(values)))]


def generate_rule_patch_candidates(workspace_dir: Path, playbook_hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = []
    candidates.extend(generate_lint_unused_var_candidates(workspace_dir, playbook_hits))
    rules = [
        ("src/login-button.js", "disabled: false", "disabled: Boolean(loading)", "patch_source_loading_disabled", "The source state ignores loading and always leaves the button enabled."),
        ("src/counter.js", "return count;", "return count + 1;", "patch_counter_increment", "increment should return the next count rather than the current count."),
        ("src/todos.js", "return todos;", "return todos.filter((todo) => !todo.completed);", "patch_filter_active_todos", "getActiveTodos should filter out completed todos."),
        ("src/calculator.py", "return a - b", "return a + b", "patch_python_add_numbers", "add should return the sum rather than subtracting the second argument."),
    ]
    for file, old, new, candidate_id, hypothesis in rules:
        file_path = workspace_dir / file
        if file_path.exists() and old in file_path.read_text():
            candidates.append(
                {
                    "id": candidate_id,
                    "hypothesis": hypothesis,
                    "playbookId": playbook_hits[0]["id"] if playbook_hits else None,
                    "riskTags": ["source-change"],
                    "source": "rule",
                    "edits": [{"file": file, "from": old, "to": new}],
                }
            )
    test_path = workspace_dir / "test" / "login-button.test.js"
    if test_path.exists() and "assert.equal(state.disabled, true)" in test_path.read_text():
        candidates.append(
            {
                "id": "patch_weaken_test_assertion",
                "hypothesis": "The test expectation may be wrong, so weaken the assertion.",
                "playbookId": None,
                "riskTags": ["test-change", "possible-overfit"],
                "source": "rule",
                "edits": [{"file": "test/login-button.test.js", "from": "assert.equal(state.disabled, true)", "to": "assert.equal(state.disabled, false)"}],
            }
        )
    if not candidates:
        candidates.append({"id": "patch_noop_report_only", "hypothesis": "No deterministic patch rule matched this failure.", "riskTags": ["noop"], "source": "rule", "edits": []})
    if len(candidates) == 1:
        candidates.append({"id": "patch_noop_baseline", "hypothesis": "Baseline no-op candidate for tournament comparison.", "riskTags": ["noop"], "source": "rule", "edits": []})
    return candidates


def generate_lint_unused_var_candidates(workspace_dir: Path, playbook_hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = []
    lint_playbook = next((hit for hit in playbook_hits if hit.get("errorCode") in {"no-unused-vars", "@typescript-eslint/no-unused-vars"}), None)
    source_files = [path for path in (workspace_dir / "src").rglob("*") if path.suffix in {".js", ".jsx", ".ts", ".tsx"}] if (workspace_dir / "src").exists() else []
    for path in source_files:
        relative = path.relative_to(workspace_dir).as_posix()
        content = path.read_text()
        for match in re.finditer(r"^[ \t]*(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*[^;\n]+;\n", content, re.M):
            variable = match.group(1)
            if count_identifier_uses(content, variable) == 1:
                candidates.append(
                    {
                        "id": f"patch_remove_unused_{variable}",
                        "hypothesis": f"Remove unused local variable `{variable}` reported by lint.",
                        "playbookId": lint_playbook["id"] if lint_playbook else None,
                        "riskTags": ["source-change", "lint-fix"],
                        "source": "rule",
                        "edits": [{"file": relative, "from": match.group(0), "to": ""}],
                    }
                )
                break
    return candidates[:2]


def count_identifier_uses(content: str, identifier: str) -> int:
    return len(re.findall(rf"\b{re.escape(identifier)}\b", content))


def public_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": candidate["id"],
        "hypothesis": candidate["hypothesis"],
        "playbookId": candidate.get("playbookId"),
        "source": candidate.get("source"),
        "riskTags": candidate.get("riskTags"),
        "changedFiles": [edit["file"] for edit in candidate.get("edits", [])],
    }
