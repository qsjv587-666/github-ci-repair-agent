from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core.artifacts import json_text
from .github import create_pr_comment, list_open_pull_statuses, parse_owner_repo
from .run import run_cifix


def run_watch(flags: dict[str, Any]) -> dict[str, Any]:
    if flags.get("once"):
        return run_watch_once(flags)
    interval_seconds = int(flags.get("interval-seconds") or 300)
    last_result: dict[str, Any] | None = None
    max_cycles = int(flags.get("max-cycles") or 0)
    cycles = 0
    while True:
        last_result = run_watch_once(flags)
        cycles += 1
        summary = last_result["summary"]
        print(
            f"[{summary['checkedAt']}] checked {summary['repo']}: "
            f"open={summary['openPulls']} failed={summary['failedPulls']} "
            f"repairs={summary['repairStarted']} report={last_result['paths']['report']}",
            flush=True,
        )
        if max_cycles and cycles >= max_cycles:
            return last_result
        time.sleep(max(10, interval_seconds))


def run_watch_once(flags: dict[str, Any]) -> dict[str, Any]:
    owner_repo = flags.get("repo")
    parsed = parse_owner_repo(owner_repo)
    if not parsed:
        raise ValueError("watch needs --repo owner/repo")

    watch_id = f"watch_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
    out_root = Path(flags.get("out") or "artifacts").resolve()
    watch_dir = out_root / watch_id
    watch_dir.mkdir(parents=True, exist_ok=True)
    state_path = resolve_state_path(flags, out_root, owner_repo)
    state = load_state(state_path)
    token = os.getenv(flags.get("token-env") or "GITHUB_TOKEN")
    if not token:
        raise ValueError("watch needs a GitHub token; set GITHUB_TOKEN or pass --token-env")

    statuses = list_open_pull_statuses(
        owner_repo=owner_repo,
        token=token,
        limit=int(flags.get("limit") or 20),
    )
    processed = state.setdefault("processed", {})
    events = []
    for status in statuses:
        event = inspect_pull_status(flags=flags, owner_repo=owner_repo, status=status, processed=processed)
        events.append(event)
        if event["action"] == "repair_started":
            repair_result = repair_failed_pull(flags=flags, status=status)
            event["repair"] = repair_result
            processed[event["dedupeKey"]] = {
                "at": datetime.now(timezone.utc).isoformat(),
                "pullNumber": status.get("pullNumber"),
                "pullUrl": status.get("pullUrl"),
                "headSha": status.get("headSha"),
                "latestRun": status.get("latestRun"),
                "resultStatus": repair_result.get("status"),
                "runId": repair_result.get("runId"),
                "githubWrite": repair_result.get("githubWrite"),
                "comment": repair_result.get("sourceComment"),
            }

    state.update(
        {
            "repo": owner_repo,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "statePath": str(state_path),
        }
    )
    save_state(state_path, state)
    summary = summarize_watch(watch_id, owner_repo, statuses, events, state_path)
    (watch_dir / "watch-summary.json").write_text(json_text(summary))
    (watch_dir / "report.md").write_text(render_watch_report(summary))
    return {
        "watchId": watch_id,
        "summary": summary,
        "paths": {
            "summary": str(watch_dir / "watch-summary.json"),
            "report": str(watch_dir / "report.md"),
            "state": str(state_path),
        },
    }


def inspect_pull_status(*, flags: dict[str, Any], owner_repo: str, status: dict[str, Any], processed: dict[str, Any]) -> dict[str, Any]:
    dedupe_key = build_dedupe_key(owner_repo, status)
    event = {
        "pullNumber": status.get("pullNumber"),
        "pullUrl": status.get("pullUrl"),
        "title": status.get("pullTitle"),
        "headSha": status.get("headSha"),
        "ciState": status.get("ciState"),
        "latestRun": status.get("latestRun"),
        "dedupeKey": dedupe_key,
    }
    if status.get("state") != "open":
        return {**event, "action": "ignored", "reason": "pull request is not open"}
    if status.get("ciState") != "failure":
        return {**event, "action": "ignored", "reason": f"ci state is {status.get('ciState')}"}
    if dedupe_key in processed and not flags.get("retry-processed"):
        return {**event, "action": "skipped", "reason": "failure already processed"}
    if flags.get("dry-run"):
        return {**event, "action": "dry_run", "reason": "would trigger repair"}
    return {**event, "action": "repair_started"}


def repair_failed_pull(*, flags: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    repair_flags = build_repair_flags(flags, status)
    try:
        result = run_cifix(repair_flags)
        if flags.get("comment-source-pr"):
            result["sourceComment"] = comment_source_pr(flags=flags, status=status, run_result=result)
        return result
    except Exception as error:
        return {"status": "error", "error": str(error)}


def build_repair_flags(flags: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    passthrough_keys = [
        "token-env",
        "command",
        "setup-command",
        "memory-path",
        "vector-db",
        "embedding-provider",
        "embedding-model",
        "embedding-dimensions",
        "embedding-base-url",
        "use-model",
        "create-pr",
        "draft-pr",
        "auto-merge-repair-pr",
        "require-repair-ci",
        "auto-merge-timeout-seconds",
        "auto-merge-poll-seconds",
        "missing-repair-ci-grace-seconds",
        "auto-merge-max-diff-lines",
        "ssh-key",
        "no-wait-source-ci",
        "sandbox",
        "docker-image",
        "docker-network",
    ]
    repair_flags = {key: flags[key] for key in passthrough_keys if key in flags}
    repair_flags["url"] = status["pullUrl"]
    repair_flags["out"] = flags.get("run-out") or flags.get("out") or "artifacts"
    return repair_flags


def comment_source_pr(*, flags: dict[str, Any], status: dict[str, Any], run_result: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv(flags.get("token-env") or "GITHUB_TOKEN")
    if not token:
        return {"enabled": True, "status": "skipped", "reason": "GitHub token missing"}
    owner = status["owner"]
    repo = status["repo"]
    pull_number = int(status["pullNumber"])
    comment_path_value = run_result.get("paths", {}).get("prComment")
    if not comment_path_value:
        return {"enabled": True, "status": "skipped", "reason": "PR comment draft missing"}
    comment_path = Path(comment_path_value)
    if not comment_path.exists() or not comment_path.is_file():
        return {"enabled": True, "status": "skipped", "reason": "PR comment draft missing"}
    body = comment_path.read_text()
    if run_result.get("githubWrite", {}).get("pullUrl"):
        body += f"\n\nRepair PR: {run_result['githubWrite']['pullUrl']}\n"
    comment = create_pr_comment(owner=owner, repo=repo, pull_number=pull_number, body=body, token=token)
    return {
        "enabled": True,
        "status": "commented",
        "commentUrl": comment.get("html_url"),
        "commentId": comment.get("id"),
    }


def build_dedupe_key(owner_repo: str, status: dict[str, Any]) -> str:
    latest_run = status.get("latestRun") or {}
    return ":".join(
        [
            owner_repo,
            f"pr-{status.get('pullNumber')}",
            str(status.get("headSha") or "no-sha"),
            f"run-{latest_run.get('id') or 'no-run'}",
        ]
    )


def resolve_state_path(flags: dict[str, Any], out_root: Path, owner_repo: str) -> Path:
    if flags.get("state-path"):
        return Path(flags["state-path"]).expanduser().resolve()
    safe_repo = owner_repo.replace("/", "__")
    return out_root / "watch-state" / f"{safe_repo}.json"


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"processed": {}}
    try:
        import json

        return json.loads(path.read_text())
    except Exception:
        return {"processed": {}}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_text(state))


def summarize_watch(watch_id: str, owner_repo: str, statuses: list[dict[str, Any]], events: list[dict[str, Any]], state_path: Path) -> dict[str, Any]:
    return {
        "watchId": watch_id,
        "repo": owner_repo,
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "openPulls": len(statuses),
        "failedPulls": len([status for status in statuses if status.get("ciState") == "failure"]),
        "repairStarted": len([event for event in events if event.get("action") == "repair_started"]),
        "dryRun": len([event for event in events if event.get("action") == "dry_run"]),
        "skipped": len([event for event in events if event.get("action") == "skipped"]),
        "statePath": str(state_path),
        "events": events,
    }


def render_watch_report(summary: dict[str, Any]) -> str:
    events = "\n".join(render_watch_event(event) for event in summary.get("events", [])) or "- none"
    return f"""# CIFix Watch Report

- Repository: {summary.get("repo")}
- Open PRs checked: {summary.get("openPulls")}
- Failed PRs: {summary.get("failedPulls")}
- Repairs started: {summary.get("repairStarted")}
- Dry-run matches: {summary.get("dryRun")}
- Already processed skips: {summary.get("skipped")}
- State file: `{summary.get("statePath")}`

## Events

{events}
"""


def render_watch_event(event: dict[str, Any]) -> str:
    repair = event.get("repair") or {}
    run_id = repair.get("runId") or "n/a"
    repair_status = repair.get("status") or "n/a"
    reason = event.get("reason") or ""
    return (
        f"- PR #{event.get('pullNumber')} `{event.get('ciState')}` "
        f"`{event.get('action')}` {reason} "
        f"run `{run_id}` result `{repair_status}`"
    )
