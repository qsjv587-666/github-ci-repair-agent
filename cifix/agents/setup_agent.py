from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.artifacts import summarize_command
from ..core.trace import step
from ..tools.command import DEFAULT_SETUP_ALLOWED_PREFIXES, run_command


def run_setup_agent(*, workspace_dir: Path, setup_command: str | None, trace: list[dict]) -> dict[str, Any] | None:
    if not setup_command:
        result = {"skipped": True, "reason": "no setup command inferred or provided"}
        trace.append(step("SetupAgent", {}, result))
        return None
    setup = run_command(setup_command, workspace_dir, 120, DEFAULT_SETUP_ALLOWED_PREFIXES)
    trace.append(step("SetupAgent", {"command": setup_command}, summarize_command(setup)))
    return setup
