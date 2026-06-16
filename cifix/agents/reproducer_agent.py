from __future__ import annotations

from pathlib import Path

from ..core.artifacts import summarize_command
from ..core.trace import step
from ..tools.command import DEFAULT_ALLOWED_PREFIXES, run_command


def run_reproducer_agent(*, workspace_dir: Path, command: str, trace: list[dict]) -> dict:
    reproduction = run_command(command, workspace_dir, 20, DEFAULT_ALLOWED_PREFIXES)
    trace.append(step("ReproducerAgent", {"command": command}, summarize_command(reproduction)))
    return reproduction
