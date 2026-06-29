from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..core.trace import step
from ..model import generate_model_triage, model_config_from_env


def run_failure_triage_agent(*, raw_log: str, command: str, repo_map: dict[str, Any], github_context: dict[str, Any] | None, reproduction: dict[str, Any], trace: list[dict], flags: dict[str, Any] | None = None, workspace_dir: Path | None = None) -> dict[str, Any]:
    fingerprint = create_failure_fingerprint(raw_log, command, repo_map, github_context, reproduction)
    trace.append(step("FailureTriageAgent", {"logChars": len(raw_log)}, fingerprint))
    model_config = model_config_from_env(flags or {})
    model_result = safe_generate_model_triage(
        workspace_dir=workspace_dir,
        fingerprint=fingerprint,
        raw_log=raw_log,
        reproduction=reproduction,
        repo_map=repo_map,
        model_config=model_config,
    )
    if model_result.get("triage"):
        fingerprint["llmTriage"] = model_result["triage"]
    trace.append(step("LLMTriageAgent", {"enabled": model_config["enabled"], "provider": model_config["provider"], "model": model_config["model"]}, model_result["diagnosis"]))
    return fingerprint


def safe_generate_model_triage(**kwargs: Any) -> dict[str, Any]:
    try:
        return generate_model_triage(**kwargs)
    except Exception as error:
        return {"triage": None, "diagnosis": {"error": str(error), "fallback": "rule fingerprint"}}


def create_failure_fingerprint(raw_log: str, command: str, repo_map: dict[str, Any], github_context: dict[str, Any] | None, reproduction: dict[str, Any]) -> dict[str, Any]:
    combined = f"{raw_log}\n{reproduction.get('stdout', '')}\n{reproduction.get('stderr', '')}"
    lint_rule_match = re.search(r"\b(?:no-unused-vars|@typescript-eslint/no-unused-vars|no-undef|no-console|F401|F841|E501|I001)\b", combined)
    mypy_match = re.search(r"\berror:\s+.+\s+\[[a-z0-9-]+\]", combined, re.I)
    python_error_match = re.search(r"\b(?:ModuleNotFoundError|ImportError|KeyError|TypeError|ValueError|AttributeError)\b", combined)
    error_code_match = re.search(r"\bTS\d{4}\b", combined) or re.search(r"\bERR_[A-Z_]+\b", combined) or lint_rule_match or python_error_match
    error_code = error_code_match.group(0) if error_code_match else ("ASSERTION" if "AssertionError" in combined else "UNKNOWN")
    if error_code.startswith("TS"):
        failure_type = "typecheck_error"
    elif mypy_match:
        failure_type = "typecheck_error"
        error_code = mypy_error_code(mypy_match.group(0))
    elif error_code in {"ModuleNotFoundError", "ImportError"}:
        failure_type = "import_error"
    elif error_code in {"KeyError", "TypeError", "ValueError", "AttributeError"}:
        failure_type = "runtime_error"
    elif lint_rule_match or re.search(r"lint|eslint", combined, re.I):
        failure_type = "lint_error"
    elif re.search(r"AssertionError|not ok|Expected|strictly equal", combined, re.I):
        failure_type = "test_assertion_failure"
    else:
        failure_type = "unknown_failure"
    failed_files = sorted(
        set(
            re.findall(r"\b(?:src|test|tests)/[A-Za-z0-9._/-]+\.(?:js|jsx|ts|tsx|py)\b", combined)
            + re.findall(r"\btest_[A-Za-z0-9._/-]+\.py\b", combined)
            + re.findall(r"\b[A-Za-z0-9._/-]+\.py(?=:\d+)", combined)
        )
    )
    languages = repo_map.get("languages", [])
    language = "python" if "python" in languages else "typescript" if "typescript" in languages else "javascript"
    package_manager = infer_package_manager(command, repo_map)
    area = "ui_state" if any("button" in file for file in failed_files) else "general"
    return {
        "platform": "github" if github_context else "local",
        "project": f"{github_context['owner']}/{github_context['repo']}" if github_context else "local-fixture",
        "pullNumber": (github_context or {}).get("pullNumber"),
        "runId": (github_context or {}).get("runId"),
        "jobId": (github_context or {}).get("jobId"),
        "failureType": failure_type,
        "errorCode": error_code,
        "failedFiles": failed_files,
        "changedFiles": (github_context or {}).get("changedFiles") or failed_files,
        "command": command,
        "language": language,
        "packageManager": package_manager,
        "normalizedSignature": f"{language}:{failure_type}:{error_code}:{area}",
    }


def infer_package_manager(command: str, repo_map: dict[str, Any]) -> str | None:
    if re.match(r"^python(?:3)?(?:\s|$)", command.strip()):
        return "python"
    if re.match(r"^(pytest|ruff|mypy)(?:\s|$)", command.strip()):
        return "python"
    return repo_map.get("packageManager")


def mypy_error_code(line: str) -> str:
    match = re.search(r"\[([a-z0-9-]+)\]", line, re.I)
    return f"mypy:{match.group(1)}" if match else "mypy:error"
