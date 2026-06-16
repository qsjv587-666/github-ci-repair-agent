from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..core.trace import step
from ..github import github_request_json


def run_github_writer_agent(
    *,
    flags: dict[str, Any],
    workspace_dir: Path,
    github_context: dict[str, Any] | None,
    selected: dict[str, Any] | None,
    fingerprint: dict[str, Any],
    command: str,
    run_id: str,
    trace: list[dict[str, Any]],
) -> dict[str, Any]:
    if not flags.get("create-pr"):
        result = {"enabled": False, "status": "skipped", "reason": "--create-pr not set"}
        trace.append(step("GitHubWriterAgent", {"enabled": False}, result))
        return result
    if not github_context:
        result = {"enabled": True, "status": "skipped", "reason": "GitHub context is required"}
        trace.append(step("GitHubWriterAgent", {"enabled": True}, result))
        return result
    if not selected or not selected.get("verification", {}).get("passed"):
        result = {"enabled": True, "status": "skipped", "reason": "No verified patch selected"}
        trace.append(step("GitHubWriterAgent", {"enabled": True}, result))
        return result

    owner = github_context["owner"]
    repo = github_context["repo"]
    pull_number = github_context.get("pullNumber")
    base_ref = github_context.get("headRef") or github_context.get("baseRef") or "main"
    branch = build_repair_branch(pull_number=pull_number, run_id=run_id)
    ssh_key = expand_optional_path(flags.get("ssh-key") or os.getenv("CIFIX_GIT_SSH_KEY") or default_ssh_key())
    token = os.getenv(flags.get("token-env") or "GITHUB_TOKEN")
    body = render_repair_pr_body(
        github_context=github_context,
        selected=selected,
        fingerprint=fingerprint,
        command=command,
        run_id=run_id,
    )

    try:
        diff_before_branch = run_git(["diff", "--stat"], workspace_dir).stdout.strip()
        if not diff_before_branch:
            result = {"enabled": True, "status": "skipped", "reason": "Selected patch left no working tree changes"}
            trace.append(step("GitHubWriterAgent", {"enabled": True}, result))
            return result

        run_git(["checkout", "-B", branch], workspace_dir)
        run_git(["add", "."], workspace_dir)
        run_git(["commit", "-m", commit_message(pull_number)], workspace_dir)
        run_git(["remote", "set-url", "--push", "origin", f"git@github.com:{owner}/{repo}.git"], workspace_dir)
        run_git(["push", "-u", "origin", branch, "--force-with-lease"], workspace_dir, ssh_key=ssh_key)
    except subprocess.CalledProcessError as error:
        result = {
            "enabled": True,
            "status": "failed",
            "stage": "git",
            "branch": branch,
            "baseRef": base_ref,
            "error": git_error(error),
        }
        trace.append(step("GitHubWriterAgent", {"enabled": True, "branch": branch}, result))
        return result

    compare_url = compare_url_for(owner, repo, base_ref, branch)
    result: dict[str, Any] = {
        "enabled": True,
        "status": "pushed",
        "branch": branch,
        "baseRef": base_ref,
        "compareUrl": compare_url,
        "commitMessage": commit_message(pull_number),
    }

    if not token:
        result.update(
            {
                "status": "pushed_no_pr",
                "reason": "GitHub token missing; set GITHUB_TOKEN or pass --token-env",
            }
        )
        trace.append(step("GitHubWriterAgent", {"enabled": True, "branch": branch}, result))
        return result

    try:
        pr = github_request_json(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            token,
            {
                "title": pr_title(pull_number),
                "head": branch,
                "base": base_ref,
                "body": body,
                "draft": bool(flags.get("draft-pr")),
            },
        )
        result.update(
            {
                "status": "pr_created",
                "pullNumber": pr.get("number"),
                "pullUrl": pr.get("html_url"),
                "draft": bool(flags.get("draft-pr")),
            }
        )
    except Exception as error:
        result.update(
            {
                "status": "pushed_pr_failed",
                "reason": str(error),
            }
        )

    trace.append(step("GitHubWriterAgent", {"enabled": True, "branch": branch}, result))
    return result


def run_git(args: list[str], cwd: Path, *, ssh_key: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if ssh_key:
        env["GIT_SSH_COMMAND"] = f"ssh -i {ssh_key} -o IdentitiesOnly=yes"
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )


def build_repair_branch(*, pull_number: int | None, run_id: str) -> str:
    suffix = re.sub(r"[^a-zA-Z0-9._-]+", "-", run_id)[-18:].strip("-")
    if pull_number:
        return f"ci-repair/pr-{pull_number}-{suffix}"
    return f"ci-repair/{suffix}"


def render_repair_pr_body(
    *,
    github_context: dict[str, Any],
    selected: dict[str, Any],
    fingerprint: dict[str, Any],
    command: str,
    run_id: str,
) -> str:
    source_pr = github_context.get("pullHtmlUrl") or f"https://github.com/{github_context['owner']}/{github_context['repo']}/pull/{github_context.get('pullNumber')}"
    risk_tags = ", ".join(selected.get("riskTags", [])) or "none"
    return f"""## CI Repair Summary

Source PR: {source_pr}
Workflow run: {github_context.get("runHtmlUrl") or github_context.get("runId") or "n/a"}
Job: {github_context.get("jobHtmlUrl") or github_context.get("jobId") or "n/a"}

## Diagnosis

- Failure type: `{fingerprint.get("failureType")}`
- Error code: `{fingerprint.get("errorCode")}`
- Command: `{command}`
- Selected patch: `{selected.get("id")}`
- Risk tags: {risk_tags}

## Verification

- Result: {"passed" if selected.get("verification", {}).get("passed") else "needs attention"}
- Exit code: {selected.get("verification", {}).get("exitCode")}

## Traceability

Generated by CI repair agent run `{run_id}` after reproducing the failure, testing candidate patches, and selecting the lowest-risk verified fix.
"""


def pr_title(pull_number: int | None) -> str:
    if pull_number:
        return f"Fix CI failure for PR #{pull_number}"
    return "Fix CI failure"


def commit_message(pull_number: int | None) -> str:
    if pull_number:
        return f"Fix CI failure for PR #{pull_number}"
    return "Fix CI failure"


def compare_url_for(owner: str, repo: str, base_ref: str, branch: str) -> str:
    return f"https://github.com/{owner}/{repo}/compare/{quote(base_ref, safe='')}...{quote(branch, safe='')}?expand=1"


def expand_optional_path(path: str | None) -> Path | None:
    if not path:
        return None
    expanded = Path(path).expanduser()
    return expanded if expanded.exists() else None


def default_ssh_key() -> str | None:
    candidate = Path.home() / ".ssh" / "github_ci_repair_agent"
    return str(candidate) if candidate.exists() else None


def git_error(error: subprocess.CalledProcessError) -> str:
    stderr = (error.stderr or "").strip()
    stdout = (error.stdout or "").strip()
    return stderr or stdout or str(error)
