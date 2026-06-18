from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..core.trace import step
from ..github import github_request_json, load_pull_status


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
        try:
            result["autoMerge"] = maybe_auto_merge_repair_pr(
                flags=flags,
                owner=owner,
                repo=repo,
                token=token,
                repair_pull_number=pr.get("number"),
                repair_base_ref=base_ref,
                source_pull_number=pull_number,
                source_head_ref=github_context.get("headRef"),
                source_base_ref=github_context.get("baseRef"),
                source_head_sha=github_context.get("headSha"),
                selected=selected,
            )
        except Exception as error:
            result["autoMerge"] = {"enabled": bool(flags.get("auto-merge-repair-pr")), "status": "failed", "reason": str(error)}
    except Exception as error:
        result.update(
            {
                "status": "pushed_pr_failed",
                "reason": str(error),
            }
        )

    trace.append(step("GitHubWriterAgent", {"enabled": True, "branch": branch}, result))
    return result


def maybe_auto_merge_repair_pr(
    *,
    flags: dict[str, Any],
    owner: str,
    repo: str,
    token: str,
    repair_pull_number: int | None,
    repair_base_ref: str,
    source_pull_number: int | None,
    source_head_ref: str | None,
    source_base_ref: str | None,
    source_head_sha: str | None,
    selected: dict[str, Any],
) -> dict[str, Any]:
    if not flags.get("auto-merge-repair-pr"):
        return {"enabled": False, "status": "skipped", "reason": "--auto-merge-repair-pr not set"}
    if not repair_pull_number:
        return {"enabled": True, "status": "blocked", "reason": "repair PR was not created"}
    gate_error = auto_merge_gate_error(
        flags=flags,
        repair_base_ref=repair_base_ref,
        source_head_ref=source_head_ref,
        source_base_ref=source_base_ref,
        selected=selected,
    )
    if gate_error:
        return {"enabled": True, "status": "blocked", "reason": gate_error}

    wait_timeout = int(flags.get("auto-merge-timeout-seconds") or 180)
    wait_interval = int(flags.get("auto-merge-poll-seconds") or 10)
    repair_status = wait_for_pull_ci_success(
        owner=owner,
        repo=repo,
        pull_number=int(repair_pull_number),
        token=token,
        timeout_seconds=wait_timeout,
        poll_seconds=wait_interval,
        missing_grace_seconds=None if flags.get("require-repair-ci") else int(flags.get("missing-repair-ci-grace-seconds") or 30),
    )
    repair_ci_missing = repair_status.get("ciState") == "missing"
    if repair_status.get("mergeable") is False:
        return {"enabled": True, "status": "blocked", "reason": "repair PR is not mergeable", "repairStatus": repair_status}
    if repair_status.get("ciState") != "success" and not (repair_ci_missing and not flags.get("require-repair-ci")):
        return {"enabled": True, "status": "blocked", "reason": "repair PR CI did not pass", "repairStatus": repair_status}

    merge_result = github_request_json(
        "PUT",
        f"/repos/{owner}/{repo}/pulls/{repair_pull_number}/merge",
        token,
        {
            "commit_title": f"Merge CI repair for PR #{source_pull_number or repair_pull_number}",
            "commit_message": "Apply verified CI repair generated by github-ci-repair-agent.",
            "merge_method": "merge",
        },
    )
    result: dict[str, Any] = {
        "enabled": True,
        "status": "merged" if merge_result.get("merged") else "merge_failed",
        "mergeSha": merge_result.get("sha"),
        "message": merge_result.get("message"),
        "repairStatus": repair_status,
    }
    if repair_ci_missing:
        result["repairCiFallback"] = "missing repair PR checks; merged using local verification, low-risk gates, and source PR CI follow-up"

    if source_pull_number and merge_result.get("merged") and not flags.get("no-wait-source-ci"):
        result["sourceStatus"] = wait_for_pull_ci_success(
            owner=owner,
            repo=repo,
            pull_number=int(source_pull_number),
            token=token,
            timeout_seconds=wait_timeout,
            poll_seconds=wait_interval,
            not_head_sha=source_head_sha,
        )
    return result


def auto_merge_gate_error(*, flags: dict[str, Any], repair_base_ref: str, source_head_ref: str | None, source_base_ref: str | None, selected: dict[str, Any]) -> str | None:
    if flags.get("draft-pr"):
        return "draft repair PRs are not auto-merged"
    if not source_head_ref:
        return "source PR head branch is unknown"
    if repair_base_ref != source_head_ref:
        return "repair PR base must be the source PR head branch"
    if source_base_ref and repair_base_ref == source_base_ref:
        return "repair PR base must not be the source PR base branch"
    risk_tags = set(selected.get("riskTags", []))
    blocked_tags = {"test-change", "possible-overfit", "noop"}
    if risk_tags & blocked_tags:
        return f"selected patch has blocked risk tags: {', '.join(sorted(risk_tags & blocked_tags))}"
    changed_files = [edit.get("file", "") for edit in selected.get("edits", [])]
    if any(file.startswith("test/") or "/test/" in file or file.endswith((".test.js", ".test.ts", ".spec.js", ".spec.ts")) for file in changed_files):
        return "selected patch changes test files"
    max_diff_lines = int(flags.get("auto-merge-max-diff-lines") or 30)
    diff_lines = count_changed_diff_lines(selected.get("diff", ""))
    if diff_lines > max_diff_lines:
        return f"selected patch diff is too large: {diff_lines} changed lines > {max_diff_lines}"
    return None


def wait_for_pull_ci_success(*, owner: str, repo: str, pull_number: int, token: str, timeout_seconds: int, poll_seconds: int, not_head_sha: str | None = None, missing_grace_seconds: int | None = None) -> dict[str, Any]:
    deadline = time.time() + max(0, timeout_seconds)
    last_status: dict[str, Any] = {}
    missing_since: float | None = None
    while True:
        status = load_pull_status(pr_url=None, owner_repo=f"{owner}/{repo}", pull_number=str(pull_number), token=token)
        last_status = status
        if not_head_sha and status.get("headSha") == not_head_sha:
            pass
        elif status.get("ciState") == "success":
            return status
        elif status.get("ciState") == "failure":
            return status
        if status.get("ciState") == "missing" and missing_grace_seconds is not None:
            missing_since = missing_since or time.time()
            if time.time() - missing_since >= max(0, missing_grace_seconds):
                return {**last_status, "timedOut": True}
        else:
            missing_since = None
        if time.time() >= deadline:
            return {**last_status, "ciState": last_status.get("ciState") or "timeout", "timedOut": True}
        time.sleep(max(1, poll_seconds))


def count_changed_diff_lines(diff: str) -> int:
    return len([line for line in diff.splitlines() if (line.startswith("+") and not line.startswith("+++")) or (line.startswith("-") and not line.startswith("---"))])


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
