from __future__ import annotations

import shutil
from pathlib import Path

from .command import git


def restore_baseline(cwd: Path) -> None:
    git(["checkout", "--", "."], cwd=cwd)
    status = git(["status", "--porcelain"], cwd=cwd).stdout
    for line in status.splitlines():
        if line.startswith("?? "):
            target = cwd / line[3:]
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()


def apply_candidate(workspace_dir: Path, candidate: dict) -> None:
    for edit in candidate.get("edits", []):
        file_path = workspace_dir / edit["file"]
        content = file_path.read_text()
        if edit["from"] not in content:
            raise ValueError(f"Candidate {candidate['id']} could not find target text in {edit['file']}")
        file_path.write_text(content.replace(edit["from"], edit["to"], 1))


def git_diff(cwd: Path) -> str:
    return git(["diff", "--", "."], cwd=cwd).stdout

