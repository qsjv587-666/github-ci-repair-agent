from __future__ import annotations

import json
from typing import Any


def preview(text: str | None, limit: int = 2000) -> str:
    return (text or "")[:limit]


def summarize_command(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "command": result.get("command"),
        "passed": result.get("passed"),
        "exitCode": result.get("exitCode"),
        "stdoutPreview": preview(result.get("stdout")),
        "stderrPreview": preview(result.get("stderr")),
        "message": result.get("message"),
        "sandbox": result.get("sandbox"),
    }


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"
