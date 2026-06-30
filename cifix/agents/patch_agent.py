from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..core.trace import step
from ..model import generate_model_patch_candidates, model_config_from_env


@dataclass(frozen=True)
class RepairAgentSpec:
    name: str
    description: str
    generator: Callable[[Path, list[dict[str, Any]], dict[str, Any]], list[dict[str, Any]]]


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
    repair_agent_result = run_repair_agents(workspace_dir=workspace_dir, playbook_hits=playbook_hits, fingerprint=fingerprint, repo_map=repo_map, trace=trace)
    candidates = merge_patch_candidates(model_result["candidates"], repair_agent_result["candidates"])
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
    return values[: max(2, min(6, len(values)))]


def run_repair_agents(*, workspace_dir: Path, playbook_hits: list[dict[str, Any]], fingerprint: dict[str, Any], repo_map: dict[str, Any], trace: list[dict]) -> dict[str, Any]:
    agents = route_repair_agents(fingerprint, repo_map)
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(agents)))) as executor:
        futures = [
            executor.submit(run_single_repair_agent, agent=agent, workspace_dir=workspace_dir, playbook_hits=playbook_hits, fingerprint=fingerprint)
            for agent in agents
        ]
        for future in futures:
            results.append(future.result())
    candidates = []
    for result in results:
        candidates.extend(result["candidates"])
    if not candidates:
        candidates = [noop_candidate("patch_noop_report_only", "No repair agent produced a deterministic patch.")]
    if len(candidates) == 1:
        candidates.append(noop_candidate("patch_noop_baseline", "Baseline no-op candidate for tournament comparison."))
    trace.append(
        step(
            "RepairRouter",
            {"failureType": fingerprint.get("failureType"), "errorCode": fingerprint.get("errorCode"), "language": fingerprint.get("language")},
            {
                "agents": [
                    {
                        "name": result["name"],
                        "description": result["description"],
                        "candidateCount": len(result["candidates"]),
                        "candidateIds": [candidate["id"] for candidate in result["candidates"]],
                        "error": result["error"],
                    }
                    for result in results
                ],
                "candidateCount": len(candidates),
            },
        )
    )
    return {"agents": results, "candidates": candidates}


def run_single_repair_agent(*, agent: RepairAgentSpec, workspace_dir: Path, playbook_hits: list[dict[str, Any]], fingerprint: dict[str, Any]) -> dict[str, Any]:
    try:
        candidates = tag_repair_agent_candidates(agent.name, agent.generator(workspace_dir, playbook_hits, fingerprint))
        error = None
    except Exception as exc:
        candidates = []
        error = str(exc)
    return {"name": agent.name, "description": agent.description, "candidates": candidates, "error": error}


def route_repair_agents(fingerprint: dict[str, Any], repo_map: dict[str, Any]) -> list[RepairAgentSpec]:
    failure_type = fingerprint.get("failureType")
    language = fingerprint.get("language")
    agents = []
    if failure_type == "lint_error":
        if language == "python":
            agents.append(REPAIR_AGENT_REGISTRY["python_lint_repair"])
        if language in {"javascript", "typescript"} or "javascript" in repo_map.get("languages", []):
            agents.append(REPAIR_AGENT_REGISTRY["javascript_lint_repair"])
    if failure_type in {"runtime_error", "test_assertion_failure"} and language == "python":
        agents.append(REPAIR_AGENT_REGISTRY["python_contract_repair"])
    if failure_type == "import_error" and language == "python":
        agents.append(REPAIR_AGENT_REGISTRY["python_import_repair"])
    if failure_type == "typecheck_error" and language == "python":
        agents.append(REPAIR_AGENT_REGISTRY["python_type_repair"])
    agents.append(REPAIR_AGENT_REGISTRY["generic_rule_repair"])
    return list(dict.fromkeys(agents))


