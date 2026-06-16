from __future__ import annotations

import re
from typing import Any

from ..core.trace import step


def run_failure_triage_agent(*, raw_log: str, command: str, repo_map: dict[str, Any], github_context: dict[str, Any] | None, reproduction: dict[str, Any], trace: list[dict]) -> dict[str, Any]:
    fingerprint = create_failure_fingerprint(raw_log, command, repo_map, github_context, reproduction)
    trace.append(step("FailureTriageAgent", {"logChars": len(raw_log)}, fingerprint))
    return fingerprint


def create_failure_fingerprint(raw_log: str, command: str, repo_map: dict[str, Any], github_context: dict[str, Any] | None, reproduction: dict[str, Any]) -> dict[str, Any]:
    combined = f"{raw_log}\n{reproduction.get('stdout', '')}\n{reproduction.get('stderr', '')}"
    lint_rule_match = re.search(r"\b(?:no-unused-vars|@typescript-eslint/no-unused-vars|no-undef|no-console)\b", combined)
    error_code_match = re.search(r"\bTS\d{4}\b", combined) or re.search(r"\bERR_[A-Z_]+\b", combined) or lint_rule_match
    error_code = error_code_match.group(0) if error_code_match else ("ASSERTION" if "AssertionError" in combined else "UNKNOWN")
    if error_code.startswith("TS"):
        failure_type = "typecheck_error"
    elif lint_rule_match or re.search(r"lint|eslint", combined, re.I):
        failure_type = "lint_error"
    elif re.search(r"AssertionError|not ok|Expected|strictly equal", combined, re.I):
        failure_type = "test_assertion_failure"
    else:
        failure_type = "unknown_failure"
    failed_files = sorted(set(re.findall(r"\b(?:src|test)/[A-Za-z0-9._/-]+\.(?:js|jsx|ts|tsx)\b", combined)))
    language = "typescript" if "typescript" in repo_map.get("languages", []) else "javascript"
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
        "packageManager": repo_map.get("packageManager"),
        "normalizedSignature": f"{language}:{failure_type}:{error_code}:{area}",
    }
