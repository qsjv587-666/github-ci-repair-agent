from __future__ import annotations

import json
import urllib.request
from typing import Any
from urllib.parse import urlencode

GITHUB_API = "https://api.github.com"


def github_json(path: str, token: str | None) -> Any:
    return github_request_json("GET", path, token)


def github_request_json(method: str, path: str, token: str | None, body: dict[str, Any] | None = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        f"{GITHUB_API}{path}",
        headers=_headers(token),
        method=method,
        data=data,
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


def github_text(path: str, token: str | None) -> str:
    request = urllib.request.Request(
        f"{GITHUB_API}{path}",
        headers=_headers(token),
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode(errors="replace")


def load_github_context(*, pr_url: str | None, owner_repo: str | None, pull_number: str | None, run_id: str | None, job_id: str | None, token: str | None) -> dict[str, Any] | None:
    if not pr_url and not owner_repo and not run_id and not job_id:
        return None

    parsed = parse_github_url(pr_url) if pr_url else parse_owner_repo(owner_repo)
    if not parsed:
        raise ValueError("GitHub mode needs --pr-url or --repo owner/repo")

    owner = parsed["owner"]
    repo = parsed["repo"]
    resolved_pull_number = parsed.get("pullNumber") or (int(pull_number) if pull_number else None)
    resolved_run_id = parsed.get("runId") or (int(run_id) if run_id else None)
    resolved_job_id = parsed.get("jobId") or (int(job_id) if job_id else None)
    warnings = []

    pull = github_json(f"/repos/{owner}/{repo}/pulls/{resolved_pull_number}", token) if resolved_pull_number else None
    files = github_json(f"/repos/{owner}/{repo}/pulls/{resolved_pull_number}/files", token) if resolved_pull_number else []
    workflow_run = github_json(f"/repos/{owner}/{repo}/actions/runs/{resolved_run_id}", token) if resolved_run_id else resolve_workflow_run_for_pull(owner, repo, pull, token)
    if workflow_run and not resolved_run_id:
        resolved_run_id = int(workflow_run["id"])
    jobs = github_json(f"/repos/{owner}/{repo}/actions/runs/{resolved_run_id}/jobs", token) if resolved_run_id else {"jobs": []}
    failed_job = resolve_job(jobs.get("jobs", []), resolved_job_id)
    raw_log = ""
    if failed_job:
        try:
            raw_log = github_text(f"/repos/{owner}/{repo}/actions/jobs/{failed_job['id']}/logs", token)
        except Exception as error:
            warnings.append(f"Could not download GitHub job logs: {error}")

    return {
        "owner": owner,
        "repo": repo,
        "pullNumber": resolved_pull_number,
        "pullTitle": (pull or {}).get("title"),
        "pullHtmlUrl": (pull or {}).get("html_url"),
        "cloneUrl": pull.get("head", {}).get("repo", {}).get("clone_url") if pull else f"https://github.com/{owner}/{repo}.git",
        "headRef": (pull or {}).get("head", {}).get("ref"),
        "baseRef": (pull or {}).get("base", {}).get("ref"),
        "headRepoFullName": (pull or {}).get("head", {}).get("repo", {}).get("full_name"),
        "baseRepoFullName": (pull or {}).get("base", {}).get("repo", {}).get("full_name"),
        "headSha": (pull or {}).get("head", {}).get("sha") or (workflow_run or {}).get("head_sha"),
        "baseSha": (pull or {}).get("base", {}).get("sha"),
        "changedFiles": [file["filename"] for file in files],
        "runId": resolved_run_id,
        "runName": (workflow_run or {}).get("name"),
        "runConclusion": (workflow_run or {}).get("conclusion"),
        "runHtmlUrl": (workflow_run or {}).get("html_url"),
        "jobId": (failed_job or {}).get("id") or resolved_job_id,
        "jobName": (failed_job or {}).get("name"),
        "jobConclusion": (failed_job or {}).get("conclusion"),
        "jobHtmlUrl": (failed_job or {}).get("html_url"),
        "rawLog": raw_log,
        "warnings": warnings,
    }


def parse_owner_repo(owner_repo: str | None) -> dict[str, str] | None:
    if not owner_repo or "/" not in owner_repo:
        return None
    owner, repo = owner_repo.split("/", 1)
    return {"owner": owner, "repo": repo.removesuffix(".git")}


def parse_github_url(url: str | None) -> dict[str, Any] | None:
    if not url:
        return None
    from urllib.parse import urlparse

    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if parsed.netloc != "github.com" or len(parts) < 2:
        raise ValueError(f"Only github.com URLs are supported: {url}")
    owner, repo = parts[0], parts[1]
    if len(parts) >= 4 and parts[2] == "pull":
        return {"owner": owner, "repo": repo, "pullNumber": int(parts[3])}
    if len(parts) >= 5 and parts[2] == "actions" and parts[3] == "runs":
        result: dict[str, Any] = {"owner": owner, "repo": repo, "runId": int(parts[4])}
        if len(parts) >= 7 and parts[5] == "job":
            result["jobId"] = int(parts[6])
        return result
    return {"owner": owner, "repo": repo}


def resolve_workflow_run_for_pull(owner: str, repo: str, pull: dict[str, Any] | None, token: str | None) -> dict[str, Any] | None:
    head_sha = (pull or {}).get("head", {}).get("sha")
    if not head_sha:
        return None
    query = urlencode({"head_sha": head_sha, "per_page": 20})
    payload = github_json(f"/repos/{owner}/{repo}/actions/runs?{query}", token)
    runs = payload.get("workflow_runs", []) if isinstance(payload, dict) else []
    if not runs:
        return None
    return (
        next((run for run in runs if run.get("conclusion") == "failure"), None)
        or next((run for run in runs if run.get("status") == "completed"), None)
        or runs[0]
    )


def resolve_job(jobs: list[dict[str, Any]], job_id: int | None) -> dict[str, Any] | None:
    if job_id:
        return next((job for job in jobs if int(job["id"]) == int(job_id)), {"id": job_id})
    return next((job for job in jobs if job.get("conclusion") == "failure"), None) or next((job for job in jobs if job.get("status") == "completed"), None)


def public_github_context(context: dict[str, Any] | None) -> dict[str, Any] | None:
    if not context:
        return None
    return {
        key: value
        for key, value in {
            **context,
            "rawLog": None,
            "rawLogChars": len(context.get("rawLog", "")),
        }.items()
        if key != "rawLog"
    }


def _headers(token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "cifix-agent",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers
