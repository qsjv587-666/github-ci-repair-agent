from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core.artifacts import json_text
from .github import load_github_context, public_github_context
from .tools.workspace import repo_looks_like_github_slug


def inspect_github(flags: dict[str, Any]) -> dict[str, Any]:
    inspect_id = f"inspect_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
    out_root = Path(flags.get("out") or "artifacts").resolve()
    inspect_dir = out_root / inspect_id
    inspect_dir.mkdir(parents=True, exist_ok=True)

    context = load_github_context(
        pr_url=flags.get("url") or flags.get("pr-url"),
        owner_repo=flags.get("repo") if repo_looks_like_github_slug(flags.get("repo")) else None,
        pull_number=flags.get("pr"),
        run_id=flags.get("run-id"),
        job_id=flags.get("job"),
        token=os.getenv(flags["token-env"]) if flags.get("token-env") else None,
    )
    if not context:
        raise ValueError("inspect needs --url or --repo owner/repo with optional --pr/--run-id/--job")

    public_context = public_github_context(context)
    (inspect_dir / "github-context.json").write_text(json_text(public_context))
    if context.get("rawLog"):
        (inspect_dir / "github-log.txt").write_text(context["rawLog"])
    (inspect_dir / "report.md").write_text(render_inspect_report(public_context))
    return {
        "inspectId": inspect_id,
        "paths": {
            "context": str(inspect_dir / "github-context.json"),
            "log": str(inspect_dir / "github-log.txt"),
            "report": str(inspect_dir / "report.md"),
        },
        "summary": public_context,
    }


def render_inspect_report(context: dict[str, Any]) -> str:
    warnings = "\n".join(f"- {warning}" for warning in context.get("warnings", [])) or "- none"
    changed_files = "\n".join(f"- {file}" for file in context.get("changedFiles", [])[:50]) or "- none"
    return f"""# CIFix GitHub Inspect Report

- Repository: {context.get("owner")}/{context.get("repo")}
- Pull request: {context.get("pullNumber") or "n/a"}
- Pull title: {context.get("pullTitle") or "n/a"}
- Workflow run: {context.get("runId") or "n/a"} {context.get("runConclusion") or ""}
- Job: {context.get("jobId") or "n/a"} {context.get("jobName") or ""}
- Log chars: {context.get("rawLogChars", 0)}
- Head SHA: {context.get("headSha") or "n/a"}
- Base SHA: {context.get("baseSha") or "n/a"}

## Changed Files

{changed_files}

## Warnings

{warnings}
"""
