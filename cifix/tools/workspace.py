from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .command import git
from ..core.trace import step
from ..github import public_github_context


def repo_looks_like_github_slug(repo: str | None) -> bool:
    if not repo:
        return False
    return not Path(repo).resolve().exists() and "/" in repo and " " not in repo


def prepare_workspace(flags: dict[str, Any], github_context: dict[str, Any] | None, workspace_dir: Path, trace: list[dict[str, Any]]) -> Path:
    repo = flags.get("repo")
    repo_is_github_slug = repo_looks_like_github_slug(repo)
    local_repo_path = Path(repo).resolve() if repo else None

    if repo and not repo_is_github_slug:
        source_repo = local_repo_path
    elif github_context:
        source_repo = clone_github_repo(github_context, workspace_dir, trace)
    else:
        source_repo = None

    if not source_repo:
        raise ValueError("Expected --repo <path> for local mode, or --repo owner/repo / --pr-url for GitHub mode")

    if repo and not repo_is_github_slug:
        copy_repo(source_repo, workspace_dir)

    if repo and not repo_is_github_slug:
        init_git(workspace_dir)
    else:
        ensure_git_identity(workspace_dir)
    return workspace_dir


def clone_github_repo(context: dict[str, Any], workspace_dir: Path, trace: list[dict[str, Any]]) -> Path:
    workspace_dir.parent.mkdir(parents=True, exist_ok=True)
    git(["clone", "--depth", "1", context["cloneUrl"], str(workspace_dir)])
    if context.get("headSha"):
        try:
            git(["fetch", "--depth", "1", "origin", context["headSha"]], cwd=workspace_dir)
            git(["checkout", "FETCH_HEAD"], cwd=workspace_dir)
        except Exception:
            git(["checkout", context["headSha"]], cwd=workspace_dir)
    trace.append(step("GitHub", {"repo": f"{context['owner']}/{context['repo']}"}, public_github_context(context)))
    return workspace_dir


def copy_repo(source: Path, destination: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(f"Repo path does not exist: {source}")
    if destination.exists():
        shutil.rmtree(destination)
    ignore = shutil.ignore_patterns("node_modules", ".git")
    shutil.copytree(source, destination, ignore=ignore)


def init_git(cwd: Path) -> None:
    git(["init"], cwd=cwd)
    ensure_git_identity(cwd)
    git(["add", "."], cwd=cwd)
    status = git(["status", "--porcelain"], cwd=cwd).stdout
    if status.strip():
        git(["commit", "-m", "baseline"], cwd=cwd)


def ensure_git_identity(cwd: Path) -> None:
    git(["config", "user.email", "cifix@example.local"], cwd=cwd)
    git(["config", "user.name", "CIFix Agent"], cwd=cwd)


def read_log(log_path: str | None) -> str:
    if not log_path:
        return ""
    return Path(log_path).resolve().read_text()


def infer_command(workspace_dir: Path) -> str:
    if (workspace_dir / "package.json").exists():
        return "npm test"
    return "echo 'No command inferred'"


def infer_setup_command(workspace_dir: Path, *, enabled: bool) -> str | None:
    if not enabled or not (workspace_dir / "package.json").exists():
        return None
    if (workspace_dir / "pnpm-lock.yaml").exists():
        return "pnpm install --frozen-lockfile"
    if (workspace_dir / "package-lock.json").exists():
        return "npm ci"
    if (workspace_dir / "yarn.lock").exists():
        return "yarn install --frozen-lockfile"
    return None


def map_repo(workspace_dir: Path) -> dict[str, Any]:
    files = list_files(workspace_dir)
    package_json_path = workspace_dir / "package.json"
    package_json: dict[str, Any] | None = None
    if package_json_path.exists():
        package_json = json.loads(package_json_path.read_text())

    return {
        "files": files,
        "languages": ["typescript"] if any(file.endswith((".ts", ".tsx")) for file in files) else ["javascript"],
        "packageManager": "pnpm" if (workspace_dir / "pnpm-lock.yaml").exists() else "npm",
        "scripts": (package_json or {}).get("scripts", {}),
    }


def list_files(root: Path) -> list[str]:
    result: list[str] = []
    for path in root.rglob("*"):
        if "node_modules" in path.parts or ".git" in path.parts:
            continue
        if path.is_file():
            result.append(path.relative_to(root).as_posix())
    return sorted(result)
