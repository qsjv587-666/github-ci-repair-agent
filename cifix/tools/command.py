from __future__ import annotations

import subprocess
import shlex
import os
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
    "python -m unittest",
    "python3 -m unittest",
    "pytest",
    "python -m pytest",
    "python3 -m pytest",
)

DEFAULT_SETUP_ALLOWED_PREFIXES = (
    "npm ci",
    "npm install",
    "pnpm install --frozen-lockfile",
    "pnpm install",
    "yarn install --frozen-lockfile",
    "yarn install",
    "pip install -r requirements.txt",
    "python -m pip install -r requirements.txt",
    "python3 -m pip install -r requirements.txt",
)

FORBIDDEN_SHELL_TOKENS = (";", "&&", "||", "|", ">", "<", "`", "$(", "\n")


def run_command(command: str, cwd: str | Path, timeout: int = 20, allowed_prefixes: tuple[str, ...] = DEFAULT_ALLOWED_PREFIXES, sandbox: dict[str, Any] | None = None) -> dict[str, Any]:
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
    sandbox_config = sandbox_config_from_env(sandbox)
    try:
        args = shlex.split(command)
        run_args = docker_args(args, cwd, sandbox_config) if sandbox_config["mode"] == "docker" else args
        completed = subprocess.run(
            run_args,
            cwd=str(cwd) if sandbox_config["mode"] == "local" else None,
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
            "sandbox": public_sandbox(sandbox_config),
        }
    except FileNotFoundError as error:
        return {
            "command": command,
            "passed": False,
            "stdout": "",
            "stderr": "",
            "exitCode": 127,
            "message": str(error),
            "sandbox": public_sandbox(sandbox_config),
        }
    except subprocess.TimeoutExpired as error:
        return {
            "command": command,
            "passed": False,
            "stdout": output_text(error.stdout),
            "stderr": output_text(error.stderr),
            "exitCode": 124,
            "message": f"Command timed out after {timeout}s",
            "sandbox": public_sandbox(sandbox_config),
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


def ensure_docker_image(image: str, timeout: int = 300) -> dict[str, Any]:
    inspect = subprocess.run(
        ["docker", "image", "inspect", image],
        text=True,
        capture_output=True,
        check=False,
    )
    if inspect.returncode == 0:
        return {"image": image, "status": "present"}
    pull = subprocess.run(
        ["docker", "pull", image],
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return {
        "image": image,
        "status": "pulled" if pull.returncode == 0 else "failed",
        "exitCode": pull.returncode,
        "stdout": pull.stdout,
        "stderr": pull.stderr,
    }


def sandbox_config_from_env(sandbox: dict[str, Any] | None = None) -> dict[str, Any]:
    sandbox = sandbox or {}
    mode = str(sandbox.get("mode") or os.getenv("CIFIX_SANDBOX") or "local").lower()
    if mode not in {"local", "docker"}:
        raise ValueError(f"Unsupported sandbox mode: {mode}")
    return {
        "mode": mode,
        "image": sandbox.get("image") or os.getenv("CIFIX_DOCKER_IMAGE") or "node:20",
        "network": sandbox.get("network") or os.getenv("CIFIX_DOCKER_NETWORK") or "bridge",
    }


def docker_args(args: list[str], cwd: str | Path, sandbox: dict[str, Any]) -> list[str]:
    workspace = Path(cwd).resolve()
    docker = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{workspace}:/workspace",
        "-w",
        "/workspace",
        "-e",
        "CI=true",
    ]
    if sandbox.get("network") == "none":
        docker.extend(["--network", "none"])
    return [*docker, sandbox["image"], *args]


def public_sandbox(sandbox: dict[str, Any]) -> dict[str, Any]:
    result = {"mode": sandbox["mode"]}
    if sandbox["mode"] == "docker":
        result["image"] = sandbox["image"]
        result["network"] = sandbox["network"]
    return result


def output_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)
