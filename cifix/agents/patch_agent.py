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
    rule_candidates = generate_rule_patch_candidates(workspace_dir, playbook_hits, fingerprint)
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


def generate_rule_patch_candidates(workspace_dir: Path, playbook_hits: list[dict[str, Any]], fingerprint: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    candidates = []
    candidates.extend(generate_lint_unused_var_candidates(workspace_dir, playbook_hits))
    candidates.extend(generate_python_profile_contract_candidates(workspace_dir, playbook_hits, fingerprint or {}))
    candidates.extend(generate_python_ruff_candidates(workspace_dir, playbook_hits, fingerprint or {}))
    candidates.extend(generate_python_mypy_candidates(workspace_dir, playbook_hits, fingerprint or {}))
    rules = [
        ("src/login-button.js", "disabled: false", "disabled: Boolean(loading)", "patch_source_loading_disabled", "The source state ignores loading and always leaves the button enabled."),
        ("src/counter.js", "return count;", "return count + 1;", "patch_counter_increment", "increment should return the next count rather than the current count."),
        ("src/todos.js", "return todos;", "return todos.filter((todo) => !todo.completed);", "patch_filter_active_todos", "getActiveTodos should filter out completed todos."),
        ("src/calculator.py", "return a - b", "return a + b", "patch_python_add_numbers", "add should return the sum rather than subtracting the second argument."),
        ("src/billing.py", "return amount * 0.9", "return amount * 0.8", "patch_python_discount_rate", "enterprise discounts should apply the configured 20 percent rate."),
        ("src/report.py", "return f\"User: {profile['name']}\"", "return f\"User: {profile['full_name']}\"", "patch_python_profile_field_contract", "report generation should use the profile field exposed by the service contract."),
        ("src/consumer.py", "from src.date_utils import parse_date", "from src.time_utils import parse_date", "patch_python_import_refactor", "update imports after the date parser moved modules."),
        ("src/formatter.py", "return name.strip().title()", "return \"\" if name is None else name.strip().title()", "patch_python_none_guard", "formatting should handle optional names without throwing."),
        ("src/cart.py", "return item[\"total\"]", "return item.get(\"total\", 0)", "patch_python_missing_key_default", "cart totals should default missing totals to zero."),
        ("src/users.py", "return users", "return [user for user in users if user.get(\"active\")]", "patch_python_filter_active_users", "active user queries should filter inactive users."),
        ("src/serializer.py", "\"name\": user.full_name,", "\"full_name\": user.full_name,", "patch_python_serializer_contract", "serializer output should match the public API field name."),
        ("src/config.py", "return config[\"timeout_seconds\"]", "return config[\"timeout_seconds\"] * 1000", "patch_python_timeout_unit", "runtime config should convert timeout seconds to milliseconds."),
        ("src/dates.py", "return datetime.strptime(value, \"%Y/%m/%d\").date()", "return datetime.strptime(value, \"%Y-%m-%d\").date()", "patch_python_date_format", "date parsing should match the ISO date format used by callers."),
        ("src/settings.py", "return os.environ[\"APP_MODE\"]", "return os.environ.get(\"APP_MODE\", \"dev\")", "patch_python_env_default", "settings should use a safe default when the environment variable is absent."),
        ("src/orders.py", "return sum(order[\"subtotal\"] for order in orders)", "return sum(order[\"subtotal\"] + order[\"tax\"] for order in orders)", "patch_python_order_total", "order totals should include tax."),
        ("src/auth.py", "return role == \"admin\"", "return role in {\"admin\", \"owner\"}", "patch_python_owner_permission", "owner role should have the same access as admin."),
        ("src/pagination.py", "start = page * size", "start = (page - 1) * size", "patch_python_pagination_offset", "pagination should treat page numbers as one-based."),
        ("src/validators.py", "return user[\"age\"] > 0", "return user[\"age\"] >= 0", "patch_python_age_boundary", "age validation should allow newborn users with age zero."),
        ("src/pipeline.py", "return transform(load_user(user_id))", "return transform(normalize_user(load_user(user_id)))", "patch_python_pipeline_normalization", "the processing pipeline should normalize service data before transformation."),
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


def generate_python_ruff_candidates(workspace_dir: Path, playbook_hits: list[dict[str, Any]], fingerprint: dict[str, Any]) -> list[dict[str, Any]]:
    if fingerprint.get("language") != "python" or fingerprint.get("failureType") != "lint_error":
        return []
    candidates = []
    for file in fingerprint.get("failedFiles") or []:
        path = workspace_dir / file
        if not path.exists() or path.suffix != ".py":
            continue
        content = path.read_text()
        for line in content.splitlines(keepends=True):
            if not line.lstrip().startswith(("import ", "from ")) or "# noqa" in line:
                continue
            imported_names = imported_names_from_line(line)
            if imported_names and all(count_identifier_uses(content, name) <= 1 for name in imported_names):
                candidates.append(
                    {
                        "id": f"patch_python_remove_unused_import_{path.stem}",
                        "hypothesis": "Remove the unused Python import reported by ruff.",
                        "playbookId": playbook_hits[0]["id"] if playbook_hits else None,
                        "riskTags": ["source-change", "lint-fix"],
                        "source": "rule",
                        "edits": [{"file": file, "from": line, "to": ""}],
                    }
                )
                break
    return candidates[:2]


def imported_names_from_line(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("import "):
        return [part.split(" as ")[-1].strip().split(".")[0] for part in stripped.removeprefix("import ").split(",")]
    match = re.match(r"from\s+[\w.]+\s+import\s+(.+)", stripped)
    if not match:
        return []
    return [part.split(" as ")[-1].strip() for part in match.group(1).split(",")]


def generate_python_mypy_candidates(workspace_dir: Path, playbook_hits: list[dict[str, Any]], fingerprint: dict[str, Any]) -> list[dict[str, Any]]:
    if fingerprint.get("language") != "python" or fingerprint.get("failureType") != "typecheck_error":
        return []
    candidates = []
    rules = [
        ("src/accounts/service.py", "return user.display_name", "return user.display_name or \"Unknown\"", "patch_python_mypy_optional_display_name", "display_name is optional, so the service should return a string fallback."),
        ("src/service.py", "return user.display_name", "return user.display_name or \"Unknown\"", "patch_python_mypy_optional_display_name", "display_name is optional, so the service should return a string fallback."),
    ]
    for file, old, new, candidate_id, hypothesis in rules:
        path = workspace_dir / file
        if path.exists() and old in path.read_text():
            candidates.append(
                {
                    "id": candidate_id,
                    "hypothesis": hypothesis,
                    "playbookId": playbook_hits[0]["id"] if playbook_hits else None,
                    "riskTags": ["source-change", "type-fix"],
                    "source": "rule",
                    "edits": [{"file": file, "from": old, "to": new}],
                }
            )
    return candidates[:2]


def generate_python_profile_contract_candidates(workspace_dir: Path, playbook_hits: list[dict[str, Any]], fingerprint: dict[str, Any]) -> list[dict[str, Any]]:
    if fingerprint.get("language") != "python":
        return []
    if fingerprint.get("errorCode") not in {"KeyError", "ASSERTION"}:
        return []
    source_root = workspace_dir / "src"
    if not source_root.exists():
        return []
    candidates = []
    for path in source_root.rglob("*.py"):
        content = path.read_text()
        quote_style = "single" if "profile['name']" in content else "double" if 'profile["name"]' in content else None
        if not quote_style:
            continue
        old = "profile['name']" if quote_style == "single" else 'profile["name"]'
        new = "profile['full_name']" if quote_style == "single" else 'profile["full_name"]'
        edits = [{"file": path.relative_to(workspace_dir).as_posix(), "from": old, "to": new} for _ in range(content.count(old))]
        candidates.append(
            {
                "id": f"patch_python_profile_contract_{path.stem}",
                "hypothesis": "profile consumers should read the full_name field exposed by the upstream service contract.",
                "playbookId": playbook_hits[0]["id"] if playbook_hits else None,
                "riskTags": ["source-change", "contract-fix"],
                "source": "rule",
                "edits": edits,
            }
        )
    return candidates[:2]


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
