from __future__ import annotations

import subprocess
import shlex
from pathlib import Path
from typing import Any

DEFAULT_ALLOWED_PREFIXES = (
    "npm test",
    "npm run test",
    "npm run lint",
    "npm run typecheck",
    "pnpm test",
    "pnpm run test",
    "pnpm lint",
    "pnpm typecheck",
    "yarn test",
    "node --test",
)

DEFAULT_SETUP_ALLOWED_PREFIXES = (
    "npm ci",
    "npm install",
    "pnpm install --frozen-lockfile",
    "pnpm install",
    "yarn install --frozen-lockfile",
    "yarn install",
)

FORBIDDEN_SHELL_TOKENS = (";", "&&", "||", "|", ">", "<", "`", "$(", "\n")


def run_command(command: str, cwd: str | Path, timeout: int = 20, allowed_prefixes: tuple[str, ...] = DEFAULT_ALLOWED_PREFIXES) -> dict[str, Any]:
    policy_error = validate_command(command, allowed_prefixes)
    if policy_error:
        return {
            "command": command,
            "passed": False,
            "stdout": "",
            "stderr": "",
            "exitCode": 126,
            "message": policy_error,
        }
    try:
        args = shlex.split(command)
        completed = subprocess.run(
            args,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return {
            "command": command,
            "passed": completed.returncode == 0,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "exitCode": completed.returncode,
        }
    except subprocess.TimeoutExpired as error:
        return {
            "command": command,
            "passed": False,
            "stdout": error.stdout or "",
            "stderr": error.stderr or "",
            "exitCode": 124,
            "message": f"Command timed out after {timeout}s",
        }


def validate_command(command: str, allowed_prefixes: tuple[str, ...] = DEFAULT_ALLOWED_PREFIXES) -> str | None:
    normalized = " ".join(command.strip().split())
    if not normalized:
        return "Command is empty."
    if any(token in normalized for token in FORBIDDEN_SHELL_TOKENS):
        return f"Command rejected by safety policy: shell control token found in `{command}`."
    if not any(normalized == prefix or normalized.startswith(f"{prefix} ") for prefix in allowed_prefixes):
        allowed = ", ".join(allowed_prefixes)
        return f"Command rejected by safety policy: `{command}` is not in the allowlist. Allowed prefixes: {allowed}"
    return None


def git(args: list[str], cwd: str | Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        check=True,
    )
