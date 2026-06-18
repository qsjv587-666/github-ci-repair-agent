from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core.artifacts import json_text
from .github import load_pull_status
from .tools.workspace import repo_looks_like_github_slug


def inspect_status(flags: dict[str, Any]) -> dict[str, Any]:
    status_id = f"status_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
    out_root = Path(flags.get("out") or "artifacts").resolve()
    status_dir = out_root / status_id
    status_dir.mkdir(parents=True, exist_ok=True)

    status = load_pull_status(
        pr_url=flags.get("url") or flags.get("pr-url"),
        owner_repo=flags.get("repo") if repo_looks_like_github_slug(flags.get("repo")) else None,
        pull_number=flags.get("pr"),
        token=os.getenv(flags.get("token-env") or "GITHUB_TOKEN"),
    )
    (status_dir / "status.json").write_text(json_text(status))
    (status_dir / "report.md").write_text(render_status_report(status))
    return {
        "statusId": status_id,
        "summary": status,
        "paths": {
            "status": str(status_dir / "status.json"),
            "report": str(status_dir / "report.md"),
        },
    }


def render_status_report(status: dict[str, Any]) -> str:
    runs = "\n".join(render_run(run) for run in status.get("runs", [])) or "- none"
    return f"""# CIFix GitHub Status Report

- Repository: {status.get("owner")}/{status.get("repo")}
- Pull request: #{status.get("pullNumber")} {status.get("pullTitle") or ""}
- URL: {status.get("pullUrl") or "n/a"}
- Branches: `{status.get("headRef")}` -> `{status.get("baseRef")}`
- Head SHA: `{status.get("headSha") or "n/a"}`
- PR state: {status.get("state")}
- Mergeable: {status.get("mergeable")}
- CI state: {status.get("ciState")}

## Workflow Runs

{runs}
"""


def render_run(run: dict[str, Any] | None) -> str:
    if not run:
        return "- none"
    return f"- [{run.get('name') or run.get('id')}]({run.get('htmlUrl')}) `{run.get('status')}` / `{run.get('conclusion')}` at `{run.get('updatedAt')}`"