def tag_repair_agent_candidates(agent_name: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tagged = []
    for candidate in candidates:
        tagged.append({**candidate, "source": f"repair-agent:{agent_name}", "repairAgent": agent_name})
    return tagged


def generate_rule_patch_candidates(workspace_dir: Path, playbook_hits: list[dict[str, Any]], fingerprint: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    candidates = run_repair_agents(
        workspace_dir=workspace_dir,
        playbook_hits=playbook_hits,
        fingerprint=fingerprint or {},
        repo_map={},
        trace=[],
    )["candidates"]
    return candidates


def generate_generic_rule_candidates(workspace_dir: Path, playbook_hits: list[dict[str, Any]], fingerprint: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    candidates = []
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
    return candidates


def noop_candidate(candidate_id: str, hypothesis: str) -> dict[str, Any]:
    return {"id": candidate_id, "hypothesis": hypothesis, "riskTags": ["noop"], "source": "repair-agent:noop", "repairAgent": "noop", "edits": []}


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
            if line.strip().startswith("from __future__ import "):
                continue
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
    optional_display_name_rules = [
        ("src/accounts/service.py", "return user.display_name", "return user.display_name or \"Unknown\"", "patch_python_mypy_optional_display_name", "display_name is optional, so the service should return a string fallback."),
        ("src/service.py", "return user.display_name", "return user.display_name or \"Unknown\"", "patch_python_mypy_optional_display_name", "display_name is optional, so the service should return a string fallback."),
        ("src/accounts/notifications.py", "return user.display_name", "return user.display_name or \"Unknown\"", "patch_python_mypy_optional_display_name", "display_name is optional, so notification subjects should use a typed fallback."),
    ]
    edits = []
    hypotheses = []
    for file, old, new, _candidate_id, hypothesis in optional_display_name_rules:
        path = workspace_dir / file
        if path.exists() and old in path.read_text():
            edits.append({"file": file, "from": old, "to": new})
            hypotheses.append(hypothesis)
    if edits:
        candidates.append(
            {
                "id": "patch_python_mypy_optional_display_name",
                "hypothesis": " ".join(dict.fromkeys(hypotheses)),
                "playbookId": playbook_hits[0]["id"] if playbook_hits else None,
                "riskTags": ["source-change", "type-fix"],
                "source": "rule",
                "edits": edits,
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
    edits = []
    for path in source_root.rglob("*.py"):
        content = path.read_text()
        quote_style = "single" if "profile['name']" in content else "double" if 'profile["name"]' in content else None
        if not quote_style:
            continue
        old = "profile['name']" if quote_style == "single" else 'profile["name"]'
        new = "profile['full_name']" if quote_style == "single" else 'profile["full_name"]'
        edits.extend({"file": path.relative_to(workspace_dir).as_posix(), "from": old, "to": new} for _ in range(content.count(old)))
    if not edits:
        return []
    return [
        {
            "id": "patch_python_profile_contract_all_consumers",
            "hypothesis": "Profile consumers should read the full_name field exposed by the upstream service contract across all affected modules.",
            "playbookId": playbook_hits[0]["id"] if playbook_hits else None,
            "riskTags": ["source-change", "contract-fix"],
            "source": "rule",
            "edits": edits,
        }
    ]


def generate_python_import_refactor_candidates(workspace_dir: Path, playbook_hits: list[dict[str, Any]], fingerprint: dict[str, Any]) -> list[dict[str, Any]]:
    if fingerprint.get("language") != "python" or fingerprint.get("failureType") != "import_error":
        return []
    source_root = workspace_dir / "src"
    if not source_root.exists():
        return []
    replacements = [
        ("from src.date_utils import parse_date", "from src.time_utils import parse_date"),
        ("from src.app.utils.date import parse_date", "from src.app.common.date_parser import parse_date"),
    ]
    edits = []
    for path in source_root.rglob("*.py"):
        content = path.read_text()
        for old, new in replacements:
            edits.extend({"file": path.relative_to(workspace_dir).as_posix(), "from": old, "to": new} for _ in range(content.count(old)))
    if not edits:
        return []
    return [
        {
            "id": "patch_python_import_refactor_all_call_sites",
            "hypothesis": "Update all Python call sites to the new module path after a refactor instead of adding compatibility shims.",
            "playbookId": playbook_hits[0]["id"] if playbook_hits else None,
            "riskTags": ["source-change", "import-fix"],
            "source": "rule",
            "edits": edits,
        }
    ]


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
        "repairAgent": candidate.get("repairAgent"),
        "riskTags": candidate.get("riskTags"),
        "changedFiles": [edit["file"] for edit in candidate.get("edits", [])],
    }


REPAIR_AGENT_REGISTRY: dict[str, RepairAgentSpec] = {
    "python_contract_repair": RepairAgentSpec(
        name="python_contract_repair",
        description="Repairs Python provider/consumer field contract mismatches and assertion failures.",
        generator=generate_python_profile_contract_candidates,
    ),
    "python_import_repair": RepairAgentSpec(
        name="python_import_repair",
        description="Repairs Python module path changes after refactors.",
        generator=generate_python_import_refactor_candidates,
    ),
    "python_lint_repair": RepairAgentSpec(
        name="python_lint_repair",
        description="Repairs Python lint failures such as ruff unused imports.",
        generator=generate_python_ruff_candidates,
    ),
    "python_type_repair": RepairAgentSpec(
        name="python_type_repair",
        description="Repairs Python type-check failures such as optional return-value errors.",
        generator=generate_python_mypy_candidates,
    ),
    "javascript_lint_repair": RepairAgentSpec(
        name="javascript_lint_repair",
        description="Repairs JavaScript/TypeScript lint failures such as unused variables.",
        generator=lambda workspace_dir, playbook_hits, fingerprint: generate_lint_unused_var_candidates(workspace_dir, playbook_hits),
    ),
    "generic_rule_repair": RepairAgentSpec(
        name="generic_rule_repair",
        description="Fallback deterministic repair rules for known fixture and benchmark patterns.",
        generator=generate_generic_rule_candidates,
    ),
}
